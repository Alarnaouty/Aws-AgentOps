# AWS DevOps Runbook — EC2 Troubleshooting

## High CPU Utilization (>85%)

**Symptoms:** `CPUUtilization` metric exceeds 85% for 5+ minutes.

**Root causes:**
- Runaway process / memory leak
- Under-provisioned instance type
- Spike in traffic without auto-scaling

**Remediation steps:**
1. Describe top processes via SSM Run Command: `top -b -n1 | head -20`
2. If a single PID is consuming > 60%: attempt graceful restart of the service
3. If traffic spike: trigger Auto Scaling Group scale-out (`set_desired_capacity`)
4. If chronic: recommend right-sizing to a larger instance type
5. Create CloudWatch alarm if not already present

**Healing actions available:**
- `restart_ec2_service` — restarts the named systemd service via SSM (first resort)
- `scale_out_asg` — increases desired capacity of attached ASG by 1 (traffic spike)
- `reboot_ec2_instance` — reboots the EC2 instance via EC2 API (last resort, CRITICAL only)

---

## Instance Status Check Failed

**Symptoms:** `StatusCheckFailed_Instance` = 1

**Remediation:**
1. Stop and Start instance (not reboot) to migrate to new host hardware
2. If EBS volume unreachable: force detach and reattach
3. Raise P1 incident if persists > 10 min

**Healing actions available:**
- `stop_start_instance` — stop then start the EC2 instance

---

## Disk Usage > 90%

**Symptoms:** Custom disk metric or CloudWatch agent `disk_used_percent` > 90%

**Remediation:**
1. Run `df -h` and `du -sh /*` via SSM to find large directories
2. Clean `/tmp`, rotated logs, old AMIs
3. Expand EBS volume if needed

**Healing actions available:**
- `cleanup_disk_ssm` — runs disk cleanup script via SSM Run Command
- `extend_ebs_volume` — increases EBS volume size by 20 GB
