"""Turn a LangGraph run into a typed Server-Sent Events stream.

LangGraph stream modes used:
- "custom":   progress events our nodes emit (plan, step, sql, sql_result, rag_result, ...)
- "messages": LLM tokens; we forward only the final-answer call (tagged in report_agent)
- "updates":  state updates; we watch it for `__interrupt__` (human approval needed)

subgraphs=True makes events from inside the SQL subgraph visible too.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk

from analyst.agents.report_agent import ANSWER_TAG

log = logging.getLogger(__name__)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def pending_interrupts(snapshot: Any) -> list[dict[str, Any]]:
    """Interrupts waiting for a human, including ones raised inside subgraphs."""
    found: dict[str, dict[str, Any]] = {}
    for intr in getattr(snapshot, "interrupts", ()) or ():
        found[intr.id] = {"id": intr.id, **(intr.value if isinstance(intr.value, dict) else {"value": intr.value})}
    for task in getattr(snapshot, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            found.setdefault(
                intr.id, {"id": intr.id, **(intr.value if isinstance(intr.value, dict) else {"value": intr.value})}
            )
    return list(found.values())


def _token_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    return "".join(b.get("text", "") for b in chunk.content if isinstance(b, dict))


async def stream_run(graph: Any, payload: Any, config: dict[str, Any]) -> AsyncIterator[str]:
    thread_id = config["configurable"]["thread_id"]
    yield sse("run_started", {"thread_id": thread_id})
    seen_interrupts: set[str] = set()
    try:
        async for _ns, mode, chunk in graph.astream(
            payload, config, stream_mode=["custom", "messages", "updates"], subgraphs=True
        ):
            if mode == "custom" and isinstance(chunk, dict):
                yield sse(str(chunk.get("type", "event")), chunk)
            elif mode == "messages":
                msg, meta = chunk
                if isinstance(msg, AIMessageChunk) and ANSWER_TAG in (meta.get("tags") or []):
                    if text := _token_text(msg):
                        yield sse("token", {"text": text})
            elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                for intr in chunk["__interrupt__"]:
                    if intr.id not in seen_interrupts:
                        seen_interrupts.add(intr.id)
                        value = intr.value if isinstance(intr.value, dict) else {"value": intr.value}
                        yield sse("approval_required", {"id": intr.id, **value})

        snapshot = await graph.aget_state(config)
        if pending := pending_interrupts(snapshot):
            for p in pending:
                if p["id"] not in seen_interrupts:
                    yield sse("approval_required", p)
            yield sse("done", {"status": "awaiting_approval"})
            return
        values = snapshot.values or {}
        yield sse("final", {**(values.get("final") or {}), "results": values.get("results", [])})
        yield sse("done", {"status": "completed"})
    except Exception:
        log.exception("run failed for thread %s", thread_id)
        yield sse("error", {"message": "The analysis failed unexpectedly. Please try again."})
        yield sse("done", {"status": "error"})
