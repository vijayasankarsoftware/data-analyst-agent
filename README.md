# Multi-Agent Data Analyst with Human-in-the-Loop

A general-purpose data analyst agent: plug in **any database** (Postgres, CSV/Parquet/DuckDB, more
via one connector class) and **any documents** (PDF, DOCX, Markdown), then ask questions in plain
English. A supervisor plans the work, specialist agents query data and read documents, risky
steps pause for human approval, and answers stream back with tables, charts and citations.

**Stack:** Python · LangGraph · MCP · FastAPI · PostgreSQL + pgvector · React · Docker · GitHub Actions

| Doc | What's in it |
|-----|--------------|
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | How the system works end to end (diagrams for every flow) |
| [docs/CODE_TOUR.md](docs/CODE_TOUR.md) | Where each concept lives in the code, in reading order |
| [docs/TESTING.md](docs/TESTING.md) | Step-by-step manual test plan |
| [docs/ROADMAP.md](docs/ROADMAP.md) | The phases, concepts and guardrails |

---

## Quickstart (local dev)

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+, an OpenAI-compatible chat endpoint
(`OPENAI_BASE_URL`/`OPENAI_MODEL`) and an OpenAI-compatible embedding endpoint (`EMBEDDING_BASE_URL`,
default: self-hosted BAAI/bge-m3 on `127.0.0.1:8008`, 1024 dims).

```bash
# 1. config
cp .env.example .env              # fill in passwords + LLM/embedding endpoints
uv run analyst gen-secrets        # paste the 3 printed lines into .env

# 2. databases (app DB on :5433, demo warehouse on :5434)
docker compose up -d --wait

# 3. backend
uv sync
uv run analyst create-workspace demo     # prints your API key: save it
uv run analyst seed-demo demo            # connects the demo warehouse + uploads demo docs
uv run uvicorn analyst.api.main:app --reload --port 8000

# 4. frontend (new terminal)
cd web && npm install && npm run dev     # http://localhost:5173, paste the API key
```

Tools run **in-process** by default (`MCP_URL` empty). To run the MCP server as its own
process like production:

```bash
uv run python -m analyst.mcp_server     # listens on :8001
# then in .env: MCP_URL=http://localhost:8001/mcp  and restart the API
```

## Whole stack in Docker

```bash
docker compose --profile app up -d --build --wait
docker compose exec api analyst create-workspace demo
docker compose exec api analyst seed-demo demo
open http://localhost:8080
```

## Tests and evals

```bash
uv run pytest -m "not integration"     # unit tests, no services needed
uv run pytest                          # + integration tests (needs docker compose DBs)
uv run python -m evals.run             # agent evals with the real LLM (costs a few cents)
uv run python -m evals.run --only sql --judge
```

## Connecting your own data

- **Postgres:** Sources page → kind `postgres` → `postgresql://readonly_user:pw@host:5432/db`.
  From inside Docker, use `host.docker.internal` instead of `localhost`.
- **CSV / Parquet / DuckDB:** put files in `./data/`, then kind `duckdb` → `files: sales.csv, …`.
- **Documents:** Documents page → upload. Data dictionaries make SQL answers noticeably better.
- **New database type:** subclass `DataSource` in `src/analyst/datasources/` and add one line to
  `registry.py`.
