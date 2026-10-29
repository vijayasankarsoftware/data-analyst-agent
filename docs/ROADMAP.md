# Learning Roadmap — Multi-Agent Data Analyst with HITL

> **Status:** all phases are implemented. Use [CODE_TOUR.md](CODE_TOUR.md) to study each phase's
> code and [TESTING.md](TESTING.md) to verify it.

Each phase follows the same loop:

1. **Concept** — what problem the technology solves and the mental model.
2. **Build** — the code for this phase, explained line by line in `docs/phases/NN-*.md`.
3. **Exercises** — things *you* change or break, to prove you understand it.
4. **Production notes** — what changes when real users and real data show up.

Don't move on until the exercises feel easy. Every phase leaves the system runnable.

---

## The target architecture

```
React UI  ──SSE stream──▶  FastAPI (async)
                              │
                              ▼
                     LangGraph Supervisor ◀──▶ Postgres checkpointer (state, HITL pauses)
                     /        |         \
               SQL agent   RAG agent   Report agent
                     \        |         /
                      ▼       ▼        ▼
                     MCP server (typed tools + guardrails)
                      │        │          │
             run_sql (RO)  search_docs  build_report
                      │        │
          any plugged-in DB   app DB (pgvector:
          (Postgres, MySQL,    docs users uploaded,
           Snowflake, CSV…)    checkpoints, registry)
```

---

## Design principle: a general agent, not a one-database bot

The agent must work with **any database and any documents a user plugs in**. So:

- **Nothing about the user's data is hardcoded.** Tables, columns, relationships and business
  terms are *discovered at runtime* (schema introspection) or *learned from uploaded docs* (RAG).
- **Two kinds of database:**
  - **App DB** (ours): source registry, encrypted credentials, document chunks + embeddings,
    checkpoints. Fixed schema we own and migrate.
  - **Data sources** (theirs): untrusted, unknown schema, possibly huge. We only ever read.
- **Connectors behind an interface.** `DataSource.introspect()`, `DataSource.run_readonly(sql)`.
  Postgres first; MySQL, SQLite, Snowflake, BigQuery and CSV/Parquet (via DuckDB) are new
  implementations of the same interface. `sqlglot` handles SQL dialect differences.
- **Safety can't depend on the user configuring things right.** We *recommend* a read-only
  login, but we *enforce* read-only sessions, SQL validation and timeouts on our side anyway.
- **Seeded demo warehouse = test fixture.** It's plugged in exactly like a user database.
  It exists because evals need known correct answers, and CI can't reach anyone's real DB.

---

## Phases

| # | Phase | Key concepts you'll learn |
|---|-------|---------------------------|
| 0 | **Foundations** | Project layout (`src/`), uv, pydantic-settings, Docker Compose, app DB vs. data sources, least-privilege roles, a demo source as test fixture |
| 1 | **LangGraph fundamentals** | `StateGraph`, nodes, edges, conditional edges, state reducers, the agent loop (LLM ↔ tools) |
| 2 | **Pluggable data sources + SQL agent** | `DataSource` interface, source registry + encrypted credentials, runtime schema introspection, schema linking for large schemas (pick relevant tables), dialect-aware text-to-SQL, `sqlglot` validation (read-only, allowlists, forced LIMIT), self-correction on errors |
| 3 | **Pluggable documents + RAG agent** | Upload pipeline (PDF/MD/DOCX → text), chunking, embeddings, pgvector, per-workspace isolation, hybrid search, citations, using docs as a data dictionary for SQL |
| 4 | **Supervisor (hierarchical multi-agent)** | Query decomposition, routing with `Command`, shared vs. private state, subgraphs, the report agent |
| 5 | **Checkpointing + Human-in-the-loop** | `PostgresSaver`, `thread_id`, `interrupt()`, resuming with `Command(resume=...)`, time travel |
| 6 | **MCP server** | Model Context Protocol, FastMCP, typed tool schemas, consuming MCP tools from LangGraph, prompt-injection filtering |
| 7 | **FastAPI serving** | async endpoints, lifespan, connection pools, streaming with SSE + `astream`, HITL approve/reject endpoints |
| 8 | **React streaming UI** | Vite + TypeScript, consuming SSE, rendering partial tokens and agent steps, approval UI |
| 9 | **Testing + LLM evals** | Unit tests with fake LLMs, integration tests against Postgres, eval datasets, execution accuracy, LLM-as-judge, regression gates |
| 10 | **Docker + CI/CD + production hardening** | Multi-stage Dockerfiles, compose profiles, GitHub Actions (lint, type check, tests, evals), tracing/observability, cost and rate limits, secrets |

---

## Responsible-AI guardrails (built progressively)

| Guardrail | Layer | Phase |
|-----------|-------|-------|
| Read-only DB role (recommended to users, demonstrated on the demo source) | User's database | 0 |
| Enforced read-only session + `statement_timeout` on every connection we open | Connector | 2 |
| SQL AST validation (single SELECT, allowlisted tables, forced LIMIT, no `SET`) | Application | 2 |
| Credentials encrypted at rest, never shown to the LLM | App DB | 2 |
| Retrieved-document sanitization and prompt-injection detection | Application | 3, 6 |
| Typed tool inputs and outputs (Pydantic) | MCP | 6 |
| Human approval before expensive or sensitive actions | Graph | 5 |
| Evals that fail CI when safety or accuracy regresses | CI | 9 |

**Defense in depth:** each layer assumes the one above it has already failed.
