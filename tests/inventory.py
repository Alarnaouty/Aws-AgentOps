"""
Live AWS resource inventory — shows everything the agent can act on right now.
"""
import boto3, json
from aws_devops_agent.config import get_settings

cfg = get_settings()
kw = dict(region_name=cfg.aws_default_region,
          aws_access_key_id=cfg.aws_access_key_id,
          aws_secret_access_key=cfg.aws_secret_access_key)

# ── EC2 ───────────────────────────────────────────────────────────────────────
ec2 = boto3.client("ec2", **kw)
asg = boto3.client("autoscaling", **kw)

instances = [i for r in ec2.describe_instances(
    Filters=[{"Name":"instance-state-name","Values":["running"]}]
)["Reservations"] for i in r["Instances"]]

print("== EC2 Running Instances ==")
ec2_ids = []
for inst in instances:
    name = next((t["Value"] for t in inst.get("Tags",[]) if t["Key"]=="Name"), "(no name)")
    print(f"  {inst['InstanceId']}  {inst['InstanceType']}  {name}")
    ec2_ids.append(inst["InstanceId"])

# ── ASG ───────────────────────────────────────────────────────────────────────
print("\n== Auto Scaling Groups ==")
asgs = asg.describe_auto_scaling_groups()["AutoScalingGroups"]
if not asgs:
    print("  (none found)")
for g in asgs:
    print(f"  {g['AutoScalingGroupName']}  desired={g['DesiredCapacity']}  min={g['MinSize']}  max={g['MaxSize']}")
    for inst in g.get("Instances",[]):
        print(f"    → {inst['InstanceId']}  {inst['LifecycleState']}")

# ── RDS ───────────────────────────────────────────────────────────────────────
rds = boto3.client("rds", **kw)
print("\n== RDS Instances ==")
dbs = rds.describe_db_instances()["DBInstances"]
if not dbs:
    print("  (none found)")
for db in dbs:
    print(f"  {db['DBInstanceIdentifier']}  {db['DBInstanceClass']}  {db['Engine']}  "
          f"storage={db['AllocatedStorage']}GB  status={db['DBInstanceStatus']}")

# ── Lambda ────────────────────────────────────────────────────────────────────
lmb = boto3.client("lambda", **kw)
print("\n== Lambda Functions ==")
fns = lmb.list_functions()["Functions"]
if not fns:
    print("  (none found)")
for fn in fns[:10]:
    print(f"  {fn['FunctionName']}  {fn['Runtime']}  mem={fn['MemorySize']}MB  timeout={fn['Timeout']}s")

# ── ECS ───────────────────────────────────────────────────────────────────────
ecs = boto3.client("ecs", **kw)
print("\n== ECS Clusters ==")
clusters = ecs.list_clusters()["clusterArns"]
if not clusters:
    print("  (none found)")
for arn in clusters:
    name = arn.split("/")[-1]
    svcs = ecs.list_services(cluster=arn)["serviceArns"]
    print(f"  {name}  ({len(svcs)} services)")
    for svc in svcs[:5]:
        sname = svc.split("/")[-1]
        info = ecs.describe_services(cluster=arn, services=[svc])["services"][0]
        print(f"    → {sname}  desired={info['desiredCount']}  running={info['runningCount']}")

# ── ALB ───────────────────────────────────────────────────────────────────────
elbv2 = boto3.client("elbv2", **kw)
print("\n== Load Balancers ==")
lbs = elbv2.describe_load_balancers()["LoadBalancers"]
if not lbs:
    print("  (none found)")
for lb in lbs:
    print(f"  {lb['LoadBalancerName']}  {lb['Type']}  {lb['State']['Code']}  {lb['DNSName'][:50]}")

# ── SSM ───────────────────────────────────────────────────────────────────────
ssm = boto3.client("ssm", **kw)
print("\n== SSM Managed Instances ==")
ssm_instances = ssm.describe_instance_information()["InstanceInformationList"]
if not ssm_instances:
    print("  (none — SSM agent not installed or IAM role missing)")
for si in ssm_instances:
    print(f"  {si['InstanceId']}  ping={si['PingStatus']}  agent={si['AgentVersion']}")
