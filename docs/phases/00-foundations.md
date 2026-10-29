# Phase 0 — Foundations

**Goal:** a reproducible dev environment with the two kinds of database a general data agent
needs, and the first guardrail: a login that *physically cannot* modify data.

No LLM yet. Agents are only as safe and as useful as the environment you drop them into.

---

## 1. Concepts

### The agent is general, so separate "ours" from "theirs"

This agent is meant to work with **any** database or document set a user plugs in. That forces a
split that beginner projects usually miss:

| | **App DB** (`app-db`, port 5433) | **Data source** (e.g. `demo-warehouse`, port 5434) |
|---|---|---|
| Contains | source registry, document embeddings, checkpoints | the user's business data |
| Schema | fixed, ours, migrated by us | unknown until introspected at runtime |
| Access | our own code, normal app security | **untrusted**: read-only, timeouts, SQL validation |
| LLM sees credentials? | never | never (tools hold them, the LLM only names a source) |

If you put both in one database, a prompt-injected `SELECT * FROM checkpoints` could read
*another user's conversation*. Separating them makes that impossible.

### Why a seeded demo warehouse at all?

The demo warehouse is **not** what the agent is built around. The agent will discover its tables
the same way it discovers yours. It exists because:

1. **Evals need ground truth.** "Did the agent get revenue by category right?" can only be
   checked against a database where we already know the answer. `setseed(0.42)` makes the
   data identical on every machine, so the answer is fixed. (Text-to-SQL research works the
   same way: benchmarks like Spider and BIRD are fixed databases with known answers.)
2. **CI can't reach a user's database.** GitHub Actions spins this one up in seconds.
3. **Demos and onboarding.** New users try it before connecting their own data.

Notice that `scripts/check_setup.py` doesn't hardcode table names. It *discovers* them through
`information_schema`, which is a preview of phase 2's schema introspection.

### Why `src/` layout?
```
src/analyst/        <- the importable package (the general agent)
examples/           <- demo data sources; nothing in src/ may depend on them
tests/              <- imports `analyst` like any user would
```
With `src/`, Python can't accidentally import from the current directory, so tests run against
the *installed* package.

### Why `uv`?
`pyproject.toml` is what you *want*, `uv.lock` is what you *got* (exact versions + hashes, so
commit it), and `.python-version` pins the interpreter. Daily commands: `uv add`, `uv add --dev`,
`uv run`, `uv sync`.

### Why pydantic-settings? (`src/analyst/config.py`)
Config lives in the environment ([12-factor](https://12factor.net/config)). `Settings` validates
types and fails at boot if something is missing. `SecretStr` keeps passwords out of logs and
traces. **Note what's *not* in Settings: user data sources.** Those are runtime data (registered
through the API, stored in the app DB, phase 2), not deploy-time config.

### Least privilege on a data source (`examples/demo_warehouse/init/03_roles.sh`)
Text-to-SQL means **an LLM writes code that runs against someone's database**. The script shows
what we'll *recommend* users do:

| Setting | What it stops |
|---------|---------------|
| Only `SELECT` granted | INSERT/UPDATE/DELETE/DDL → `permission denied` |
| `default_transaction_read_only = on` | writes blocked even if someone mis-grants a privilege later |
| `statement_timeout = '5s'` | runaway cross joins can't take the DB down |

But a general agent **can't assume users followed the advice**. Someone will paste their admin
credentials. So in phase 2 the connector enforces safety on *every* connection it opens (a
read-only session, a timeout, and SQL validation), whatever the credentials allow.

---

## 2. Run it

```bash
docker compose up -d --wait
uv run python scripts/check_setup.py
uv run pytest -m integration -v
```

Explore the demo source as a user's agent would see it (password = `DEMO_DB_RO_PASSWORD` in `.env`):
```bash
psql "postgresql://analyst_ro@localhost:5434/demo_retail"
```

---

## 3. Exercises

1. **Write ground-truth queries.** As `analyst_ro`, answer the questions below. Save the
   question + SQL + result, because these become your first eval cases in phase 9.
   - Total revenue (excluding cancelled orders) per category in 2024.
   - Top 5 customers by lifetime revenue.
   - Return rate by channel.
   - Month-over-month revenue growth for 2025.
2. **Think like the agent.** Pretend you've never seen this schema. Using only
   `information_schema` and `pg_catalog`, write queries that list every table, its columns and
   types, its foreign keys and its comments. You're designing phase 2's `introspect()`.
3. **Attack the role.** Try `SET default_transaction_read_only = off;` then a DELETE. Why does
   it work or not? Then imagine the user had pasted *admin* credentials: what would stop a
   DELETE then? (Nothing yet. That's phase 2's job.)
4. **Plug in a second source.** Start any other Postgres DB you have (your local one on 5432
   works) and run the discovery queries from exercise 2 against it. Did they work unchanged?
   What would break on MySQL?

---

## 4. Production notes

- Users connect read **replicas** or warehouses (Snowflake, BigQuery), not production primaries.
- Stored source credentials are **encrypted at rest** (envelope encryption with a KMS key) and
  scoped to a workspace. Phase 2 builds a simple version of this.
- Network: in production the agent often can't reach a customer database directly, so you need
  allowlisted egress IPs, SSH tunnels or private links. That's worth knowing for interviews.
- Schema changes to the **app DB** go through migrations (Alembic). Data sources are never migrated
  by us.
