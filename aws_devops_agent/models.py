"""
Shared data models (Pydantic) used across all modules.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Severity / Status enums ───────────────────────────────────────────────────

class Severity(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class HealingStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# ── Monitoring primitives ─────────────────────────────────────────────────────

class MetricPoint(BaseModel):
    name: str
    value: float
    unit: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ResourceSnapshot(BaseModel):
    """Point-in-time health snapshot of a single AWS resource."""
    resource_id: str
    resource_type: str          # ec2 | rds | ecs | lambda | alb
    region: str
    metrics: List[MetricPoint] = []
    tags: Dict[str, str] = {}
    raw: Dict[str, Any] = {}
    collected_at: datetime = Field(default_factory=datetime.utcnow)


# ── Anomaly / Finding ─────────────────────────────────────────────────────────

class Anomaly(BaseModel):
    resource_id: str
    resource_type: str
    severity: Severity
    title: str
    description: str
    metrics: List[MetricPoint] = []
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = {}


# ── Healing action ────────────────────────────────────────────────────────────

class HealingAction(BaseModel):
    action_id: str
    anomaly: Anomaly
    action_type: str            # e.g. restart_instance, scale_out, reboot_rds …
    parameters: Dict[str, Any] = {}
    reasoning: str = ""
    status: HealingStatus = HealingStatus.PENDING
    result: Optional[str] = None
    executed_at: Optional[datetime] = None
    retries: int = 0


# ── Agent event (streamed to dashboard) ──────────────────────────────────────

class AgentEvent(BaseModel):
    event_type: str             # monitor | analyze | heal | info | error
    payload: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
