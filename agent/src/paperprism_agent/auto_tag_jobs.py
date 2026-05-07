"""In-memory async job manager for bulk auto-tagging.

A single job represents one user click of "Auto-tag selected papers".
It slices the paper set into batches, drives the LLM batch-by-batch,
writes results to the DB as they arrive, and finally asks the LLM to
summarise the collection into a Topic row.

Why in-memory:
  - Jobs finish in minutes; persisting state across Agent restarts is
    more work than it's worth. If the Agent dies mid-job the user can
    simply re-select the remaining papers.
  - Per-batch writes land in SQLite right away, so partial results are
    safe even if the process crashes.

Cancellation:
  - ``cancel()`` sets an asyncio.Event. The runner checks the flag at
    the top of each batch, so in-flight batches finish before the job
    stops. Status becomes 'cancelled'; no Topic row is created.

Retry:
  - ``retry_failed()`` re-queues any batches that ended in error by
    appending new batches that cover those paper_ids. Success counters
    accumulate; the same topic is produced once everything finishes.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from paperprism_agent import repository, tagger
from paperprism_agent.config import Config
from paperprism_agent.events import Event, EventLogger
from paperprism_agent.llm import LLMClient, LLMConfig, LLMConfigError, LLMError

log = logging.getLogger("paperprism.auto_tag_jobs")

DEFAULT_BATCH_SIZE = 15
MAX_PAPERS_PER_JOB = 2000  # safety ceiling; UI should enforce something lower
BATCH_RETRY_COUNT = 2
BATCH_RETRY_BACKOFF_S = 3.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Job dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class BatchRecord:
    index: int
    paper_ids: list[int]
    status: str = "pending"            # pending | running | ok | failed | cancelled
    error: str | None = None
    tagged_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class AutoTagJob:
    id: str
    total_papers: int
    batch_size: int
    batches: list[BatchRecord] = field(default_factory=list)
    status: str = "running"           # running | done | cancelled | failed
    processed_papers: int = 0
    succeeded_batches: int = 0
    failed_batches: int = 0
    cancelled_batches: int = 0
    tag_counter: Counter = field(default_factory=Counter)
    all_paper_ids: list[int] = field(default_factory=list)
    succeeded_paper_ids: set[int] = field(default_factory=set)
    topic_id: int | None = None
    topic_slug: str | None = None
    topic_name: str | None = None
    topic_summary: str | None = None
    model_label: str | None = None
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    finished_at: str | None = None
    last_error: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None

    # ---- snapshot helpers ------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """JSON-serialisable view returned to the HTTP client."""
        total_batches = len(self.batches)
        processed_batches = sum(
            1 for b in self.batches if b.status in ("ok", "failed", "cancelled")
        )
        current = next((b for b in self.batches if b.status == "running"), None)
        errors = [
            {
                "batch_index": b.index,
                "paper_ids": b.paper_ids,
                "message": b.error,
            }
            for b in self.batches
            if b.status == "failed" and b.error
        ]
        return {
            "job_id": self.id,
            "status": self.status,
            "total_papers": self.total_papers,
            "processed_papers": self.processed_papers,
            "total_batches": total_batches,
            "processed_batches": processed_batches,
            "succeeded_batches": self.succeeded_batches,
            "failed_batches": self.failed_batches,
            "cancelled_batches": self.cancelled_batches,
            "batch_size": self.batch_size,
            "current_batch": (
                {
                    "index": current.index,
                    "paper_ids": current.paper_ids,
                }
                if current
                else None
            ),
            "errors": errors[-10:],
            "topic_id": self.topic_id,
            "topic_slug": self.topic_slug,
            "topic_name": self.topic_name,
            "topic_summary": self.topic_summary,
            "model": self.model_label,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "last_error": self.last_error,
        }


# --------------------------------------------------------------------------- #
# Manager (module-level singleton)
# --------------------------------------------------------------------------- #

_JOBS: dict[str, AutoTagJob] = {}
_JOBS_LOCK = threading.Lock()


def _register(job: AutoTagJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.id] = job


def get_job(job_id: str) -> AutoTagJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def list_recent_jobs(limit: int = 10) -> list[AutoTagJob]:
    with _JOBS_LOCK:
        return sorted(_JOBS.values(), key=lambda j: j.started_at, reverse=True)[:limit]


# --------------------------------------------------------------------------- #
# Public entrypoints
# --------------------------------------------------------------------------- #

def create_job(
    *,
    cfg: Config,
    conn: sqlite3.Connection,
    paper_ids: list[int],
    batch_size: int | None = None,
) -> AutoTagJob:
    """Validate input, snapshot paper context, kick off the background task."""
    # Dedupe + sanity limits.
    seen: list[int] = []
    seen_set: set[int] = set()
    for pid in paper_ids:
        if pid in seen_set:
            continue
        seen_set.add(pid)
        seen.append(pid)
    if not seen:
        raise ValueError("no paper ids")
    if len(seen) > MAX_PAPERS_PER_JOB:
        raise ValueError(f"too many papers: {len(seen)} (max {MAX_PAPERS_PER_JOB})")

    # Only tag papers that actually exist.
    existing = _filter_existing_paper_ids(conn, seen)
    if not existing:
        raise ValueError("none of the given paper ids exist")

    # Require an LLM config up front so the job fails fast, not mid-batch.
    try:
        llm_cfg = LLMConfig.load(cfg.paths.llm_config_file)
        _ = LLMClient(llm_cfg)  # construction validates api key / base url
    except (LLMConfigError, LLMError) as exc:
        raise ValueError(f"LLM not configured: {exc}") from exc

    effective_batch_size = batch_size if batch_size and batch_size > 0 else DEFAULT_BATCH_SIZE
    batched = tagger.plan_batches(existing, effective_batch_size)

    job = AutoTagJob(
        id=uuid.uuid4().hex,
        total_papers=len(existing),
        batch_size=effective_batch_size,
        batches=[BatchRecord(index=i, paper_ids=ids) for i, ids in enumerate(batched)],
        all_paper_ids=list(existing),
    )
    _register(job)

    loop = asyncio.get_running_loop()
    job._task = loop.create_task(_run_job(cfg, job))
    return job


async def cancel_job(job_id: str) -> bool:
    job = get_job(job_id)
    if job is None:
        return False
    if job.status != "running":
        return False
    job.cancel_event.set()
    return True


async def retry_failed(
    *,
    cfg: Config,
    job_id: str,
) -> AutoTagJob | None:
    """Re-queue batches marked failed / cancelled and restart the runner
    if the job is idle. The same topic summary will be produced on completion."""
    job = get_job(job_id)
    if job is None:
        return None
    if job.status == "running":
        # Another retry already inflight — just return current snapshot.
        return job

    retry_indices = [
        b.index for b in job.batches if b.status in ("failed", "cancelled")
    ]
    if not retry_indices:
        return job

    # Reset the retried batches back to pending.
    for b in job.batches:
        if b.status in ("failed", "cancelled"):
            b.status = "pending"
            b.error = None
            b.tagged_count = 0
            b.started_at = None
            b.finished_at = None
    # Re-adjust counters for the retried batches.
    job.failed_batches = max(0, job.failed_batches - len(retry_indices))
    job.cancelled_batches = 0
    job.last_error = None
    job.cancel_event = asyncio.Event()
    job.status = "running"
    job.finished_at = None
    job.updated_at = _now()

    loop = asyncio.get_running_loop()
    job._task = loop.create_task(_run_job(cfg, job))
    return job


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

async def _run_job(cfg: Config, job: AutoTagJob) -> None:
    """Main coroutine: iterate pending batches, write DB, summarise."""
    from paperprism_agent import db as db_module  # avoid circular import at load

    try:
        llm_cfg = LLMConfig.load(cfg.paths.llm_config_file)
        client = LLMClient(llm_cfg)
        job.model_label = client.provider_label
    except (LLMConfigError, LLMError) as exc:
        job.status = "failed"
        job.last_error = f"LLM config error: {exc}"
        job.finished_at = _now()
        job.updated_at = _now()
        log.exception("auto-tag job %s: LLM unavailable", job.id)
        return

    conn = db_module.connect(cfg.paths.db_file)

    try:
        for batch in job.batches:
            if batch.status != "pending":
                continue
            if job.cancel_event.is_set():
                batch.status = "cancelled"
                job.cancelled_batches += 1
                job.updated_at = _now()
                continue

            batch.status = "running"
            batch.started_at = _now()
            job.updated_at = _now()

            try:
                ctxs = _load_paper_ctxs(conn, batch.paper_ids)
                if not ctxs:
                    batch.status = "failed"
                    batch.error = "paper rows not found"
                    job.failed_batches += 1
                    continue

                hint_tags = [t for t, _ in job.tag_counter.most_common(tagger.HINT_TAG_LIMIT)]
                result = await _run_batch_with_retries(client, ctxs, hint_tags)

                # Persist each paper's tags; aggregate counters.
                written_papers = 0
                auto_events: list[Event] = []
                for pid, tags in result.per_paper.items():
                    written = await asyncio.to_thread(
                        repository.add_paper_tags,
                        conn,
                        paper_id=pid,
                        tag_names=tags,
                        source="llm",
                        topic_id=None,    # backfilled once the topic row exists
                        actor="llm",
                    )
                    if written:
                        written_papers += 1
                    for t in tags:
                        job.tag_counter[t] += 1
                        auto_events.append(
                            Event(
                                actor="llm",
                                event_type="tag.auto_generated",
                                subject_type="tag",
                                subject_id=t,
                                payload={"paper_id": pid, "model": job.model_label},
                            )
                        )
                    job.succeeded_paper_ids.add(pid)

                # Batch-write audit trail for LLM-generated tags.
                if auto_events:
                    await asyncio.to_thread(EventLogger.emit_many, conn, auto_events)

                for t in result.hint_tags:
                    # hint tags get a small seed weight so future batches see them
                    if job.tag_counter[t] == 0:
                        job.tag_counter[t] = 1

                batch.tagged_count = written_papers
                batch.status = "ok"
                batch.finished_at = _now()
                job.succeeded_batches += 1
                job.processed_papers += len(batch.paper_ids)
                job.updated_at = _now()

            except asyncio.CancelledError:
                batch.status = "cancelled"
                job.cancelled_batches += 1
                raise
            except Exception as exc:  # noqa: BLE001
                batch.status = "failed"
                batch.error = f"{type(exc).__name__}: {exc}"
                batch.finished_at = _now()
                job.failed_batches += 1
                job.last_error = batch.error
                job.processed_papers += len(batch.paper_ids)
                job.updated_at = _now()
                log.warning("auto-tag job %s batch %s failed: %s", job.id, batch.index, exc)

        # --- finalise -----------------------------------------------------
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.finished_at = _now()
            job.updated_at = _now()
            log.info("auto-tag job %s cancelled", job.id)
            return

        if job.succeeded_batches == 0:
            job.status = "failed"
            job.last_error = job.last_error or "all batches failed"
            job.finished_at = _now()
            job.updated_at = _now()
            return

        await _finalise_topic(conn, client, job)
        job.status = "done"
        job.finished_at = _now()
        job.updated_at = _now()
        log.info(
            "auto-tag job %s done: topic=%s papers=%s batches_ok=%s batches_failed=%s",
            job.id, job.topic_slug, job.processed_papers,
            job.succeeded_batches, job.failed_batches,
        )
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.finished_at = _now()
        job.updated_at = _now()
        raise


async def _run_batch_with_retries(
    client: LLMClient,
    ctxs: list[dict[str, Any]],
    hint_tags: list[str],
) -> tagger.BatchResult:
    """Run one LLM batch with bounded retries on transient errors."""
    last: Exception | None = None
    for attempt in range(1, BATCH_RETRY_COUNT + 2):
        try:
            return await asyncio.to_thread(
                tagger.run_batch,
                client=client,
                paper_ctxs=ctxs,
                existing_top_tags=hint_tags,
            )
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning(
                "auto-tag batch attempt %s/%s failed: %s",
                attempt, BATCH_RETRY_COUNT + 1, exc,
            )
            if attempt <= BATCH_RETRY_COUNT:
                await asyncio.sleep(BATCH_RETRY_BACKOFF_S * attempt)
                continue
            raise
    # unreachable
    raise last or RuntimeError("run_batch failed")


async def _finalise_topic(
    conn: sqlite3.Connection,
    client: LLMClient,
    job: AutoTagJob,
) -> None:
    """Create a topic row covering every successfully-tagged paper and
    backfill paper_tags.topic_id so the UI can render 'tagged by topic X'."""
    paper_rows = await asyncio.to_thread(
        _load_titles_for_papers, conn, sorted(job.succeeded_paper_ids)
    )
    titles = [r["title"] or r["full_id"] for r in paper_rows]

    draft = await asyncio.to_thread(
        tagger.summarize_topic,
        client=client,
        titles=titles,
        tag_counts=job.tag_counter,
    )

    unique_slug = await asyncio.to_thread(
        repository.reserve_unique_topic_slug, conn, draft.slug_hint,
    )

    topic_id = await asyncio.to_thread(
        repository.create_topic,
        conn,
        slug=unique_slug,
        name=draft.name,
        summary=draft.summary,
        model=client.provider_label,
        source_job_id=job.id,
    )
    await asyncio.to_thread(
        repository.add_topic_papers,
        conn,
        topic_id=topic_id,
        paper_ids=sorted(job.succeeded_paper_ids),
    )
    await asyncio.to_thread(
        repository.backfill_topic_id_on_paper_tags,
        conn,
        topic_id=topic_id,
        paper_ids=sorted(job.succeeded_paper_ids),
        source_job_id=job.id,
    )

    job.topic_id = topic_id
    job.topic_slug = unique_slug
    job.topic_name = draft.name
    job.topic_summary = draft.summary


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def _filter_existing_paper_ids(
    conn: sqlite3.Connection, paper_ids: list[int]
) -> list[int]:
    if not paper_ids:
        return []
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT id FROM papers WHERE id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    keep = {r["id"] for r in rows}
    return [pid for pid in paper_ids if pid in keep]


def _load_paper_ctxs(
    conn: sqlite3.Connection, paper_ids: list[int]
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"""
        SELECT id, full_id, title, abstract, arxiv_categories_json
        FROM papers
        WHERE id IN ({placeholders})
        """,
        paper_ids,
    ).fetchall()
    import json as _json
    out: list[dict[str, Any]] = []
    for r in rows:
        cats_raw = r["arxiv_categories_json"]
        cats = _json.loads(cats_raw) if cats_raw else []
        out.append(
            {
                "paper_id": r["id"],
                "full_id": r["full_id"],
                "title": r["title"],
                "abstract": r["abstract"],
                "arxiv_categories": cats,
            }
        )
    return out


def _load_titles_for_papers(
    conn: sqlite3.Connection, paper_ids: list[int]
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT id, full_id, title FROM papers WHERE id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    return [dict(r) for r in rows]
