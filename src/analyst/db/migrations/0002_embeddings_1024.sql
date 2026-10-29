-- Switch embeddings from 1536 (OpenAI text-embedding-3-small) to 1024 (BAAI/bge-m3).
--
-- Vectors from different models live in different spaces, so old vectors are useless with
-- the new model: they're cleared, not converted. Rebuild them with:  uv run analyst reembed

DROP INDEX IF EXISTS doc_chunks_embedding_idx;

UPDATE doc_chunks SET embedding = NULL;
UPDATE schema_tables SET embedding = NULL;

ALTER TABLE doc_chunks    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE schema_tables ALTER COLUMN embedding TYPE vector(1024);

CREATE INDEX doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);
