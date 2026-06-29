"""
EC2 Stress Simulator — injects synthetic overload scenarios directly into
the agent's analyzer and watches the full detect → analyze (LLM+RAG) → heal
pipeline in real time.

Since the EC2 instance is not SSM-managed and AWS blocks writes to the
AWS/ namespace, we simulate by:
  1. Building synthetic ResourceSnapshots with critical metric values
  2. Running them through the real analyzer (rule engine + LLM + RAG)
  3. Watching the real healing recommendations come out
  4. Streaming all events live to the WebSocket dashboard

Run: python tests/stress_scenario.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import List

import httpx
import websockets

from aws_devops_agent.analyzer.engine import analyze_anomaly, detect_anomalies
from aws_devops_agent.config import get_settings
from aws_devops_agent.models import MetricPoint, ResourceSnapshot, Severity, Anomaly

BASE     = "http://localhost:8000"
WS_BASE  = "ws://localhost:8000"
INSTANCE = "i-0e9efcc6253ff7a3e"
UTC      = timezone.utc

# ── ANSI colours ──────────────────────────────────────────────────────────────
RED    = "\033[91m"
YEL    = "\033[93m"
GRN    = "\033[92m"
BLU    = "\033[94m"
CYN    = "\033[96m"
BOLD   = "\033[1m"
RST    = "\033[0m"
DIM    = "\033[2m"


def hdr(title: str):
    print(f"\n{BOLD}{BLU}{'─'*60}{RST}")
    print(f"{BOLD}{BLU}  {title}{RST}")
    print(f"{BOLD}{BLU}{'─'*60}{RST}")


def ok(msg):  print(f"  {GRN}✓{RST}  {msg}")
def warn(msg): print(f"  {YEL}⚠{RST}  {msg}")
def err(msg):  print(f"  {RED}✗{RST}  {msg}")
def info(msg): print(f"  {DIM}→{RST}  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Stress scenarios — each is a ResourceSnapshot with critical values
# ─────────────────────────────────────────────────────────────────────────────

def make_snapshot(metrics: List[dict], resource_type: str = "ec2",
                  resource_id: str = INSTANCE, raw: dict = None) -> ResourceSnapshot:
    return ResourceSnapshot(
        resource_id=resource_id,
        resource_type=resource_type,
        region=get_settings().aws_default_region,
        metrics=[MetricPoint(**m) for m in metrics],
        raw=raw or {},
    )


SCENARIOS = [
    {
        "name": "EC2 CPU Spike — 94%",
        "emoji": "🔥",
        "snapshot": make_snapshot([
            {"name": "CPUUtilization",            "value": 94.0,  "unit": "%"},
            {"name": "NetworkIn",                 "value": 850000,"unit": "Bytes"},
            {"name": "StatusCheckFailed",         "value": 0.0,   "unit": "Count"},
        ], raw={"instance_type": "c7i-flex.large", "state": "running"}),
    },
    {
        "name": "EC2 Status Check Failed",
        "emoji": "💀",
        "snapshot": make_snapshot([
            {"name": "CPUUtilization",              "value": 12.0, "unit": "%"},
            {"name": "StatusCheckFailed",           "value": 1.0,  "unit": "Count"},
            {"name": "StatusCheckFailed_Instance",  "value": 1.0,  "unit": "Count"},
        ], raw={"instance_type": "c7i-flex.large", "state": "running"}),
    },
    {
        "name": "RDS Storage Critical — 1.2 GB left",
        "emoji": "💾",
        "snapshot": make_snapshot([
            {"name": "CPUUtilization",      "value": 55.0,      "unit": "%"},
            {"name": "FreeStorageSpace",    "value": 1_200_000_000, "unit": "Bytes"},
            {"name": "DatabaseConnections", "value": 42.0,      "unit": "Count"},
        ], resource_type="rds", resource_id="prod-mysql-01",
           raw={"engine": "mysql", "instance_class": "db.t3.medium", "status": "available"}),
    },
    {
        "name": "RDS Connection Flood — 98 connections",
        "emoji": "🌊",
        "snapshot": make_snapshot([
            {"name": "CPUUtilization",      "value": 78.0, "unit": "%"},
            {"name": "DatabaseConnections", "value": 98.0, "unit": "Count"},
            {"name": "FreeStorageSpace",    "value": 8_000_000_000, "unit": "Bytes"},
        ], resource_type="rds", resource_id="prod-mysql-01",
           raw={"engine": "mysql", "instance_class": "db.t3.medium", "status": "available"}),
    },
    {
        "name": "Lambda Error Storm — 35 errors",
        "emoji": "⚡",
        "snapshot": make_snapshot([
            {"name": "Invocations", "value": 200.0, "unit": "Count"},
            {"name": "Errors",      "value": 35.0,  "unit": "Count"},
            {"name": "Throttles",   "value": 12.0,  "unit": "Count"},
            {"name": "Duration",    "value": 4800.0,"unit": "Milliseconds"},
        ], resource_type="lambda", resource_id="prod-api-handler",
           raw={"runtime": "python3.12", "memory": 512, "timeout": 6}),
    },
    {
        "name": "ALB 5xx Surge — 45 server errors",
        "emoji": "🚨",
        "snapshot": make_snapshot([
            {"name": "RequestCount",              "value": 1200.0, "unit": "Count"},
            {"name": "HTTPCode_ELB_5XX_Count",    "value": 45.0,   "unit": "Count"},
            {"name": "HTTPCode_Target_5XX_Count", "value": 38.0,   "unit": "Count"},
            {"name": "TargetResponseTime",        "value": 4.2,    "unit": "Seconds"},
            {"name": "UnHealthyHostCount",        "value": 2.0,    "unit": "Count"},
            {"name": "HealthyHostCount",          "value": 1.0,    "unit": "Count"},
        ], resource_type="alb", resource_id="prod-alb",
           raw={"dns_name": "prod-alb.us-east-1.elb.amazonaws.com", "state": "active"}),
    },
    {
        "name": "ECS Memory Exhaustion — 97%",
        "emoji": "🐳",
        "snapshot": make_snapshot([
            {"name": "CPUUtilization",    "value": 82.0,  "unit": "%"},
            {"name": "MemoryUtilization", "value": 97.0,  "unit": "%"},
            {"name": "RunningCount",      "value": 2.0,   "unit": "Count"},
            {"name": "DesiredCount",      "value": 4.0,   "unit": "Count"},
        ], resource_type="ecs", resource_id="prod-cluster/api-service",
           raw={"cluster": "prod-cluster", "status": "ACTIVE", "launch_type": "FARGATE"}),
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket event listener (background)
# ─────────────────────────────────────────────────────────────────────────────

ws_events: List[dict] = []

async def _ws_listener():
    """Collect real-time events from the agent during the scenario run."""
    try:
        async with websockets.connect(f"{WS_BASE}/ws/events", ping_interval=None) as ws:
            ok("WebSocket connected — watching for agent events...")
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                ev = json.loads(raw)
                if ev.get("event_type") == "ping":
                    continue
                ws_events.append(ev)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Run one scenario
# ─────────────────────────────────────────────────────────────────────────────

def run_scenario(scenario: dict) -> dict:
    name    = scenario["name"]
    emoji   = scenario["emoji"]
    snap    = scenario["snapshot"]

    print(f"\n  {emoji}  {BOLD}{name}{RST}")
    t0 = time.perf_counter()

    # Step 1 — rule-based detection
    anomalies = detect_anomalies([snap])
    if not anomalies:
        warn("No anomaly detected (below threshold)")
        return {"name": name, "anomalies": 0, "action": None, "elapsed": 0}

    for a in anomalies:
        sev_color = RED if a.severity == Severity.CRITICAL else YEL
        info(f"Detected: {sev_color}{a.severity.value}{RST}  {a.title}")

    # Step 2 — LLM + RAG analysis on the worst anomaly
    worst = sorted(anomalies, key=lambda x: (x.severity == Severity.CRITICAL), reverse=True)[0]
    info(f"Analyzing with LLM+RAG: {DIM}{worst.description}{RST}")

    try:
        summary, healing = analyze_anomaly(worst)
        elapsed = time.perf_counter() - t0

        if healing:
            ok(f"Healing action: {GRN}{healing.action_type}{RST}")
            info(f"Parameters   : {healing.parameters}")
            info(f"Reasoning    : {DIM}{healing.reasoning[:120]}...{RST}" if len(healing.reasoning) > 120 else f"Reasoning    : {DIM}{healing.reasoning}{RST}")
        else:
            warn("LLM returned no healing action")

        # Print root cause from summary
        for line in summary.splitlines():
            info(line)

        info(f"Elapsed: {elapsed:.1f}s")
        return {
            "name": name,
            "anomalies": len(anomalies),
            "severity": worst.severity.value,
            "action": healing.action_type if healing else None,
            "params": healing.parameters if healing else {},
            "elapsed": elapsed,
        }
    except Exception as exc:
        err(f"LLM analysis failed: {exc}")
        return {"name": name, "anomalies": len(anomalies), "action": "ERROR", "elapsed": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}{'='*60}")
    print("  AWS DevOps RAG Agent — Live Stress Scenario Runner")
    print(f"{'='*60}{RST}")

    cfg = get_settings()
    print(f"\n  Instance : {INSTANCE}")
    print(f"  Region   : {cfg.aws_default_region}")
    print(f"  Dry run  : {GRN}YES — no real AWS changes{RST}")
    print(f"  Scenarios: {len(SCENARIOS)}")

    # Verify server is up
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{BASE}/health", timeout=5)
            assert r.status_code == 200
        ok(f"Server reachable at {BASE}")
    except Exception as e:
        err(f"Server not reachable — start it first: python main.py\n  ({e})")
        return

    # Start WS listener in background
    ws_task = asyncio.create_task(_ws_listener())
    await asyncio.sleep(0.5)

    # ── Run all scenarios ────────────────────────────────────────────────────
    results = []
    wall = time.perf_counter()

    for i, scenario in enumerate(SCENARIOS, 1):
        hdr(f"Scenario {i}/{len(SCENARIOS)}")
        result = await asyncio.get_event_loop().run_in_executor(
            None, run_scenario, scenario
        )
        results.append(result)

    ws_task.cancel()
    total_wall = time.perf_counter() - wall

    # ── Summary table ────────────────────────────────────────────────────────
    hdr("STRESS TEST RESULTS")
    print(f"\n  {'Scenario':<45} {'Severity':<10} {'Action':<35} {'Time':>6}")
    print(f"  {'─'*45} {'─'*10} {'─'*35} {'─'*6}")
    all_pass = True
    for r in results:
        sev   = r.get("severity", "—")
        sev_c = RED if sev == "CRITICAL" else YEL if sev == "WARNING" else DIM
        act   = r.get("action") or "—"
        act_c = GRN if act not in ("—", "ERROR", None) else RED if act == "ERROR" else DIM
        t     = f"{r['elapsed']:.1f}s" if r['elapsed'] else "—"
        name  = r["name"][:44]
        print(f"  {name:<45} {sev_c}{sev:<10}{RST} {act_c}{act:<35}{RST} {t:>6}")
        if act in ("—", "ERROR", None):
            all_pass = False

    detected   = sum(1 for r in results if r.get("anomalies", 0) > 0)
    with_action = sum(1 for r in results if r.get("action") and r["action"] not in ("—", "ERROR"))

    print(f"\n  {'─'*60}")
    print(f"  Anomalies detected : {detected}/{len(SCENARIOS)}")
    print(f"  Healing recommended: {with_action}/{len(SCENARIOS)}")
    print(f"  WS events captured : {len(ws_events)}")
    print(f"  Total wall time    : {total_wall:.1f}s")

    if with_action == len(SCENARIOS):
        print(f"\n  {GRN}{BOLD}ALL SCENARIOS PASSED — Agent is fully operational{RST}")
    else:
        print(f"\n  {YEL}{BOLD}Some scenarios need attention (see above){RST}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
