"""Shared graph state.

State values are plain dicts/lists (TypedDicts), not Pydantic objects: they're stored in
checkpoints, and plain JSON-like data is the most robust thing to serialize and to
evolve across deploys. Pydantic is used at the edges (LLM structured output, tool I/O).
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class Task(TypedDict):
    id: int
    kind: Literal["sql", "rag"]
    question: str
    source_id: str | None


class Citation(TypedDict):
    chunk_id: int
    document: str
    page: int | None
    snippet: str


class TaskResult(TypedDict, total=False):
    task_id: int
    kind: Literal["sql", "rag"]
    question: str
    ok: bool
    error: str | None
    # sql
    source: str | None
    sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    attempts: int
    # rag
    answer: str | None
    citations: list[Citation]


class ChartSpec(TypedDict):
    type: Literal["bar", "line", "pie"]
    title: str
    task_id: int
    x: str
    y: list[str]


class Final(TypedDict, total=False):
    answer: str
    chart: ChartSpec | None
    report_id: str | None


RESET: dict[str, Any] = {"__reset__": True}


def merge_results(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Append-only reducer with a reset marker.

    Agents append results; a new question appends RESET first so results from the
    previous question don't leak into this answer (while chat history is kept).
    """
    merged = list(left or []) + list(right or [])
    for i in range(len(merged) - 1, -1, -1):
        if isinstance(merged[i], dict) and merged[i].get("__reset__"):
            return merged[i + 1 :]
    return merged


class AnalystState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    workspace_id: str
    thread_id: str
    source_ids: list[str]  # restrict this run to these sources ([] = all)
    plan: list[Task]
    task_index: int
    results: Annotated[list[TaskResult], merge_results]
    last_result: TaskResult | None  # written by an agent, folded into `results` by `collect`
    step_count: int
    replans: int
    export_report: bool
    direct_answer: str | None
    final: Final | None
