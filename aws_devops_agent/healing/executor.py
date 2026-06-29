"""
Self-healing action executor.

Each action_type maps to a handler function that calls the relevant
AWS API.  All destructive operations are guarded by DRY_RUN mode.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
import structlog

from aws_devops_agent.config import get_settings
from aws_devops_agent.models import HealingAction, HealingStatus

log = structlog.get_logger(__name__)
UTC = timezone.utc


def _boto(service: str):
    cfg = get_settings()
    kwargs: Dict[str, Any] = {"region_name": cfg.aws_default_region}
    if cfg.aws_access_key_id:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client(service, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Individual action handlers
# ─────────────────────────────────────────────────────────────────────────────

def _restart_ec2_service(params: Dict) -> str:
    """Run a systemd service restart on an EC2 instance via SSM."""
    instance_id = params["instance_id"]
    service_name = params.get("service_name", "myapp")
    ssm = _boto("ssm")
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [f"systemctl restart {service_name}"]},
    )
    cmd_id = resp["Command"]["CommandId"]
    return f"SSM command sent: {cmd_id}"


def _stop_start_instance(params: Dict) -> str:
    instance_id = params["instance_id"]
    ec2 = _boto("ec2")
    ec2.stop_instances(InstanceIds=[instance_id])
    # Poll until stopped
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[instance_id])
    ec2.start_instances(InstanceIds=[instance_id])
    return f"Instance {instance_id} stop/started"


def _scale_out_asg(params: Dict) -> str:
    asg_name = params["asg_name"]
    increment = int(params.get("increment", 1))
    asg = _boto("autoscaling")
    current = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    group = current["AutoScalingGroups"][0]
    new_desired = group["DesiredCapacity"] + increment
    max_cap = group["MaxSize"]
    if new_desired > max_cap:
        return f"Cannot scale out: desired {new_desired} exceeds max {max_cap}"
    asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=new_desired)
    return f"ASG {asg_name} desired capacity set to {new_desired}"


def _cleanup_disk_ssm(params: Dict) -> str:
    instance_id = params["instance_id"]
    ssm = _boto("ssm")
    commands = [
        "find /tmp -type f -atime +7 -delete",
        "journalctl --vacuum-time=7d",
        "find /var/log -name '*.gz' -mtime +14 -delete",
    ]
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
    )
    cmd_id = resp["Command"]["CommandId"]
    return f"Disk cleanup SSM command sent: {cmd_id}"


def _reboot_rds_instance(params: Dict) -> str:
    db_id = params["db_instance_id"]
    rds = _boto("rds")
    rds.reboot_db_instance(DBInstanceIdentifier=db_id)
    return f"RDS instance {db_id} reboot initiated"


def _modify_rds_storage(params: Dict) -> str:
    db_id = params["db_instance_id"]
    extra_gb = int(params.get("extra_gb", 20))
    rds = _boto("rds")
    info = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]
    new_size = info["AllocatedStorage"] + extra_gb
    rds.modify_db_instance(
        DBInstanceIdentifier=db_id,
        AllocatedStorage=new_size,
        ApplyImmediately=True,
    )
    return f"RDS {db_id} storage increase to {new_size} GB requested"


def _rollback_lambda_version(params: Dict) -> str:
    function_name = params["function_name"]
    alias = params.get("alias", "live")
    lmb = _boto("lambda")
    # Get current alias version
    current = lmb.get_alias(FunctionName=function_name, Name=alias)
    current_version = current["FunctionVersion"]
    # List versions and pick the one before current
    versions = lmb.list_versions_by_function(FunctionName=function_name)["Versions"]
    numbered = [v for v in versions if v["Version"] != "$LATEST" and v["Version"] != current_version]
    if not numbered:
        return "No previous version to roll back to"
    prev = sorted(numbered, key=lambda v: int(v["Version"]))[-1]
    lmb.update_alias(FunctionName=function_name, Name=alias, FunctionVersion=prev["Version"])
    return f"Lambda {function_name} alias {alias} rolled back to version {prev['Version']}"


def _update_lambda_timeout(params: Dict) -> str:
    function_name = params["function_name"]
    increment = int(params.get("increment_seconds", 30))
    lmb = _boto("lambda")
    config = lmb.get_function_configuration(FunctionName=function_name)
    new_timeout = min(900, config["Timeout"] + increment)
    lmb.update_function_configuration(FunctionName=function_name, Timeout=new_timeout)
    return f"Lambda {function_name} timeout updated to {new_timeout}s"


def _update_lambda_memory(params: Dict) -> str:
    function_name = params["function_name"]
    increment = int(params.get("increment_mb", 256))
    lmb = _boto("lambda")
    config = lmb.get_function_configuration(FunctionName=function_name)
    new_mem = min(10240, config["MemorySize"] + increment)
    lmb.update_function_configuration(FunctionName=function_name, MemorySize=new_mem)
    return f"Lambda {function_name} memory updated to {new_mem} MB"


def _update_ecs_service(params: Dict) -> str:
    cluster = params["cluster"]
    service = params["service"]
    ecs = _boto("ecs")
    ecs.update_service(cluster=cluster, service=service, forceNewDeployment=True)
    return f"ECS service {service} force-new-deployment triggered"


def _scale_ecs_service(params: Dict) -> str:
    cluster = params["cluster"]
    service = params["service"]
    increment = int(params.get("increment", 2))
    ecs = _boto("ecs")
    svc = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
    new_count = svc["desiredCount"] + increment
    ecs.update_service(cluster=cluster, service=service, desiredCount=new_count)
    return f"ECS service {service} desired count set to {new_count}"


def _deregister_unhealthy_targets(params: Dict) -> str:
    tg_arn = params["target_group_arn"]
    elbv2 = _boto("elbv2")
    health = elbv2.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
    unhealthy = [
        {"Id": t["Target"]["Id"], "Port": t["Target"]["Port"]}
        for t in health
        if t["TargetHealth"]["State"] != "healthy"
    ]
    if not unhealthy:
        return "No unhealthy targets found"
    elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=unhealthy)
    return f"Deregistered {len(unhealthy)} unhealthy target(s) from {tg_arn}"


# ─────────────────────────────────────────────────────────────────────────────
# Action registry
# ─────────────────────────────────────────────────────────────────────────────

ACTION_REGISTRY = {
    "restart_ec2_service":        _restart_ec2_service,
    "stop_start_instance":        _stop_start_instance,
    "scale_out_asg":              _scale_out_asg,
    "cleanup_disk_ssm":           _cleanup_disk_ssm,
    "reboot_rds_instance":        _reboot_rds_instance,
    "modify_rds_storage":         _modify_rds_storage,
    "rollback_lambda_version":    _rollback_lambda_version,
    "update_lambda_timeout":      _update_lambda_timeout,
    "update_lambda_memory":       _update_lambda_memory,
    "update_ecs_service":         _update_ecs_service,
    "scale_ecs_service":          _scale_ecs_service,
    "deregister_unhealthy_targets": _deregister_unhealthy_targets,
}


# ─────────────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────────────

def execute_healing_action(action: HealingAction) -> HealingAction:
    """
    Execute a healing action, respecting DRY_RUN and retry limits.
    Mutates and returns the action with updated status/result.
    """
    cfg = get_settings()

    if cfg.agent_dry_run:
        log.info(
            "healing.dry_run",
            action_type=action.action_type,
            params=action.parameters,
        )
        action.status = HealingStatus.SKIPPED
        action.result = f"[DRY_RUN] Would execute: {action.action_type} with {action.parameters}"
        return action

    handler = ACTION_REGISTRY.get(action.action_type)
    if not handler:
        action.status = HealingStatus.FAILED
        action.result = f"Unknown action type: {action.action_type}"
        log.error("healing.unknown_action", action_type=action.action_type)
        return action

    action.status = HealingStatus.IN_PROGRESS
    action.executed_at = datetime.now(UTC)

    backoff = [30, 60, 120]
    for attempt in range(cfg.agent_max_healing_retries):
        try:
            result = handler(action.parameters)
            action.status = HealingStatus.SUCCESS
            action.result = result
            log.info(
                "healing.success",
                action_type=action.action_type,
                result=result,
                attempt=attempt + 1,
            )
            return action
        except Exception as exc:
            action.retries += 1
            log.warning(
                "healing.retry",
                action_type=action.action_type,
                attempt=attempt + 1,
                error=str(exc),
            )
            if attempt < len(backoff):
                time.sleep(backoff[attempt])

    action.status = HealingStatus.FAILED
    action.result = f"All {cfg.agent_max_healing_retries} attempts failed"
    log.error("healing.failed", action_type=action.action_type)
    return action
