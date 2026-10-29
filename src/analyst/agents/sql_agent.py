"""SQL agent: a subgraph with a self-correcting loop and a human-approval gate.

    prepare ─▶ generate ─▶ validate ──invalid & retries left──▶ generate
                              │ ok
                    needs approval? ──yes──▶ human_approval ──approve──▶ execute
                              │ no                  │ edit ─▶ validate
                              ▼                     │ reject ─▶ fail
                           execute ──DB error & retries left──▶ generate
                              │ ok
                              ▼
                             END (last_result)

The subgraph has PRIVATE state (sql, attempts, history...) and only `last_result` flows
back to the supervisor, which keeps the supervisor's context small.
"""

import json
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from analyst.agents import prompts
from analyst.agents.events import emit
from analyst.agents.state import Task, TaskResult
from analyst.config import get_settings
from analyst.db.audit import audit
from analyst.llm import structured_model
from analyst.security.approval import sign_approval
from analyst.security.injection import wrap_untrusted
from analyst.tools_client import ToolError, get_tool_client

PREVIEW_ROWS = 100


class SQLInput(TypedDict):
    workspace_id: str
    thread_id: str
    plan: list[Task]
    task_index: int


class SQLOutput(TypedDict):
    last_result: TaskResult


class SQLState(SQLInput, total=False):
    task: Task
    source_id: str | None
    source_name: str
    dialect: str
    schema_text: str
    definitions: str
    sql: str
    attempts: int
    history: list[dict[str, str]]
    approval_reasons: list[str]
    approval_token: str | None
    human_edited: bool
    failure: str | None
    last_result: TaskResult


class SQLDraft(BaseModel):
    reasoning: str = Field(description="One or two sentences: which tables/joins/filters and why.")
    sql: str = Field(description="A single SELECT statement.")


def _base_result(state: SQLState) -> TaskResult:
    task = state["task"]
    return TaskResult(
        task_id=task["id"],
        kind="sql",
        question=task["question"],
        source=state.get("source_name"),
        attempts=state.get("attempts", 0),
    )


async def prepare(state: SQLState) -> Command[Literal["generate", "fail"]]:
    task = state["plan"][state["task_index"]]
    emit("step", task_id=task["id"], agent="sql", status="started", question=task["question"])
    update: dict[str, Any] = {
        "task": task,
        "attempts": 0,
        "history": [],
        "human_edited": False,
        "approval_token": None,
        "failure": None,
    }
    if not task.get("source_id"):
        return Command(goto="fail", update={**update, "failure": "no data source selected for this task"})

    tools = get_tool_client()
    try:
        linked = await tools.call(
            "find_relevant_tables",
            workspace_id=state["workspace_id"],
            source_id=task["source_id"],
            question=task["question"],
        )
    except ToolError as e:
        return Command(goto="fail", update={**update, "failure": str(e)})

    # Uploaded docs double as a data dictionary ("active customer = ordered in last 90 days").
    definitions = ""
    try:
        hits = (await tools.call("search_docs", workspace_id=state["workspace_id"], query=task["question"], k=3))[
            "hits"
        ]
        if hits:
            definitions = "Business definitions from documents (reference only):\n" + "\n".join(
                wrap_untrusted(h["content"][:1200], label=h["document"]) for h in hits
            )
    except ToolError:
        pass

    return Command(
        goto="generate",
        update={
            **update,
            "source_id": task["source_id"],
            "source_name": linked["source_name"],
            "dialect": linked["dialect"],
            "schema_text": "\n\n".join(t["ddl"] for t in linked["tables"]),
            "definitions": definitions,
        },
    )


async def generate(state: SQLState) -> Command[Literal["validate"]]:
    s = get_settings()
    system = prompts.SQL_WRITER.format(
        dialect=state["dialect"],
        max_rows=s.max_result_rows,
        security=prompts.SECURITY_RULES,
        schema=state["schema_text"],
        definitions=state.get("definitions", ""),
    )
    if state.get("history"):
        system += "\n\n" + prompts.SQL_RETRY.format(
            history="\n".join(f"- SQL: {h['sql']}\n  Error: {h['error']}" for h in state["history"])
        )
    llm = structured_model(SQLDraft)
    draft: SQLDraft = await llm.ainvoke([SystemMessage(system), HumanMessage(state["task"]["question"])])  # type: ignore[assignment]
    attempts = state.get("attempts", 0) + 1
    emit("sql", task_id=state["task"]["id"], sql=draft.sql, attempt=attempts, reasoning=draft.reasoning)
    return Command(goto="validate", update={"sql": draft.sql, "attempts": attempts})


async def validate(state: SQLState) -> Command[Literal["generate", "human_approval", "execute", "fail"]]:
    s = get_settings()
    v = await get_tool_client().call(
        "validate_sql", workspace_id=state["workspace_id"], source_id=state["source_id"], sql=state["sql"]
    )
    if not v["ok"]:
        history = [*state.get("history", []), {"sql": state["sql"], "error": "; ".join(v["errors"])}]
        if state.get("human_edited") or state.get("attempts", 0) >= s.max_sql_attempts:
            return Command(
                goto="fail", update={"history": history, "failure": "SQL failed validation: " + "; ".join(v["errors"])}
            )
        return Command(goto="generate", update={"history": history})

    update = {"sql": v["sql"], "approval_reasons": v["approval_reasons"]}
    if v["requires_approval"] and not state.get("approval_token"):
        if state.get("human_edited"):
            # The human wrote this SQL themselves after seeing the risks: that IS the approval.
            return Command(
                goto="execute", update={**update, "approval_token": sign_approval(state["source_id"] or "", v["sql"])}
            )
        return Command(goto="human_approval", update=update)
    return Command(goto="execute", update=update)


async def human_approval(state: SQLState) -> Command[Literal["execute", "validate", "fail"]]:
    # Everything before interrupt() re-runs on resume, so keep this node side-effect free until then.
    decision: dict[str, Any] = interrupt(
        {
            "kind": "sql_approval",
            "task_id": state["task"]["id"],
            "question": state["task"]["question"],
            "source": state.get("source_name"),
            "sql": state["sql"],
            "reasons": state.get("approval_reasons", []),
            "actions": ["approve", "edit", "reject"],
        }
    )
    action = (decision or {}).get("action", "reject")
    await audit(
        "approval",
        actor="user",
        workspace_id=state["workspace_id"],
        thread_id=state["thread_id"],
        source_id=state.get("source_id"),
        detail={
            "kind": "sql_approval",
            "action": action,
            "sql": state["sql"],
            "edited_sql": decision.get("sql") if action == "edit" else None,
            "comment": decision.get("comment"),
        },
    )
    if action == "approve":
        return Command(goto="execute", update={"approval_token": sign_approval(state["source_id"] or "", state["sql"])})
    if action == "edit" and decision.get("sql"):
        emit("sql", task_id=state["task"]["id"], sql=decision["sql"], attempt=state.get("attempts", 0), edited=True)
        return Command(goto="validate", update={"sql": decision["sql"], "human_edited": True, "approval_token": None})
    reason = decision.get("comment") or "no reason given"
    return Command(goto="fail", update={"failure": f"Query was rejected by the user ({reason})."})


async def execute(state: SQLState) -> Command[Literal["generate", "fail", "__end__"]]:
    s = get_settings()
    try:
        out = await get_tool_client().call(
            "run_sql",
            workspace_id=state["workspace_id"],
            source_id=state["source_id"],
            sql=state["sql"],
            approval_token=state.get("approval_token"),
            thread_id=state["thread_id"],
        )
    except ToolError as e:
        history = [*state.get("history", []), {"sql": state["sql"], "error": str(e)}]
        retryable = "requires human approval" not in str(e) and not state.get("human_edited")
        if retryable and state.get("attempts", 0) < s.max_sql_attempts:
            return Command(goto="generate", update={"history": history, "approval_token": None})
        return Command(goto="fail", update={"history": history, "failure": str(e)})

    result = _base_result(state)
    result.update(
        ok=True,
        error=None,
        sql=out["sql"],
        columns=out["columns"],
        rows=out["rows"][:PREVIEW_ROWS],
        row_count=out["row_count"],
        truncated=out["truncated"] or out["row_count"] > PREVIEW_ROWS,
    )
    emit(
        "sql_result",
        task_id=state["task"]["id"],
        sql=out["sql"],
        columns=out["columns"],
        rows=out["rows"][:PREVIEW_ROWS],
        row_count=out["row_count"],
        truncated=result["truncated"],
        elapsed_ms=out["elapsed_ms"],
    )
    emit("step", task_id=state["task"]["id"], agent="sql", status="done")
    return Command(goto=END, update={"last_result": result})


async def fail(state: SQLState) -> dict[str, Any]:
    result = _base_result(state)
    result.update(ok=False, sql=state.get("sql"), error=state.get("failure") or "unknown error")
    emit("step", task_id=state["task"]["id"], agent="sql", status="failed", error=result["error"])
    return {"last_result": result}


def build_sql_agent() -> Any:
    g = StateGraph(SQLState, input_schema=SQLInput, output_schema=SQLOutput)
    g.add_node("prepare", prepare)
    g.add_node("generate", generate)
    g.add_node("validate", validate)
    g.add_node("human_approval", human_approval)
    g.add_node("execute", execute)
    g.add_node("fail", fail)
    g.add_edge(START, "prepare")
    g.add_edge("fail", END)
    return g.compile()


def rows_as_json(result: TaskResult, limit: int = 50) -> str:
    return json.dumps({"columns": result.get("columns", []), "rows": result.get("rows", [])[:limit]}, default=str)
