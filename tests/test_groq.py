"""Step 3 — validate Groq LLM responds."""
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from aws_devops_agent.config import get_settings

cfg = get_settings()
llm = ChatGroq(model=cfg.groq_model, api_key=cfg.groq_api_key, temperature=0)
resp = llm.invoke([HumanMessage(content='Reply with exactly: {"status": "ok"}')])
print(f"✅ Groq responded: {resp.content[:120]}")
