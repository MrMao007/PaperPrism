-- 0005_embeddings.sql — sqlite-vec virtual tables for paper embeddings.
-- Requires pysqlite3 (supports load_extension) + sqlite-vec extension.

-- User library embeddings (abstract only, bge-small-en-v1.5, 384-dim).
-- paper_id references papers.id; removed_at tracks soft-deleted papers
-- so embeddings can be lazily cleaned up.
CREATE VIRTUAL TABLE IF NOT EXISTS paper_embeddings USING vec0(
    paper_id INTEGER,
    embedding FLOAT[384]
);

-- arXiv feed embeddings for recommendation / map overlay.
-- 30-day TTL is enforced by application logic, not the schema.
CREATE VIRTUAL TABLE IF NOT EXISTS arxiv_feed_embeddings USING vec0(
    arxiv_id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
