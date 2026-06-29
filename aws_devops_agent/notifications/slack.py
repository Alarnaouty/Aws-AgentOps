"""
Slack webhook notifications.

Two public functions:
  notify_anomaly(anomaly)         — fires when an anomaly is detected
  notify_healing(action, summary) — fires when a healing action completes

Both are no-ops (logged warning) when SLACK_WEBHOOK_URL is not configured.
"""
from __future__ import annotations

from typing import Optional

import httpx
import structlog

from aws_devops_agent.config import get_settings
from aws_devops_agent.models import Anomaly, HealingAction, HealingStatus, Severity

log = structlog.get_logger(__name__)

# Severity → Slack colour sidebar
_SEVERITY_COLOR = {
    Severity.CRITICAL: "#e01e5a",   # red
    Severity.WARNING:  "#ecb22e",   # yellow
    Severity.OK:       "#2eb67d",   # green
    Severity.UNKNOWN:  "#868686",   # grey
}

# HealingStatus → Slack colour sidebar
_STATUS_COLOR = {
    HealingStatus.SUCCESS:     "#2eb67d",   # green
    HealingStatus.FAILED:      "#e01e5a",   # red
    HealingStatus.SKIPPED:     "#ecb22e",   # yellow
    HealingStatus.IN_PROGRESS: "#36c5f0",   # blue
    HealingStatus.PENDING:     "#868686",   # grey
}


def _post(payload: dict) -> None:
    """POST a Block Kit payload to the configured Slack webhook. Fire-and-forget."""
    cfg = get_settings()
    if not cfg.slack_webhook_url:
        log.debug("slack.not_configured")
        return
    try:
        r = httpx.post(cfg.slack_webhook_url, json=payload, timeout=5)
        if r.status_code != 200:
            log.warning("slack.post_failed", status=r.status_code, body=r.text)
    except Exception as exc:
        log.warning("slack.post_error", error=str(exc))


def notify_anomaly(anomaly: Anomaly) -> None:
    """Send a Slack alert when an anomaly is detected."""
    severity = anomaly.severity
    color    = _SEVERITY_COLOR.get(severity, "#868686")
    icon     = ":rotating_light:" if severity == Severity.CRITICAL else ":warning:"

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{icon}  AWS Anomaly Detected — {severity.value}",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Resource*\n`{anomaly.resource_id}`"},
                            {"type": "mrkdwn", "text": f"*Type*\n{anomaly.resource_type.upper()}"},
                            {"type": "mrkdwn", "text": f"*Severity*\n{severity.value}"},
                            {"type": "mrkdwn", "text": f"*Detected*\n{anomaly.detected_at.strftime('%Y-%m-%d %H:%M:%S')} UTC"},
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{anomaly.title}*\n{anomaly.description}",
                        },
                    },
                    {"type": "divider"},
                ],
            }
        ]
    }
    log.info("slack.anomaly_sent", resource_id=anomaly.resource_id, severity=severity.value)
    _post(payload)


def notify_healing(action: HealingAction, analysis_summary: Optional[str] = None) -> None:
    """Send a Slack report when a healing action completes (success, failed, or skipped)."""
    status = action.status
    color  = _STATUS_COLOR.get(status, "#868686")

    status_icon = {
        HealingStatus.SUCCESS:  ":white_check_mark:",
        HealingStatus.FAILED:   ":x:",
        HealingStatus.SKIPPED:  ":fast_forward:",
    }.get(status, ":hourglass:")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_icon}  Healing Action {status.value} — {action.action_type}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Resource*\n`{action.anomaly.resource_id}`"},
                {"type": "mrkdwn", "text": f"*Type*\n{action.anomaly.resource_type.upper()}"},
                {"type": "mrkdwn", "text": f"*Action*\n`{action.action_type}`"},
                {"type": "mrkdwn", "text": f"*Status*\n{status.value}"},
            ],
        },
    ]

    if action.reasoning:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reasoning*\n{action.reasoning}"},
        })

    if action.result:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Result*\n{action.result}"},
        })

    if analysis_summary:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Analysis*\n{analysis_summary}"},
        })

    if action.retries:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":repeat: Retried {action.retries} time(s)"}
            ],
        })

    blocks.append({"type": "divider"})

    payload = {"attachments": [{"color": color, "blocks": blocks}]}
    log.info("slack.healing_sent", action_type=action.action_type, status=status.value)
    _post(payload)
