"""
Central configuration loaded from environment / .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── AWS ──────────────────────────────────────────────────────────────────
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_default_region: str = "us-east-1"

    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: str = "openai"          # openai | bedrock | groq | ollama | watsonx
    openai_api_key: Optional[str] = None
    llm_model: str = "gpt-4o"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"

    # Groq
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"   # or llama-3.1-8b-instant, gemma2-9b-it

    # Ollama (local open models)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"                 # any model pulled via `ollama pull`
    ollama_embed_model: str = "nomic-embed-text"   # embedding model for RAG

    # HuggingFace (free embeddings — works alongside Groq)
    embed_provider: str = "huggingface"            # huggingface | openai | ollama | bedrock | watsonx
    huggingface_api_key: Optional[str] = None      # hf.co → Settings → Access Tokens (read)
    hf_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # IBM WatsonX
    watsonx_api_key: Optional[str] = None          # IBM Cloud API key
    watsonx_project_id: Optional[str] = None       # WatsonX.ai project ID
    watsonx_url: str = "https://us-south.ml.cloud.ibm.com"
    watsonx_llm_model: str = "ibm/granite-3-8b-instruct"   # or meta-llama/llama-3-1-70b-instruct
    watsonx_embed_model: str = "ibm/slate-125m-english-rtrvr"

    # ── Agent behaviour ───────────────────────────────────────────────────────
    agent_poll_interval_seconds: int = 60
    agent_max_healing_retries: int = 3
    agent_dry_run: bool = False

    # ── Vector store ──────────────────────────────────────────────────────────
    vector_store_path: str = "./data/vector_store"
    knowledge_base_path: str = "./knowledge_base"

    # ── API server ────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── Notifications ─────────────────────────────────────────────────────────
    slack_webhook_url: Optional[str] = None   # https://hooks.slack.com/services/...

    # ── Detection mode ────────────────────────────────────────────────────────
    anomaly_detection_mode: str = "hybrid"    # threshold | llm | hybrid

    # ── Monitored resources (optional filter lists) ───────────────────────────
    monitor_ec2_instance_ids: str = ""
    monitor_rds_cluster_ids: str = ""
    monitor_ecs_cluster_names: str = ""
    monitor_lambda_function_names: str = ""
    monitor_alb_names: str = ""

    # ── Helpers ───────────────────────────────────────────────────────────────
    def ec2_ids(self) -> List[str]:
        return [x.strip() for x in self.monitor_ec2_instance_ids.split(",") if x.strip()]

    def rds_ids(self) -> List[str]:
        return [x.strip() for x in self.monitor_rds_cluster_ids.split(",") if x.strip()]

    def ecs_clusters(self) -> List[str]:
        return [x.strip() for x in self.monitor_ecs_cluster_names.split(",") if x.strip()]

    def lambda_functions(self) -> List[str]:
        return [x.strip() for x in self.monitor_lambda_function_names.split(",") if x.strip()]

    def alb_names_list(self) -> List[str]:
        return [x.strip() for x in self.monitor_alb_names.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
