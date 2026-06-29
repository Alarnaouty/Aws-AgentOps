"""Check EC2 instances and SSM connectivity."""
import boto3
from aws_devops_agent.config import get_settings

cfg = get_settings()
kwargs = dict(
    region_name=cfg.aws_default_region,
    aws_access_key_id=cfg.aws_access_key_id,
    aws_secret_access_key=cfg.aws_secret_access_key,
)
ec2 = boto3.client("ec2", **kwargs)
ssm = boto3.client("ssm", **kwargs)

resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
instances = [i for r in resp["Reservations"] for i in r["Instances"]]
print(f"Running instances: {len(instances)}")
for inst in instances:
    name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "(no name)")
    print(f"  {inst['InstanceId']}  {inst['InstanceType']}  {name}")

# Check SSM management
ssm_info = ssm.describe_instance_information().get("InstanceInformationList", [])
ssm_ids = [i["InstanceId"] for i in ssm_info]
print(f"\nSSM-managed: {ssm_ids or 'none'}")
for inst in instances:
    iid = inst["InstanceId"]
    status = "SSM READY ✓" if iid in ssm_ids else "NOT SSM MANAGED ✗"
    print(f"  {iid}: {status}")
