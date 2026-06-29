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
| `LLM_PROVIDER` | `openai` | `openai` \| `bedrock` \| `groq` \| `ollama` \| `watsonx` |
| `LLM_MODEL` | `gpt-4o` | OpenAI model name |
| `AGENT_POLL_INTERVAL_SECONDS` | `60` | How often to run a monitoring cycle |
| `AGENT_MAX_HEALING_RETRIES` | `3` | Max retries per healing action |
| `AGENT_DRY_RUN` | `false` | If `true`, log actions without executing |
| `VECTOR_STORE_PATH` | `./data/vector_store` | Path to FAISS index |
| `KNOWLEDGE_BASE_PATH` | `./knowledge_base` | Path to runbook `.md` files |

### IBM WatsonX Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WATSONX_API_KEY` | — | IBM Cloud API key (create at cloud.ibm.com → API keys) |
| `WATSONX_PROJECT_ID` | — | WatsonX.ai project ID (from your project settings) |
| `WATSONX_URL` | `https://us-south.ml.cloud.ibm.com` | WatsonX regional endpoint |
| `WATSONX_LLM_MODEL` | `ibm/granite-3-8b-instruct` | Any deployed WatsonX foundation model |
| `WATSONX_EMBED_MODEL` | `ibm/slate-125m-english-rtrvr` | Embedding model for RAG |

**Quick WatsonX setup:**
```bash
# 1. Get your IBM Cloud API key: https://cloud.ibm.com/iam/apikeys
# 2. Open WatsonX.ai: https://dataplatform.cloud.ibm.com
# 3. Create a project and copy its ID from Project → Manage → General
# 4. Set in .env:
LLM_PROVIDER=watsonx
WATSONX_API_KEY=your_ibm_cloud_api_key
WATSONX_PROJECT_ID=your_project_id
WATSONX_URL=https://us-south.ml.cloud.ibm.com   # eu-de or jp-tok also available
WATSONX_LLM_MODEL=ibm/granite-3-8b-instruct
WATSONX_EMBED_MODEL=ibm/slate-125m-english-rtrvr
```

**Available WatsonX LLM models** (set `WATSONX_LLM_MODEL`):

| Model ID | Description |
|---|---|
| `ibm/granite-3-8b-instruct` | IBM Granite 3 — fast, instruction-tuned (default) |
| `ibm/granite-3-2b-instruct` | IBM Granite 3 — lightweight |
| `meta-llama/llama-3-1-70b-instruct` | Meta Llama 3.1 70B |
| `meta-llama/llama-3-1-8b-instruct` | Meta Llama 3.1 8B — fast |
| `mistralai/mistral-large` | Mistral Large |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/status` | Live resource snapshot + anomalies |
| `POST` | `/api/query` | RAG Q&A → LLM-synthesized `answer` field + `sources` |
| `POST` | `/api/heal` | Trigger any of the 12 self-healing actions on-demand |
| `POST` | `/api/knowledge/rebuild` | Rebuild FAISS vector store |
| `GET` | `/openapi-orchestrate.json` | WatsonX Orchestrate skill descriptor |
| `WS` | `/ws/events` | Stream agent events as JSON |

---

## WatsonX Orchestrate Integration

Import this agent as **3 conversational skills** into WatsonX Orchestrate so users can interact with it via natural language chat.

### Architecture

```
User chat in Orchestrate
        │
        ▼  natural language
WatsonX Orchestrate (skill router)
        │
        ├─► GET  /api/status   →  "Any anomalies in my AWS account?"
        ├─► POST /api/query    →  "How do I fix high RDS CPU?"
        └─► POST /api/heal     →  "Restart the web service on i-0e9efcc6"
        │
        ▼
  Your FastAPI Agent (this project)
        │
        ▼
  AWS APIs + LLM (Groq / WatsonX / OpenAI)
```

### Step 1 — Expose your agent publicly (ngrok)

```bash
# Terminal 1 — start the agent
python main.py

# Terminal 2 — expose it with ngrok (free)
ngrok http 8000
# → copy the https://xxxx.ngrok.io URL
```

### Step 2 — Import the skills into Orchestrate

1. Go to **WatsonX Orchestrate → Skills catalog → Add skill → From API**
2. Paste your skill descriptor URL:
   ```
   https://xxxx.ngrok.io/openapi-orchestrate.json
   ```
3. Orchestrate will detect **3 skills**:

| Skill | Triggered when user says… |
|---|---|
| **Get live AWS anomaly status** | "Check my AWS", "Any issues?", "What's failing?" |
| **Ask the AWS knowledge base** | "How do I fix…", "What causes…", "Best practice for…" |
| **Trigger a self-healing action** | "Restart the service on…", "Scale up…", "Reboot RDS…" |

4. Click **Publish** on each skill
5. Add all 3 skills to your assistant

### Step 3 — Test in chat

```
You:        "Are there any anomalies in my AWS infrastructure?"
Orchestrate: Calls GET /api/status
             → "Found 2 anomalies:
                • CRITICAL — EC2 i-0e9efcc6253ff7a3e: CPUUtilization is 91.2%
                • WARNING  — RDS database-1: FreeStorageSpace is 1.8 GB"

You:        "How do I fix high EC2 CPU?"
Orchestrate: Calls POST /api/query {"question": "How to fix high EC2 CPU?"}
             → "High EC2 CPU can be caused by runaway processes or traffic spikes.
                Recommended actions: scale_out_asg to add capacity, or
                restart_ec2_service to restart the offending service via SSM..."

You:        "Scale out the auto scaling group my-asg by 2"
Orchestrate: Calls POST /api/heal
             {"resource_id": "my-asg", "resource_type": "ec2",
              "action_type": "scale_out_asg",
              "parameters": {"asg_name": "my-asg", "increment": 2}}
             → "SUCCESS — ASG my-asg desired capacity set to 5"
```

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
