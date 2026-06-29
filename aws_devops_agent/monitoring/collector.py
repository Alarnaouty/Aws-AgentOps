"""
AWS monitoring collector — gathers metrics from CloudWatch, EC2, RDS,
ECS, Lambda, and ALB then produces ResourceSnapshot objects.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
import structlog

from aws_devops_agent.config import get_settings
from aws_devops_agent.models import MetricPoint, ResourceSnapshot

log = structlog.get_logger(__name__)

UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _boto(service: str):
    cfg = get_settings()
    kwargs: Dict[str, Any] = {"region_name": cfg.aws_default_region}
    if cfg.aws_access_key_id:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client(service, **kwargs)


def _cw_stat(
    cw,
    namespace: str,
    metric_name: str,
    dimensions: List[Dict],
    stat: str = "Average",
    minutes: int = 5,
) -> Optional[float]:
    """Fetch single latest CloudWatch statistic value."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    resp = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start,
        EndTime=end,
        Period=300,
        Statistics=[stat],
    )
    points = resp.get("Datapoints", [])
    if not points:
        return None
    latest = sorted(points, key=lambda p: p["Timestamp"])[-1]
    return latest.get(stat)


# ─────────────────────────────────────────────────────────────────────────────
# EC2 Collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_ec2() -> List[ResourceSnapshot]:
    cfg = get_settings()
    ec2 = _boto("ec2")
    cw = _boto("cloudwatch")
    snapshots: List[ResourceSnapshot] = []

    filters = [{"Name": "instance-state-name", "Values": ["running"]}]
    if cfg.ec2_ids():
        filters.append({"Name": "instance-id", "Values": cfg.ec2_ids()})

    pages = ec2.get_paginator("describe_instances").paginate(Filters=filters)
    instances = [i for p in pages for r in p["Reservations"] for i in r["Instances"]]

    for inst in instances:
        iid = inst["InstanceId"]
        dims = [{"Name": "InstanceId", "Value": iid}]

        def stat(name, s="Average"):
            return _cw_stat(cw, "AWS/EC2", name, dims, stat=s)

        tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
        metrics: List[MetricPoint] = []

        for mname in ("CPUUtilization", "NetworkIn", "NetworkOut",
                       "StatusCheckFailed", "StatusCheckFailed_Instance",
                       "StatusCheckFailed_System"):
            val = stat(mname)
            if val is not None:
                metrics.append(MetricPoint(name=mname, value=val, unit="%"))

        snapshots.append(
            ResourceSnapshot(
                resource_id=iid,
                resource_type="ec2",
                region=cfg.aws_default_region,
                metrics=metrics,
                tags=tags,
                raw={
                    "instance_type": inst.get("InstanceType"),
                    "state": inst["State"]["Name"],
                    "launch_time": str(inst.get("LaunchTime")),
                },
            )
        )
    log.info("collector.ec2", count=len(snapshots))
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# RDS Collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_rds() -> List[ResourceSnapshot]:
    cfg = get_settings()
    rds = _boto("rds")
    cw = _boto("cloudwatch")
    snapshots: List[ResourceSnapshot] = []

    instances = rds.describe_db_instances().get("DBInstances", [])
    if cfg.rds_ids():
        instances = [i for i in instances if i["DBInstanceIdentifier"] in cfg.rds_ids()]

    for db in instances:
        dbid = db["DBInstanceIdentifier"]
        dims = [{"Name": "DBInstanceIdentifier", "Value": dbid}]

        def stat(name, s="Average"):
            return _cw_stat(cw, "AWS/RDS", name, dims, stat=s)

        metrics: List[MetricPoint] = []
        for mname, unit in [
            ("CPUUtilization", "%"),
            ("FreeStorageSpace", "Bytes"),
            ("DatabaseConnections", "Count"),
            ("ReadLatency", "Seconds"),
            ("WriteLatency", "Seconds"),
            ("FreeableMemory", "Bytes"),
        ]:
            val = stat(mname)
            if val is not None:
                metrics.append(MetricPoint(name=mname, value=val, unit=unit))

        snapshots.append(
            ResourceSnapshot(
                resource_id=dbid,
                resource_type="rds",
                region=cfg.aws_default_region,
                metrics=metrics,
                raw={
                    "engine": db.get("Engine"),
                    "instance_class": db.get("DBInstanceClass"),
                    "status": db.get("DBInstanceStatus"),
                    "multi_az": db.get("MultiAZ"),
                },
            )
        )
    log.info("collector.rds", count=len(snapshots))
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# ECS Collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_ecs() -> List[ResourceSnapshot]:
    cfg = get_settings()
    ecs = _boto("ecs")
    cw = _boto("cloudwatch")
    snapshots: List[ResourceSnapshot] = []

    cluster_arns = ecs.list_clusters().get("clusterArns", [])
    if cfg.ecs_clusters():
        cluster_arns = [a for a in cluster_arns if any(n in a for n in cfg.ecs_clusters())]

    for cluster_arn in cluster_arns:
        cluster_name = cluster_arn.split("/")[-1]
        svc_arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
        if not svc_arns:
            continue
        services = ecs.describe_services(cluster=cluster_arn, services=svc_arns).get("services", [])

        for svc in services:
            svc_name = svc["serviceName"]
            dims = [
                {"Name": "ClusterName", "Value": cluster_name},
                {"Name": "ServiceName", "Value": svc_name},
            ]

            def stat(name, s="Average"):
                return _cw_stat(cw, "AWS/ECS", name, dims, stat=s)

            metrics: List[MetricPoint] = []
            for mname, unit in [("CPUUtilization", "%"), ("MemoryUtilization", "%")]:
                val = stat(mname)
                if val is not None:
                    metrics.append(MetricPoint(name=mname, value=val, unit=unit))

            metrics.append(MetricPoint(name="RunningCount", value=svc["runningCount"], unit="Count"))
            metrics.append(MetricPoint(name="DesiredCount", value=svc["desiredCount"], unit="Count"))

            snapshots.append(
                ResourceSnapshot(
                    resource_id=f"{cluster_name}/{svc_name}",
                    resource_type="ecs",
                    region=cfg.aws_default_region,
                    metrics=metrics,
                    raw={
                        "cluster": cluster_name,
                        "status": svc.get("status"),
                        "launch_type": svc.get("launchType"),
                        "task_definition": svc.get("taskDefinition"),
                    },
                )
            )
    log.info("collector.ecs", count=len(snapshots))
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# Lambda Collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_lambda() -> List[ResourceSnapshot]:
    cfg = get_settings()
    lmb = _boto("lambda")
    cw = _boto("cloudwatch")
    snapshots: List[ResourceSnapshot] = []

    functions = lmb.list_functions().get("Functions", [])
    if cfg.lambda_functions():
        functions = [f for f in functions if f["FunctionName"] in cfg.lambda_functions()]

    for fn in functions:
        fname = fn["FunctionName"]
        dims = [{"Name": "FunctionName", "Value": fname}]

        def stat(name, s="Sum"):
            return _cw_stat(cw, "AWS/Lambda", name, dims, stat=s)

        metrics: List[MetricPoint] = []
        for mname, s, unit in [
            ("Invocations", "Sum", "Count"),
            ("Errors", "Sum", "Count"),
            ("Throttles", "Sum", "Count"),
            ("Duration", "Average", "Milliseconds"),
            ("ConcurrentExecutions", "Maximum", "Count"),
        ]:
            val = _cw_stat(cw, "AWS/Lambda", mname, dims, stat=s)
            if val is not None:
                metrics.append(MetricPoint(name=mname, value=val, unit=unit))

        snapshots.append(
            ResourceSnapshot(
                resource_id=fname,
                resource_type="lambda",
                region=cfg.aws_default_region,
                metrics=metrics,
                raw={
                    "runtime": fn.get("Runtime"),
                    "memory": fn.get("MemorySize"),
                    "timeout": fn.get("Timeout"),
                    "last_modified": fn.get("LastModified"),
                },
            )
        )
    log.info("collector.lambda", count=len(snapshots))
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# ALB Collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_alb() -> List[ResourceSnapshot]:
    cfg = get_settings()
    elbv2 = _boto("elbv2")
    cw = _boto("cloudwatch")
    snapshots: List[ResourceSnapshot] = []

    lbs = elbv2.describe_load_balancers().get("LoadBalancers", [])
    if cfg.alb_names_list():
        lbs = [lb for lb in lbs if lb["LoadBalancerName"] in cfg.alb_names_list()]

    for lb in lbs:
        lb_arn = lb["LoadBalancerArn"]
        lb_name_dim = lb_arn.split("loadbalancer/")[-1]   # e.g. app/my-lb/abc123
        dims = [{"Name": "LoadBalancer", "Value": lb_name_dim}]

        def stat(name, s="Sum"):
            return _cw_stat(cw, "AWS/ApplicationELB", name, dims, stat=s)

        metrics: List[MetricPoint] = []
        for mname, s, unit in [
            ("RequestCount", "Sum", "Count"),
            ("HTTPCode_ELB_5XX_Count", "Sum", "Count"),
            ("HTTPCode_ELB_4XX_Count", "Sum", "Count"),
            ("HTTPCode_Target_5XX_Count", "Sum", "Count"),
            ("TargetResponseTime", "Average", "Seconds"),
            ("HealthyHostCount", "Average", "Count"),
            ("UnHealthyHostCount", "Average", "Count"),
            ("ActiveConnectionCount", "Sum", "Count"),
        ]:
            val = _cw_stat(cw, "AWS/ApplicationELB", mname, dims, stat=s)
            if val is not None:
                metrics.append(MetricPoint(name=mname, value=val, unit=unit))

        snapshots.append(
            ResourceSnapshot(
                resource_id=lb["LoadBalancerName"],
                resource_type="alb",
                region=cfg.aws_default_region,
                metrics=metrics,
                raw={
                    "dns_name": lb.get("DNSName"),
                    "state": lb.get("State", {}).get("Code"),
                    "scheme": lb.get("Scheme"),
                },
            )
        )
    log.info("collector.alb", count=len(snapshots))
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# Unified collector
# ─────────────────────────────────────────────────────────────────────────────

def collect_all() -> List[ResourceSnapshot]:
    """Collect snapshots from all monitored resource types."""
    results: List[ResourceSnapshot] = []
    collectors = [collect_ec2, collect_rds, collect_ecs, collect_lambda, collect_alb]
    for fn in collectors:
        try:
            results.extend(fn())
        except Exception as exc:
            log.error("collector.error", collector=fn.__name__, error=str(exc))
    return results
