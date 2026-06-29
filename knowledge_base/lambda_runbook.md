# AWS DevOps Runbook — Lambda Troubleshooting

## Lambda Error Rate > 5%

**Symptoms:** `Errors` / `Invocations` ratio > 5%

**Root causes:**
- Unhandled exception in function code
- Dependency / import error after deploy
- Downstream service timeout

**Remediation:**
1. Check CloudWatch Logs `/aws/lambda/<function>` for stack traces
2. Check X-Ray traces for downstream latency
3. If recent deployment: rollback to previous version alias
4. Increase timeout if downstream latency is root cause

**Healing actions available:**
- `rollback_lambda_version` — updates alias to point to previous version
- `update_lambda_timeout` — increases function timeout by 30s (up to 900s)

---

## Lambda Throttles > 0

**Symptoms:** `Throttles` metric > 0 consistently

**Remediation:**
1. Request concurrency limit increase
2. Enable provisioned concurrency for latency-sensitive functions
3. Add SQS queue + event source mapping for batch processing

**Healing actions available:**
- `put_function_concurrency` — raises reserved concurrency

---

## Lambda Duration > 80% of Timeout

**Symptoms:** Average `Duration` > 80% of configured `Timeout`

**Remediation:**
1. Profile code for hotspots
2. Increase memory (also increases CPU allocation)
3. Move heavy lifting to async Step Functions workflow

**Healing actions available:**
- `update_lambda_memory` — increases memory configuration by 256 MB
