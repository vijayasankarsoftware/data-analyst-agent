"""FastAPI app: run with  uvicorn analyst.api.main:app --reload"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from analyst.agents.graph import build_graph
from analyst.api.routes import documents, reports, sources, threads
from analyst.config import get_settings
from analyst.db.migrate import migrate
from analyst.db.pool import close_app_pool, get_app_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await migrate()
    pool = await get_app_pool()
    # Checkpoints live in the app DB: conversation state survives restarts, and a run
    # paused for human approval can be resumed by ANY API replica, minutes or days later.
    checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await checkpointer.setup()
    app.state.graph = build_graph(checkpointer)
    yield
    await close_app_pool()


app = FastAPI(title="Data Analyst Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (sources.router, documents.router, threads.router, reports.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
async def health() -> dict[str, str]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok"}
