-- Memory Ledger L0 — append-only event log (v0.5).
--
-- Single source of truth for "what happened" across the agent. State tables
-- (papers / tags / topics) remain authoritative for "what is true now"; this
-- table is the side-channel audit trail. Every business write that mutates
-- a tracked subject MUST emit one event in the SAME transaction (see
-- events.EventLogger).
--
-- Hard rules:
--   * No UPDATEs, no DELETEs against this table outside of GDPR-style purges.
--   * `ts` is UTC ISO8601 with trailing 'Z'.
--   * `payload` and `related_ids` are JSON text; canonicalised by EventLogger
--     (sort_keys, compact separators, 16 KB cap).
--   * `actor` is one of {user, agent, llm, system}; the HTTP layer maps the
--     `X-PaperPrism-Actor` header (default: user).
--   * `subject_type` is one of {paper, tag, topic}.
--   * `schema_v` lets us evolve payload shape per event_type without a
--     migration; bump when adding REQUIRED keys.

CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT    NOT NULL,
  actor         TEXT    NOT NULL,
  event_type    TEXT    NOT NULL,
  subject_type  TEXT    NOT NULL,
  subject_id    TEXT    NOT NULL,
  related_ids   TEXT,
  payload       TEXT,
  schema_v      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_events_ts         ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_subject    ON events(subject_type, subject_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_type       ON events(event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_actor_type ON events(actor, event_type, ts DESC);

-- Soft-delete column for papers so paper.deleted events can still JOIN back
-- to the row for timeline reconstruction. NULL = live; ISO8601 = tombstoned.
-- NOTE: SQLite ALTER TABLE ADD COLUMN does not support IF NOT EXISTS, but
-- this migration only runs once because db.py guards on schema_version.
ALTER TABLE papers ADD COLUMN deleted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_papers_deleted_at ON papers(deleted_at);
