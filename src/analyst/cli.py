"""Admin CLI:  uv run analyst <command>

gen-secrets                     print fresh secrets for .env
migrate                         apply app-DB migrations
create-workspace NAME           create a workspace and print its API key (shown once)
create-key NAME                 issue another API key for a workspace
seed-demo NAME                  connect the demo warehouse + upload demo docs into a workspace
reembed                         rebuild all vectors with the current embedding model
"""

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from analyst.config import get_settings
from analyst.db.migrate import migrate
from analyst.db.pool import close_app_pool, get_app_pool
from analyst.security.crypto import hash_api_key, new_api_key

DEMO_DOCS = Path("examples/demo_docs")  # relative to the repo root (or /app in Docker)


def gen_secrets() -> None:
    print(f"APP_SECRET_KEY={Fernet.generate_key().decode()}")
    print(f"APPROVAL_SIGNING_KEY={secrets.token_hex(32)}")
    print(f"MCP_AUTH_TOKEN={secrets.token_urlsafe(32)}")


async def workspace_id(name: str, create: bool = False) -> str:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        if create:
            cur = await conn.execute(
                "INSERT INTO workspaces (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
                " RETURNING id::text",
                [name],
            )
        else:
            cur = await conn.execute("SELECT id::text FROM workspaces WHERE name = %s", [name])
        row = await cur.fetchone()
    if row is None:
        sys.exit(f"workspace {name!r} not found")
    return str(row["id"])


async def issue_key(ws_id: str, label: str) -> str:
    key = new_api_key()
    pool = await get_app_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO api_keys (workspace_id, key_hash, label) VALUES (%s, %s, %s)",
            [ws_id, hash_api_key(key), label],
        )
    return key


async def seed_demo(ws_id: str) -> None:
    from analyst.documents.ingest import ingest_document, list_documents
    from analyst.tools_client import ToolError, get_tool_client

    tools = get_tool_client()
    existing = {s["name"] for s in (await tools.call("list_sources", workspace_id=ws_id))["sources"]}
    if "demo_retail" not in existing:
        try:
            out = await tools.call(
                "register_source",
                workspace_id=ws_id,
                name="demo_retail",
                kind="postgres",
                credentials={"dsn": get_settings().demo_warehouse_conninfo},
                description="Demo retail warehouse: customers, products, orders, order line items.",
            )
            print(f"connected demo_retail: {out['source']['table_count']} tables; warnings={out['warnings']}")
        except ToolError as e:
            print(f"could not connect demo warehouse: {e}")
    else:
        print("demo_retail already connected")

    have = {d.filename for d in await list_documents(ws_id)}
    paths = await asyncio.to_thread(lambda: sorted(DEMO_DOCS.glob("*.md")))
    for path in paths:
        if path.name in have:
            continue
        data = await asyncio.to_thread(path.read_bytes)
        doc = await ingest_document(ws_id, path.name, "text/markdown", data)
        print(f"uploaded {path.name}: {doc.n_chunks} chunks, {doc.flagged_chunks} flagged")


async def reembed() -> None:
    """Rebuild every stored vector with the current embedding model (after switching models)."""
    from psycopg.rows import dict_row

    from analyst.datasources.base import TableInfo
    from analyst.db.pool import to_vector
    from analyst.llm import embed_texts

    batch = get_settings().embedding_batch_size
    pool = await get_app_pool()
    for table, text_of in (
        ("schema_tables", lambda r: TableInfo.model_validate(r["info"]).embedding_text()),
        ("doc_chunks", lambda r: r["content"]),
    ):
        column = "info" if table == "schema_tables" else "content"
        async with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(f"SELECT id, {column} FROM {table} ORDER BY id")
            rows = await cur.fetchall()
        for i in range(0, len(rows), batch):
            chunk = rows[i : i + batch]
            vectors = await embed_texts([text_of(r) for r in chunk])
            async with pool.connection() as conn, conn.cursor() as c:
                await c.executemany(
                    f"UPDATE {table} SET embedding = %s::vector WHERE id = %s",
                    [(to_vector(v), r["id"]) for v, r in zip(vectors, chunk, strict=True)],
                )
        print(f"re-embedded {len(rows)} rows in {table}")


async def _main(args: argparse.Namespace) -> None:
    try:
        await migrate()
        if args.cmd == "migrate":
            print("migrations applied")
        elif args.cmd == "create-workspace":
            ws = await workspace_id(args.name, create=True)
            print(
                f"workspace {args.name} ({ws})\nAPI key (store it now, it is not shown again):\n"
                f"{await issue_key(ws, 'cli')}"
            )
        elif args.cmd == "create-key":
            print(await issue_key(await workspace_id(args.name), "cli"))
        elif args.cmd == "seed-demo":
            await seed_demo(await workspace_id(args.name))
        elif args.cmd == "reembed":
            await reembed()
    finally:
        await close_app_pool()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="analyst", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-secrets")
    sub.add_parser("migrate")
    sub.add_parser("reembed")
    for cmd in ("create-workspace", "create-key", "seed-demo"):
        sub.add_parser(cmd).add_argument("name")
    args = parser.parse_args()
    if args.cmd == "gen-secrets":
        gen_secrets()
        return
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
