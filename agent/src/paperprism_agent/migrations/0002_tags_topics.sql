-- Tags & topics (v0.3)
--
-- Design:
--   `tags` is a global, normalised tag dictionary (lowercase + hyphenated name).
--   `paper_tags` is the many-to-many glue; `source` tracks provenance so the UI
--       can render LLM vs user-added tags differently. `topic_id` remembers
--       which auto-tag run produced the tag (NULL for purely-manual tags).
--   `topics` is an aggregate entity created by a single auto-tag job. Deleting
--       a topic only detaches papers from the topic; tag rows themselves
--       survive (users can clean up manually).
--   `topic_papers` records membership + display order.
--
-- All constraints have ON DELETE semantics that match the no-orphan rule:
--   paper deleted -> cascade everywhere.
--   tag deleted   -> cascade from paper_tags (rare; tags are effectively append-only).
--   topic deleted -> cascade from topic_papers, but paper_tags.topic_id is SET NULL
--                    so the already-assigned labels survive.

CREATE TABLE IF NOT EXISTS tags (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE, -- normalised: lowercase, spaces->hyphens
    display_name TEXT,                                 -- original casing, for UI
    description  TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);

CREATE TABLE IF NOT EXISTS topics (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,  -- url-safe, e.g. "efficient-transformers-3a9f"
    name           TEXT NOT NULL,
    summary        TEXT,
    model          TEXT,                  -- provider/model that produced this topic
    source_job_id  TEXT,                  -- UUID of the auto-tag job (debug / audit)
    is_archived    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_created ON topics(created_at);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id   INTEGER NOT NULL,
    tag_id     INTEGER NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',   -- 'llm' | 'user'
    topic_id   INTEGER,                        -- NULL for user-added; set when produced by an auto-tag job
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, tag_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)   REFERENCES tags(id)   ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_tags_paper ON paper_tags(paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_tags_tag   ON paper_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_paper_tags_topic ON paper_tags(topic_id);

CREATE TABLE IF NOT EXISTS topic_papers (
    topic_id   INTEGER NOT NULL,
    paper_id   INTEGER NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (topic_id, paper_id),
    FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_topic_papers_paper ON topic_papers(paper_id);
