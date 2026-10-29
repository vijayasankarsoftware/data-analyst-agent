"""Flow C: conversations, streaming runs and human-in-the-loop resume."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from analyst.api.deps import GraphDep, ThreadDep, WorkspaceDep
from analyst.api.schemas import ResumeRequest, RunRequest, ThreadCreate
from analyst.api.streaming import pending_interrupts, stream_run
from analyst.db.audit import audit
from analyst.db.pool import get_app_pool
from analyst.security.injection import scan_text

router = APIRouter(prefix="/threads", tags=["threads"])

# One run per thread at a time. (In-process: with several API replicas, use a Postgres
# advisory lock or a Redis lock instead.)
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


async def _locked(thread_id: str, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async with _locks[thread_id]:
        async for chunk in stream:
            yield chunk
    pool = await get_app_pool()
    async with pool.connection() as conn:
        await conn.execute("UPDATE threads SET updated_at = now() WHERE id::text = %s", [thread_id])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_thread(body: ThreadCreate, ws: WorkspaceDep) -> dict[str, Any]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO threads (workspace_id, title) VALUES (%s, coalesce(%s, 'New analysis'))"
            " RETURNING id::text AS id, title, created_at::text AS created_at",
            [ws.id, body.title],
        )
        row = await cur.fetchone()
    return dict(row or {})


@router.get("")
async def list_threads(ws: WorkspaceDep) -> dict[str, Any]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id::text AS id, title, updated_at::text AS updated_at FROM threads"
            " WHERE workspace_id = %s ORDER BY updated_at DESC LIMIT 100",
            [ws.id],
        )
        return {"threads": await cur.fetchall()}


@router.get("/{thread_id}")
async def get_thread(thread: ThreadDep, graph: GraphDep) -> dict[str, Any]:
    snapshot = await graph.aget_state(_config(thread["id"]))
    values = snapshot.values or {}
    messages = [
        {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in values.get("messages", [])
        if isinstance(m, (HumanMessage, AIMessage))
    ]
    return {
        **thread,
        "messages": messages,
        "plan": values.get("plan", []),
        "results": values.get("results", []),
        "final": values.get("final"),
        "pending_approvals": pending_interrupts(snapshot),
    }


@router.post("/{thread_id}/runs")
async def run(body: RunRequest, thread: ThreadDep, ws: WorkspaceDep, graph: GraphDep) -> StreamingResponse:
    thread_id = thread["id"]
    if _locks[thread_id].locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "a run is already in progress for this thread")
    if pending_interrupts(await graph.aget_state(_config(thread_id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "this thread is waiting for your approval; resume it first")

    scan = scan_text(body.message)
    if scan.flagged:  # logged, not blocked: users are authenticated and capabilities are read-only anyway
        await audit(
            "suspicious_input",
            actor="user",
            workspace_id=ws.id,
            thread_id=thread_id,
            detail={"reasons": scan.reasons, "message": body.message[:500]},
        )
    if thread["title"] == "New analysis":
        pool = await get_app_pool()
        async with pool.connection() as conn:
            await conn.execute("UPDATE threads SET title = %s WHERE id::text = %s", [body.message[:80], thread_id])

    payload = {
        "messages": [HumanMessage(body.message)],
        "workspace_id": ws.id,
        "thread_id": thread_id,
        "source_ids": body.source_ids,
    }
    stream = stream_run(graph, payload, _config(thread_id))
    return StreamingResponse(_locked(thread_id, stream), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{thread_id}/resume")
async def resume(body: ResumeRequest, thread: ThreadDep, graph: GraphDep) -> StreamingResponse:
    thread_id = thread["id"]
    if _locks[thread_id].locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "a run is already in progress for this thread")
    if not pending_interrupts(await graph.aget_state(_config(thread_id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing is waiting for approval in this thread")
    # The graph restarts at the paused node; interrupt() returns this decision there.
    command = Command(resume=body.decision.model_dump())
    stream = stream_run(graph, command, _config(thread_id))
    return StreamingResponse(_locked(thread_id, stream), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread: ThreadDep, graph: GraphDep) -> None:
    await graph.checkpointer.adelete_thread(thread["id"])
    pool = await get_app_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM threads WHERE id::text = %s", [thread["id"]])
