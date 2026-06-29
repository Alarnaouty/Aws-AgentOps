# AWS DevOps Runbook — ALB / Networking Troubleshooting

## ALB 5xx Error Rate > 1%

**Symptoms:** `HTTPCode_ELB_5XX_Count` or `HTTPCode_Target_5XX_Count` > 1% of requests

**Root causes:**
- Unhealthy targets (502/504)
- Target group exhausted
- Long-running requests hitting idle timeout

**Remediation:**
1. Check target group health: `describe_target_health`
2. Deregister unhealthy targets and let ASG replace them
3. Increase ALB idle timeout if `RequestCountPerTarget` is spiking
4. Check security group rules if 502 on new deployment

**Healing actions available:**
- `deregister_unhealthy_targets` — removes targets failing health checks
- `increase_alb_idle_timeout` — sets idle timeout to 120s

---

## ALB Target Response Time > 2s

**Symptoms:** `TargetResponseTime` P95 > 2000ms

**Remediation:**
1. Correlate with backend CPU / memory
2. Check DB query duration (RDS Performance Insights)
3. Add caching layer (ElastiCache)

---

## ALB No Healthy Hosts

**Symptoms:** `HealthyHostCount` = 0

**Remediation:**
1. Immediately scale out the backend ASG
2. Check security groups between ALB and EC2 targets
3. Verify application port is correct in target group

**Healing actions available:**
- `scale_out_asg` — emergency scale-out of backend ASG
