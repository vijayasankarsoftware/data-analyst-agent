"""Document ingestion pipeline: extract -> chunk -> injection scan -> embed -> store."""

from typing import Any

from pydantic import BaseModel

from analyst.db.pool import get_app_pool, to_vector
from analyst.documents.chunk import chunk_pages
from analyst.documents.extract import extract_pages
from analyst.llm import embed_texts
from analyst.security.injection import scan_text


class DocumentRecord(BaseModel):
    id: str
    filename: str
    size_bytes: int
    n_chunks: int
    flagged_chunks: int
    created_at: str


async def ingest_document(workspace_id: str, filename: str, content_type: str | None, data: bytes) -> DocumentRecord:
    pages = extract_pages(filename, data)
    chunks = chunk_pages(pages)
    if not chunks:
        raise ValueError("no text could be extracted from this file")

    scans = [scan_text(c.text) for c in chunks]
    embeddings = await embed_texts([c.text for c in chunks])

    pool = await get_app_pool()
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO documents (workspace_id, filename, content_type, size_bytes, n_chunks, flagged_chunks)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text, created_at::text",
            [workspace_id, filename, content_type, len(data), len(chunks), sum(s.flagged for s in scans)],
        )
        row = await cur.fetchone()
        assert row is not None
        async with conn.cursor() as c:
            await c.executemany(
                "INSERT INTO doc_chunks (document_id, workspace_id, chunk_index, page, content,"
                " injection_flag, injection_reasons, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                [
                    (row["id"], workspace_id, ch.index, ch.page, ch.text, sc.flagged, sc.reasons, to_vector(emb))
                    for ch, sc, emb in zip(chunks, scans, embeddings, strict=True)
                ],
            )
    return DocumentRecord(
        id=row["id"],
        filename=filename,
        size_bytes=len(data),
        n_chunks=len(chunks),
        flagged_chunks=sum(s.flagged for s in scans),
        created_at=row["created_at"],
    )


async def list_documents(workspace_id: str) -> list[DocumentRecord]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id::text, filename, size_bytes, n_chunks, flagged_chunks, created_at::text"
            " FROM documents WHERE workspace_id = %s ORDER BY created_at DESC",
            [workspace_id],
        )
        rows: list[dict[str, Any]] = await cur.fetchall()
    return [DocumentRecord(**r) for r in rows]


async def delete_document(workspace_id: str, document_id: str) -> bool:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM documents WHERE workspace_id = %s AND id::text = %s", [workspace_id, document_id]
        )
        return cur.rowcount > 0
