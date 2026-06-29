"""Step 4 — validate HuggingFace embeddings + FAISS vector store build."""
from aws_devops_agent.rag.vector_store import build_vector_store

store = build_vector_store(force_rebuild=True)
results = store.similarity_search("EC2 high CPU how to fix", k=3)
print(f"✅ Vector store built — top {len(results)} results:")
for i, doc in enumerate(results, 1):
    print(f"   [{i}] {doc.page_content[:120].strip()}")
