"""RAG agent: retrieve -> answer only from excerpts -> verified citations."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from analyst.agents import prompts
from analyst.agents.events import emit
from analyst.agents.state import AnalystState, Citation, TaskResult
from analyst.llm import structured_model
from analyst.security.injection import wrap_untrusted
from analyst.tools_client import ToolError, get_tool_client


class RAGAnswer(BaseModel):
    found: bool = Field(description="False if the excerpts don't answer the question.")
    answer: str
    cited_chunk_ids: list[int] = Field(default_factory=list)


async def rag_agent(state: AnalystState) -> dict[str, Any]:
    task = state["plan"][state["task_index"]]
    emit("step", task_id=task["id"], agent="rag", status="started", question=task["question"])
    result = TaskResult(task_id=task["id"], kind="rag", question=task["question"], citations=[])

    try:
        hits = (
            await get_tool_client().call("search_docs", workspace_id=state["workspace_id"], query=task["question"], k=6)
        )["hits"]
    except ToolError as e:
        result.update(ok=False, error=f"document search failed: {e}")
        emit("step", task_id=task["id"], agent="rag", status="failed", error=result["error"])
        return {"last_result": result}

    if not hits:
        result.update(ok=False, error="no relevant documents found", answer=None)
        emit("step", task_id=task["id"], agent="rag", status="failed", error=result["error"])
        return {"last_result": result}

    excerpts = "\n\n".join(
        f"[id={h['chunk_id']}] {h['document']}"
        + (f" p.{h['page']}" if h["page"] else "")
        + "\n"
        + wrap_untrusted(h["content"], label=h["document"])
        for h in hits
    )
    llm = structured_model(RAGAnswer)
    out: RAGAnswer = await llm.ainvoke(
        [  # type: ignore[assignment]
            SystemMessage(prompts.RAG_ANSWER.format(security=prompts.SECURITY_RULES, excerpts=excerpts)),
            HumanMessage(task["question"]),
        ]
    )

    # Only keep citations that point at chunks we actually retrieved (no hallucinated sources).
    by_id = {h["chunk_id"]: h for h in hits}
    citations = [
        Citation(chunk_id=i, document=by_id[i]["document"], page=by_id[i]["page"], snippet=by_id[i]["content"][:300])
        for i in dict.fromkeys(out.cited_chunk_ids)
        if i in by_id
    ]
    result.update(
        ok=out.found,
        answer=out.answer,
        citations=citations,
        error=None if out.found else "documents don't contain the answer",
    )
    emit("rag_result", task_id=task["id"], answer=out.answer, found=out.found, citations=citations)
    emit("step", task_id=task["id"], agent="rag", status="done" if out.found else "failed")
    return {"last_result": result}
