"""Report agent: synthesize results into an answer + chart spec; export behind human approval."""

import json
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from analyst.agents import prompts
from analyst.agents.events import emit
from analyst.agents.state import AnalystState, ChartSpec, TaskResult
from analyst.agents.supervisor import _latest_question
from analyst.db.audit import audit
from analyst.llm import get_chat_model, strip_thinking, structured_model
from analyst.security.injection import wrap_untrusted
from analyst.tools_client import ToolError, get_tool_client

ANSWER_TAG = "final_answer"  # the API streams tokens only from LLM calls carrying this tag


class ChartChoice(BaseModel):
    chart: Literal["bar", "line", "pie", "none"]
    title: str = ""
    task_id: int = 0
    x: str = ""
    y: list[str] = []


def _results_context(results: list[TaskResult]) -> str:
    parts = []
    for r in results:
        head = f"### Task {r['task_id']} ({r['kind']}): {r['question']}"
        if not r.get("ok"):
            parts.append(f"{head}\nFAILED: {r.get('error')}")
        elif r["kind"] == "sql":
            data = json.dumps({"columns": r.get("columns", []), "rows": r.get("rows", [])[:50]}, default=str)
            note = f" (showing first 50 of {r.get('row_count')} rows)" if r.get("row_count", 0) > 50 else ""
            note += " (result was truncated at the row limit)" if r.get("truncated") else ""
            parts.append(f"{head}\nSource: {r.get('source')}{note}\n" + wrap_untrusted(data, label="query_result"))
        else:
            cites = ", ".join(
                f"{c['document']}" + (f" p.{c['page']}" if c.get("page") else "") for c in r.get("citations", [])
            )
            parts.append(f"{head}\nAnswer from documents: {r.get('answer')}\nSources: {cites or 'none'}")
    return "\n\n".join(parts)


async def _choose_chart(results: list[TaskResult]) -> ChartSpec | None:
    candidates = [
        r
        for r in results
        if r["kind"] == "sql" and r.get("ok") and r.get("row_count", 0) >= 2 and len(r.get("columns", [])) >= 2
    ]
    if not candidates:
        return None
    desc = "\n".join(
        f"task_id={r['task_id']} question={r['question']!r} columns={r['columns']} first_rows={r.get('rows', [])[:3]}"
        for r in candidates
    )
    llm = structured_model(ChartChoice)
    choice: ChartChoice = await llm.ainvoke([SystemMessage(prompts.CHART), HumanMessage(desc)])  # type: ignore[assignment]
    if choice.chart == "none":
        return None
    task = next((r for r in candidates if r["task_id"] == choice.task_id), None)
    # Validate against real columns: the UI must never get a spec it can't render.
    if (
        task is None
        or choice.x not in task["columns"]
        or not choice.y
        or any(y not in task["columns"] for y in choice.y)
    ):
        return None
    return ChartSpec(type=choice.chart, title=choice.title, task_id=choice.task_id, x=choice.x, y=choice.y)


async def report(state: AnalystState) -> Command[Literal["approve_export", "__end__"]]:
    results = state.get("results", [])
    question = _latest_question(state)
    emit("step", agent="report", status="started")

    llm = get_chat_model().with_config(tags=[ANSWER_TAG])
    response = await llm.ainvoke(
        [
            SystemMessage(prompts.REPORT.format(security=prompts.SECURITY_RULES)),
            HumanMessage(f"User request: {question}\n\nTask results:\n{_results_context(results)}"),
        ]
    )
    answer = strip_thinking(response.content if isinstance(response.content, str) else str(response.content))
    chart = await _choose_chart(results)
    emit("step", agent="report", status="done")

    update: dict[str, Any] = {
        "messages": [AIMessage(answer)],
        "final": {"answer": answer, "chart": chart, "report_id": None},
    }
    if state.get("export_report"):
        return Command(goto="approve_export", update=update)
    return Command(goto=END, update=update)


def _report_markdown(question: str, answer: str, results: list[TaskResult]) -> str:
    lines = [f"# {question}", "", answer, "", "---", "## Appendix: how this was computed", ""]
    for r in results:
        lines.append(f"### {r['question']}")
        if r["kind"] == "sql" and r.get("sql"):
            lines += [f"Source: `{r.get('source')}`", "", "```sql", r["sql"], "```", ""]
        for c in r.get("citations", []) or []:
            lines.append(f"- {c['document']}" + (f" p.{c['page']}" if c.get("page") else ""))
        if not r.get("ok"):
            lines.append(f"_Failed: {r.get('error')}_")
        lines.append("")
    return "\n".join(lines)


async def approve_export(state: AnalystState) -> dict[str, Any]:
    final = dict(state.get("final") or {})
    question = _latest_question(state)
    decision: dict[str, Any] = interrupt(
        {
            "kind": "export_report",
            "title": question[:120],
            "preview": str(final.get("answer", ""))[:800],
            "actions": ["approve", "reject"],
        }
    )
    action = (decision or {}).get("action", "reject")
    await audit(
        "approval",
        actor="user",
        workspace_id=state["workspace_id"],
        thread_id=state["thread_id"],
        detail={"kind": "export_report", "action": action},
    )
    if action != "approve":
        emit("export", status="rejected")
        return {}
    try:
        ref = await get_tool_client().call(
            "save_report",
            workspace_id=state["workspace_id"],
            thread_id=state["thread_id"],
            title=question[:200],
            content_md=_report_markdown(question, final.get("answer", ""), state.get("results", [])),
        )
    except ToolError as e:
        emit("export", status="failed", error=str(e))
        return {}
    final["report_id"] = ref["id"]
    emit("export", status="saved", report_id=ref["id"])
    return {"final": final}
