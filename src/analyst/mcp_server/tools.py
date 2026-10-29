"""Tool implementations: the ONLY code that touches user data sources.

Every tool:
- takes typed inputs (validated by the MCP SDK from these signatures),
- returns a typed Pydantic model (becomes the tool's structured output schema),
- is workspace-scoped (a source id from another workspace is 'not found'),
- raises ToolError for anticipated failures, so the message reaches the agent and it can
  self-correct. Unexpected exceptions are hidden from the client by the SDK and logged.
"""

import logging
from typing import Any

import duckdb
import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from analyst.config import get_settings
from analyst.datasources import store
from analyst.datasources.base import DataSource, QueryResult
from analyst.datasources.registry import CONNECTORS, make_datasource
from analyst.datasources.sql_guard import validate_sql as guard_sql
from analyst.datasources.store import SourceRecord
from analyst.db.audit import audit
from analyst.db.pool import get_app_pool
from analyst.documents.search import DocHit, hybrid_search
from analyst.llm import embed_query, embed_texts
from analyst.security.approval import verify_approval

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- output models
class SourceList(BaseModel):
    sources: list[SourceRecord]


class RegisterResult(BaseModel):
    source: SourceRecord
    warnings: list[str]


class TableDoc(BaseModel):
    qualified_name: str
    ddl: str
    pii_columns: list[str]


class TableList(BaseModel):
    source_id: str
    source_name: str
    dialect: str
    tables: list[TableDoc]


class Validation(BaseModel):
    ok: bool
    sql: str
    errors: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    pii_columns: list[str] = Field(default_factory=list)
    estimated_rows: int | None = None
    requires_approval: bool = False
    approval_reasons: list[str] = Field(default_factory=list)


class QueryOutput(QueryResult):
    sql: str


class DocHits(BaseModel):
    hits: list[DocHit]


class OverviewSource(BaseModel):
    id: str
    name: str
    kind: str
    description: str
    tables: list[str]


class WorkspaceOverview(BaseModel):
    sources: list[OverviewSource]
    documents: list[str]


class ReportRef(BaseModel):
    id: str


class Deleted(BaseModel):
    deleted: bool


# ----------------------------------------------------------------------------- helpers
async def _open(workspace_id: str, source_id: str) -> tuple[store.SourceWithCredentials, DataSource]:
    try:
        src = await store.get_source(workspace_id, source_id)
    except LookupError as e:
        raise ToolError(str(e)) from None
    if src.record.status != "ready":
        raise ToolError(f"data source '{src.record.name}' is not ready (status={src.record.status})")
    return src, make_datasource(src.record.kind, src.credentials)


async def _index(source_id: str, ds: DataSource) -> int:
    tables = await ds.introspect()
    embeddings = await embed_texts([t.embedding_text() for t in tables])
    await store.save_schema(source_id, tables, embeddings)
    return len(tables)


async def _validate(workspace_id: str, source_id: str, sql: str) -> tuple[Validation, DataSource]:
    s = get_settings()
    src, ds = await _open(workspace_id, source_id)
    tables = await store.load_tables(source_id)
    result = guard_sql(
        sql,
        dialect=ds.dialect,
        allowed_tables={t.qualified_name.lower() for t in tables},
        max_rows=s.max_result_rows,
        pii_columns={t.qualified_name.lower(): set(t.pii_columns) for t in tables},
        default_schema="main" if ds.dialect == "duckdb" else "public",
    )
    if not result.ok:
        await audit(
            "sql_rejected",
            actor="agent",
            workspace_id=workspace_id,
            source_id=source_id,
            detail={"sql": sql, "errors": result.errors},
        )
        return Validation(ok=False, sql=sql, errors=result.errors, tables=result.tables), ds

    reasons: list[str] = []
    if result.pii_columns:
        reasons.append(f"reads personal data columns: {', '.join(result.pii_columns)}")
    estimated = await ds.estimate_rows(result.sql)
    if estimated is not None and estimated > s.approval_row_threshold:
        reasons.append(f"large query: planner estimates ~{estimated:,} rows processed")
    return Validation(
        ok=True,
        sql=result.sql,
        tables=result.tables,
        pii_columns=result.pii_columns,
        estimated_rows=estimated,
        requires_approval=bool(reasons),
        approval_reasons=reasons,
    ), ds


# ----------------------------------------------------------------------------- source admin tools
async def list_sources(workspace_id: str) -> SourceList:
    """List the data sources connected to a workspace."""
    return SourceList(sources=await store.list_sources(workspace_id))


async def register_source(
    workspace_id: str, name: str, kind: str, credentials: dict[str, Any], description: str = ""
) -> RegisterResult:
    """Connect a new data source: test it, store encrypted credentials, and index its schema."""
    if kind not in CONNECTORS:
        raise ToolError(f"unsupported kind {kind!r}; available: {sorted(CONNECTORS)}")
    try:
        ds = make_datasource(kind, credentials)
        warnings = await ds.test_connection()
    except (ValueError, psycopg.Error, duckdb.Error, OSError) as e:
        raise ToolError(f"connection test failed: {e}") from None
    try:
        source_id = await store.create_source(workspace_id, name, kind, credentials, description)
    except psycopg.errors.UniqueViolation:
        raise ToolError(f"a source named {name!r} already exists in this workspace") from None
    try:
        count = await _index(source_id, ds)
        await store.set_status(source_id, "ready", warnings=warnings)
    except Exception as e:
        log.exception("indexing failed for source %s", source_id)
        await store.set_status(source_id, "error", error=str(e)[:500])
        raise ToolError(f"schema indexing failed: {e}") from None
    await audit(
        "register_source",
        actor="user",
        workspace_id=workspace_id,
        source_id=source_id,
        detail={"name": name, "kind": kind, "tables": count, "warnings": warnings},
    )
    src = await store.get_source(workspace_id, source_id)
    return RegisterResult(source=src.record, warnings=warnings)


async def refresh_source(workspace_id: str, source_id: str) -> SourceRecord:
    """Re-introspect a source's schema (after the user changed their database)."""
    try:
        src = await store.get_source(workspace_id, source_id)
    except LookupError as e:
        raise ToolError(str(e)) from None
    try:
        await _index(source_id, make_datasource(src.record.kind, src.credentials))
        await store.set_status(source_id, "ready")
    except Exception as e:
        await store.set_status(source_id, "error", error=str(e)[:500])
        raise ToolError(f"refresh failed: {e}") from None
    return (await store.get_source(workspace_id, source_id)).record


async def delete_source(workspace_id: str, source_id: str) -> Deleted:
    """Disconnect a data source and drop its schema snapshot and stored credentials."""
    deleted = await store.delete_source(workspace_id, source_id)
    if deleted:
        await audit("delete_source", actor="user", workspace_id=workspace_id, source_id=source_id)
    return Deleted(deleted=deleted)


# ----------------------------------------------------------------------------- agent tools
async def workspace_overview(workspace_id: str) -> WorkspaceOverview:
    """What the planner can work with: ready sources (with table names) and uploaded documents."""
    sources = []
    for rec in await store.list_sources(workspace_id):
        if rec.status != "ready":
            continue
        names = [t.qualified_name for t in await store.load_tables(rec.id)]
        sources.append(
            OverviewSource(id=rec.id, name=rec.name, kind=rec.kind, description=rec.description, tables=names[:60])
        )
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT filename FROM documents WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 50",
            [workspace_id],
        )
        docs = [r["filename"] for r in await cur.fetchall()]
    return WorkspaceOverview(sources=sources, documents=docs)


async def find_relevant_tables(workspace_id: str, source_id: str, question: str, k: int = 8) -> TableList:
    """Schema linking: return only the tables relevant to a question (plus their FK neighbours)."""
    src, ds = await _open(workspace_id, source_id)
    tables = await store.load_tables(source_id)
    if len(tables) > 12:
        top = await store.search_tables(source_id, await embed_query(question), k)
        by_name = {t.qualified_name: t for t in tables}
        chosen = [by_name[n] for n in top if n in by_name]
        # Add 1-hop foreign-key neighbours so the LLM can write the joins.
        for t in list(chosen):
            for fk in t.foreign_keys:
                if fk.ref_table in by_name and by_name[fk.ref_table] not in chosen and len(chosen) < k + 4:
                    chosen.append(by_name[fk.ref_table])
        tables = chosen
    return TableList(
        source_id=source_id,
        source_name=src.record.name,
        dialect=ds.dialect,
        tables=[
            TableDoc(qualified_name=t.qualified_name, ddl=t.to_prompt(), pii_columns=t.pii_columns) for t in tables
        ],
    )


async def describe_tables(workspace_id: str, source_id: str, tables: list[str] | None = None) -> TableList:
    """Full schema details for specific tables (or all tables when `tables` is omitted)."""
    src, ds = await _open(workspace_id, source_id)
    infos = await store.load_tables(source_id, tables)
    return TableList(
        source_id=source_id,
        source_name=src.record.name,
        dialect=ds.dialect,
        tables=[TableDoc(qualified_name=t.qualified_name, ddl=t.to_prompt(), pii_columns=t.pii_columns) for t in infos],
    )


async def validate_sql(workspace_id: str, source_id: str, sql: str) -> Validation:
    """Check SQL against the guardrails without running it; reports whether human approval is needed."""
    validation, _ = await _validate(workspace_id, source_id, sql)
    return validation


async def run_sql(
    workspace_id: str,
    source_id: str,
    sql: str,
    approval_token: str | None = None,
    thread_id: str | None = None,
) -> QueryOutput:
    """Run ONE read-only SELECT. Re-validates; risky queries need a signed human approval token."""
    s = get_settings()
    validation, ds = await _validate(workspace_id, source_id, sql)
    if not validation.ok:
        raise ToolError("SQL rejected by guardrails: " + "; ".join(validation.errors))
    approved = verify_approval(source_id, validation.sql, approval_token) or verify_approval(
        source_id, sql, approval_token
    )
    if validation.requires_approval and not approved:
        raise ToolError("requires human approval: " + "; ".join(validation.approval_reasons))
    try:
        result = await ds.run_readonly(validation.sql, max_rows=s.max_result_rows, timeout_ms=s.query_timeout_ms)
    except psycopg.errors.QueryCanceled:
        raise ToolError(f"query timed out after {s.query_timeout_ms} ms; simplify or aggregate more") from None
    except (psycopg.Error, duckdb.Error, TimeoutError, ValueError) as e:
        # DB error messages ("column x does not exist") are exactly what the agent needs to self-correct.
        raise ToolError(f"database error: {str(e).strip()}") from None
    await audit(
        "run_sql",
        actor="agent",
        workspace_id=workspace_id,
        thread_id=thread_id,
        source_id=source_id,
        detail={
            "sql": validation.sql,
            "rows": result.row_count,
            "ms": result.elapsed_ms,
            "approved": approved,
            "approval_reasons": validation.approval_reasons,
        },
    )
    return QueryOutput(sql=validation.sql, **result.model_dump())


async def search_docs(workspace_id: str, query: str, k: int = 6) -> DocHits:
    """Hybrid (vector + keyword) search over the workspace's uploaded documents."""
    hits = await hybrid_search(workspace_id, query, k=min(k, 20), exclude_flagged=get_settings().exclude_flagged_chunks)
    return DocHits(hits=hits)


async def save_report(workspace_id: str, title: str, content_md: str, thread_id: str | None = None) -> ReportRef:
    """Persist a generated report so it can be downloaded or shared."""
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO reports (workspace_id, thread_id, title, content_md) VALUES (%s, %s, %s, %s)"
            " RETURNING id::text",
            [workspace_id, thread_id, title[:200], content_md[:500_000]],
        )
        row = await cur.fetchone()
    assert row is not None
    await audit(
        "save_report",
        actor="agent",
        workspace_id=workspace_id,
        thread_id=thread_id,
        detail={"report_id": row["id"], "title": title},
    )
    return ReportRef(id=row["id"])


ALL_TOOLS = [
    list_sources,
    register_source,
    refresh_source,
    delete_source,
    workspace_overview,
    find_relevant_tables,
    describe_tables,
    validate_sql,
    run_sql,
    search_docs,
    save_report,
]
