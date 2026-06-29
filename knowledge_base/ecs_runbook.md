# AWS DevOps Runbook — ECS / Fargate Troubleshooting

## ECS Task CrashLoopBackOff / Repeated Failures

**Symptoms:** Task `lastStatus` cycling RUNNING→STOPPED repeatedly

**Root causes:**
- Application exception at startup
- Missing environment variable / secret
- OOM (memory limit too low)

**Remediation:**
1. Fetch stopped task logs: `describe_tasks` + CloudWatch Logs group
2. Check exit code: 137 = OOM kill; 1 = app crash
3. For OOM: increase task memory definition and force new deployment
4. For app crash: roll back to previous task definition revision

**Healing actions available:**
- `update_ecs_service` — force new deployment with latest stable task def
- `rollback_ecs_task_def` — redeploy previous task definition revision
- `increase_ecs_task_memory` — update task def with higher memory limit

---

## ECS Service Desired Count Not Met

**Symptoms:** `RunningCount` < `DesiredCount` for > 5 min

**Remediation:**
1. Check placement constraints and capacity provider
2. Check Fargate spot interruptions
3. Switch to on-demand capacity if spot interrupted

**Healing actions available:**
- `update_ecs_desired_count` — set desired count to a safe minimum
- `toggle_capacity_provider` — switch service to FARGATE from FARGATE_SPOT

---

## High ECS Service CPU / Memory

**Symptoms:** Average CPU or memory > 80% across tasks

**Remediation:**
1. Scale out service horizontally (increase desired count)
2. Enable Application Auto Scaling target-tracking policy

**Healing actions available:**
- `scale_ecs_service` — increase desired count by +2
