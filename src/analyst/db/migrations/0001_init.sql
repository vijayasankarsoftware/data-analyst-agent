-- App DB schema. Everything here is OURS; user business data never lands here
-- (except bounded previews inside conversation checkpoints).

CREATE EXTENSION IF NOT EXISTS vector;

-- Tenancy -------------------------------------------------------------------
CREATE TABLE workspaces (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    key_hash      text NOT NULL UNIQUE,          -- sha256 of the key; the key itself is shown once
    label         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz
);

-- Plugged-in data sources ----------------------------------------------------
CREATE TABLE data_sources (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name          text NOT NULL,
    kind          text NOT NULL,                 -- 'postgres', 'duckdb', ... (connector registry)
    credentials   bytea NOT NULL,                -- Fernet-encrypted JSON
    description   text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'error')),
    error         text,
    warnings      text[] NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    refreshed_at  timestamptz,
    UNIQUE (workspace_id, name)
);

-- Schema snapshot of each source, one row per table, embedded for schema linking.
CREATE TABLE schema_tables (
    id            bigserial PRIMARY KEY,
    source_id     uuid NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    schema_name   text NOT NULL,
    table_name    text NOT NULL,
    info          jsonb NOT NULL,                -- serialized TableInfo (columns, keys, samples)
    pii_columns   text[] NOT NULL DEFAULT '{}',
    embedding     vector(1536),
    UNIQUE (source_id, schema_name, table_name)
);

-- Documents -----------------------------------------------------------------
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    filename        text NOT NULL,
    content_type    text,
    size_bytes      int NOT NULL,
    n_chunks        int NOT NULL DEFAULT 0,
    flagged_chunks  int NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE doc_chunks (
    id                 bigserial PRIMARY KEY,
    document_id        uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    workspace_id       uuid NOT NULL,            -- denormalized so every search filters cheaply
    chunk_index        int NOT NULL,
    page               int,
    content            text NOT NULL,
    injection_flag     boolean NOT NULL DEFAULT false,
    injection_reasons  text[] NOT NULL DEFAULT '{}',
    tsv                tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding          vector(1536)
);
CREATE INDEX doc_chunks_workspace_idx ON doc_chunks (workspace_id);
CREATE INDEX doc_chunks_tsv_idx ON doc_chunks USING gin (tsv);
CREATE INDEX doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- Conversations & outputs ----------------------------------------------------
-- LangGraph's PostgresSaver creates its own checkpoint tables at startup.
CREATE TABLE threads (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    title         text NOT NULL DEFAULT 'New analysis',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reports (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    thread_id     uuid REFERENCES threads (id) ON DELETE SET NULL,
    title         text NOT NULL,
    content_md    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Audit: every SQL run, approval decision and admin action -------------------
CREATE TABLE audit_log (
    id            bigserial PRIMARY KEY,
    workspace_id  uuid,
    thread_id     uuid,
    actor         text NOT NULL,                 -- 'agent', 'user', 'system'
    action        text NOT NULL,                 -- 'run_sql', 'sql_rejected', 'approval', ...
    source_id     uuid,
    detail        jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_workspace_idx ON audit_log (workspace_id, created_at DESC);
