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


def _reboot_ec2_instance(params: Dict) -> str:
    """Reboot an EC2 instance — last resort for persistent high CPU or unresponsive instance."""
    instance_id = params["instance_id"]
    ec2 = _boto("ec2")
    ec2.reboot_instances(InstanceIds=[instance_id])
    return f"EC2 instance {instance_id} reboot initiated"


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


# ── EC2 ───────────────────────────────────────────────────────────────────────

def _extend_ebs_volume(params: Dict) -> str:
    """Increase an EBS volume size by extra_gb (default 20)."""
    volume_id = params["volume_id"]
    extra_gb   = int(params.get("extra_gb", 20))
    ec2 = _boto("ec2")
    vol = ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]
    new_size = vol["Size"] + extra_gb
    ec2.modify_volume(VolumeId=volume_id, Size=new_size)
    return f"EBS volume {volume_id} resize to {new_size} GB requested"


# ── RDS ───────────────────────────────────────────────────────────────────────

def _enable_rds_proxy(params: Dict) -> str:
    """Create an RDS Proxy in front of a DB instance to pool connections."""
    db_id      = params["db_instance_id"]
    proxy_name = params.get("proxy_name", f"proxy-{db_id}")
    role_arn   = params["role_arn"]          # IAM role ARN with rds-db:connect
    subnet_ids = params["subnet_ids"]        # list of subnet IDs
    rds = _boto("rds")
    resp = rds.create_db_proxy(
        DBProxyName=proxy_name,
        EngineFamily="MYSQL",               # caller should override for POSTGRESQL
        Auth=[{"AuthScheme": "SECRETS", "IAMAuth": "DISABLED"}],
        RoleArn=role_arn,
        VpcSubnetIds=subnet_ids,
        RequireTLS=True,
    )
    return f"RDS Proxy {proxy_name} creation initiated for {db_id}"


# ── ECS ───────────────────────────────────────────────────────────────────────

def _rollback_ecs_task_def(params: Dict) -> str:
    """Re-deploy the previous task definition revision for an ECS service."""
    cluster = params["cluster"]
    service = params["service"]
    ecs = _boto("ecs")
    svc = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
    current_arn = svc["taskDefinition"]        # arn:…:family:N
    family  = current_arn.split("/")[-1].rsplit(":", 1)[0]
    current_rev = int(current_arn.rsplit(":", 1)[-1])
    if current_rev <= 1:
        return f"No previous revision to roll back to for {family}"
    prev_arn = current_arn.rsplit(":", 1)[0] + f":{current_rev - 1}"
    ecs.update_service(cluster=cluster, service=service, taskDefinition=prev_arn)
    return f"ECS service {service} rolled back to task def revision {current_rev - 1}"


def _increase_ecs_task_memory(params: Dict) -> str:
    """Register a new task definition revision with higher memory and deploy it."""
    cluster      = params["cluster"]
    service      = params["service"]
    extra_mb     = int(params.get("extra_mb", 512))
    ecs = _boto("ecs")
    svc = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
    td  = ecs.describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    new_mem = str(int(td.get("memory", "1024")) + extra_mb)
    # Register new revision
    register_kwargs = {
        k: td[k] for k in (
            "family", "containerDefinitions", "networkMode",
            "requiresCompatibilities", "cpu",
        ) if k in td
    }
    register_kwargs["memory"] = new_mem
    new_td = ecs.register_task_definition(**register_kwargs)
    new_arn = new_td["taskDefinition"]["taskDefinitionArn"]
    ecs.update_service(cluster=cluster, service=service, taskDefinition=new_arn)
    return f"ECS service {service} redeployed with memory {new_mem} MB"


def _update_ecs_desired_count(params: Dict) -> str:
    """Set ECS service desired count to an explicit safe minimum."""
    cluster     = params["cluster"]
    service     = params["service"]
    desired     = int(params["desired_count"])
    ecs = _boto("ecs")
    ecs.update_service(cluster=cluster, service=service, desiredCount=desired)
    return f"ECS service {service} desired count set to {desired}"


def _toggle_capacity_provider(params: Dict) -> str:
    """Switch an ECS service from FARGATE_SPOT to FARGATE (on-demand)."""
    cluster = params["cluster"]
    service = params["service"]
    ecs = _boto("ecs")
    ecs.update_service(
        cluster=cluster,
        service=service,
        capacityProviderStrategy=[{"capacityProvider": "FARGATE", "weight": 1, "base": 0}],
        forceNewDeployment=True,
    )
    return f"ECS service {service} switched to FARGATE on-demand capacity"


# ── ALB ───────────────────────────────────────────────────────────────────────

def _increase_alb_idle_timeout(params: Dict) -> str:
    """Increase ALB idle timeout to reduce 504 Gateway Timeout errors."""
    load_balancer_arn = params["load_balancer_arn"]
    timeout_seconds   = int(params.get("timeout_seconds", 120))
    elbv2 = _boto("elbv2")
    elbv2.modify_load_balancer_attributes(
        LoadBalancerArn=load_balancer_arn,
        Attributes=[{"Key": "idle_timeout.timeout_seconds", "Value": str(timeout_seconds)}],
    )
    return f"ALB {load_balancer_arn} idle timeout set to {timeout_seconds}s"


# ── Lambda ────────────────────────────────────────────────────────────────────

def _put_function_concurrency(params: Dict) -> str:
    """Set reserved concurrency for a Lambda function to prevent throttling."""
    function_name = params["function_name"]
    concurrency   = int(params.get("reserved_concurrency", 100))
    lmb = _boto("lambda")
    lmb.put_function_concurrency(
        FunctionName=function_name,
        ReservedConcurrentExecutions=concurrency,
    )
    return f"Lambda {function_name} reserved concurrency set to {concurrency}"


# ─────────────────────────────────────────────────────────────────────────────
# Action registry
# ─────────────────────────────────────────────────────────────────────────────

ACTION_REGISTRY = {
    # EC2
    "restart_ec2_service":          _restart_ec2_service,
    "reboot_ec2_instance":          _reboot_ec2_instance,
    "stop_start_instance":          _stop_start_instance,
    "scale_out_asg":                _scale_out_asg,
    "cleanup_disk_ssm":             _cleanup_disk_ssm,
    "extend_ebs_volume":            _extend_ebs_volume,
    # RDS
    "reboot_rds_instance":          _reboot_rds_instance,
    "modify_rds_storage":           _modify_rds_storage,
    "enable_rds_proxy":             _enable_rds_proxy,
    # ECS
    "update_ecs_service":           _update_ecs_service,
    "scale_ecs_service":            _scale_ecs_service,
    "rollback_ecs_task_def":        _rollback_ecs_task_def,
    "increase_ecs_task_memory":     _increase_ecs_task_memory,
    "update_ecs_desired_count":     _update_ecs_desired_count,
    "toggle_capacity_provider":     _toggle_capacity_provider,
    # ALB
    "deregister_unhealthy_targets": _deregister_unhealthy_targets,
    "increase_alb_idle_timeout":    _increase_alb_idle_timeout,
    # Lambda
    "rollback_lambda_version":      _rollback_lambda_version,
    "update_lambda_timeout":        _update_lambda_timeout,
    "update_lambda_memory":         _update_lambda_memory,
    "put_function_concurrency":     _put_function_concurrency,
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
