# AWS DevOps Runbook — RDS Troubleshooting

## High Database CPU (>80%)

**Symptoms:** `CPUUtilization` > 80% sustained

**Root causes:**
- Slow / missing index queries
- Connections spike
- Maintenance window conflict

**Remediation:**
1. Query Performance Insights for top SQL
2. Identify and kill long-running queries via `pg_terminate_backend` or `KILL QUERY`
3. Consider read replica promotion for read-heavy workloads
4. Check for index bloat

**Healing actions available:**
- `reboot_rds_instance` — reboots the DB instance (short downtime)
- `enable_rds_performance_insights` — enables Performance Insights

---

## RDS FreeStorageSpace < 2 GB

**Symptoms:** `FreeStorageSpace` metric drops below 2 GB

**Remediation:**
1. Identify large tables / indexes
2. Purge old data or archive to S3
3. Increase allocated storage (auto-scaling if supported)

**Healing actions available:**
- `modify_rds_storage` — increases allocated storage by 20 GB

---

## Too Many Database Connections

**Symptoms:** `DatabaseConnections` > 90% of `max_connections`

**Remediation:**
1. Enable RDS Proxy to pool connections
2. Reduce connection pool size in application config

**Healing actions available:**
- `enable_rds_proxy` — creates RDS Proxy for the instance
