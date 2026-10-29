"""Assemble the hierarchical multi-agent graph.

    START ─▶ plan ─▶ dispatch ─┬─▶ sql_agent (subgraph) ─┐
               │       ▲       ├─▶ rag_agent ────────────┤
               │       └───────┴──── collect ◀───────────┘
               │               ├─▶ review ─▶ dispatch | report
               ▼               └─▶ report ─▶ approve_export? ─▶ END
            respond ─▶ END

Routing is done with Command(goto=...) returned by nodes; `destinations` tells
LangGraph (and graph visualizations) where each node may go.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from analyst.agents.rag_agent import rag_agent
from analyst.agents.report_agent import approve_export, report
from analyst.agents.sql_agent import build_sql_agent
from analyst.agents.state import AnalystState
from analyst.agents.supervisor import collect, dispatch, plan, respond, review


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    g = StateGraph(AnalystState)
    g.add_node("plan", plan, destinations=("dispatch", "respond"))
    g.add_node("dispatch", dispatch, destinations=("sql_agent", "rag_agent", "review", "report"))
    g.add_node("sql_agent", build_sql_agent())  # compiled subgraph as a node (shares keys with parent)
    g.add_node("rag_agent", rag_agent)
    g.add_node("collect", collect, destinations=("dispatch",))
    g.add_node("review", review, destinations=("dispatch", "report"))
    g.add_node("report", report, destinations=("approve_export", END))
    g.add_node("approve_export", approve_export)
    g.add_node("respond", respond)

    g.add_edge(START, "plan")
    g.add_edge("sql_agent", "collect")
    g.add_edge("rag_agent", "collect")
    g.add_edge("approve_export", END)
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)
