"""Custom progress events streamed to the UI (stream_mode='custom')."""

from typing import Any

from langgraph.config import get_stream_writer


def emit(event: str, **data: Any) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:  # called outside a graph run (e.g. unit tests)
        return
    writer({"type": event, **data})
