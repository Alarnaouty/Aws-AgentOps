"""Step 2 — validate AWS connectivity."""
import boto3
from aws_devops_agent.config import get_settings

cfg = get_settings()
ec2 = boto3.client(
    "ec2",
    region_name=cfg.aws_default_region,
    aws_access_key_id=cfg.aws_access_key_id,
    aws_secret_access_key=cfg.aws_secret_access_key,
)
resp = ec2.describe_instances(
    Filters=[{"Name": "instance-state-name", "Values": ["running"]}],
    MaxResults=5,
)
instances = [i for r in resp["Reservations"] for i in r["Instances"]]
print(f"✅ AWS connected — running instances found: {len(instances)}")
for inst in instances:
    name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "(no name)")
    print(f"   {inst['InstanceId']}  {inst['InstanceType']}  {name}")
