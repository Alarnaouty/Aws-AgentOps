"""Check CloudWatch put_metric_data permissions."""
import boto3
from aws_devops_agent.config import get_settings

cfg = get_settings()
cw = boto3.client("cloudwatch",
    region_name=cfg.aws_default_region,
    aws_access_key_id=cfg.aws_access_key_id,
    aws_secret_access_key=cfg.aws_secret_access_key,
)

# Try listing alarms (read) and putting a test metric (write)
try:
    alarms = cw.describe_alarms(MaxRecords=5)
    print(f"CloudWatch READ: OK  (alarms found: {len(alarms['MetricAlarms'])})")
except Exception as e:
    print(f"CloudWatch READ: FAIL  {e}")

try:
    from datetime import datetime, timezone
    cw.put_metric_data(
        Namespace="AWS/EC2",
        MetricData=[{
            "MetricName": "CPUUtilization",
            "Dimensions": [{"Name": "InstanceId", "Value": "i-0e9efcc6253ff7a3e"}],
            "Timestamp": datetime.now(timezone.utc),
            "Value": 1.0,   # harmless test value
            "Unit": "Percent",
        }]
    )
    print("CloudWatch WRITE (put_metric_data): OK")
except Exception as e:
    print(f"CloudWatch WRITE: FAIL  {e}")
