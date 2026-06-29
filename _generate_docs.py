"""
Generates full project documentation as a DOCX file.
Run with the system Python (python-docx is already installed globally).
"""
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
import datetime

doc = Document()

# ── Page margins ──────────────────────────────────────────────────────────────
for section in doc.sections:
    section.top_margin    = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin   = Inches(1.2)
    section.right_margin  = Inches(1.2)

# ── Colour palette ────────────────────────────────────────────────────────────
DARK_BLUE  = RGBColor(0x1A, 0x3C, 0x6E)
MID_BLUE   = RGBColor(0x23, 0x6F, 0xBF)
LIGHT_GRAY = RGBColor(0xF0, 0xF4, 0xFA)
TEXT_DARK  = RGBColor(0x1F, 0x23, 0x28)
CODE_BG    = RGBColor(0xEE, 0xF2, 0xF7)
WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
TABLE_HDR  = RGBColor(0x1A, 0x3C, 0x6E)
TABLE_ROW  = RGBColor(0xF5, 0xF8, 0xFF)


def shade_cell(cell, rgb: RGBColor):
    """Fill a table cell background with a solid colour."""
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement('w:shd')
    hex_color = f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    shd.set(qn('w:val'),   'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'),  hex_color)
    tcPr.append(shd)


def set_cell_border(cell, top=None, bottom=None, left=None, right=None):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for side, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        if val:
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:val'),   val.get('val', 'single'))
            el.set(qn('w:sz'),    val.get('sz', '4'))
            el.set(qn('w:color'), val.get('color', 'auto'))
            tcBorders.append(el)
    tcPr.append(tcBorders)


def heading(text, level=1):
    p   = doc.add_heading(text, level=level)
    run = p.runs[0] if p.runs else p.add_run(text)
    run.font.color.rgb = DARK_BLUE if level == 1 else MID_BLUE
    run.font.bold = True
    run.font.size = Pt(20 - (level - 1) * 3)
    p.paragraph_format.space_before = Pt(18 if level == 1 else 10)
    p.paragraph_format.space_after  = Pt(6)
    return p


def body(text, bold=False, italic=False, size=11):
    p   = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size  = Pt(size)
    run.font.bold  = bold
    run.font.italic = italic
    run.font.color.rgb = TEXT_DARK
    p.paragraph_format.space_after = Pt(4)
    return p


def bullet(text, level=0):
    p   = doc.add_paragraph(style='List Bullet')
    run = p.add_run(text)
    run.font.size = Pt(10.5)
    run.font.color.rgb = TEXT_DARK
    p.paragraph_format.left_indent  = Inches(0.3 * (level + 1))
    p.paragraph_format.space_after  = Pt(2)
    return p


def code_block(text):
    """Render text in a lightly shaded monospace paragraph."""
    p   = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = 'Courier New'
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x20, 0x40, 0x60)
    p.paragraph_format.left_indent   = Inches(0.4)
    p.paragraph_format.space_before  = Pt(4)
    p.paragraph_format.space_after   = Pt(4)
    # light background via paragraph shading
    pPr  = p._p.get_or_add_pPr()
    shd  = OxmlElement('w:shd')
    shd.set(qn('w:val'),   'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'),  'EEF2F7')
    pPr.append(shd)
    return p


def add_table(headers, rows, col_widths=None):
    n_cols = len(headers)
    t      = doc.add_table(rows=1 + len(rows), cols=n_cols)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT

    # header row
    hdr_cells = t.rows[0].cells
    for i, h in enumerate(headers):
        shade_cell(hdr_cells[i], TABLE_HDR)
        hdr_cells[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        run = hdr_cells[i].paragraphs[0].add_run(h)
        run.font.bold  = True
        run.font.color.rgb = WHITE
        run.font.size  = Pt(10)

    # data rows
    for r_idx, row in enumerate(rows):
        row_cells = t.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            if r_idx % 2 == 0:
                shade_cell(row_cells[c_idx], TABLE_ROW)
            run = row_cells[c_idx].paragraphs[0].add_run(str(val))
            run.font.size = Pt(10)
            run.font.color.rgb = TEXT_DARK

    # column widths
    if col_widths:
        for i, w in enumerate(col_widths):
            for row in t.rows:
                row.cells[i].width = Inches(w)
    return t


def hr():
    p   = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:color'), '23558F')
    pBdr.append(bottom)
    pPr.append(pBdr)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)


# ══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ══════════════════════════════════════════════════════════════════════════════

cover = doc.add_paragraph()
cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
cover.paragraph_format.space_before = Pt(80)
r = cover.add_run("AWS DevOps RAG Agent")
r.font.size  = Pt(32)
r.font.bold  = True
r.font.color.rgb = DARK_BLUE

sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r2 = sub.add_run("Full Project Documentation — IBM WatsonX Edition")
r2.font.size = Pt(16)
r2.font.color.rgb = MID_BLUE

doc.add_paragraph()
date_p = doc.add_paragraph()
date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r3 = date_p.add_run(f"Generated: {datetime.date.today().strftime('%B %d, %Y')}")
r3.font.size  = Pt(11)
r3.font.color.rgb = RGBColor(0x57, 0x60, 0x6A)
r3.font.italic = True

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS (manual — no field codes needed)
# ══════════════════════════════════════════════════════════════════════════════

heading("Table of Contents", level=1)
toc_entries = [
    ("1.", "Project Overview"),
    ("2.", "Architecture"),
    ("3.", "Project Structure"),
    ("4.", "Installation & Quick Start"),
    ("5.", "Configuration Reference"),
    ("6.", "Core Modules"),
    ("  6.1", "Config (config.py)"),
    ("  6.2", "Data Models (models.py)"),
    ("  6.3", "Monitoring Collector (monitoring/collector.py)"),
    ("  6.4", "Analyzer Engine (analyzer/engine.py)"),
    ("  6.5", "Self-Healing Executor (healing/executor.py)"),
    ("  6.6", "RAG Vector Store (rag/vector_store.py)"),
    ("  6.7", "Agent Orchestrator (agent/orchestrator.py)"),
    ("  6.8", "FastAPI Server (server.py)"),
    ("  6.9", "Entry Point (main.py)"),
    ("7.", "Monitored Resources & Metrics"),
    ("8.", "Self-Healing Actions"),
    ("9.", "API Reference"),
    ("10.", "Knowledge Base & Runbooks"),
    ("11.", "Safety & Operational Guardrails"),
    ("12.", "Extending the Agent"),
    ("13.", "Dependencies"),
    ("14.", "Data Models Reference"),
]
for num, title in toc_entries:
    p   = doc.add_paragraph()
    run = p.add_run(f"{num}  {title}")
    run.font.size = Pt(11)
    run.font.color.rgb = MID_BLUE if num.strip().endswith('.') else TEXT_DARK
    p.paragraph_format.space_after = Pt(2)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 1. PROJECT OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

heading("1. Project Overview")
hr()
body(
    "AWS DevOps RAG Agent is an autonomous, AI-powered Site Reliability Engineering (SRE) "
    "agent that continuously monitors AWS infrastructure, detects anomalies using rule-based "
    "thresholds, reasons about root causes using a Large Language Model (LLM) augmented with "
    "Retrieval-Augmented Generation (RAG), and automatically executes self-healing actions "
    "against AWS APIs — all without human intervention."
)
doc.add_paragraph()
body("Key capabilities:", bold=True)
bullet("Continuous polling of EC2, RDS, ECS, Lambda, and ALB resources via CloudWatch and AWS APIs")
bullet("Rule-based anomaly detection with WARNING and CRITICAL severity tiers")
bullet("LLM-driven root-cause analysis using retrieved runbook context (RAG)")
bullet("12 distinct self-healing actions executed against AWS APIs")
bullet("DRY_RUN mode for safe audit without side effects")
bullet("Real-time event streaming via WebSocket to a built-in HTML dashboard")
bullet("REST API for on-demand status queries and knowledge-base management")
bullet("Support for multiple LLM providers: OpenAI, AWS Bedrock, Groq, Ollama, and IBM WatsonX")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 2. ARCHITECTURE
# ══════════════════════════════════════════════════════════════════════════════

heading("2. Architecture")
hr()
body(
    "The agent is built on a LangGraph state machine that drives a four-node cycle: "
    "MONITOR → ANALYZE → HEAL → REPORT. Each cycle is triggered at a configurable "
    "poll interval and runs entirely within a single asyncio event loop managed by FastAPI/uvicorn."
)
doc.add_paragraph()
body("LangGraph State Machine Nodes:", bold=True)
bullet("MONITOR  — Calls all resource collectors and assembles ResourceSnapshot objects")
bullet("ANALYZE  — Runs rule-based anomaly detection; for each anomaly invokes the LLM + RAG chain")
bullet("HEAL     — Executes recommended HealingActions against AWS APIs (with retry/back-off)")
bullet("REPORT   — Emits a cycle summary event to the WebSocket event bus")
doc.add_paragraph()
body("Supporting Infrastructure:", bold=True)
add_table(
    ["Layer", "Technology", "Purpose"],
    [
        ["Agent Loop",      "LangGraph 0.2",            "State machine orchestration"],
        ["LLM Reasoning",   "OpenAI / Bedrock / Groq / Ollama / WatsonX", "Root-cause analysis & action selection"],
        ["RAG Retrieval",   "FAISS + LangChain",         "Runbook context retrieval"],
        ["Embeddings",      "HuggingFace sentence-transformers", "Semantic search over knowledge base"],
        ["AWS SDK",         "boto3 1.34",                "Metric collection & healing actions"],
        ["API Server",      "FastAPI + uvicorn",         "REST + WebSocket + HTML dashboard"],
        ["Configuration",   "pydantic-settings",         "Type-safe env-var config"],
        ["Structured Logs", "structlog",                 "JSON-formatted operational logs"],
    ],
    col_widths=[1.6, 2.2, 2.8],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 3. PROJECT STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

heading("3. Project Structure")
hr()
code_block(
    "aws-devops-agent/\n"
    "├── main.py                          # Entry point\n"
    "├── requirements.txt                 # Python dependencies\n"
    "├── .env.example                     # Environment variable template\n"
    "├── pyproject.toml\n"
    "├── knowledge_base/                  # Runbook Markdown files (RAG source)\n"
    "│   ├── ec2_runbook.md\n"
    "│   ├── rds_runbook.md\n"
    "│   ├── ecs_runbook.md\n"
    "│   ├── lambda_runbook.md\n"
    "│   ├── alb_runbook.md\n"
    "│   └── general_best_practices.md\n"
    "├── data/\n"
    "│   └── vector_store/                # FAISS index (auto-created on first run)\n"
    "│       ├── index.faiss\n"
    "│       └── index.pkl\n"
    "├── tests/                           # Test & diagnostic scripts\n"
    "└── aws_devops_agent/\n"
    "    ├── __init__.py\n"
    "    ├── config.py                    # pydantic-settings configuration\n"
    "    ├── models.py                    # Shared Pydantic data models\n"
    "    ├── server.py                    # FastAPI app + built-in dashboard\n"
    "    ├── agent/\n"
    "    │   └── orchestrator.py          # LangGraph state machine\n"
    "    ├── monitoring/\n"
    "    │   └── collector.py             # AWS metric collectors\n"
    "    ├── analyzer/\n"
    "    │   └── engine.py                # Anomaly detection + LLM analysis\n"
    "    ├── healing/\n"
    "    │   └── executor.py              # Self-healing action executor\n"
    "    └── rag/\n"
    "        └── vector_store.py          # FAISS vector store + retriever"
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 4. INSTALLATION & QUICK START
# ══════════════════════════════════════════════════════════════════════════════

heading("4. Installation & Quick Start")
hr()

heading("4.1  Prerequisites", level=2)
bullet("Python 3.10 or 3.12")
bullet("AWS account with CloudWatch read access and SSM/EC2/RDS/ECS/Lambda/ELBv2 write access")
bullet("One of: OpenAI API key, Groq API key, AWS Bedrock access, or local Ollama installation")

heading("4.2  Clone & Install", level=2)
code_block(
    "git clone <repo-url>\n"
    "cd aws-devops-agent\n"
    "python -m venv .venv\n"
    "source .venv/bin/activate      # Windows: .venv\\Scripts\\activate\n"
    "pip install -r requirements.txt"
)

heading("4.3  Configure", level=2)
code_block(
    "cp .env.example .env\n"
    "# Edit .env — fill in AWS credentials, LLM provider, region"
)

heading("4.4  Run", level=2)
code_block("python main.py")
body("Open http://localhost:8000 in a browser to view the live dashboard.")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 5. CONFIGURATION REFERENCE
# ══════════════════════════════════════════════════════════════════════════════

heading("5. Configuration Reference")
hr()
body(
    "All configuration is loaded from environment variables or a .env file at the project root. "
    "The Settings class in config.py uses pydantic-settings for type-safe parsing."
)
doc.add_paragraph()
add_table(
    ["Variable", "Default", "Description"],
    [
        ["AWS_ACCESS_KEY_ID",           "(empty)",              "AWS access key (optional if using IAM role)"],
        ["AWS_SECRET_ACCESS_KEY",       "(empty)",              "AWS secret key"],
        ["AWS_DEFAULT_REGION",          "us-east-1",            "AWS region to monitor"],
        ["LLM_PROVIDER",                "openai",               "openai | bedrock | groq | ollama | watsonx"],
        ["OPENAI_API_KEY",              "(empty)",              "OpenAI API key (for openai provider)"],
        ["LLM_MODEL",                   "gpt-4o",               "OpenAI model name"],
        ["BEDROCK_MODEL_ID",            "claude-3-5-sonnet-…",  "Bedrock model ID (for bedrock provider)"],
        ["GROQ_API_KEY",                "(empty)",              "Groq API key (for groq provider)"],
        ["GROQ_MODEL",                  "llama-3.3-70b-versatile", "Groq model name"],
        ["OLLAMA_BASE_URL",             "http://localhost:11434","Ollama server base URL"],
        ["OLLAMA_MODEL",                "llama3.1",             "Ollama model name"],
        ["OLLAMA_EMBED_MODEL",          "nomic-embed-text",     "Ollama embedding model"],
        ["EMBED_PROVIDER",              "huggingface",          "huggingface | openai | ollama | bedrock | watsonx"],
        ["HUGGINGFACE_API_KEY",         "(empty)",              "HuggingFace token (optional for public models)"],
        ["HF_EMBED_MODEL",              "all-MiniLM-L6-v2",    "Sentence-transformer embedding model"],
        ["AGENT_POLL_INTERVAL_SECONDS", "60",                   "Monitoring cycle interval in seconds"],
        ["AGENT_MAX_HEALING_RETRIES",   "3",                    "Max retry attempts per healing action"],
        ["AGENT_DRY_RUN",               "false",                "If true, log actions without executing"],
        ["VECTOR_STORE_PATH",           "./data/vector_store",  "Path to FAISS index files"],
        ["KNOWLEDGE_BASE_PATH",         "./knowledge_base",     "Directory containing runbook .md files"],
        ["API_HOST",                    "0.0.0.0",              "FastAPI server bind address"],
        ["API_PORT",                    "8000",                 "FastAPI server port"],
        ["MONITOR_EC2_INSTANCE_IDS",    "(empty)",              "Comma-separated EC2 IDs to monitor (blank = all)"],
        ["MONITOR_RDS_CLUSTER_IDS",     "(empty)",              "Comma-separated RDS IDs (blank = all)"],
        ["MONITOR_ECS_CLUSTER_NAMES",   "(empty)",              "Comma-separated ECS cluster names"],
        ["MONITOR_LAMBDA_FUNCTION_NAMES","(empty)",             "Comma-separated Lambda function names"],
        ["MONITOR_ALB_NAMES",           "(empty)",              "Comma-separated ALB names"],
        ["WATSONX_API_KEY",             "(empty)",              "IBM Cloud API key"],
        ["WATSONX_PROJECT_ID",          "(empty)",              "WatsonX.ai project ID"],
        ["WATSONX_URL",                 "https://us-south.ml.cloud.ibm.com", "WatsonX regional endpoint"],
        ["WATSONX_LLM_MODEL",           "ibm/granite-3-8b-instruct",         "WatsonX foundation model ID"],
        ["WATSONX_EMBED_MODEL",         "ibm/slate-125m-english-rtrvr",      "WatsonX embedding model ID"],
    ],
    col_widths=[2.2, 1.6, 2.8],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 6. CORE MODULES
# ══════════════════════════════════════════════════════════════════════════════

heading("6. Core Modules")
hr()

# 6.1 Config
heading("6.1  config.py — Settings", level=2)
body(
    "Central configuration singleton using pydantic-settings. "
    "Loaded once via get_settings() decorated with @lru_cache, ensuring a single instance "
    "across the entire application lifetime."
)
body("Key helper methods:", bold=True)
bullet("ec2_ids()         — parses MONITOR_EC2_INSTANCE_IDS into a list")
bullet("rds_ids()         — parses MONITOR_RDS_CLUSTER_IDS into a list")
bullet("ecs_clusters()    — parses MONITOR_ECS_CLUSTER_NAMES into a list")
bullet("lambda_functions()— parses MONITOR_LAMBDA_FUNCTION_NAMES into a list")
bullet("alb_names_list()  — parses MONITOR_ALB_NAMES into a list")

# 6.2 Models
heading("6.2  models.py — Shared Data Models", level=2)
body(
    "All inter-module data transfer uses Pydantic v2 models defined in this file. "
    "This provides runtime type safety and easy JSON serialisation for the event bus."
)
add_table(
    ["Model", "Fields", "Purpose"],
    [
        ["Severity (enum)",       "OK | WARNING | CRITICAL | UNKNOWN",   "Anomaly severity level"],
        ["HealingStatus (enum)",  "PENDING | IN_PROGRESS | SUCCESS | FAILED | SKIPPED", "Action execution state"],
        ["MetricPoint",           "name, value, unit, timestamp",         "Single CloudWatch data point"],
        ["ResourceSnapshot",      "resource_id, resource_type, region, metrics, tags, raw, collected_at", "Point-in-time resource health snapshot"],
        ["Anomaly",               "resource_id, resource_type, severity, title, description, metrics, detected_at, context", "Detected metric anomaly"],
        ["HealingAction",         "action_id, anomaly, action_type, parameters, reasoning, status, result, executed_at, retries", "Healing action record"],
        ["AgentEvent",            "event_type, payload, timestamp",       "Event streamed to WebSocket clients"],
    ],
    col_widths=[1.8, 2.8, 2.0],
)

# 6.3 Monitoring Collector
heading("6.3  monitoring/collector.py — AWS Metric Collectors", level=2)
body(
    "Polls AWS CloudWatch and service APIs to produce ResourceSnapshot objects for each "
    "monitored resource. All collectors are synchronous (called inside run_in_executor from "
    "the async orchestrator)."
)
body("Functions:", bold=True)
bullet("_boto(service)           — returns a boto3 client with credentials from Settings")
bullet("_cw_stat(...)            — fetches a single latest CloudWatch metric statistic")
bullet("collect_ec2()            — discovers running EC2 instances; collects CPU, status checks, network")
bullet("collect_rds()            — lists RDS instances; collects CPU, storage, connections, latency")
bullet("collect_ecs()            — iterates ECS clusters/services; collects CPU, memory, task counts")
bullet("collect_lambda()         — lists Lambda functions; collects errors, throttles, duration, concurrency")
bullet("collect_alb()            — lists ALBs; collects 5xx counts, response time, healthy/unhealthy host counts")
bullet("collect_all()            — calls all five collectors; swallows per-collector exceptions")

# 6.4 Analyzer Engine
heading("6.4  analyzer/engine.py — Anomaly Detection & LLM Analysis", level=2)
body(
    "Two-phase analysis: first a fast rule-based threshold check, then an LLM chain "
    "that retrieves relevant runbook context and reasons about root cause and remediation."
)
body("_THRESHOLDS dict — threshold rules (excerpt):", bold=True)
add_table(
    ["Resource Type", "Metric", "Warning", "Critical", "Direction"],
    [
        ["ec2",    "CPUUtilization",          "> 75%",  "> 90%",  "Higher = bad"],
        ["ec2",    "StatusCheckFailed",       "> 0.5",  "> 0.5",  "Higher = bad"],
        ["rds",    "CPUUtilization",          "> 75%",  "> 85%",  "Higher = bad"],
        ["rds",    "FreeStorageSpace",        "< 5 GB", "< 2 GB", "Lower = bad"],
        ["rds",    "DatabaseConnections",     "> 80",   "> 95",   "Higher = bad"],
        ["lambda", "Errors",                  "> 1",    "> 5",    "Higher = bad"],
        ["lambda", "Throttles",               "> 1",    "> 10",   "Higher = bad"],
        ["ecs",    "CPUUtilization",          "> 75%",  "> 90%",  "Higher = bad"],
        ["ecs",    "MemoryUtilization",       "> 80%",  "> 95%",  "Higher = bad"],
        ["alb",    "HTTPCode_ELB_5XX_Count",  "> 5",    "> 20",   "Higher = bad"],
        ["alb",    "UnHealthyHostCount",      ">= 1",   ">= 1",   "Higher = bad"],
        ["alb",    "TargetResponseTime",      "> 1 s",  "> 3 s",  "Higher = bad"],
    ],
    col_widths=[1.0, 2.2, 0.9, 0.9, 1.6],
)
doc.add_paragraph()
body("Key functions:", bold=True)
bullet("detect_anomalies(snapshots) — applies threshold rules; returns List[Anomaly]")
bullet("_get_llm()                  — instantiates the correct LangChain chat model per LLM_PROVIDER (openai/bedrock/groq/ollama/watsonx)")
bullet("analyze_anomaly(anomaly)    — RAG retrieval + LLM chain invocation; returns (summary_str, HealingAction|None)")

# 6.5 Healing Executor
heading("6.5  healing/executor.py — Self-Healing Action Executor", level=2)
body(
    "Executes healing actions against AWS APIs. Each action type is mapped to a handler "
    "function in ACTION_REGISTRY. The main execute_healing_action() function handles "
    "DRY_RUN checking, retries with exponential back-off, and status updates."
)
body("Retry policy:", bold=True)
bullet("Maximum attempts: AGENT_MAX_HEALING_RETRIES (default 3)")
bullet("Back-off delays: 30 s → 60 s → 120 s")
bullet("On final failure: status set to FAILED and error logged")

# 6.6 RAG Vector Store
heading("6.6  rag/vector_store.py — FAISS Vector Store", level=2)
body(
    "Builds and maintains a FAISS vector store from Markdown runbook files in knowledge_base/. "
    "The store is a module-level singleton protected by a threading.Lock. "
    "It is built once on startup and can be force-rebuilt via the API."
)
body("Key functions:", bold=True)
bullet("_get_embeddings()         — returns the singleton embeddings model (HuggingFace / OpenAI / Ollama / Bedrock / WatsonX)")
bullet("_load_documents(path)     — loads all .md and .txt files from the knowledge base directory")
bullet("build_vector_store(force) — loads from disk if index exists, otherwise builds from documents and saves")
bullet("get_retriever(k=6)        — returns a similarity-search retriever backed by the singleton store")

# 6.7 Agent Orchestrator
heading("6.7  agent/orchestrator.py — LangGraph Orchestrator", level=2)
body(
    "Defines the LangGraph state machine and runs the infinite monitoring loop. "
    "Events are published to a singleton asyncio.Queue (event bus) consumed by the "
    "FastAPI WebSocket relay."
)
body("AgentState TypedDict fields:", bold=True)
bullet("snapshots          — List[ResourceSnapshot] collected in the current cycle")
bullet("anomalies          — List[Anomaly] detected from snapshots")
bullet("healing_actions    — List[HealingAction] created by the analyzer")
bullet("analysis_summaries — List[str] one-liner summaries per anomaly")
bullet("cycle_id           — 8-char UUID prefix identifying the cycle")
doc.add_paragraph()
body("Graph topology:", bold=True)
code_block(
    "monitor  ──▶  analyze  ──┬──(has actions)──▶  heal  ──▶  report\n"
    "                         └──(no actions)────────────────▶  report"
)

# 6.8 FastAPI Server
heading("6.8  server.py — FastAPI Server", level=2)
body(
    "Hosts the REST API, WebSocket event stream, and built-in HTML dashboard. "
    "Background tasks (vector store build, event relay, agent loop) are started "
    "inside an asynccontextmanager lifespan so the server accepts connections immediately."
)
body("Background tasks started at startup:", bold=True)
bullet("_build_vector_store_bg() — builds FAISS index without blocking the server")
bullet("_event_relay()           — reads from the agent event bus and broadcasts to all WebSocket clients")
bullet("run_agent_loop()         — runs the LangGraph monitoring loop indefinitely")

# 6.9 main.py
heading("6.9  main.py — Entry Point", level=2)
body(
    "Configures structlog structured logging and launches uvicorn with the FastAPI app. "
    "Reads API_HOST and API_PORT from Settings."
)
code_block("python main.py")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 7. MONITORED RESOURCES & METRICS
# ══════════════════════════════════════════════════════════════════════════════

heading("7. Monitored Resources & Metrics")
hr()
add_table(
    ["Service", "Metrics Collected", "CloudWatch Namespace"],
    [
        ["EC2",    "CPUUtilization, NetworkIn, NetworkOut, StatusCheckFailed, StatusCheckFailed_Instance, StatusCheckFailed_System", "AWS/EC2"],
        ["RDS",    "CPUUtilization, FreeStorageSpace, DatabaseConnections, ReadLatency, WriteLatency, FreeableMemory",              "AWS/RDS"],
        ["ECS",    "CPUUtilization, MemoryUtilization, RunningCount (from API), DesiredCount (from API)",                           "AWS/ECS"],
        ["Lambda", "Invocations, Errors, Throttles, Duration, ConcurrentExecutions",                                                "AWS/Lambda"],
        ["ALB",    "RequestCount, HTTPCode_ELB_5XX_Count, HTTPCode_ELB_4XX_Count, HTTPCode_Target_5XX_Count, TargetResponseTime, HealthyHostCount, UnHealthyHostCount, ActiveConnectionCount", "AWS/ApplicationELB"],
    ],
    col_widths=[0.8, 3.6, 2.2],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 8. SELF-HEALING ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

heading("8. Self-Healing Actions")
hr()
body(
    "The following 12 actions are registered in ACTION_REGISTRY. "
    "All are guarded by DRY_RUN mode; destructive actions additionally require CRITICAL severity."
)
doc.add_paragraph()
add_table(
    ["Action Type", "Target Service", "What It Does", "Required Parameters"],
    [
        ["restart_ec2_service",        "EC2 / SSM",    "Restarts a systemd service via SSM Run Command",          "instance_id, [service_name]"],
        ["stop_start_instance",        "EC2",           "Stops then starts an EC2 instance (new host migration)",  "instance_id"],
        ["scale_out_asg",              "Auto Scaling",  "Increases ASG desired capacity by increment",             "asg_name, [increment=1]"],
        ["cleanup_disk_ssm",           "EC2 / SSM",    "Runs /tmp, journal, log cleanup commands via SSM",        "instance_id"],
        ["reboot_rds_instance",        "RDS",           "Reboots an RDS DB instance",                              "db_instance_id"],
        ["modify_rds_storage",         "RDS",           "Increases allocated RDS storage by 20 GB",                "db_instance_id, [extra_gb=20]"],
        ["rollback_lambda_version",    "Lambda",        "Updates alias to point to previous function version",     "function_name, [alias=live]"],
        ["update_lambda_timeout",      "Lambda",        "Increases Lambda timeout by 30 s (max 900 s)",            "function_name, [increment_seconds=30]"],
        ["update_lambda_memory",       "Lambda",        "Increases Lambda memory by 256 MB (max 10 240 MB)",       "function_name, [increment_mb=256]"],
        ["update_ecs_service",         "ECS",           "Forces a new ECS deployment",                             "cluster, service"],
        ["scale_ecs_service",          "ECS",           "Increases ECS service desired count by increment",        "cluster, service, [increment=2]"],
        ["deregister_unhealthy_targets","ELBv2",        "Deregisters all unhealthy targets from a target group",   "target_group_arn"],
    ],
    col_widths=[1.9, 1.1, 2.2, 1.8],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 9. API REFERENCE
# ══════════════════════════════════════════════════════════════════════════════

heading("9. API Reference")
hr()
add_table(
    ["Method", "Path", "Description", "Request Body"],
    [
        ["GET",  "/",                      "Built-in HTML live dashboard",                  "—"],
        ["GET",  "/health",                "Liveness probe — returns {\"status\":\"ok\"}",  "—"],
        ["GET",  "/api/status",            "Runs a live collect + detect cycle; returns resources scanned, anomalies list, dry_run flag, region", "—"],
        ["POST", "/api/query",             "Ad-hoc RAG question over the knowledge base",   "{\"question\": \"...\", \"k\": 6}"],
        ["POST", "/api/knowledge/rebuild", "Force-rebuilds the FAISS vector store from knowledge_base/ files", "—"],
        ["WS",   "/ws/events",             "Streams AgentEvent JSON objects; sends a ping frame every 20 s to maintain connection", "—"],
    ],
    col_widths=[0.6, 1.9, 2.8, 1.7],
)
doc.add_paragraph()
body("Example: query the knowledge base", bold=True)
code_block(
    'curl -X POST http://localhost:8000/api/query \\\n'
    '     -H "Content-Type: application/json" \\\n'
    '     -d \'{"question": "How do I handle high RDS CPU?", "k": 4}\''
)
body("Example: check live status", bold=True)
code_block("curl http://localhost:8000/api/status")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 10. KNOWLEDGE BASE & RUNBOOKS
# ══════════════════════════════════════════════════════════════════════════════

heading("10. Knowledge Base & Runbooks")
hr()
body(
    "The knowledge_base/ directory contains Markdown runbooks that are chunked, embedded, "
    "and stored in the FAISS vector store. During anomaly analysis the top-k most relevant "
    "chunks are retrieved and injected into the LLM prompt as context."
)
doc.add_paragraph()
add_table(
    ["File", "Covers"],
    [
        ["ec2_runbook.md",             "High CPU, Status Check failures, Disk usage > 90%"],
        ["rds_runbook.md",             "High DB CPU, FreeStorageSpace < 2 GB, Too many connections"],
        ["ecs_runbook.md",             "CrashLoopBackOff, Desired count not met, High CPU/Memory"],
        ["lambda_runbook.md",          "Error rate > 5%, Throttles, Duration near timeout"],
        ["alb_runbook.md",             "5xx error rate, Target response time > 2 s, No healthy hosts"],
        ["general_best_practices.md",  "Severity classification, healing decision framework, safety guards"],
    ],
    col_widths=[2.4, 4.2],
)
doc.add_paragraph()
body("Text splitter settings:", bold=True)
bullet("chunk_size: 800 characters")
bullet("chunk_overlap: 100 characters")
bullet("Separators: paragraph breaks → line breaks → spaces → characters")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 11. SAFETY & OPERATIONAL GUARDRAILS
# ══════════════════════════════════════════════════════════════════════════════

heading("11. Safety & Operational Guardrails")
hr()
add_table(
    ["Guardrail", "Implementation"],
    [
        ["DRY_RUN mode",            "AGENT_DRY_RUN=true skips all AWS API calls; logs intended action instead"],
        ["No data deletion",        "No action in ACTION_REGISTRY deletes S3 objects, RDS snapshots, or any data"],
        ["Destructive action gate", "stop_start and reboot actions are only triggered on CRITICAL severity anomalies (enforced by LLM prompt instructions)"],
        ["Retry + back-off",        "3 attempts with 30 s / 60 s / 120 s delays before marking action as FAILED"],
        ["Event audit trail",       "Every action start and result is emitted to the WebSocket event bus for full audit"],
        ["Unknown action guard",    "If the LLM recommends an action_type not in ACTION_REGISTRY, the action is marked FAILED immediately"],
        ["Queue overflow guard",    "Event bus is capped at 1 000 entries; oldest event is dropped when full"],
    ],
    col_widths=[2.0, 4.6],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 12. EXTENDING THE AGENT
# ══════════════════════════════════════════════════════════════════════════════

heading("12. Extending the Agent")
hr()

heading("12.1  Add a new runbook", level=2)
bullet("Drop a .md or .txt file into knowledge_base/")
bullet("Call POST /api/knowledge/rebuild to rebuild the vector store")
bullet("The LLM will automatically use the new content in subsequent analyses")

heading("12.2  Add a new healing action", level=2)
bullet("Write a handler function _my_action(params: Dict) -> str in healing/executor.py")
bullet("Register it in ACTION_REGISTRY: \"my_action_type\": _my_action")
bullet("Add the action name to the Available healing action types list in the LLM system prompt inside analyzer/engine.py")

heading("12.3  Add a new monitored resource type", level=2)
bullet("Add a collect_<type>() function in monitoring/collector.py that returns List[ResourceSnapshot]")
bullet("Call it inside collect_all()")
bullet("Add threshold rules for the new resource type and metrics in analyzer/engine.py _THRESHOLDS")
bullet("Create a runbook file in knowledge_base/ describing common failure modes and remediation steps")

heading("12.4  Switch LLM provider at runtime", level=2)
bullet("Set LLM_PROVIDER in .env to openai | bedrock | groq | ollama | watsonx and restart")
bullet("No code changes required — _get_llm() in engine.py handles all five providers")

heading("12.5  Configure IBM WatsonX", level=2)
bullet("Set LLM_PROVIDER=watsonx in .env")
bullet("Add WATSONX_API_KEY (IBM Cloud API key from cloud.ibm.com/iam/apikeys)")
bullet("Add WATSONX_PROJECT_ID (from your WatsonX.ai project → Manage → General)")
bullet("Optionally override WATSONX_LLM_MODEL (default: ibm/granite-3-8b-instruct)")
bullet("Optionally override WATSONX_EMBED_MODEL (default: ibm/slate-125m-english-rtrvr)")
bullet("Install extra packages: pip install ibm-watsonx-ai==1.1.2 langchain-ibm==0.1.12")

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 13. DEPENDENCIES
# ══════════════════════════════════════════════════════════════════════════════

heading("13. Dependencies")
hr()
add_table(
    ["Package", "Version", "Purpose"],
    [
        ["langchain",               "0.2.16",  "Core LangChain framework"],
        ["langchain-community",     "0.2.16",  "Community integrations (FAISS loader, etc.)"],
        ["langchain-openai",        "0.1.23",  "OpenAI LLM and embeddings"],
        ["langchain-aws",           "0.1.17",  "AWS Bedrock LLM and embeddings"],
        ["langchain-groq",          "0.1.9",   "Groq LLM integration"],
        ["langchain-ollama",        "0.1.3",   "Ollama local LLM integration"],
        ["langchain-huggingface",   "0.0.3",   "HuggingFace embeddings"],
        ["langchain-ibm",           "0.1.12",  "IBM WatsonX LLM and embeddings"],
        ["ibm-watsonx-ai",          "1.1.2",   "IBM WatsonX AI Python SDK"],
        ["langgraph",               "0.2.16",  "State machine graph for agent orchestration"],
        ["openai",                  "1.40.0",  "OpenAI Python SDK"],
        ["groq",                    "0.9.0",   "Groq Python SDK"],
        ["faiss-cpu",               "1.8.0",   "FAISS vector similarity search"],
        ["sentence-transformers",   "3.0.1",   "Local sentence embedding models"],
        ["huggingface-hub",         "0.24.6",  "HuggingFace Hub client"],
        ["boto3",                   "1.34.162","AWS SDK for Python"],
        ["fastapi",                 "0.112.0", "Async REST + WebSocket API framework"],
        ["uvicorn[standard]",       "0.30.6",  "ASGI server"],
        ["websockets",              "12.0",    "WebSocket protocol support"],
        ["pydantic",                "2.8.2",   "Data validation and serialisation"],
        ["pydantic-settings",       "2.4.0",   "Settings from environment variables"],
        ["structlog",               "24.4.0",  "Structured logging"],
        ["tenacity",                "8.5.0",   "Retry / back-off utilities"],
        ["python-dotenv",           "1.0.1",   ".env file loader"],
        ["numpy",                   "1.26.4",  "Numerical operations (FAISS dependency)"],
        ["pandas",                  "2.2.2",   "Data analysis utilities"],
    ],
    col_widths=[2.0, 1.0, 3.6],
)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 14. DATA MODELS REFERENCE
# ══════════════════════════════════════════════════════════════════════════════

heading("14. Data Models Reference")
hr()
body("All models are defined in aws_devops_agent/models.py and use Pydantic v2.")
doc.add_paragraph()

heading("MetricPoint", level=2)
add_table(
    ["Field", "Type", "Default", "Description"],
    [
        ["name",      "str",      "—",             "Metric name (e.g. CPUUtilization)"],
        ["value",     "float",    "—",             "Numeric metric value"],
        ["unit",      "str",      "\"\"",          "Unit string (%, Bytes, Count, etc.)"],
        ["timestamp", "datetime", "utcnow()",      "Time the metric was sampled"],
    ],
    col_widths=[1.2, 0.9, 1.0, 3.5],
)
doc.add_paragraph()

heading("ResourceSnapshot", level=2)
add_table(
    ["Field", "Type", "Default", "Description"],
    [
        ["resource_id",   "str",            "—",        "Unique resource identifier (instance ID, function name, etc.)"],
        ["resource_type", "str",            "—",        "ec2 | rds | ecs | lambda | alb"],
        ["region",        "str",            "—",        "AWS region"],
        ["metrics",       "List[MetricPoint]","[]",     "Collected metric data points"],
        ["tags",          "Dict[str,str]",  "{}",       "AWS resource tags"],
        ["raw",           "Dict[str,Any]",  "{}",       "Raw API response fields (instance_type, engine, etc.)"],
        ["collected_at",  "datetime",       "utcnow()", "Snapshot collection timestamp"],
    ],
    col_widths=[1.2, 1.5, 0.7, 3.2],
)
doc.add_paragraph()

heading("Anomaly", level=2)
add_table(
    ["Field", "Type", "Default", "Description"],
    [
        ["resource_id",   "str",            "—",         "Resource that triggered the anomaly"],
        ["resource_type", "str",            "—",         "ec2 | rds | ecs | lambda | alb"],
        ["severity",      "Severity",       "—",         "WARNING or CRITICAL"],
        ["title",         "str",            "—",         "Short human-readable anomaly title"],
        ["description",   "str",            "—",         "Metric value vs threshold description"],
        ["metrics",       "List[MetricPoint]","[]",      "Metrics that triggered the anomaly"],
        ["detected_at",   "datetime",       "utcnow()",  "Detection timestamp"],
        ["context",       "Dict[str,Any]",  "{}",        "Raw resource context (from ResourceSnapshot.raw)"],
    ],
    col_widths=[1.2, 1.5, 0.7, 3.2],
)
doc.add_paragraph()

heading("HealingAction", level=2)
add_table(
    ["Field", "Type", "Default", "Description"],
    [
        ["action_id",   "str",          "—",            "UUID for this action"],
        ["anomaly",     "Anomaly",      "—",            "The anomaly that triggered this action"],
        ["action_type", "str",          "—",            "One of the 12 registered action types"],
        ["parameters",  "Dict[str,Any]","{}",           "Action-specific parameters"],
        ["reasoning",   "str",          "\"\"",         "LLM-provided justification"],
        ["status",      "HealingStatus","PENDING",      "Current execution state"],
        ["result",      "Optional[str]","None",         "Outcome message from the handler"],
        ["executed_at", "Optional[datetime]","None",    "Execution start timestamp"],
        ["retries",     "int",          "0",            "Number of retry attempts made"],
    ],
    col_widths=[1.2, 1.5, 0.9, 3.0],
)

# ── Footer ────────────────────────────────────────────────────────────────────
doc.add_paragraph()
hr()
footer_p = doc.add_paragraph()
footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
fr = footer_p.add_run(f"AWS DevOps RAG Agent — IBM WatsonX Edition  |  {datetime.date.today().strftime('%Y')}")
fr.font.size = Pt(9)
fr.font.color.rgb = RGBColor(0x57, 0x60, 0x6A)
fr.font.italic = True

# ── Save ──────────────────────────────────────────────────────────────────────
output_path = "AWS_DevOps_RAG_Agent_WatsonX_Documentation.docx"
doc.save(output_path)
print(f"Saved: {output_path}")
