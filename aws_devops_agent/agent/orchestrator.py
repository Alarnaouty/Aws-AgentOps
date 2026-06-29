"""
LangGraph-based agent orchestrator.

Graph nodes:
  monitor  →  analyze  →  heal  →  report

The graph runs in an infinite loop driven by the poll interval.
Events are published via the shared event_bus asyncio.Queue so the
FastAPI WebSocket layer can stream them to connected clients.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from aws_devops_agent.analyzer.engine import analyze_anomaly, detect_anomalies
from aws_devops_agent.config import get_settings
from aws_devops_agent.healing.executor import execute_healing_action
from aws_devops_agent.notifications.slack import notify_anomaly, notify_healing
from aws_devops_agent.models import (
    AgentEvent,
    Anomaly,
    HealingAction,
    HealingStatus,
    ResourceSnapshot,
)
from aws_devops_agent.monitoring.collector import collect_all

log = structlog.get_logger(__name__)

# ── Shared event bus — created lazily inside the running event loop ───────────
# Do NOT instantiate asyncio.Queue at module level on Python 3.10+.
_event_bus: Optional[asyncio.Queue] = None


def get_event_bus() -> asyncio.Queue:
    """Return the singleton event bus, creating it on first call inside the loop."""
    global _event_bus
    if _event_bus is None:
        _event_bus = asyncio.Queue(maxsize=1000)
    return _event_bus


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
    bus = get_event_bus()
    event = AgentEvent(event_type=event_type, payload=payload)
    try:
        bus.put_nowait(event)
    except asyncio.QueueFull:
        bus.get_nowait()       # drop oldest to make room
        bus.put_nowait(event)


# ─────────────────────────────────────────────────────────────────────────────
# Graph state schema
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    snapshots: List[ResourceSnapshot]
    anomalies: List[Anomaly]
    healing_actions: List[HealingAction]
    analysis_summaries: List[str]
    cycle_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Node: monitor
# ─────────────────────────────────────────────────────────────────────────────

def node_monitor(state: AgentState) -> AgentState:
    log.info("agent.node", node="monitor")
    snapshots = collect_all()
    _emit("monitor", {
        "cycle_id": state["cycle_id"],
        "resources_collected": len(snapshots),
        "resources": [
            {
                "id": s.resource_id,
                "type": s.resource_type,
                "metrics": [{"name": m.name, "value": m.value} for m in s.metrics],
            }
            for s in snapshots
        ],
    })
    return {**state, "snapshots": snapshots}


# ─────────────────────────────────────────────────────────────────────────────
# Node: analyze
# ─────────────────────────────────────────────────────────────────────────────

def node_analyze(state: AgentState) -> AgentState:
    log.info("agent.node", node="analyze")
    anomalies = detect_anomalies(state["snapshots"])

    summaries: List[str] = []
    healing_actions: List[HealingAction] = []

    for anomaly in anomalies:
        _emit("analyze", {
            "cycle_id": state["cycle_id"],
            "resource_id": anomaly.resource_id,
            "severity": anomaly.severity.value,
            "title": anomaly.title,
        })
        # Slack: alert on anomaly detection
        notify_anomaly(anomaly)
        try:
            summary, action = analyze_anomaly(anomaly)
            summaries.append(summary)
            if action:
                healing_actions.append(action)
        except Exception as exc:
            log.error("agent.analyze_error", error=str(exc), resource_id=anomaly.resource_id)
            _emit("error", {"message": str(exc), "resource_id": anomaly.resource_id})

    return {
        **state,
        "anomalies": anomalies,
        "analysis_summaries": summaries,
        "healing_actions": healing_actions,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: heal
# ─────────────────────────────────────────────────────────────────────────────

def node_heal(state: AgentState) -> AgentState:
    log.info("agent.node", node="heal", actions=len(state["healing_actions"]))
    executed: List[HealingAction] = []

    summaries = state.get("analysis_summaries", [])

    for i, action in enumerate(state["healing_actions"]):
        _emit("heal", {
            "cycle_id": state["cycle_id"],
            "action_id": action.action_id,
            "action_type": action.action_type,
            "resource_id": action.anomaly.resource_id,
            "reasoning": action.reasoning,
            "status": "starting",
        })
        result = execute_healing_action(action)
        executed.append(result)
        _emit("heal", {
            "cycle_id": state["cycle_id"],
            "action_id": action.action_id,
            "action_type": action.action_type,
            "resource_id": action.anomaly.resource_id,
            "status": result.status.value,
            "result": result.result,
        })
        # Slack: report healing outcome with matching analysis summary (if available)
        analysis_summary = summaries[i] if i < len(summaries) else None
        notify_healing(result, analysis_summary)

    return {**state, "healing_actions": executed}


# ─────────────────────────────────────────────────────────────────────────────
# Node: report
# ─────────────────────────────────────────────────────────────────────────────

def node_report(state: AgentState) -> AgentState:
    succeeded = [a for a in state["healing_actions"] if a.status == HealingStatus.SUCCESS]
    failed    = [a for a in state["healing_actions"] if a.status == HealingStatus.FAILED]
    skipped   = [a for a in state["healing_actions"] if a.status == HealingStatus.SKIPPED]

    log.info(
        "agent.cycle_complete",
        cycle_id=state["cycle_id"],
        anomalies=len(state["anomalies"]),
        actions_total=len(state["healing_actions"]),
        succeeded=len(succeeded),
        failed=len(failed),
    )

    _emit("info", {
        "cycle_id": state["cycle_id"],
        "summary": {
            "resources_scanned":  len(state["snapshots"]),
            "anomalies_found":    len(state["anomalies"]),
            "actions_succeeded":  len(succeeded),
            "actions_failed":     len(failed),
            "actions_skipped":    len(skipped),
        },
        "analysis_summaries": state["analysis_summaries"],
    })
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Conditional edge
# ─────────────────────────────────────────────────────────────────────────────

def _should_heal(state: AgentState) -> str:
    return "heal" if state["healing_actions"] else "report"


# ─────────────────────────────────────────────────────────────────────────────
# Build the LangGraph
# ─────────────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("monitor", node_monitor)
    g.add_node("analyze", node_analyze)
    g.add_node("heal",    node_heal)
    g.add_node("report",  node_report)

    g.set_entry_point("monitor")
    g.add_edge("monitor", "analyze")
    g.add_conditional_edges(
        "analyze", _should_heal, {"heal": "heal", "report": "report"}
    )
    g.add_edge("heal", "report")
    g.add_edge("report", END)
    return g.compile()


_graph = build_graph()


# ─────────────────────────────────────────────────────────────────────────────
# Agent loop
# ─────────────────────────────────────────────────────────────────────────────

async def run_agent_loop() -> None:
    """Async loop: runs one monitoring cycle per poll interval."""
    cfg = get_settings()
    log.info("agent.starting", poll_interval=cfg.agent_poll_interval_seconds)

    # Small delay so the HTTP server is fully accepting connections first
    await asyncio.sleep(2)

    while True:
        cycle_id = str(uuid.uuid4())[:8]
        log.info("agent.cycle_start", cycle_id=cycle_id)
        _emit("info", {"message": "Cycle started", "cycle_id": cycle_id})

        initial_state: AgentState = {
            "snapshots":          [],
            "anomalies":          [],
            "healing_actions":    [],
            "analysis_summaries": [],
            "cycle_id":           cycle_id,
        }

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _graph.invoke(initial_state))
        except Exception as exc:
            log.error("agent.cycle_error", cycle_id=cycle_id, error=str(exc))
            _emit("error", {"message": str(exc), "cycle_id": cycle_id})

        await asyncio.sleep(cfg.agent_poll_interval_seconds)
