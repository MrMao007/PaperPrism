-- 0006: Weekly research digests (LLM-generated + user-edited)
CREATE TABLE IF NOT EXISTS weekly_digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week       TEXT    NOT NULL UNIQUE,   -- ISO week label like 2026-W18
    week_start TEXT    NOT NULL,          -- Monday date string
    content    TEXT    NOT NULL,          -- LLM-generated digest text
    user_note  TEXT    NOT NULL DEFAULT '', -- user-edited personal reflection
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
