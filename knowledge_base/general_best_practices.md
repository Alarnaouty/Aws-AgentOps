# AWS DevOps Agent — General Best Practices

## Incident Severity Classification

| Severity | Criteria | Target Response |
|----------|----------|-----------------|
| CRITICAL | Production down, data loss risk, security breach | Immediate auto-heal + alert |
| WARNING  | Degraded performance, approaching limit | Auto-heal + notify |
| OK       | All metrics within bounds | No action |

## Auto-Healing Decision Framework

1. **Confirm** anomaly is real (check at least 2 consecutive metric points)
2. **Retrieve** relevant runbook from knowledge base (RAG retrieval)
3. **Reason** about root cause using LLM analysis
4. **Select** least-disruptive healing action first
5. **Execute** action (skip if DRY_RUN=true)
6. **Verify** healing worked by re-checking metrics after 60s
7. **Escalate** if healing fails after MAX_RETRIES

## Retry & Back-off Policy

- Max retries: 3
- Back-off: 30s, 60s, 120s (exponential)
- If all retries fail: emit CRITICAL alert and halt further auto-healing for that resource

## Safety Guards

- Never terminate an instance if it is the only healthy host in a target group
- Never delete data (S3 objects, RDS snapshots) automatically
- Destructive actions (reboot, stop/start) require severity = CRITICAL
- DRY_RUN mode logs intended actions without executing them
