"""Hybrid document search: vector similarity + full-text, fused with Reciprocal Rank Fusion.

Vectors find paraphrases ("refund window" ~ "return period"); full-text finds exact
terms (policy codes, product names) that embeddings blur. RRF merges two rankings
without having to calibrate their very different scores: score = sum(1 / (60 + rank)).
"""

from pydantic import BaseModel

from analyst.db.pool import get_app_pool, to_vector
from analyst.llm import embed_query

_RRF_K = 60

_SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(emb)s::vector) AS r
    FROM doc_chunks
    WHERE workspace_id = %(ws)s AND (NOT %(exclude)s OR NOT injection_flag)
    ORDER BY embedding <=> %(emb)s::vector
    LIMIT %(pool)s
),
fts AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
    FROM doc_chunks, websearch_to_tsquery('english', %(q)s) AS q
    WHERE workspace_id = %(ws)s AND tsv @@ q AND (NOT %(exclude)s OR NOT injection_flag)
    ORDER BY ts_rank_cd(tsv, q) DESC
    LIMIT %(pool)s
),
fused AS (
    SELECT id, sum(1.0 / (%(rrf)s + r)) AS score
    FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) AS u
    GROUP BY id
)
SELECT c.id, d.filename, c.page, c.content, c.injection_flag, f.score
FROM fused f
JOIN doc_chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
ORDER BY f.score DESC
LIMIT %(k)s
"""


class DocHit(BaseModel):
    chunk_id: int
    document: str
    page: int | None
    content: str
    flagged: bool
    score: float


async def hybrid_search(workspace_id: str, query: str, *, k: int = 6, exclude_flagged: bool = True) -> list[DocHit]:
    emb = to_vector(await embed_query(query))
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            _SQL,
            {
                "emb": emb,
                "ws": workspace_id,
                "q": query,
                "k": k,
                "pool": max(20, k * 4),
                "rrf": _RRF_K,
                "exclude": exclude_flagged,
            },
        )
        rows = await cur.fetchall()
    return [
        DocHit(
            chunk_id=r["id"],
            document=r["filename"],
            page=r["page"],
            content=r["content"],
            flagged=r["injection_flag"],
            score=float(r["score"]),
        )
        for r in rows
    ]
