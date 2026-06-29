# AWS DevOps RAG Agent 🚀

An autonomous **RAG-powered AWS DevOps agent** that continuously monitors your AWS infrastructure, analyzes anomalies with LLM reasoning, and executes self-healing actions — just like a human SRE on-call, but automated.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        LangGraph Orchestrator                   │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ MONITOR  │──▶│ ANALYZE  │──▶│  HEAL    │──▶│  REPORT  │    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘    │
│       │               │                                         │
│  CloudWatch       RAG Retriever                                 │
│  EC2/RDS/ECS      + LLM (GPT-4o                                 │
│  Lambda/ALB         or Bedrock)                                 │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
    FastAPI Server
    ├── REST  /api/status  /api/query  /api/knowledge/rebuild
    ├── WS    /ws/events   (live stream)
    └── HTML  /            (built-in dashboard)
```

### Core Components

| Module | Purpose |
|--------|---------|
| `monitoring/collector.py` | Polls CloudWatch + AWS APIs for EC2, RDS, ECS, Lambda, ALB metrics |
| `analyzer/engine.py` | Rule-based anomaly detection + LLM root-cause analysis via RAG |
| `healing/executor.py` | Executes 12 self-healing actions against AWS APIs |
| `agent/orchestrator.py` | LangGraph state machine driving the monitor→analyze→heal loop |
| `rag/vector_store.py` | FAISS vector store built from runbook Markdown files |
| `server.py` | FastAPI REST + WebSocket API with built-in HTML dashboard |

---

## Monitored Resources

| Service | Metrics |
|---------|---------|
| **EC2** | CPUUtilization, StatusCheckFailed, NetworkIn/Out |
| **RDS** | CPU, FreeStorageSpace, DatabaseConnections, Latency |
| **ECS** | CPUUtilization, MemoryUtilization, RunningCount vs DesiredCount |
| **Lambda** | Errors, Throttles, Duration, ConcurrentExecutions |
| **ALB** | 5xx rate, TargetResponseTime, HealthyHostCount |

---

## Self-Healing Actions

| Action | What It Does |
|--------|-------------|
| `restart_ec2_service` | Restarts a systemd service via SSM Run Command |
| `stop_start_instance` | Stop + Start EC2 (migrates to new host hardware) |
| `scale_out_asg` | Increases Auto Scaling Group desired capacity |
| `cleanup_disk_ssm` | Runs disk cleanup commands via SSM |
| `reboot_rds_instance` | Reboots an RDS DB instance |
| `modify_rds_storage` | Increases RDS allocated storage by 20 GB |
| `rollback_lambda_version` | Rolls alias back to previous Lambda version |
| `update_lambda_timeout` | Increases Lambda timeout by 30s |
| `update_lambda_memory` | Increases Lambda memory by 256 MB |
| `update_ecs_service` | Forces a new ECS deployment |
| `scale_ecs_service` | Increases ECS service desired count |
| `deregister_unhealthy_targets` | Removes unhealthy targets from ALB target group |

---

## Quick Start

### 1. Clone & Install

```bash
git clone <repo>
cd aws-devops-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — fill in AWS credentials, OpenAI key, region
```

### 3. Run

```bash
python main.py
```

Open **http://localhost:8000** to see the live dashboard.

---

## Configuration Reference (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region to monitor |
| `LLM_PROVIDER` | `openai` | `openai` or `bedrock` |
| `LLM_MODEL` | `gpt-4o` | OpenAI model name |
| `AGENT_POLL_INTERVAL_SECONDS` | `60` | How often to run a monitoring cycle |
| `AGENT_MAX_HEALING_RETRIES` | `3` | Max retries per healing action |
| `AGENT_DRY_RUN` | `false` | If `true`, log actions without executing |
| `VECTOR_STORE_PATH` | `./data/vector_store` | Path to FAISS index |
| `KNOWLEDGE_BASE_PATH` | `./knowledge_base` | Path to runbook `.md` files |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/status` | Live resource snapshot + anomalies |
| `POST` | `/api/query` | Ad-hoc RAG query `{"question": "..."}` |
| `POST` | `/api/knowledge/rebuild` | Rebuild FAISS vector store |
| `WS` | `/ws/events` | Stream agent events as JSON |

---

## Extending the Agent

### Add a new runbook
Drop a `.md` file into `knowledge_base/` and call `POST /api/knowledge/rebuild`.

### Add a new healing action
1. Write a handler function in [`healing/executor.py`](aws_devops_agent/healing/executor.py)
2. Register it in `ACTION_REGISTRY`
3. Add the action name to the LLM system prompt in [`analyzer/engine.py`](aws_devops_agent/analyzer/engine.py)

### Add a new resource type
1. Add a `collect_<type>()` function in [`monitoring/collector.py`](aws_devops_agent/monitoring/collector.py)
2. Add it to `collect_all()`
3. Add threshold rules in [`analyzer/engine.py`](aws_devops_agent/analyzer/engine.py) `_THRESHOLDS`

---

## Safety

- **DRY_RUN mode** — set `AGENT_DRY_RUN=true` to audit all actions without executing.
- **Destructive guard** — the agent never deletes data. Reboots/stop-starts only trigger on CRITICAL severity.
- **Retry + back-off** — 3 retries with 30/60/120s back-off before giving up.
- **Event bus** — every action is emitted to the WebSocket stream for full audit trail.

---

## Project Structure

```
aws-devops-agent/
├── main.py                          # Entry point
├── requirements.txt
├── .env.example
├── knowledge_base/                  # Runbook Markdown files (RAG source)
│   ├── ec2_runbook.md
│   ├── rds_runbook.md
│   ├── ecs_runbook.md
│   ├── lambda_runbook.md
│   ├── alb_runbook.md
│   └── general_best_practices.md
├── data/
│   └── vector_store/                # FAISS index (auto-created)
└── aws_devops_agent/
    ├── config.py                    # Settings (pydantic-settings)
    ├── models.py                    # Shared Pydantic data models
    ├── server.py                    # FastAPI app + dashboard
    ├── agent/
    │   └── orchestrator.py          # LangGraph state machine
    ├── monitoring/
    │   └── collector.py             # AWS metric collectors
    ├── analyzer/
    │   └── engine.py                # Anomaly detection + LLM analysis
    ├── healing/
    │   └── executor.py              # Self-healing action executor
    └── rag/
        └── vector_store.py          # FAISS vector store + retriever
```
