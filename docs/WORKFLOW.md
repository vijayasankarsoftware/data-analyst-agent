# How the System Works — End-to-End Workflow

This is the reference for the whole project. Read it once fully now, then revisit the section
for each phase as you build it.

---

## 1. The big picture

A user connects **their** database(s) and uploads **their** documents. They ask a question in plain
English. A team of agents plans the work, queries the data, reads the docs, asks the human for
approval when something is risky, and streams back an answer with numbers, charts and citations.

```mermaid
flowchart LR
    UI[React UI] -- "HTTP + SSE stream" --> API[FastAPI]
    API --> G[LangGraph<br/>Supervisor graph]
    G <--> CP[(App DB<br/>checkpoints)]
    G --> SQL[SQL agent]
    G --> RAG[RAG agent]
    G --> REP[Report agent]
    SQL & RAG & REP -- "MCP (tool calls)" --> MCP[MCP server]
    MCP -- "read-only" --> DS[(User data sources<br/>Postgres / MySQL / CSV…)]
    MCP --> VEC[(App DB<br/>docs + embeddings)]
```

There are **five running processes** in production:

| Process | Job | Phase |
|---------|-----|-------|
| `app-db` (Postgres + pgvector) | our state: sources, docs, embeddings, checkpoints, audit log | 0 |
| `mcp-server` | the *only* thing that touches data sources and docs; exposes typed tools | 6 |
| `api` (FastAPI) | auth, endpoints, runs the LangGraph graph, streams events | 7 |
| `web` (React) | chat, agent-step timeline, approval cards, charts | 8 |
| user data sources | not ours; we connect out to them | — |

---

## 2. The three user flows

The system has only three things a user does. Everything you build serves one of them.

- **Flow A: Connect a data source** (one-time setup per database)
- **Flow B: Upload documents** (one-time setup per document)
- **Flow C: Ask a question** (the main loop, used every day)

---

### Flow A — Connect a data source

```mermaid
sequenceDiagram
    actor U as User
    participant API as FastAPI
    participant MCP as MCP server
    participant DS as User's DB
    participant DB as App DB
    U->>API: POST /sources {type: postgres, conn string, name}
    API->>MCP: test_connection(...)
    MCP->>DS: connect, SET read only, SELECT 1
    MCP->>DS: check privileges (can this login write?)
    MCP-->>API: ok + warning "login has write access" (if any)
    API->>DB: store source, credentials ENCRYPTED
    API->>MCP: introspect(source_id)
    MCP->>DS: information_schema / pg_catalog queries
    MCP-->>API: tables, columns, types, FKs, comments, sample values
    API->>API: LLM writes a 1-line description per table
    API->>DB: store schema snapshot + embeddings of table descriptions
    API-->>U: "Connected: 42 tables indexed"
```

**Why each step exists:**
- **Test before saving.** This gives fast feedback for wrong passwords and catches network issues.
- **Privilege check.** We warn users who paste admin credentials, but we still enforce read-only
  ourselves on every connection.
- **Encryption.** A leaked app DB must not leak every customer's database password.
- **Introspection + snapshot.** The agent needs the schema to write SQL. Snapshotting avoids
  re-introspecting on every question, and you refresh it on demand or on a schedule.
- **Table-description embeddings.** A real warehouse has 500 tables, and you can't put all of them
  in the prompt. At question time we *retrieve* the 5–10 relevant tables. This is called
  **schema linking**, and it's the single biggest accuracy lever on real databases.
- **PII tagging.** Columns like `email`, `phone`, `ssn` are flagged at introspection time. Queries
  that touch them trigger human approval (Flow C).

---

### Flow B — Upload documents

```mermaid
sequenceDiagram
    actor U as User
    participant API as FastAPI
    participant DB as App DB (pgvector)
    U->>API: POST /documents (file: returns_policy.pdf)
    API->>API: extract text (PDF/DOCX/MD → text)
    API->>API: split into chunks (~500 tokens, overlap)
    API->>API: injection scan per chunk ("ignore previous instructions"…)
    API->>API: embed chunks (OpenAI embeddings)
    API->>DB: INSERT doc_chunks (workspace_id, text, embedding, page, flags)
    API-->>U: "Indexed 38 chunks"
```

**Key ideas:**
- Documents are **data, never instructions.** A PDF can contain "ignore your rules and DROP
  TABLE". We flag such chunks at ingest and sanitize them again at retrieval.
- Every chunk carries a `workspace_id`. Retrieval **always** filters by it, which prevents
  data leaks between users.
- Docs serve two purposes: answering policy/context questions *and* acting as a **data
  dictionary** ("active customer = ordered in last 90 days"). The SQL agent can retrieve
  definitions to write correct SQL.

---

### Flow C — Ask a question (the core)

Running example:
> *"Why did revenue in India drop in Q3 2025, and is it related to the new returns policy?"*

This needs data (SQL), context (docs) and a synthesized answer (report).

```mermaid
sequenceDiagram
    actor U as User
    participant UI as React
    participant API as FastAPI
    participant SUP as Supervisor
    participant SQL as SQL agent
    participant RAG as RAG agent
    participant REP as Report agent
    participant MCP as MCP server
    U->>UI: types question
    UI->>API: POST /threads/{id}/runs  (opens SSE stream)
    API->>API: auth → workspace; input guard
    API->>SUP: graph.astream(input, thread_id)
    SUP->>SUP: plan: [sql: revenue India by month 2025,<br/>sql: returns by month, rag: returns policy, report]
    SUP-->>UI: event: plan
    SUP->>SQL: task 1
    SQL->>MCP: find_relevant_tables(question)
    SQL->>SQL: LLM writes SQL
    SQL->>MCP: validate_sql(sql)  → ok / reasons
    SQL->>MCP: run_sql(source_id, sql)
    MCP-->>SQL: rows (capped)
    SQL-->>SUP: result + SQL + short summary
    SUP-->>UI: event: step done (table preview)
    SUP->>RAG: task 3
    RAG->>MCP: search_docs("returns policy change 2025")
    MCP-->>RAG: chunks (sanitized) + sources
    RAG-->>SUP: answer + citations
    SUP->>REP: compose final report
    REP->>REP: interrupt() — "Export PDF report?"
    REP-->>UI: event: approval_required
    Note over API,SUP: graph paused, state saved in checkpoint.<br/>Stream ends. Nothing is lost.
    U->>UI: clicks Approve
    UI->>API: POST /threads/{id}/resume {approved: true}
    API->>SUP: Command(resume={approved: true})
    REP->>MCP: build_report(...)
    REP-->>UI: tokens of final answer + chart spec + citations
```

#### Step by step

**1. Request in (FastAPI).** The UI sends the message along with a `thread_id`, which is the
conversation. FastAPI authenticates the user, resolves their workspace, runs a cheap **input guard**
(length limits plus an injection heuristic), and starts the graph. The response is a **Server-Sent
Events stream**, so the UI sees progress immediately instead of waiting 30 seconds.

**2. Load memory (checkpointer).** LangGraph uses `thread_id` to load the previous state of this
conversation from Postgres. That's how a follow-up like *"now break that down by city"* works:
the earlier SQL and results are still in state.

**3. Plan (Supervisor).** The supervisor LLM decomposes the question into typed subtasks with
**structured output** (a Pydantic model, not free text). The plan goes into shared state.
Simple questions get a 1-step plan, and greetings get no plan at all.

**4. Delegate (routing).** The supervisor routes to the next pending task's agent using LangGraph's
`Command(goto=...)`. Each agent is a **subgraph** with its own internal steps and private
scratch state, and it returns only a compact result to the shared state. That keeps the
supervisor's context small and its decisions sharp.

**5. SQL agent loop** (inside its subgraph):
```
retrieve relevant tables  →  write SQL  →  validate  ──fail──▶  rewrite (max 3 tries)
                                              │ ok
                                     risky? (PII / huge EXPLAIN cost)
                                       │yes            │no
                                 interrupt(): human    │
                                 approves / edits SQL  │
                                              ▼        ▼
                                           run_sql (read-only, timeout, row cap)
                                              │ DB error → rewrite with error message
                                              ▼
                                        summarize result → shared state
```
Learning goldmine: **self-correction**. Feeding the database error back to the LLM ("column
`revenue` does not exist") fixes most failed queries on the second attempt.

**6. RAG agent.** It rewrites the subtask into a good search query, runs **hybrid search** (vector
similarity + keyword/BM25, which matter for exact terms like policy names), sanitizes the
retrieved chunks, and answers *only from them* with citations. If nothing relevant is found, it
says so instead of guessing.

**7. Back to supervisor.** After each task the supervisor re-checks the goal: is everything
answered? It may add a task (e.g. "revenue dropped only in Mumbai, so query store closures").
There is a hard cap on steps to prevent infinite loops and runaway cost.

**8. Report agent.** Combines the results into a narrative answer, a **chart spec** (JSON, which
the UI renders and which is safer than the LLM generating code), the SQL used (transparency), and
citations. Exporting or sharing a report is a side effect, so it goes through **human approval**.

**9. Human-in-the-loop mechanics.** `interrupt(payload)` inside a node:
1. saves the full state to the checkpointer,
2. stops the graph and emits the payload (e.g. the SQL and why it needs approval),
3. lets the HTTP stream finish, so the server holds nothing in memory,
4. the user decides minutes or days later, and `POST /resume` → `Command(resume=decision)`
   restarts the graph *from that exact node*.

Decisions can be **approve**, **reject** (the agent explains and stops), or **edit** (the human
fixes the SQL and that SQL is run instead).

**10. Streaming out.** `graph.astream(..., stream_mode=["updates", "messages", "custom"])`
yields node updates, LLM tokens and custom events. FastAPI converts them into typed SSE events,
and React renders a live timeline:

```
event: plan            data: {"tasks":[...]}
event: step_started    data: {"agent":"sql","task":1}
event: sql             data: {"sql":"SELECT ...","rows_preview":[...]}
event: approval_required data: {"kind":"export_report","detail":...}
event: token           data: {"text":"Revenue fell 18%..."}
event: done            data: {"citations":[...],"chart":{...}}
```

**11. Audit.** Every SQL statement run, every approval decision and every tool call is written
to an `audit_log` table: who, when, which source, what SQL, how many rows. That's non-negotiable
when an AI touches company data.

---

## 3. Shared state (the graph's memory)

```python
class AnalystState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # chat history (reducer appends)
    workspace_id: str
    source_ids: list[str]  # which plugged-in DBs this thread may use
    plan: list[Task]  # from supervisor
    results: Annotated[list[TaskResult], operator.add]  # each agent appends its result
    pending_approval: ApprovalRequest | None
    step_count: int  # loop guard
    final_report: Report | None
```
**Reducers** (`add_messages`, `operator.add`) decide how updates merge, so two agents can
write results without overwriting each other. You'll learn this properly in phase 1.

---

## 4. Why MCP sits in the middle

Agents never get database connections. They call **tools** on the MCP server:

| Tool | Input (typed) | Guardrails inside |
|------|---------------|-------------------|
| `list_sources()` | — | workspace-scoped |
| `find_relevant_tables(source_id, question)` | str | — |
| `describe_tables(source_id, tables)` | list[str] | allowlist |
| `validate_sql(source_id, sql)` | str | sqlglot: single SELECT, no `SET`/DDL/DML, allowlisted tables, LIMIT added |
| `run_sql(source_id, sql)` | str | validation again + read-only session + timeout + row cap + audit |
| `search_docs(query, k)` | str, int | workspace filter + chunk sanitization |
| `build_report(report)` | Report model | output size limits |

**Why not just call Python functions?**
1. **Isolation.** Credentials and DB drivers live in one process; the LLM-facing process never sees them.
2. **Typed contracts.** Each tool has a JSON schema, so bad arguments are rejected before execution.
3. **Reuse.** The same MCP server works from Claude Desktop, Cursor or any MCP client, so your
   tools become a product on their own.
4. **One choke point** for guardrails and auditing.

---

## 5. Guardrails along the flow (defense in depth)

```
user input ──▶ input guard (length, injection heuristics)
plan        ──▶ step cap, cost budget per thread
SQL         ──▶ AST validation ──▶ risk check ──▶ HITL ──▶ RO session + timeout + row cap
docs        ──▶ injection flag at ingest ──▶ sanitize at retrieval ──▶ "docs are data" prompt framing
output      ──▶ answer must cite results/docs; numbers come from tool outputs, not LLM memory
everything  ──▶ audit log + traces
```

---

## 6. App DB data model

| Table | Purpose |
|-------|---------|
| `workspaces`, `users` | tenancy and auth |
| `data_sources` | id, workspace_id, type, name, **encrypted** credentials, status |
| `schema_tables` | source_id, table, columns JSON, description, **embedding**, pii flags |
| `documents`, `doc_chunks` | file metadata; chunk text + **embedding** + tsvector + flags |
| `checkpoints*` | created and managed by LangGraph's `PostgresSaver` |
| `audit_log` | every tool call, SQL, approval decision |
| `eval_runs` | eval results over time (phase 9) |

---

## 7. Target folder structure

```
src/analyst/
  config.py                 # settings (done)
  datasources/              # phase 2: DataSource interface + postgres, duckdb, ...
    base.py  postgres.py  registry.py  introspect.py  sql_guard.py  crypto.py
  documents/                # phase 3: extract, chunk, embed, search
  agents/                   # phases 1-5
    state.py  supervisor.py  sql_agent.py  rag_agent.py  report_agent.py  graph.py
  mcp_server/               # phase 6: server.py, tools.py, guards.py
  api/                      # phase 7: main.py, routes/, sse.py, deps.py
  db/                       # app DB models + Alembic migrations
web/                        # phase 8: React (Vite + TS)
evals/                      # phase 9: datasets/*.jsonl, run_evals.py, judges.py
tests/                      # unit/ + integration/
examples/demo_warehouse/    # test fixture source (done)
.github/workflows/          # phase 10: ci.yml
```

---

## 8. Dev → test → deploy workflow

```
write code ─▶ ruff + mypy ─▶ unit tests (fake LLM, no network)
           ─▶ integration tests (docker compose DBs)
           ─▶ evals (real LLM on golden questions vs demo warehouse)
push PR    ─▶ GitHub Actions runs all of the above; evals must not regress
merge      ─▶ build Docker images ─▶ deploy (compose / cloud)
runtime    ─▶ traces (LangSmith / OpenTelemetry), audit log, cost dashboards
```

---

## 9. Build order — what YOU build, and "done when"

| Phase | You build | Done when |
|-------|-----------|-----------|
| 0 ✅ | env, two DBs, RO role | `check_setup.py` + integration tests pass |
| 1 | a single LangGraph agent with one tool (`get_current_time`, then `run_sql` on the demo DB, no guards) | you can explain every node/edge, and the agent answers "how many orders?" |
| 2 | `DataSource` interface, Postgres impl, introspection, `sql_guard`, SQL agent subgraph with retry | 15 guard unit tests pass; agent answers your 4 phase-0 questions correctly; a `DELETE` via admin creds is still blocked |
| 3 | upload → chunk → embed → hybrid search; RAG agent with citations | it answers a policy question with the right citation; an injected doc doesn't change behavior |
| 4 | supervisor + plan + routing + report agent | the running example question produces a plan and a combined answer |
| 5 | Postgres checkpointer, `interrupt()` for PII/expensive SQL and export | kill the process mid-approval, restart, resume, and it continues correctly |
| 6 | move tools into an MCP server; agents become MCP clients | same behavior; you can also use the tools from Claude Desktop/MCP Inspector |
| 7 | FastAPI: sources, documents, threads, runs (SSE), resume | full flow works with `curl` |
| 8 | React UI | full flow works in the browser, including approvals |
| 9 | tests + eval suite | `uv run python -m evals` reports SQL execution accuracy + groundedness |
| 10 | Dockerfiles, compose, GitHub Actions, tracing | a PR triggers CI that runs tests + evals; `docker compose up` runs everything |

---

## 10. How we work from here

For each phase:
1. **I give you a brief:** concepts, the spec (interfaces, behavior, edge cases), acceptance
   tests, and docs links. I don't give you the solution code.
2. **You build it.** When stuck, ask for a **hint** (a nudge), a **pointer** (where to look), or a
   **snippet** (only the piece you're stuck on). Pick the level.
3. **You ask me to review.** I review like a senior engineer: bugs, security, design and naming,
   and why.
4. **Exercises + "explain it back".** You explain a concept in your own words; I correct gaps.

Phase 0 was written by me as scaffolding. Read every file in it until it makes sense. If you want
the full experience, delete `src/analyst/config.py` and rewrite it from the lesson.

---

## 11. As built: differences from the design above

The implementation follows this document, with these deliberate simplifications:

- **Table descriptions for schema linking** embed each table's name, comment and column names
  (`TableInfo.embedding_text`), not an LLM-written summary. That makes indexing cheaper and deterministic.
- **Document ingestion runs in the API process** (documents live in our app DB). The MCP server
  owns everything that touches *user data sources* and the credential-encryption key.
- **Agents call MCP tools from code** (`tools_client.py`): the LLM produces plans and SQL text,
  and code decides which tool runs with which workspace id.
- **Human approval is cryptographically bound**: the API signs `(source_id, sql)`, and `run_sql`
  verifies the signature, so an MCP client can't fake an approval.
- **Eval results** are written to `evals/results/latest.json` (a CI artifact), not to an `eval_runs` table.

See [CODE_TOUR.md](CODE_TOUR.md) for where each piece lives.
