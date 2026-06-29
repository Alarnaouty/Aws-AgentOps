"""
Analyzer engine — turns raw ResourceSnapshots into Anomalies and then
uses the LLM + RAG context to reason about root cause and suggest a
HealingAction.

Detection modes (ANOMALY_DETECTION_MODE in .env):
  threshold  — fast rule-based check against _THRESHOLDS dict (original)
  llm        — sends ALL metrics to LLM; no fixed thresholds required
  hybrid     — threshold first, then LLM catch-all for uncovered metrics (default)
"""
from __future__ import annotations

import json
import uuid
from typing import List, Optional, Tuple

import structlog
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from aws_devops_agent.config import get_settings
from aws_devops_agent.models import (
    Anomaly,
    HealingAction,
    MetricPoint,
    ResourceSnapshot,
    Severity,
)
from aws_devops_agent.rag.vector_store import get_retriever

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based anomaly detection (fast, no LLM)
# ─────────────────────────────────────────────────────────────────────────────

_THRESHOLDS: dict = {
    # (resource_type, metric_name): (warning_threshold, critical_threshold, higher_is_bad)
    ("ec2", "CPUUtilization"):             (75.0, 90.0, True),
    ("ec2", "StatusCheckFailed"):          (0.5,  0.5,  True),
    ("ec2", "StatusCheckFailed_Instance"): (0.5,  0.5,  True),
    ("rds", "CPUUtilization"):             (75.0, 85.0, True),
    ("rds", "FreeStorageSpace"):           (5e9,  2e9,  False),  # bytes; lower is bad
    ("rds", "DatabaseConnections"):        (80.0, 95.0, True),   # % of max (approximated)
    ("lambda", "Errors"):                  (1.0,  5.0,  True),   # count
    ("lambda", "Throttles"):               (1.0,  10.0, True),
    ("ecs", "CPUUtilization"):             (75.0, 90.0, True),
    ("ecs", "MemoryUtilization"):          (80.0, 95.0, True),
    ("alb", "HTTPCode_ELB_5XX_Count"):     (5.0,  20.0, True),
    ("alb", "UnHealthyHostCount"):         (1.0,  1.0,  True),
    ("alb", "TargetResponseTime"):         (1.0,  3.0,  True),   # seconds
}


def detect_anomalies(snapshots: List[ResourceSnapshot]) -> List[Anomaly]:
    anomalies: List[Anomaly] = []

    for snap in snapshots:
        for metric in snap.metrics:
            key = (snap.resource_type, metric.name)
            if key not in _THRESHOLDS:
                continue
            warn_t, crit_t, higher_is_bad = _THRESHOLDS[key]

            def _exceeds(val: float, threshold: float) -> bool:
                return val > threshold if higher_is_bad else val < threshold

            severity: Optional[Severity] = None
            if _exceeds(metric.value, crit_t):
                severity = Severity.CRITICAL
            elif _exceeds(metric.value, warn_t):
                severity = Severity.WARNING

            if severity:
                anomalies.append(
                    Anomaly(
                        resource_id=snap.resource_id,
                        resource_type=snap.resource_type,
                        severity=severity,
                        title=f"{snap.resource_type.upper()} {metric.name} anomaly on {snap.resource_id}",
                        description=(
                            f"{metric.name} is {metric.value:.2f} {metric.unit} "
                            f"(threshold: warn={warn_t}, crit={crit_t})"
                        ),
                        metrics=[metric],
                        context=snap.raw,
                    )
                )

    log.info("analyzer.anomalies_detected", count=len(anomalies))
    return anomalies


# ─────────────────────────────────────────────────────────────────────────────
# LLM-based anomaly detection (no thresholds)
# ─────────────────────────────────────────────────────────────────────────────

_DETECTION_SYSTEM = """You are an expert AWS Site Reliability Engineer.
You will receive a snapshot of metrics for a single AWS resource.
Your job is to identify ANY anomalous or concerning metrics — without relying on fixed thresholds.
Consider: sudden spikes, unusually high/low values, metrics that indicate failure states,
values that are unusual for the resource type, or combinations of metrics that together signal a problem.

For each anomaly you find, respond with a JSON array (may be empty []):
[
  {{
    "metric_name": "<name of the metric>",
    "severity": "WARNING" or "CRITICAL",
    "title": "<short description>",
    "description": "<detailed explanation of why this is anomalous>",
    "value": <numeric value>
  }}
]

RESPOND ONLY with the JSON array. No extra text."""


def detect_anomalies_llm(snapshots: List[ResourceSnapshot]) -> List[Anomaly]:
    """Threshold-free anomaly detection using the LLM to judge all metrics."""
    llm = _get_llm()
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_messages([
        ("system", _DETECTION_SYSTEM),
        ("human", (
            "Resource ID: {resource_id}\n"
            "Resource Type: {resource_type}\n"
            "Region: {region}\n"
            "Resource Context: {raw}\n\n"
            "Current Metrics:\n{metrics_text}"
        )),
    ])
    chain = prompt | llm | StrOutputParser()

    anomalies: List[Anomaly] = []

    for snap in snapshots:
        if not snap.metrics:
            continue

        metrics_text = "\n".join(
            f"  {m.name}: {m.value:.4f} {m.unit}" for m in snap.metrics
        )

        try:
            raw_output = chain.invoke({
                "resource_id":   snap.resource_id,
                "resource_type": snap.resource_type,
                "region":        snap.region,
                "raw":           str(snap.raw),
                "metrics_text":  metrics_text,
            })

            # Strip markdown code fences if present
            cleaned = raw_output.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
                cleaned = cleaned.rsplit("```", 1)[0]

            findings: List[dict] = json.loads(cleaned)

            for f in findings:
                metric_name = f.get("metric_name", "unknown")
                severity_str = f.get("severity", "WARNING").upper()
                severity = Severity.CRITICAL if severity_str == "CRITICAL" else Severity.WARNING

                # Attach the matching MetricPoint if we have it
                matched = [m for m in snap.metrics if m.name == metric_name]

                anomalies.append(Anomaly(
                    resource_id=snap.resource_id,
                    resource_type=snap.resource_type,
                    severity=severity,
                    title=f.get("title", f"{snap.resource_type.upper()} {metric_name} anomaly"),
                    description=f.get("description", ""),
                    metrics=matched,
                    context=snap.raw,
                ))

            log.info(
                "analyzer.llm_detection",
                resource_id=snap.resource_id,
                findings=len(findings),
            )

        except Exception as exc:
            log.warning(
                "analyzer.llm_detection_error",
                resource_id=snap.resource_id,
                error=str(exc),
            )

    log.info("analyzer.llm_anomalies_detected", count=len(anomalies))
    return anomalies


def detect_anomalies_hybrid(snapshots: List[ResourceSnapshot]) -> List[Anomaly]:
    """
    Hybrid: threshold check first (fast), then LLM on snapshots whose metrics
    were NOT covered by any threshold rule — catches uncovered metrics like
    NetworkIn, ReadLatency, FreeableMemory, Duration.
    """
    threshold_anomalies = detect_anomalies(snapshots)

    # Find snapshots that had at least one uncovered metric
    covered_keys = set(_THRESHOLDS.keys())
    uncovered_snaps = [
        snap for snap in snapshots
        if any((snap.resource_type, m.name) not in covered_keys for m in snap.metrics)
    ]

    llm_anomalies = detect_anomalies_llm(uncovered_snaps) if uncovered_snaps else []

    # Deduplicate: drop LLM finding if threshold already flagged same resource+metric
    threshold_keys = {(a.resource_id, m.name) for a in threshold_anomalies for m in a.metrics}
    deduped_llm = [
        a for a in llm_anomalies
        if not any((a.resource_id, m.name) in threshold_keys for m in a.metrics)
    ]

    all_anomalies = threshold_anomalies + deduped_llm
    log.info("analyzer.hybrid_anomalies_detected",
             threshold=len(threshold_anomalies), llm=len(deduped_llm))
    return all_anomalies


# ─────────────────────────────────────────────────────────────────────────────
# LLM-based root-cause analysis + healing recommendation
# ─────────────────────────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are an expert AWS DevOps Site Reliability Engineer.
You will be given:
1. A detected anomaly on an AWS resource.
2. Relevant runbook context retrieved from the knowledge base.

Your job is to:
- Identify the most likely root cause.
- Recommend ONE healing action from the available action types.
- Provide clear reasoning.

Available healing action types:
EC2:    restart_ec2_service, reboot_ec2_instance, stop_start_instance, scale_out_asg, cleanup_disk_ssm, extend_ebs_volume
RDS:    reboot_rds_instance, modify_rds_storage, enable_rds_proxy
ECS:    update_ecs_service, scale_ecs_service, rollback_ecs_task_def, increase_ecs_task_memory, update_ecs_desired_count, toggle_capacity_provider
ALB:    deregister_unhealthy_targets, increase_alb_idle_timeout
Lambda: rollback_lambda_version, update_lambda_timeout, update_lambda_memory, put_function_concurrency

Decision guides:
- EC2 high CPU:      restart_ec2_service (WARNING) → scale_out_asg (traffic spike) → reboot_ec2_instance (CRITICAL last resort)
- EC2 disk full:     cleanup_disk_ssm first → extend_ebs_volume if still full
- RDS connections:   enable_rds_proxy (pool connections) — do NOT reboot for connection issues
- ECS crash loop:    rollback_ecs_task_def (bad deploy) or increase_ecs_task_memory (OOM/exit 137)
- ECS Fargate spot:  toggle_capacity_provider → switch to on-demand FARGATE
- ALB 504 timeouts:  increase_alb_idle_timeout before deregistering targets
- Lambda throttles:  put_function_concurrency to raise reserved concurrency limit

RESPOND ONLY with valid JSON matching this schema:
{{
  "root_cause": "<concise root cause>",
  "recommended_action": "<action_type from list above>",
  "action_parameters": {{<key: value pairs required by that action>}},
  "reasoning": "<brief explanation>",
  "confidence": "high|medium|low"
}}""",
        ),
        (
            "human",
            """Anomaly:
Resource ID: {resource_id}
Resource Type: {resource_type}
Severity: {severity}
Title: {title}
Description: {description}
Context: {context}

Runbook / Knowledge Base Context:
{runbook_context}

Provide your analysis as JSON.""",
        ),
    ]
)


def _get_llm():
    cfg = get_settings()

    if cfg.llm_provider == "bedrock":
        from langchain_aws import ChatBedrock
        return ChatBedrock(
            model_id=cfg.bedrock_model_id,
            region_name=cfg.aws_default_region,
        )

    if cfg.llm_provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=cfg.groq_model,
            api_key=cfg.groq_api_key,
            temperature=0,
        )

    if cfg.llm_provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=cfg.ollama_model,
            base_url=cfg.ollama_base_url,
            temperature=0,
            format="json",          # instructs Ollama to always return JSON
        )

    if cfg.llm_provider == "watsonx":
        from langchain_ibm import ChatWatsonx
        from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
        return ChatWatsonx(
            model_id=cfg.watsonx_llm_model,
            url=cfg.watsonx_url,
            apikey=cfg.watsonx_api_key,
            project_id=cfg.watsonx_project_id,
            params={
                GenParams.DECODING_METHOD: "greedy",
                GenParams.MAX_NEW_TOKENS: 1024,
                GenParams.MIN_NEW_TOKENS: 1,
                GenParams.TEMPERATURE: 0,
            },
        )

    # default: openai
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=cfg.llm_model,
        openai_api_key=cfg.openai_api_key,
        temperature=0,
        response_format={"type": "json_object"},
    )


def analyze_anomaly(anomaly: Anomaly) -> Tuple[str, Optional[HealingAction]]:
    """
    Run LLM analysis on a single anomaly.
    Returns (analysis_text, HealingAction | None).
    """
    retriever = get_retriever(k=6)
    query = f"{anomaly.resource_type} {anomaly.title} {anomaly.description}"
    docs = retriever.invoke(query)
    runbook_context = "\n\n".join(d.page_content for d in docs)

    llm = _get_llm()
    chain = _ANALYSIS_PROMPT | llm | JsonOutputParser()

    result: dict = chain.invoke(
        {
            "resource_id": anomaly.resource_id,
            "resource_type": anomaly.resource_type,
            "severity": anomaly.severity.value,
            "title": anomaly.title,
            "description": anomaly.description,
            "context": str(anomaly.context),
            "runbook_context": runbook_context,
        }
    )

    log.info(
        "analyzer.llm_result",
        resource_id=anomaly.resource_id,
        action=result.get("recommended_action"),
        confidence=result.get("confidence"),
    )

    action_type = result.get("recommended_action", "")
    healing: Optional[HealingAction] = None

    if action_type:
        healing = HealingAction(
            action_id=str(uuid.uuid4()),
            anomaly=anomaly,
            action_type=action_type,
            parameters=result.get("action_parameters", {}),
            reasoning=result.get("reasoning", ""),
        )

    summary = (
        f"Root cause: {result.get('root_cause', 'unknown')}\n"
        f"Confidence: {result.get('confidence', 'unknown')}\n"
        f"Recommended action: {action_type}"
    )
    return summary, healing
