-- The APP database: our own state, never user business data.
-- Holds: registered data sources, uploaded document chunks + embeddings (pgvector),
-- and LangGraph checkpoints. Tables are added by migrations in later phases.
CREATE EXTENSION IF NOT EXISTS vector;




