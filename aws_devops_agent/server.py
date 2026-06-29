"""
FastAPI server — REST API + WebSocket live feed.

Endpoints
---------
GET  /health                 — liveness probe
GET  /api/status             — latest agent status snapshot
POST /api/knowledge/rebuild  — force-rebuild the vector store
POST /api/query              — ad-hoc RAG query
WS   /ws/events              — stream AgentEvent JSON objects to clients
GET  /                       — built-in HTML dashboard
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import List

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from aws_devops_agent.agent.orchestrator import get_event_bus, run_agent_loop
from aws_devops_agent.analyzer.engine import detect_anomalies
from aws_devops_agent.config import get_settings
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
    title="AWS DevOps RAG Agent",
    version="1.0.0",
    description="Monitoring · Analyzing · Self-Healing · Acting",
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


class QueryRequest(BaseModel):
    question: str
    k: int = 6


@app.post("/api/query")
async def rag_query(req: QueryRequest):
    loop = asyncio.get_event_loop()
    retriever = await loop.run_in_executor(None, get_retriever, req.k)
    docs = await loop.run_in_executor(None, retriever.invoke, req.question)
    return {
        "question": req.question,
        "results": [
            {"content": d.page_content, "source": d.metadata.get("source", "")}
            for d in docs
        ],
    }


@app.post("/api/knowledge/rebuild")
async def rebuild_knowledge_base():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: build_vector_store(force_rebuild=True))
    return {"message": "Vector store rebuilt successfully"}


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
<title>AWS DevOps RAG Agent</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;background:#0d1117;color:#c9d1d9;font-size:14px}
  header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;gap:12px}
  header h1{font-size:18px;font-weight:600}
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
  .ts{color:#58a6ff;margin-right:8px}
  .type{font-weight:700;margin-right:8px;min-width:60px;display:inline-block}
  .type-monitor{color:#3fb950}.type-analyze{color:#d29922}
  .type-heal{color:#58a6ff}.type-error{color:#f85149}
  .type-info{color:#8b949e}.type-ping{color:#21262d}
  h2{font-size:13px;margin-bottom:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.4px}
  .status-bar{font-size:12px;color:#57606a;margin-bottom:16px}
</style>
</head>
<body>
<header>
  <h1>&#9889; AWS DevOps RAG Agent</h1>
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
