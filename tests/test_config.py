"""Step 1 — validate config loads."""
from aws_devops_agent.config import get_settings

cfg = get_settings()
print("✅ Config loaded")
print(f"   LLM provider  : {cfg.llm_provider}")
print(f"   Groq model    : {cfg.groq_model}")
print(f"   Embed provider: {cfg.embed_provider}")
print(f"   HF model      : {cfg.hf_embed_model}")
print(f"   Region        : {cfg.aws_default_region}")
print(f"   Dry run       : {cfg.agent_dry_run}")
ec2 = cfg.monitor_ec2_instance_ids or "(auto-discover)"
print(f"   EC2 filter    : {ec2}")
assert cfg.llm_provider == "groq", "Expected groq"
assert cfg.groq_api_key, "GROQ_API_KEY missing"
assert cfg.huggingface_api_key, "HUGGINGFACE_API_KEY missing"
assert cfg.aws_access_key_id, "AWS_ACCESS_KEY_ID missing"
print("✅ All assertions passed")
