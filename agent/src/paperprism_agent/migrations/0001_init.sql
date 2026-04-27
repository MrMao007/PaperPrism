-- Initial schema for PaperPrism v0.2
-- Applied automatically by paperprism_agent.db.migrate() on startup.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- ============================================================================
-- papers: one row per ingested paper
-- ============================================================================
CREATE TABLE IF NOT EXISTS papers (
    id                      INTEGER PRIMARY KEY,
    full_id                 TEXT UNIQUE NOT NULL,        -- "2504.19413v1" or "cs.LG/0512001v2"
    arxiv_id                TEXT NOT NULL,               -- "2504.19413"
    version                 TEXT,                        -- "v1" | null
    is_legacy               INTEGER NOT NULL DEFAULT 0,

    -- enrichment fields (filled by P2.2 from arxiv API + PDF)
    title                   TEXT,
    authors_json            TEXT,                        -- '["Alice","Bob"]'
    first_author            TEXT,
    affiliations_json       TEXT,                        -- '["MIT","..."]'
    abstract                TEXT,
    arxiv_categories_json   TEXT,                        -- '["cs.LG","cs.CL"]'
    published_at            TEXT,                        -- ISO date
    updated_at_arxiv        TEXT,
    venue                   TEXT,                        -- journal-ref or comment
    code_url                TEXT,                        -- github.com/...

    -- filesystem
    pdf_path                TEXT NOT NULL,
    vault_dir               TEXT NOT NULL,
    source_url              TEXT,
    abs_url                 TEXT,
    sha256                  TEXT,
    size_bytes              INTEGER,

    -- lifecycle
    ingested_at             TEXT NOT NULL,
    enriched_at             TEXT,                        -- P2.2: arxiv+pdf done
    classified_at           TEXT,                        -- P2.3: LLM done
    classification_version  INTEGER NOT NULL DEFAULT 0   -- dims.yaml revision
);

CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_at);
CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(venue);

-- ============================================================================
-- classifications: long table, one row per (paper, dim_name, value)
--   enum        -> 1 row
--   multi_enum  -> N rows
--   number      -> 1 row, numeric_value filled
--   free_text   -> 1..N rows
-- ============================================================================
CREATE TABLE IF NOT EXISTS classifications (
    paper_id        INTEGER NOT NULL,
    dim_name        TEXT NOT NULL,
    value           TEXT NOT NULL,
    numeric_value   REAL,
    confidence      REAL,
    model           TEXT,
    classified_at   TEXT NOT NULL,
    PRIMARY KEY (paper_id, dim_name, value),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cls_dim_value ON classifications(dim_name, value);
CREATE INDEX IF NOT EXISTS idx_cls_paper ON classifications(paper_id);

-- ============================================================================
-- tasks: async work queue
-- ============================================================================
CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY,
    paper_id          INTEGER NOT NULL,
    kind              TEXT NOT NULL,       -- 'enrich' | 'classify' | 'reclassify'
    status            TEXT NOT NULL,       -- pending | running | ok | failed | dead
    attempts          INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    next_attempt_at   TEXT,                -- ISO; NULL means ready immediately
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_ready ON tasks(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_tasks_paper ON tasks(paper_id, kind);

-- ============================================================================
-- FTS5 full-text search on papers (title + abstract + venue)
-- ============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, venue,
    content='papers', content_rowid='id',
    tokenize='porter unicode61'
);

-- triggers keep fts in sync with papers
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, venue)
    VALUES (new.id, new.title, new.abstract, new.venue);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, venue)
    VALUES ('delete', old.id, old.title, old.abstract, old.venue);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, venue)
    VALUES ('delete', old.id, old.title, old.abstract, old.venue);
    INSERT INTO papers_fts(rowid, title, abstract, venue)
    VALUES (new.id, new.title, new.abstract, new.venue);
END;
