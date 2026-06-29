"""
FastAPI server — REST API + WebSocket live feed.

Endpoints
---------
GET  /health                       — liveness probe
GET  /api/status                   — latest agent status snapshot
POST /api/query                    — RAG question → LLM-synthesized answer
POST /api/heal                     — trigger a healing action on-demand
POST /api/knowledge/rebuild        — force-rebuild the vector store
GET  /openapi-orchestrate.json     — WatsonX Orchestrate skill descriptor
WS   /ws/events                    — stream AgentEvent JSON objects to clients
GET  /                             — built-in HTML dashboard
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from aws_devops_agent.agent.orchestrator import get_event_bus, run_agent_loop
from aws_devops_agent.analyzer.engine import detect_anomalies, _get_llm
from aws_devops_agent.config import get_settings
from aws_devops_agent.healing.executor import execute_healing_action
from aws_devops_agent.models import Anomaly, HealingAction, Severity
from aws_devops_agent.monitoring.collector import collect_all
from aws_devops_agent.rag.vector_store import build_vector_store, get_retriever

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info("ws.connected", total=len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info("ws.disconnected", total=len(self.active))

    async def broadcast(self, message: str):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


# ─────────────────────────────────────────────────────────────────────────────
# Background: relay agent events → all connected WebSocket clients
# ─────────────────────────────────────────────────────────────────────────────

async def _event_relay():
    bus = get_event_bus()          # safe — called inside the running loop
    while True:
        try:
            event = await bus.get()
            payload = json.dumps(event.model_dump(mode="json"))
            await manager.broadcast(payload)
        except Exception as exc:
            log.error("relay.error", error=str(exc))
            await asyncio.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — start tasks WITHOUT blocking server startup
# ─────────────────────────────────────────────────────────────────────────────

async def _build_vector_store_bg():
    """Build vector store in background so server is ready immediately."""
    try:
        log.info("vector_store.building_bg")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, build_vector_store)
        log.info("vector_store.ready")
    except Exception as exc:
        log.error("vector_store.build_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start all background tasks immediately — server stays responsive
    vs_task    = asyncio.create_task(_build_vector_store_bg())
    relay_task = asyncio.create_task(_event_relay())
    agent_task = asyncio.create_task(run_agent_loop())

    log.info("server.startup", tasks=["vector_store", "event_relay", "agent_loop"])

    yield   # ← server is UP and accepting connections right here

    agent_task.cancel()
    relay_task.cancel()
    vs_task.cancel()


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AWS DevOps RAG Agent — WatsonX Edition",
    version="2.0.0",
    description="Monitoring · Analyzing · Self-Healing · Acting — Powered by IBM WatsonX",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/status")
async def get_status():
    cfg = get_settings()
    try:
        loop = asyncio.get_event_loop()
        snapshots = await loop.run_in_executor(None, collect_all)
        anomalies = await loop.run_in_executor(None, detect_anomalies, snapshots)
        return {
            "resources_scanned": len(snapshots),
            "anomalies": [
                {
                    "resource_id":   a.resource_id,
                    "resource_type": a.resource_type,
                    "severity":      a.severity.value,
                    "title":         a.title,
                    "description":   a.description,
                }
                for a in anomalies
            ],
            "dry_run": cfg.agent_dry_run,
            "region":  cfg.aws_default_region,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Prompt for the synthesized-answer chain ──────────────────────────────────
_QA_SYSTEM = (
    "You are an expert AWS DevOps and SRE assistant. "
    "Use the provided knowledge-base context to answer the user's question concisely and accurately. "
    "If the context does not contain enough information, say so clearly. "
    "Do NOT invent facts. Keep the answer under 200 words."
)


def _synthesize_answer(question: str, context: str) -> str:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    prompt = ChatPromptTemplate.from_messages([
        ("system", _QA_SYSTEM),
        ("human", "Context:\n{context}\n\nQuestion: {question}"),
    ])
    chain = prompt | _get_llm() | StrOutputParser()
    return chain.invoke({"context": context, "question": question})


class QueryRequest(BaseModel):
    question: str
    k: int = 6


@app.post(
    "/api/query",
    summary="Ask the AWS knowledge base",
    description=(
        "Ask any AWS DevOps / SRE question. "
        "Retrieves relevant runbook context and returns an LLM-synthesized answer. "
        "Use this skill in WatsonX Orchestrate to get instant expert guidance."
    ),
    tags=["Knowledge Base"],
)
async def rag_query(req: QueryRequest):
    loop = asyncio.get_event_loop()
    retriever = await loop.run_in_executor(None, get_retriever, req.k)
    docs      = await loop.run_in_executor(None, retriever.invoke, req.question)
    context   = "\n\n".join(d.page_content for d in docs)
    answer    = await loop.run_in_executor(None, _synthesize_answer, req.question, context)
    return {
        "question": req.question,
        "answer":   answer,
        "sources": [
            {"content": d.page_content[:300], "source": d.metadata.get("source", "")}
            for d in docs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# On-demand healing trigger — for WatsonX Orchestrate "Heal resource" skill
# ─────────────────────────────────────────────────────────────────────────────

class HealRequest(BaseModel):
    resource_id:   str
    resource_type: str
    action_type:   str
    parameters:    Dict[str, Any] = {}
    reasoning:     Optional[str] = "Triggered manually via Orchestrate"


@app.post(
    "/api/heal",
    summary="Trigger a self-healing action",
    description=(
        "Manually trigger one of the 12 registered self-healing actions against an AWS resource. "
        "Respects DRY_RUN mode. Use this skill in WatsonX Orchestrate to remediate issues on demand."
    ),
    tags=["Self-Healing"],
)
async def trigger_heal(req: HealRequest):
    # Build a minimal synthetic anomaly so execute_healing_action has the right shape
    synthetic_anomaly = Anomaly(
        resource_id=req.resource_id,
        resource_type=req.resource_type,
        severity=Severity.CRITICAL,
        title=f"Manual heal via Orchestrate: {req.action_type}",
        description=req.reasoning or "",
    )
    action = HealingAction(
        action_id=str(uuid.uuid4()),
        anomaly=synthetic_anomaly,
        action_type=req.action_type,
        parameters=req.parameters,
        reasoning=req.reasoning or "",
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, execute_healing_action, action)
    return {
        "action_id":   result.action_id,
        "action_type": result.action_type,
        "resource_id": req.resource_id,
        "status":      result.status.value,
        "result":      result.result,
        "dry_run":     get_settings().agent_dry_run,
    }


@app.post(
    "/api/knowledge/rebuild",
    summary="Rebuild the knowledge base",
    description="Force-rebuild the FAISS vector store from all runbook files in knowledge_base/.",
    tags=["Knowledge Base"],
)
async def rebuild_knowledge_base():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: build_vector_store(force_rebuild=True))
    return {"message": "Vector store rebuilt successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# WatsonX Orchestrate skill descriptor
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/openapi-orchestrate.json",
    include_in_schema=False,
    summary="WatsonX Orchestrate skill descriptor",
)
async def orchestrate_openapi(request: Request):
    """
    Returns a trimmed OpenAPI document containing only the three skills
    that WatsonX Orchestrate should import:
      - GET  /api/status  — live anomaly snapshot
      - POST /api/query   — RAG knowledge-base Q&A
      - POST /api/heal    — on-demand healing action
    """
    cfg = get_settings()
    base_url = str(request.base_url).rstrip("/")
    spec: Dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "AWS DevOps RAG Agent — WatsonX Orchestrate Skills",
            "version": "2.0.0",
            "description": (
                "Three skills for monitoring and remediating AWS infrastructure anomalies. "
                "Import this spec at: WatsonX Orchestrate → Skills catalog → Add skill → From API."
            ),
        },
        "servers": [{"url": base_url, "description": "AWS DevOps RAG Agent"}],
        "paths": {
            "/api/status": {
                "get": {
                    "operationId": "getAwsStatus",
                    "summary": "Get live AWS anomaly status",
                    "description": (
                        "Scans all monitored AWS resources (EC2, RDS, ECS, Lambda, ALB) "
                        "and returns any detected anomalies with severity and description."
                    ),
                    "tags": ["Monitoring"],
                    "responses": {
                        "200": {
                            "description": "Live resource snapshot",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "resources_scanned": {"type": "integer"},
                                            "anomalies": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "resource_id":   {"type": "string"},
                                                        "resource_type": {"type": "string"},
                                                        "severity":      {"type": "string", "enum": ["OK", "WARNING", "CRITICAL"]},
                                                        "title":         {"type": "string"},
                                                        "description":   {"type": "string"},
                                                    },
                                                },
                                            },
                                            "dry_run": {"type": "boolean"},
                                            "region":  {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/query": {
                "post": {
                    "operationId": "queryKnowledgeBase",
                    "summary": "Ask the AWS DevOps knowledge base",
                    "description": (
                        "Ask any AWS DevOps or SRE question. "
                        "Returns an AI-synthesized answer grounded in AWS runbooks."
                    ),
                    "tags": ["Knowledge Base"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["question"],
                                    "properties": {
                                        "question": {"type": "string", "description": "The question to answer"},
                                        "k":        {"type": "integer", "default": 6, "description": "Number of runbook chunks to retrieve"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "AI-synthesized answer",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "question": {"type": "string"},
                                            "answer":   {"type": "string", "description": "LLM-synthesized answer"},
                                            "sources":  {"type": "array", "items": {"type": "object"}},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/heal": {
                "post": {
                    "operationId": "triggerHealingAction",
                    "summary": "Trigger a self-healing action on an AWS resource",
                    "description": (
                        "Manually execute one of the 12 registered AWS self-healing actions. "
                        "Actions: restart_ec2_service, stop_start_instance, scale_out_asg, "
                        "cleanup_disk_ssm, reboot_rds_instance, modify_rds_storage, "
                        "rollback_lambda_version, update_lambda_timeout, update_lambda_memory, "
                        "update_ecs_service, scale_ecs_service, deregister_unhealthy_targets."
                    ),
                    "tags": ["Self-Healing"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["resource_id", "resource_type", "action_type"],
                                    "properties": {
                                        "resource_id":   {"type": "string", "description": "AWS resource ID (e.g. i-0abc123)"},
                                        "resource_type": {"type": "string", "description": "ec2 | rds | ecs | lambda | alb"},
                                        "action_type":   {"type": "string", "description": "One of the 12 registered action types"},
                                        "parameters":    {"type": "object", "description": "Action-specific parameters"},
                                        "reasoning":     {"type": "string", "description": "Why this action is being triggered"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Action execution result",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "action_id":   {"type": "string"},
                                            "action_type": {"type": "string"},
                                            "resource_id": {"type": "string"},
                                            "status":      {"type": "string", "enum": ["SUCCESS", "FAILED", "SKIPPED"]},
                                            "result":      {"type": "string"},
                                            "dry_run":     {"type": "boolean"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }
    return JSONResponse(content=spec)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — proper ping/pong keepalive, no receive_text() blocking
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/events")
async def websocket_events(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # Send a ping every 20 s to keep the connection alive through
            # proxies / firewalls. The browser ws.onmessage handles it fine.
            await asyncio.sleep(20)
            await ws.send_text(json.dumps({"event_type": "ping", "payload": {}}))
    except (WebSocketDisconnect, Exception):
        manager.disconnect(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in dashboard
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AWS DevOps RAG Agent — WatsonX</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;background:#0d1117;color:#c9d1d9;font-size:14px}
  header{background:#1a1f2e;border-bottom:2px solid #4a90d9;padding:14px 24px;display:flex;align-items:center;gap:12px}
  header h1{font-size:18px;font-weight:600}
  .ibm-tag{font-size:10px;font-weight:700;color:#4a90d9;border:1px solid #4a90d9;padding:1px 7px;border-radius:3px;letter-spacing:.5px;margin-left:4px}
  .badge{background:#238636;color:#fff;font-size:11px;padding:2px 10px;border-radius:12px;font-weight:600}
  .badge.offline{background:#484f58}
  .badge.warn{background:#9e6a03}
  main{max-width:1100px;margin:24px auto;padding:0 16px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
  .card .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
  .card .value{font-size:28px;font-weight:700;margin-top:6px}
  #log{background:#161b22;border:1px solid #30363d;border-radius:8px;height:480px;overflow-y:auto;padding:12px;font-family:monospace;font-size:12px}
  .ev{padding:5px 0;border-bottom:1px solid #21262d;line-height:1.5}
  .ts{color:#4a90d9;margin-right:8px}
  .type{font-weight:700;margin-right:8px;min-width:60px;display:inline-block}
  .type-monitor{color:#3fb950}.type-analyze{color:#d29922}
  .type-heal{color:#4a90d9}.type-error{color:#f85149}
  .type-info{color:#8b949e}.type-ping{color:#21262d}
  h2{font-size:13px;margin-bottom:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.4px}
  .status-bar{font-size:12px;color:#57606a;margin-bottom:16px}
  .provider-pill{font-size:10px;background:#1a3a5c;color:#4a90d9;border-radius:4px;padding:2px 8px;margin-left:8px}
</style>
</head>
<body>
<header>
  <h1>&#9889; AWS DevOps RAG Agent <span class="ibm-tag">IBM WATSONX</span></h1>
  <span class="badge offline" id="conn-badge">CONNECTING</span>
  <span style="margin-left:auto;font-size:12px;color:#8b949e" id="last-seen"></span>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="label">Resources Scanned</div><div class="value" id="c-resources">—</div></div>
    <div class="card"><div class="label">Anomalies Found</div><div class="value" id="c-anomalies">—</div></div>
    <div class="card"><div class="label">Actions Succeeded</div><div class="value" id="c-healed">—</div></div>
    <div class="card"><div class="label">Actions Failed</div><div class="value" id="c-failed">—</div></div>
    <div class="card"><div class="label">Cycles Run</div><div class="value" id="c-cycles">0</div></div>
  </div>
  <h2>Live Event Stream</h2>
  <div id="log"><div class="ev" style="color:#484f58;padding:8px 0">Waiting for agent events...</div></div>
</main>
<script>
  let cycles = 0;
  let retryDelay = 1000;
  const badge = document.getElementById('conn-badge');
  const lastSeen = document.getElementById('last-seen');

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(proto + '://' + location.host + '/ws/events');

    ws.onopen = () => {
      badge.textContent = 'LIVE';
      badge.className = 'badge';
      retryDelay = 1000;
      appendEvent({event_type:'info', payload:{message:'WebSocket connected'}, timestamp: new Date().toISOString()});
    };

    ws.onclose = () => {
      badge.textContent = 'RECONNECTING...';
      badge.className = 'badge offline';
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.event_type === 'ping') return;          // ignore heartbeat
      lastSeen.textContent = 'Last event: ' + new Date().toLocaleTimeString();
      appendEvent(ev);
      if (ev.event_type === 'info' && ev.payload.summary) {
        const s = ev.payload.summary;
        document.getElementById('c-resources').textContent = s.resources_scanned ?? '—';
        document.getElementById('c-anomalies').textContent = s.anomalies_found ?? '—';
        document.getElementById('c-healed').textContent   = s.actions_succeeded ?? '—';
        document.getElementById('c-failed').textContent   = s.actions_failed ?? '—';
        cycles++;
        document.getElementById('c-cycles').textContent = cycles;
      }
    };
  }

  function appendEvent(ev) {
    const logEl = document.getElementById('log');
    // Clear placeholder
    if (logEl.firstElementChild && logEl.firstElementChild.style.color === 'rgb(72, 79, 88)') {
      logEl.innerHTML = '';
    }
    const div = document.createElement('div');
    div.className = 'ev';
    const ts = new Date(ev.timestamp || new Date()).toLocaleTimeString();
    const payload = JSON.stringify(ev.payload).substring(0, 260);
    div.innerHTML = '<span class="ts">' + ts + '</span>'
      + '<span class="type type-' + ev.event_type + '">' + ev.event_type.toUpperCase() + '</span>'
      + '<span>' + escHtml(payload) + '</span>';
    logEl.prepend(div);
    if (logEl.children.length > 300) logEl.lastElementChild.remove();
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  connect();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
