"""
Analyzer engine — turns raw ResourceSnapshots into Anomalies and then
uses the LLM + RAG context to reason about root cause and suggest a
HealingAction.
"""
from __future__ import annotations

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
restart_ec2_service, reboot_ec2_instance, stop_start_instance, scale_out_asg, cleanup_disk_ssm,
reboot_rds_instance, modify_rds_storage, rollback_lambda_version,
update_lambda_timeout, update_lambda_memory, update_ecs_service,
scale_ecs_service, deregister_unhealthy_targets

EC2 high CPU decision guide (follow in order):
1. restart_ec2_service  — if a runaway process/service is suspected (WARNING or CRITICAL)
2. scale_out_asg        — if traffic spike is the cause
3. reboot_ec2_instance  — last resort for CRITICAL CPU that does not respond to service restart

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
