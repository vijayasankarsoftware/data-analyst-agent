"""Supervisor: plan -> dispatch -> collect -> (review) -> report.

The supervisor never touches data itself. It decomposes the request, routes each task to
a specialist with `Command(goto=...)`, folds results into shared state, and decides when
the work is complete. Hard caps (tasks per plan, total steps, one re-plan) bound cost and
prevent infinite loops.
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from analyst.agents import prompts
from analyst.agents.events import emit
from analyst.agents.state import RESET, AnalystState, Task
from analyst.config import get_settings
from analyst.llm import structured_model
from analyst.tools_client import get_tool_client


class PlannedTask(BaseModel):
    kind: Literal["sql", "rag"]
    question: str = Field(description="Self-contained question for the specialist.")
    source_id: str | None = Field(default=None, description="Required for sql tasks.")


class Plan(BaseModel):
    reasoning: str = Field(description="Brief reasoning about how to answer.")
    tasks: list[PlannedTask]
    direct_answer: str | None = Field(default=None, description="Reply when no tasks are needed.")
    export_report: bool = False


class Review(BaseModel):
    complete: bool
    additional_tasks: list[PlannedTask] = Field(default_factory=list)


def _latest_question(state: AnalystState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def _history(state: AnalystState, n: int = 10) -> list[BaseMessage]:
    """Recent conversation (user questions + final answers) for resolving follow-ups."""
    return [m for m in state.get("messages", []) if isinstance(m, (HumanMessage, AIMessage))][-n:]


async def _overview(state: AnalystState) -> dict[str, Any]:
    overview = await get_tool_client().call("workspace_overview", workspace_id=state["workspace_id"])
    allowed = set(state.get("source_ids") or [])
    if allowed:
        overview["sources"] = [s for s in overview["sources"] if s["id"] in allowed]
    return overview


def _describe(overview: dict[str, Any]) -> tuple[str, str]:
    sources = (
        "\n".join(
            f"- id={s['id']} name={s['name']} kind={s['kind']} description={s['description'] or '-'}\n"
            f"  tables: {', '.join(s['tables'][:40])}"
            for s in overview["sources"]
        )
        or "(none connected)"
    )
    documents = "\n".join(f"- {d}" for d in overview["documents"]) or "(none uploaded)"
    return sources, documents


def _sanitize(tasks: list[PlannedTask], overview: dict[str, Any], start_id: int, limit: int) -> list[Task]:
    """Never trust the LLM's plan blindly: fix or drop tasks that reference things that don't exist."""
    source_ids = [s["id"] for s in overview["sources"]]
    out: list[Task] = []
    for t in tasks:
        if len(out) >= limit:
            break
        if t.kind == "sql":
            sid = t.source_id if t.source_id in source_ids else (source_ids[0] if len(source_ids) == 1 else None)
            if sid is None:
                continue
            out.append(Task(id=start_id + len(out), kind="sql", question=t.question, source_id=sid))
        elif overview["documents"]:
            out.append(Task(id=start_id + len(out), kind="rag", question=t.question, source_id=None))
    return out


async def plan(state: AnalystState) -> Command[Literal["dispatch", "respond"]]:
    s = get_settings()
    overview = await _overview(state)
    sources, documents = _describe(overview)
    reset: dict[str, Any] = {
        "results": [RESET],
        "task_index": 0,
        "step_count": 0,
        "replans": 0,
        "final": None,
        "last_result": None,
        "direct_answer": None,
    }

    if not overview["sources"] and not overview["documents"]:
        return Command(
            goto="respond",
            update={
                **reset,
                "plan": [],
                "export_report": False,
                "direct_answer": (
                    "I don't have any data to work with yet. Connect a database on the **Sources** page "
                    "or upload documents on the **Documents** page, then ask again."
                ),
            },
        )

    llm = structured_model(Plan)
    system = prompts.PLANNER.format(
        max_tasks=s.max_tasks_per_plan, security=prompts.SECURITY_RULES, sources=sources, documents=documents
    )
    result: Plan = await llm.ainvoke([SystemMessage(system), *_history(state)])  # type: ignore[assignment]
    tasks = _sanitize(result.tasks, overview, start_id=1, limit=s.max_tasks_per_plan)

    emit("plan", reasoning=result.reasoning, tasks=tasks)
    update = {**reset, "plan": tasks, "export_report": result.export_report and bool(tasks)}
    if not tasks:
        return Command(
            goto="respond",
            update={
                **update,
                "direct_answer": result.direct_answer
                or ("I couldn't map that request to the connected data or documents. Could you rephrase it?"),
            },
        )
    return Command(goto="dispatch", update=update)


async def dispatch(state: AnalystState) -> Command[Literal["sql_agent", "rag_agent", "review", "report"]]:
    if state.get("step_count", 0) >= get_settings().max_graph_steps:
        return Command(goto="report")
    i = state.get("task_index", 0)
    tasks = state.get("plan", [])
    if i >= len(tasks):
        return Command(goto="review")
    return Command(goto="sql_agent" if tasks[i]["kind"] == "sql" else "rag_agent")


async def collect(state: AnalystState) -> Command[Literal["dispatch"]]:
    return Command(
        goto="dispatch",
        update={
            "results": [state["last_result"]] if state.get("last_result") else [],
            "last_result": None,
            "task_index": state.get("task_index", 0) + 1,
            "step_count": state.get("step_count", 0) + 1,
        },
    )


async def review(state: AnalystState) -> Command[Literal["dispatch", "report"]]:
    s = get_settings()
    results = state.get("results", [])
    # Skip the extra LLM call for the common case: a single task that succeeded.
    if state.get("replans", 0) >= 1 or (len(results) == 1 and results[0].get("ok")):
        return Command(goto="report")

    overview = await _overview(state)
    sources, documents = _describe(overview)
    summary = "\n".join(
        f"- [{r['kind']}] {r['question']} -> "
        + (
            f"ok, {r.get('row_count', 0)} rows, columns {r.get('columns')}"
            if r["kind"] == "sql" and r.get("ok")
            else f"ok: {str(r.get('answer'))[:300]}"
            if r.get("ok")
            else f"FAILED: {r.get('error')}"
        )
        for r in results
    )
    llm = structured_model(Review)
    decision: Review = await llm.ainvoke(
        [  # type: ignore[assignment]
            SystemMessage(prompts.REVIEW.format(security=prompts.SECURITY_RULES, sources=sources, documents=documents)),
            HumanMessage(f"Request: {_latest_question(state)}\n\nTask results:\n{summary}"),
        ]
    )
    remaining = s.max_tasks_per_plan - len(state.get("plan", []))
    extra = _sanitize(
        decision.additional_tasks, overview, start_id=len(state.get("plan", [])) + 1, limit=min(2, max(remaining, 0))
    )
    if decision.complete or not extra:
        return Command(goto="report")
    emit("plan", reasoning="Follow-up tasks after review", tasks=extra, revision=True)
    return Command(
        goto="dispatch", update={"plan": [*state.get("plan", []), *extra], "replans": state.get("replans", 0) + 1}
    )


async def respond(state: AnalystState) -> dict[str, Any]:
    answer = state.get("direct_answer") or "How can I help with your data?"
    emit("token", text=answer)
    return {"messages": [AIMessage(answer)], "final": {"answer": answer, "chart": None, "report_id": None}}
