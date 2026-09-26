from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from app.core.persistence import connection, initialize_persistence


EMBEDDING_DIMENSIONS = 256
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_-]{1,}", (text or "").lower())


def _embedding(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude:
        vector = [value / magnitude for value in vector]
    return vector


def _similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _chunks_for_page(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + CHUNK_SIZE)
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + CHUNK_SIZE // 2:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _extract_chunks(raw: bytes) -> list[dict[str, Any]]:
    try:
        reader = PdfReader(BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"PDF could not be read: {exc}") from exc

    chunks = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk_index, chunk_text in enumerate(_chunks_for_page(text), start=1):
            chunks.append(
                {
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                    "text": chunk_text,
                }
            )

    if not chunks:
        raise ValueError(
            "PDF contains no extractable text. Scanned/image-only PDFs "
            "must be OCR-processed before upload."
        )
    return chunks


def _document_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(row["document_id"]),
        "workspace_id": row["workspace_id"],
        "filename": row["filename"],
        "uploaded_at": row["uploaded_at"].isoformat()
        if hasattr(row["uploaded_at"], "isoformat")
        else str(row["uploaded_at"]),
        "page_count": row.get("page_count", 0),
        "chunk_count": row.get("chunk_count", 0),
        "status": row.get("status", "ready"),
        "error": row.get("error"),
    }


def store_pdf_document(
    *,
    workspace_id: str,
    filename: str,
    raw: bytes,
) -> dict[str, Any]:
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("PDF exceeds the 20 MB workspace document limit.")
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF documents are supported.")

    chunks = _extract_chunks(raw)
    document_id = str(uuid.uuid4())
    uploaded_at = _utc_now()
    page_count = max(chunk["page_number"] for chunk in chunks)

    initialize_persistence()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO workspace_documents (
                document_id, workspace_id, filename, uploaded_at,
                page_count, chunk_count, status, error
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'ready', NULL)
            """,
            (
                document_id,
                workspace_id,
                filename,
                uploaded_at,
                page_count,
                len(chunks),
            ),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
            """
            INSERT INTO workspace_document_chunks (
                chunk_id, document_id, workspace_id, page_number,
                chunk_index, text, embedding, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
                [
                    (
                        str(uuid.uuid4()),
                        document_id,
                        workspace_id,
                        chunk["page_number"],
                        chunk["chunk_index"],
                        chunk["text"],
                        json.dumps(_embedding(chunk["text"])),
                        uploaded_at,
                    )
                    for chunk in chunks
                ],
            )

    return get_document(workspace_id=workspace_id, document_id=document_id) or {}


def list_documents(*, workspace_id: str) -> list[dict[str, Any]]:
    initialize_persistence()
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT document_id, workspace_id, filename, uploaded_at,
                   page_count, chunk_count, status, error
            FROM workspace_documents
            WHERE workspace_id = %s
            ORDER BY uploaded_at DESC
            """,
            (workspace_id,),
        ).fetchall()
    return [_document_payload(dict(row)) for row in rows]


def get_document(*, workspace_id: str, document_id: str) -> dict[str, Any] | None:
    initialize_persistence()
    with connection() as conn:
        row = conn.execute(
            """
            SELECT document_id, workspace_id, filename, uploaded_at,
                   page_count, chunk_count, status, error
            FROM workspace_documents
            WHERE workspace_id = %s AND document_id = %s
            """,
            (workspace_id, document_id),
        ).fetchone()
    return _document_payload(dict(row)) if row else None


def retrieve_passages(
    *,
    workspace_id: str,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    clean_query = (query or "").strip()
    if not clean_query:
        raise ValueError("A document question or search query is required.")

    top_k = max(1, min(int(top_k or 5), 20))
    query_vector = _embedding(clean_query)
    initialize_persistence()
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.document_id, c.workspace_id,
                   d.filename, c.page_number, c.chunk_index,
                   c.text, c.embedding
            FROM workspace_document_chunks c
            INNER JOIN workspace_documents d
              ON d.document_id = c.document_id
             AND d.workspace_id = c.workspace_id
            WHERE c.workspace_id = %s
              AND d.status = 'ready'
            """,
            (workspace_id,),
        ).fetchall()

    ranked = []
    query_tokens = set(_tokens(clean_query))
    for row in rows:
        item = dict(row)
        vector = item.get("embedding")
        if isinstance(vector, str):
            vector = json.loads(vector)
        score = _similarity(query_vector, vector or [])
        token_overlap = len(query_tokens.intersection(_tokens(item["text"])))
        ranked.append(
            {
                "document_id": str(item["document_id"]),
                "workspace_id": item["workspace_id"],
                "filename": item["filename"],
                "page": item["page_number"],
                "chunk": item["chunk_index"],
                "text": item["text"],
                "score": round(score + min(token_overlap, 5) * 0.01, 6),
                "citation": f"{item['filename']} (page {item['page_number']}, chunk {item['chunk_index']})",
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]


def ask_documents(
    *,
    workspace_id: str,
    question: str,
    top_k: int = 5,
) -> dict[str, Any]:
    passages = retrieve_passages(
        workspace_id=workspace_id,
        query=question,
        top_k=top_k,
    )
    return {
        "workspace_id": workspace_id,
        "question": question,
        "answer": (
            "Retrieved document evidence is provided below. "
            "Use only these passages and citations; no model-generated "
            "answer was requested by this endpoint."
        ),
        "passages": passages,
        "citations": [item["citation"] for item in passages],
    }
