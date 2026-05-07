"""Memory Ledger writer. Owned by repository.py; DO NOT call from server.py."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

log = logging.getLogger("paperprism.events")

Actor = Literal["user", "agent", "llm", "system"]
SubjectType = Literal["paper", "tag", "topic"]

_VALID_EVENT_TYPES: set[str] = {
    "paper.ingested.downloaded",
    "paper.ingested.uploaded",
    "paper.ingested.bulk_imported",
    "paper.deleted",
    "paper.opened",
    "paper.read_session",
    "topic.created",
    "topic.renamed",
    "topic.deleted",
    "topic.papers_added",
    "topic.papers_removed",
    "tag.auto_generated",
    "tag.added_by_user",
    "tag.removed_by_user",
    "tag.added_by_llm",
    "tag.removed_by_llm",
}

_PAYLOAD_MAX_BYTES = 16 * 1024


class UnknownEventType(ValueError):
    """Raised when event_type is not in the whitelist."""


class PayloadTooLarge(ValueError):
    """Raised when canonicalised payload exceeds _PAYLOAD_MAX_BYTES."""


@dataclass(frozen=True, slots=True)
class Event:
    """A single ledger entry. Immutable."""

    actor: Actor
    event_type: str
    subject_type: SubjectType
    subject_id: str
    related_ids: list[str] | None = None
    payload: dict | None = None
    schema_v: int = 1


class EventLogger:
    """Append events to the ledger in the caller's transaction."""

    @staticmethod
    def emit(conn: sqlite3.Connection, event: Event) -> int:
        """Insert a single event. Caller MUST already be inside a transaction.

        Returns the generated ``events.id``.
        """
        if event.event_type not in _VALID_EVENT_TYPES:
            raise UnknownEventType(f"unknown event_type: {event.event_type!r}")
        if event.actor not in {"user", "agent", "llm", "system"}:
            raise ValueError(f"invalid actor: {event.actor!r}")
        if event.subject_type not in {"paper", "tag", "topic"}:
            raise ValueError(f"invalid subject_type: {event.subject_type!r}")

        ts = _now_iso()
        payload_json = _canonicalize(event.payload) if event.payload else None
        related = json.dumps(event.related_ids, separators=(",", ":")) if event.related_ids else None

        cur = conn.execute(
            "INSERT INTO events (ts, actor, event_type, subject_type, subject_id, related_ids, payload, schema_v) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                event.actor,
                event.event_type,
                event.subject_type,
                event.subject_id,
                related,
                payload_json,
                event.schema_v,
            ),
        )
        return int(cur.lastrowid)

    @staticmethod
    def emit_many(conn: sqlite3.Connection, events: Sequence[Event]) -> int:
        """Bulk-insert helper for auto-tag jobs. Same TXN as caller.

        Returns the number of rows inserted.
        """
        if not events:
            return 0

        rows: list[tuple] = []
        for event in events:
            if event.event_type not in _VALID_EVENT_TYPES:
                raise UnknownEventType(f"unknown event_type: {event.event_type!r}")
            if event.actor not in {"user", "agent", "llm", "system"}:
                raise ValueError(f"invalid actor: {event.actor!r}")
            if event.subject_type not in {"paper", "tag", "topic"}:
                raise ValueError(f"invalid subject_type: {event.subject_type!r}")

            ts = _now_iso()
            payload_json = _canonicalize(event.payload) if event.payload else None
            related = json.dumps(event.related_ids, separators=(",", ":")) if event.related_ids else None
            rows.append(
                (
                    ts,
                    event.actor,
                    event.event_type,
                    event.subject_type,
                    event.subject_id,
                    related,
                    payload_json,
                    event.schema_v,
                )
            )

        conn.executemany(
            "INSERT INTO events (ts, actor, event_type, subject_type, subject_id, related_ids, payload, schema_v) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(events)


def _now_iso() -> str:
    """UTC ISO8601 with millisecond precision and trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonicalize(payload: dict) -> str:
    """Canonical JSON: sorted keys, compact separators, capped size."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    encoded = raw.encode("utf-8")
    if len(encoded) > _PAYLOAD_MAX_BYTES:
        raise PayloadTooLarge(
            f"payload {len(encoded)} B exceeds cap {_PAYLOAD_MAX_BYTES} B"
        )
    return raw
