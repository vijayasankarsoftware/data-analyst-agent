"""Deterministic pieces of the agent layer (no LLM needed)."""

from analyst.agents.state import RESET, merge_results
from analyst.agents.supervisor import PlannedTask, _sanitize
from analyst.api.streaming import sse
from evals.run import results_match

OVERVIEW = {
    "sources": [{"id": "s1", "name": "db", "kind": "postgres", "description": "", "tables": []}],
    "documents": ["policy.md"],
}


def test_results_append() -> None:
    assert merge_results([{"a": 1}], [{"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_results_reset_marker_clears_previous_question() -> None:
    assert merge_results([{"old": 1}], [RESET]) == []
    assert merge_results([{"old": 1}], [RESET, {"new": 1}]) == [{"new": 1}]


def test_sanitize_fills_single_source_and_drops_unknown() -> None:
    tasks = [PlannedTask(kind="sql", question="q1", source_id="made-up"), PlannedTask(kind="rag", question="q2")]
    out = _sanitize(tasks, OVERVIEW, start_id=1, limit=5)
    assert [(t["id"], t["kind"], t["source_id"]) for t in out] == [(1, "sql", "s1"), (2, "rag", None)]


def test_sanitize_drops_rag_without_documents_and_caps() -> None:
    overview = {**OVERVIEW, "documents": []}
    tasks = [PlannedTask(kind="rag", question="q")] + [PlannedTask(kind="sql", question=f"q{i}") for i in range(9)]
    out = _sanitize(tasks, overview, start_id=1, limit=3)
    assert len(out) == 3 and all(t["kind"] == "sql" for t in out)


def test_sse_format() -> None:
    assert sse("token", {"text": "hi"}) == 'event: token\ndata: {"text": "hi"}\n\n'


def test_eval_result_matching_is_order_and_extra_column_tolerant() -> None:
    gold = [["Shoes", 1234.5], ["Shirts", 99.0]]
    assert results_match(gold, [[99.0, "shirts", 0.1], [1234.49, "Shoes", 0.9]])
    assert not results_match(gold, [["Shoes", 1000.0], ["Shirts", 99.0]])
    assert not results_match(gold, [["Shoes", 1234.5]])
