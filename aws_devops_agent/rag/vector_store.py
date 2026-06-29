"""
RAG pipeline — builds / loads a FAISS vector store from the knowledge-base
Markdown documents and exposes a retriever used by the LLM agent.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

import structlog
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from aws_devops_agent.config import get_settings

log = structlog.get_logger(__name__)

# ── Module-level singletons — built once, reused by all threads ───────────────
_store_lock = threading.Lock()
_store: Optional[FAISS] = None
_embeddings = None


def _get_embeddings():
    """Return the singleton embeddings instance (thread-safe, created once)."""
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    cfg = get_settings()

    if cfg.llm_provider == "bedrock":
        from langchain_aws import BedrockEmbeddings
        _embeddings = BedrockEmbeddings(region_name=cfg.aws_default_region)

    elif cfg.llm_provider == "groq" or cfg.embed_provider == "huggingface":
        # HuggingFaceEmbeddings runs sentence-transformers locally — free, no API key.
        # Created ONCE here; all threads reuse the same instance (it is thread-safe for inference).
        from langchain_huggingface import HuggingFaceEmbeddings
        log.info("embeddings.huggingface_local", model=cfg.hf_embed_model)
        _embeddings = HuggingFaceEmbeddings(model_name=cfg.hf_embed_model)

    elif cfg.llm_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        _embeddings = OllamaEmbeddings(
            model=cfg.ollama_embed_model,
            base_url=cfg.ollama_base_url,
        )

    else:
        _embeddings = OpenAIEmbeddings(openai_api_key=cfg.openai_api_key)

    return _embeddings


def _load_documents(kb_path: str) -> List[Document]:
    """Load all .md / .txt files from the knowledge-base directory."""
    path = Path(kb_path)
    if not path.exists():
        log.warning("knowledge_base.missing", path=str(path))
        return []

    docs: List[Document] = []
    for ext in ("**/*.md", "**/*.txt"):
        loader = DirectoryLoader(
            str(path),
            glob=ext,
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"},
            silent_errors=True,
        )
        docs.extend(loader.load())

    log.info("knowledge_base.loaded", doc_count=len(docs))
    return docs


def build_vector_store(force_rebuild: bool = False) -> FAISS:
    """Build (or load from disk) the FAISS vector store — thread-safe singleton."""
    global _store
    with _store_lock:
        if _store is not None and not force_rebuild:
            return _store

        cfg = get_settings()
        store_path = Path(cfg.vector_store_path)
        index_file = store_path / "index.faiss"
        embeddings = _get_embeddings()

        if index_file.exists() and not force_rebuild:
            log.info("vector_store.loading_from_disk", path=str(store_path))
            _store = FAISS.load_local(
                str(store_path),
                embeddings,
                allow_dangerous_deserialization=True,
            )
            return _store

        # Build from scratch
        docs = _load_documents(cfg.knowledge_base_path)
        if not docs:
            docs = [Document(page_content="AWS DevOps agent knowledge base — no documents loaded yet.")]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        log.info("vector_store.building", chunks=len(chunks))

        _store = FAISS.from_documents(chunks, embeddings)
        store_path.mkdir(parents=True, exist_ok=True)
        _store.save_local(str(store_path))
        log.info("vector_store.saved", path=str(store_path))
        return _store


def get_retriever(k: int = 6):
    """Return a retriever backed by the singleton FAISS store."""
    store = build_vector_store()
    return store.as_retriever(search_type="similarity", search_kwargs={"k": k})
