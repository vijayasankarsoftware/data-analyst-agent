# Code Tour

Read in this order. Each stop names the concept and the file, so you can learn the system
one idea at a time.

## 1. Config and the app database
| Concept | File |
|---------|------|
| Typed settings, secrets, limits | `src/analyst/config.py` |
| Connection pool (shared by API, MCP, checkpointer) | `src/analyst/db/pool.py` |
| Forward-only migrations + advisory lock | `src/analyst/db/migrate.py`, `db/migrations/0001_init.sql` |
| Audit log | `src/analyst/db/audit.py` |

## 2. Pluggable data sources
| Concept | File |
|---------|------|
| The connector interface (`DataSource`, `TableInfo`) | `src/analyst/datasources/base.py` |
| Postgres: read-only sessions, timeouts, introspection, EXPLAIN | `src/analyst/datasources/postgres.py` |
| DuckDB: CSV/Parquet with external access locked down | `src/analyst/datasources/duckdb_source.py` |
| Connector registry (add new DB types here) | `src/analyst/datasources/registry.py` |
| **SQL guard** (sqlglot AST validation) | `src/analyst/datasources/sql_guard.py` |
| PII column detection | `src/analyst/datasources/pii.py` |
| Encrypted source registry + schema snapshots + schema-linking search | `src/analyst/datasources/store.py` |

## 3. Documents (RAG)
| Concept | File |
|---------|------|
| PDF/DOCX/MD → text | `src/analyst/documents/extract.py` |
| Chunking with overlap | `src/analyst/documents/chunk.py` |
| Ingest: injection scan + embeddings | `src/analyst/documents/ingest.py` |
| **Hybrid search** (pgvector + full-text + RRF) | `src/analyst/documents/search.py` |

## 4. Security
| Concept | File |
|---------|------|
| Prompt-injection heuristics + untrusted-content fencing | `src/analyst/security/injection.py` |
| Signed human approvals (HMAC) | `src/analyst/security/approval.py` |
| Credential encryption, API-key hashing | `src/analyst/security/crypto.py` |

## 5. MCP
| Concept | File |
|---------|------|
| Typed tools: the only code that touches user data | `src/analyst/mcp_server/tools.py` |
| MCP server registration | `src/analyst/mcp_server/server.py` |
| Streamable HTTP + bearer auth | `src/analyst/mcp_server/__main__.py` |
| Client used by agents/API (HTTP or in-process) | `src/analyst/tools_client.py` |

## 6. Agents (LangGraph)
| Concept | File |
|---------|------|
| Shared state, reducers, reset marker | `src/analyst/agents/state.py` |
| All prompts (security rules first) | `src/analyst/agents/prompts.py` |
| **Supervisor**: plan, dispatch with `Command`, collect, review | `src/analyst/agents/supervisor.py` |
| **SQL subgraph**: schema linking, self-correction, `interrupt()` approval | `src/analyst/agents/sql_agent.py` |
| **RAG agent**: grounded answers, verified citations | `src/analyst/agents/rag_agent.py` |
| **Report agent**: tagged token streaming, chart spec, export approval | `src/analyst/agents/report_agent.py` |
| Wiring it together | `src/analyst/agents/graph.py` |
| Progress events (`stream_mode="custom"`) | `src/analyst/agents/events.py` |

## 7. API
| Concept | File |
|---------|------|
| Lifespan: migrations, Postgres checkpointer, graph | `src/analyst/api/main.py` |
| API-key auth → workspace, thread ownership | `src/analyst/api/deps.py` |
| **LangGraph stream → SSE** | `src/analyst/api/streaming.py` |
| Runs, resume (HITL), per-thread locking | `src/analyst/api/routes/threads.py` |
| Sources / documents / reports | `src/analyst/api/routes/*.py` |
| Admin CLI | `src/analyst/cli.py` |

## 8. Frontend
| Concept | File |
|---------|------|
| fetch + SSE parser (POST streams) | `web/src/api.ts` |
| Event reducer: SSE events → UI state | `web/src/pages/Chat.tsx` (`applyEvent`) |
| Approval card (approve / edit / reject) | `web/src/components/ApprovalCard.tsx` |
| Chart spec → Recharts | `web/src/components/ChartView.tsx` |

## 9. Quality
| Concept | File |
|---------|------|
| Unit tests (guard, security, chunking, reducers) | `tests/unit/` |
| Integration tests (read-only even with admin creds) | `tests/integration/` |
| **Evals**: execution accuracy, RAG, safety, LLM-as-judge | `evals/run.py`, `evals/datasets/*.jsonl` |
| CI/CD | `.github/workflows/ci.yml` |
| Containers | `Dockerfile`, `web/Dockerfile`, `web/nginx.conf`, `docker-compose.yml` |

## Design decisions worth explaining in an interview
1. **Agents call tools from code, not free-form tool calling.** The LLM decides *what* (plan, SQL text);
   code decides *how* (which tool, which workspace). The LLM can't pick another workspace's id or skip validation.
2. **Validation runs twice** (`validate_sql` before approval, again inside `run_sql`): never trust the caller.
3. **Approval tokens bind to the exact SQL.** Approving one query can't be replayed for another.
4. **The subgraph returns only `last_result`.** Private retries and errors don't bloat the supervisor's context.
5. **Chart specs, not chart code.** The UI renders a validated JSON spec, so nothing generated gets executed.
6. **Execution accuracy over string match** in evals: many different SQL queries are correct.
