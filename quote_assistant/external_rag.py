from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import httpx

from .db import connect, db_enabled
from .knowledge_base import iter_rule_documents, row_image_knowledge_text


logger = logging.getLogger("uvicorn.error")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def rag_enabled() -> bool:
    if not _env_bool("QUOTE_RAG_ENABLED", False):
        return False
    if rag_provider() in {"pgvector", "postgres", "postgresql", "local"}:
        return db_enabled()
    return bool(os.getenv("QUOTE_RAG_API_KEY") and os.getenv("QUOTE_RAG_BASE_URL"))


def rag_provider() -> str:
    return (os.getenv("QUOTE_RAG_PROVIDER") or "dify").strip().lower()


def _dataset_id() -> str:
    return (
        os.getenv("QUOTE_RAG_DATASET_ID")
        or os.getenv("QUOTE_RAG_KNOWLEDGE_ID")
        or ""
    ).strip()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('QUOTE_RAG_API_KEY', '')}",
        "Content-Type": "application/json",
        "User-Agent": "quote-agent-assistant/0.1",
    }


def _base_url() -> str:
    return str(os.getenv("QUOTE_RAG_BASE_URL") or "").rstrip("/")


def _embedding_base_url() -> str:
    return str(os.getenv("QUOTE_RAG_EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")


def _embedding_api_key() -> str:
    return str(os.getenv("QUOTE_RAG_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or "")


def _embedding_model() -> str:
    if _embedding_provider() == "hash":
        return f"local-hash-{_hash_embedding_dimensions()}"
    return str(os.getenv("QUOTE_RAG_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _embedding_provider() -> str:
    value = str(os.getenv("QUOTE_RAG_EMBEDDING_PROVIDER") or "").strip().lower()
    if value:
        return value
    return "openai" if _embedding_base_url() and _embedding_api_key() else "hash"


def _hash_embedding_dimensions() -> int:
    return max(_env_int("QUOTE_RAG_HASH_DIMENSIONS", 384), 64)


def _env_csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name)
    value = default if raw is None else raw
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def _embedding_endpoint() -> str:
    explicit = str(os.getenv("QUOTE_RAG_EMBEDDING_ENDPOINT") or "").strip()
    if explicit:
        return explicit
    base_url = _embedding_base_url()
    return f"{base_url}/embeddings" if base_url else ""


def _compact_query(query: str) -> str:
    limit = max(_env_int("QUOTE_RAG_QUERY_MAX_CHARS", 900), 120)
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    return text[:limit]


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


def _hash_embedding(text: str) -> list[float]:
    dimensions = _hash_embedding_dimensions()
    vector = [0.0] * dimensions
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    tokens = re.findall(r"[a-z0-9][a-z0-9_.:/#-]*|[\u4e00-\u9fff]", normalized)
    tokens.extend(normalized[index : index + 2] for index in range(max(len(normalized) - 1, 0)))
    for token in tokens:
        if not token.strip():
            continue
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = sum(value * value for value in vector) ** 0.5
    if not norm:
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


async def _create_embedding(text: str) -> list[float]:
    provider = _embedding_provider()
    if provider == "hash":
        return _hash_embedding(text)
    if provider not in {"openai", "openai-compatible", "compatible"}:
        raise RuntimeError(f"Unsupported RAG embedding provider: {provider}")

    endpoint = _embedding_endpoint()
    api_key = _embedding_api_key()
    model = _embedding_model()
    if not endpoint or not api_key or not model:
        raise RuntimeError(
            "Missing RAG embedding config. Set QUOTE_RAG_EMBEDDING_MODEL, "
            "QUOTE_RAG_EMBEDDING_BASE_URL and QUOTE_RAG_EMBEDDING_API_KEY."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "quote-agent-assistant/0.1",
    }
    payload = {"model": model, "input": text}
    async with httpx.AsyncClient(timeout=_env_float("QUOTE_RAG_EMBEDDING_TIMEOUT_SECONDS", 30.0)) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"RAG embedding failed {response.status_code}: {response.text[:800]}")
    data = response.json()
    embedding = ((data.get("data") or [{}])[0] or {}).get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("RAG embedding response did not include data[0].embedding.")
    return [float(value) for value in embedding]


def _rag_table_name() -> str:
    raw = (os.getenv("QUOTE_RAG_TABLE") or "quote_rag_documents").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        raise ValueError("QUOTE_RAG_TABLE must be a simple SQL identifier.")
    return raw


def _ensure_pgvector_schema() -> None:
    if not db_enabled():
        raise RuntimeError("Database is not enabled. Set QUOTE_DATABASE_URL.")

    table = _rag_table_name()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id BIGSERIAL PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'row_image',
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding VECTOR,
                    embedding_model TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(source, source_id)
                );
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_stage_updated ON {table}(stage, updated_at DESC);")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_source_id ON {table}(source, source_id);")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_content_hash ON {table}(content_hash);")


def _record_content(record: dict[str, Any]) -> str:
    for key in ("content", "text", "answer"):
        value = record.get(key)
        if value:
            return str(value)
    segment = record.get("segment")
    if isinstance(segment, dict) and segment.get("content"):
        return str(segment.get("content"))
    document = record.get("document")
    if isinstance(document, dict) and document.get("content"):
        return str(document.get("content"))
    return ""


def _format_records(records: list[dict[str, Any]], *, max_chars: int) -> str:
    chunks: list[str] = []
    used = 0
    for index, record in enumerate(records, start=1):
        content = _record_content(record).strip()
        if not content:
            continue
        score = record.get("score")
        document = record.get("document")
        doc_name = ""
        if isinstance(document, dict):
            doc_name = str(document.get("name") or document.get("title") or "")
        prefix = f"[RAG {index}"
        if score is not None:
            prefix += f" score={score}"
        if doc_name:
            prefix += f" source={doc_name}"
        prefix += "] "
        block = prefix + content
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        chunks.append(block)
        used += len(block) + 2
    return "\n\n".join(chunks)


async def _retrieve_dify(query: str) -> list[dict[str, Any]]:
    dataset_id = _dataset_id()
    if not dataset_id:
        logger.warning("quote rag skipped: missing QUOTE_RAG_DATASET_ID")
        return []
    url = f"{_base_url()}/datasets/{dataset_id}/retrieve"
    top_k = max(_env_int("QUOTE_RAG_TOP_K", 5), 1)
    threshold = max(min(_env_float("QUOTE_RAG_SCORE_THRESHOLD", 0.0), 1.0), 0.0)
    payload: dict[str, Any] = {
        "query": _compact_query(query),
        "retrieval_model": {
            "search_method": os.getenv("QUOTE_RAG_SEARCH_METHOD", "hybrid_search"),
            "reranking_enable": _env_bool("QUOTE_RAG_RERANKING_ENABLED", False),
            "top_k": top_k,
            "score_threshold_enabled": threshold > 0,
            "score_threshold": threshold,
        },
    }
    async with httpx.AsyncClient(timeout=_env_float("QUOTE_RAG_TIMEOUT_SECONDS", 12.0)) as client:
        response = await client.post(url, headers=_headers(), json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"Dify RAG retrieve failed {response.status_code}: {response.text[:800]}")
    data = response.json()
    records = data.get("records") or data.get("data") or []
    return [item for item in records if isinstance(item, dict)]


async def _retrieve_custom(query: str, metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    url = _base_url()
    if not url.endswith("/retrieval"):
        url = f"{url}/retrieval"
    payload = {
        "knowledge_id": _dataset_id(),
        "query": _compact_query(query),
        "retrieval_setting": {
            "top_k": max(_env_int("QUOTE_RAG_TOP_K", 5), 1),
            "score_threshold": _env_float("QUOTE_RAG_SCORE_THRESHOLD", 0.0),
        },
        "metadata": metadata or {},
    }
    async with httpx.AsyncClient(timeout=_env_float("QUOTE_RAG_TIMEOUT_SECONDS", 12.0)) as client:
        response = await client.post(url, headers=_headers(), json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"Custom RAG retrieve failed {response.status_code}: {response.text[:800]}")
    data = response.json()
    records = data.get("records") or data.get("chunks") or data.get("data") or []
    return [item for item in records if isinstance(item, dict)]


async def _retrieve_pgvector(
    query: str,
    *,
    stage: str,
    metadata: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    _ensure_pgvector_schema()
    embedding = await _create_embedding(_compact_query(query))
    query_vector = _vector_literal(embedding)
    table = _rag_table_name()
    top_k = max(_env_int("QUOTE_RAG_TOP_K", 5), 1)
    threshold = _env_float("QUOTE_RAG_SCORE_THRESHOLD", 0.0)
    stage_filter = str((metadata or {}).get("stage") or stage or "").strip()

    with connect() as conn:
        with conn.cursor() as cur:
            if stage_filter:
                cur.execute(
                    f"""
                    SELECT title, content, metadata,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {table}
                    WHERE embedding IS NOT NULL
                      AND stage = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vector, stage_filter, query_vector, top_k),
                )
            else:
                cur.execute(
                    f"""
                    SELECT title, content, metadata,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {table}
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vector, query_vector, top_k),
                )
            rows = cur.fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        score = row.get("score")
        if score is not None and threshold > 0 and float(score) < threshold:
            continue
        records.append(
            {
                "content": row.get("content") or "",
                "score": float(score) if score is not None else None,
                "document": {"name": row.get("title") or ""},
                "metadata": row.get("metadata") or {},
            }
        )
    return records


async def retrieve_knowledge_text(
    query: str,
    *,
    stage: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    if not rag_enabled():
        if _env_bool("QUOTE_RAG_STATIC_FALLBACK", True):
            return row_image_knowledge_text() if stage == "row_image" else ""
        return ""
    try:
        provider = rag_provider()
        if provider == "dify":
            records = await _retrieve_dify(query)
        elif provider in {"custom", "external"}:
            records = await _retrieve_custom(query, metadata)
        elif provider in {"pgvector", "postgres", "postgresql", "local"}:
            records = await _retrieve_pgvector(query, stage=stage, metadata=metadata)
        else:
            logger.warning("quote rag skipped: unsupported provider=%s", provider)
            records = []
    except Exception as exc:
        logger.warning("quote rag retrieval failed provider=%s error=%s", rag_provider(), exc)
        records = []

    max_chars = max(_env_int("QUOTE_RAG_MAX_CONTEXT_CHARS", 2400), 400)
    text = _format_records(records, max_chars=max_chars)
    if text:
        return text
    if _env_bool("QUOTE_RAG_STATIC_FALLBACK", True):
        return row_image_knowledge_text() if stage == "row_image" else ""
    return ""


async def sync_seed_documents_to_external() -> dict[str, Any]:
    if not rag_enabled():
        raise RuntimeError("RAG is not configured.")
    if rag_provider() in {"pgvector", "postgres", "postgresql", "local"}:
        return await sync_seed_documents_to_pgvector()
    if rag_provider() != "dify":
        raise RuntimeError("rag-sync currently supports provider=dify or provider=pgvector.")
    dataset_id = _dataset_id()
    if not dataset_id:
        raise RuntimeError("Missing QUOTE_RAG_DATASET_ID.")

    url = f"{_base_url()}/datasets/{dataset_id}/document/create-by-text"
    documents = iter_rule_documents()
    created: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_env_float("QUOTE_RAG_TIMEOUT_SECONDS", 30.0)) as client:
        for item in documents:
            name = f"quote-agent/{item['name']}"
            payload = {
                "name": name,
                "text": item["text"],
                "indexing_technique": os.getenv("QUOTE_RAG_INDEXING_TECHNIQUE", "high_quality"),
                "doc_form": os.getenv("QUOTE_RAG_DOC_FORM", "text_model"),
                "doc_language": os.getenv("QUOTE_RAG_DOC_LANGUAGE", "English"),
                "process_rule": {
                    "mode": "automatic",
                },
            }
            response = await client.post(url, headers=_headers(), json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"Dify create document failed {response.status_code}: {response.text[:800]}")
            created.append({"name": name, "response": response.json()})
    return {"provider": "dify", "dataset_id": dataset_id, "created": created}


async def sync_seed_documents_to_pgvector() -> dict[str, Any]:
    documents = iter_rule_documents()
    synced: list[dict[str, Any]] = []
    for item in documents:
        metadata = dict(item.get("metadata") or {})
        source = str(metadata.get("source") or "quote_assistant_rules")
        source_id = str(metadata.get("file") or item.get("name") or _content_hash(item["text"])[:16])
        stage = str(metadata.get("stage") or "row_image")
        title = str(item.get("title") or item.get("name") or source_id)
        content = str(item.get("text") or "")
        synced.append(
            await upsert_rag_document(
                source=source,
                source_id=source_id,
                title=title,
                stage=stage,
                content=content,
                metadata=metadata,
            )
        )
    return {"provider": "pgvector", "table": _rag_table_name(), "embedding_model": _embedding_model(), "synced": synced}


async def upsert_rag_document(
    *,
    source: str,
    source_id: str,
    title: str,
    stage: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not rag_enabled():
        return {"synced": False, "reason": "rag_disabled"}

    provider = rag_provider()
    if provider not in {"pgvector", "postgres", "postgresql", "local"}:
        return {"synced": False, "reason": f"provider_{provider}_write_not_supported"}

    from psycopg.types.json import Jsonb

    source = (str(source or "").strip() or "quote_agent")[:120]
    source_id = (str(source_id or "").strip() or _content_hash(content)[:24])[:240]
    title = (str(title or "").strip() or source_id)[:300]
    stage = (str(stage or "").strip() or "feedback")[:80]
    content = str(content or "").strip()
    if not content:
        return {"synced": False, "reason": "empty_content"}

    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("source", source)
    metadata_payload.setdefault("source_id", source_id)
    metadata_payload.setdefault("stage", stage)

    _ensure_pgvector_schema()
    table = _rag_table_name()
    content_hash = _content_hash(content)
    embedding_text = content[: max(_env_int("QUOTE_RAG_EMBEDDING_MAX_CHARS", 6000), 500)]
    embedding = await _create_embedding(embedding_text)
    vector = _vector_literal(embedding)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table} (
                    source, source_id, title, stage, content, metadata,
                    embedding, embedding_model, content_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (source, source_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    stage = EXCLUDED.stage,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model,
                    content_hash = EXCLUDED.content_hash,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    source,
                    source_id,
                    title,
                    stage,
                    content,
                    Jsonb(metadata_payload),
                    vector,
                    _embedding_model(),
                    content_hash,
                ),
            )
            row = cur.fetchone()
    return {
        "synced": True,
        "id": row["id"] if row else None,
        "source": source,
        "source_id": source_id,
        "title": title,
        "stage": stage,
        "embedding_model": _embedding_model(),
        "content_hash": content_hash,
    }


async def sync_feedback_document_to_rag(
    *,
    job_id: str,
    feedback: dict[str, Any],
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _env_bool("QUOTE_RAG_FEEDBACK_SYNC_ENABLED", True):
        return {"synced": False, "reason": "feedback_sync_disabled"}

    stages = _env_csv("QUOTE_RAG_FEEDBACK_STAGES", "row_image,feedback")
    if not stages:
        return {"synced": False, "reason": "no_feedback_stages"}

    feedback_id = str(feedback.get("id") or "")
    user_id = str(feedback.get("user_id") or "anonymous")
    verdict = str(feedback.get("verdict") or "unknown")
    base_metadata = dict(metadata or {})
    base_metadata.update(
        {
            "job_id": job_id,
            "feedback_id": feedback_id,
            "feedback_user_id": user_id,
            "feedback_verdict": verdict,
            "document_type": "user_feedback",
        }
    )

    results: list[dict[str, Any]] = []
    for stage in stages:
        stage_metadata = dict(base_metadata)
        stage_metadata["stage"] = stage
        source_id = f"{job_id}:{user_id}:{stage}"
        results.append(
            await upsert_rag_document(
                source="quote_user_feedback",
                source_id=source_id,
                title=title,
                stage=stage,
                content=content,
                metadata=stage_metadata,
            )
        )

    return {
        "synced": any(item.get("synced") for item in results),
        "provider": rag_provider(),
        "items": results,
    }


def init_rag_store() -> dict[str, Any]:
    provider = rag_provider()
    if provider not in {"pgvector", "postgres", "postgresql", "local"}:
        return {"provider": provider, "initialized": False, "reason": "provider does not use local pgvector"}
    _ensure_pgvector_schema()
    return {"provider": "pgvector", "initialized": True, "table": _rag_table_name()}


def seed_documents_preview() -> str:
    return json.dumps(iter_rule_documents(), ensure_ascii=False, indent=2)
