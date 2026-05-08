-- 0007_arxiv_feed_papers.sql — store metadata for daily arXiv feed papers.

-- Stores title, abstract and feed_date for papers fetched from arXiv RSS.
-- This complements the arxiv_feed_embeddings vec0 table (which only holds
-- arxiv_id + embedding) so the map API can return titles and filter by date.
CREATE TABLE IF NOT EXISTS arxiv_feed_papers (
    arxiv_id   TEXT PRIMARY KEY,
    title      TEXT,
    abstract   TEXT,
    feed_date  TEXT NOT NULL,   -- ISO date, e.g. '2026-05-08'
    created_at TEXT NOT NULL
);
