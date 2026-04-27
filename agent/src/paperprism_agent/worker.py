"""Background worker that drains the `tasks` queue.

Responsibilities (P2.2 scope):
  - poll `tasks` for the oldest ready row
  - dispatch on `kind`:
      * ``enrich`` -> hit arxiv API + scan PDF head, fill metadata columns
      * ``classify`` -> (P2.3) LLM fills the `classifications` table
  - mark `ok` on success, `fail(err)` on exception (task layer handles
    backoff / dead lettering).

Runtime model:
  - A single asyncio task, started by the FastAPI lifespan.
  - CPU/IO-bound work (pymupdf, httpx) runs in a thread via
    `asyncio.to_thread`, so the event loop stays responsive.
  - Graceful shutdown via an `asyncio.Event`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from paperprism_agent import arxiv_client, pdf, repository, tasks
from paperprism_agent.config import Config
from paperprism_agent.classifier import (
    CLASSIFICATION_VERSION,
    PaperContext,
    classify,
    has_enough_context,
)
from paperprism_agent import dimensions as dim_module
from paperprism_agent import llm as llm_module

log = logging.getLogger("paperprism.worker")

DEFAULT_POLL_INTERVAL = 5.0   # seconds between empty-queue polls
IDLE_SLEEP_ON_ERROR = 10.0    # after an unexpected worker loop error


class Worker:
    def __init__(
        self,
        *,
        cfg: Config,
        conn: sqlite3.Connection,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._cfg = cfg
        self._conn = conn
        self._poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # LLM stack is loaded lazily on first classify task (so ingest and
        # enrich keep working even if llm.yaml is misconfigured).
        self._llm_client: llm_module.LLMClient | None = None
        self._llm_error: str | None = None
        self._dimensions: dim_module.DimensionsConfig | None = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="paperprism-worker")
        log.info("worker started (poll=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            log.warning("worker did not exit in 10s; cancelling")
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        log.info("worker stopped")

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self._tick()
            except Exception:
                log.exception("worker loop crashed; sleeping before retry")
                await self._sleep(IDLE_SLEEP_ON_ERROR)
                continue
            if not processed:
                await self._sleep(self._poll_interval)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    async def _tick(self) -> bool:
        """Process one task if available. Returns True if one was handled."""
        row = tasks.claim_next(self._conn)
        if row is None:
            return False

        task_id = row["id"]
        kind = row["kind"]
        paper_id = row["paper_id"]
        log.info("claimed task id=%s kind=%s paper_id=%s", task_id, kind, paper_id)

        try:
            if kind == "enrich":
                await asyncio.to_thread(self._run_enrich, paper_id)
            elif kind == "classify":
                await asyncio.to_thread(self._run_classify, paper_id)
            else:
                raise RuntimeError(f"unknown task kind: {kind}")

            tasks.complete(self._conn, task_id=task_id)
            log.info("task id=%s kind=%s ok", task_id, kind)
        except Exception as exc:
            log.exception("task id=%s kind=%s failed", task_id, kind)
            tasks.fail(self._conn, task_id=task_id, error=f"{type(exc).__name__}: {exc}")
        return True

    # ------------------------------------------------------------------ #
    # handlers
    # ------------------------------------------------------------------ #

    def _run_enrich(self, paper_id: int) -> None:
        """Blocking enrich implementation (called via to_thread)."""
        paper = repository.get_paper(self._conn, paper_id)
        if paper is None:
            raise RuntimeError(f"paper {paper_id} vanished before enrichment")

        # 1) arxiv API --------------------------------------------------
        query_id = paper["full_id"]   # versioned if we have one
        meta = arxiv_client.fetch_by_id(query_id)

        # 2) PDF head ---------------------------------------------------
        pdf_path = Path(paper["pdf_path"])
        head = pdf.read_head(pdf_path, pages=1)

        # 3) code URL: prefer abstract, fall back to comment + pdf ------
        code_url = pdf.find_code_url(
            meta.abstract or "",
            meta.comment or "",
            head.text or "",
        )

        # 4) venue hint (raw for now; P2.3 LLM will normalize) ----------
        #    prefer journal_ref since it's the author-attested field.
        venue = meta.journal_ref or meta.comment or None

        # 5) affiliations: only the author-declared ones from arxiv.
        #    LLM-from-PDF path is P2.3; leave None if empty.
        affiliations = meta.affiliations or None

        # 6) write ------------------------------------------------------
        repository.mark_enriched(
            self._conn,
            paper_id=paper_id,
            title=meta.title,
            authors=meta.authors or None,
            abstract=meta.abstract,
            categories=meta.categories or None,
            published_at=meta.published_at,
            updated_at_arxiv=meta.updated_at,
            venue=venue,
            code_url=code_url,
            affiliations=affiliations,
        )

        # 7) queue classify step (P2.3 will consume it)
        tasks.enqueue(self._conn, paper_id=paper_id, kind="classify")

        log.info(
            "enriched paper_id=%s title=%r authors=%s code_url=%s",
            paper_id,
            (meta.title or "")[:60],
            len(meta.authors),
            code_url,
        )

    # ------------------------------------------------------------------ #
    # classify handler
    # ------------------------------------------------------------------ #

    def _run_classify(self, paper_id: int) -> None:
        """Blocking classify implementation (called via to_thread)."""
        paper = repository.get_paper(self._conn, paper_id)
        if paper is None:
            raise RuntimeError(f"paper {paper_id} vanished before classification")

        # Build the input context from what enrich already wrote.
        import json as _json
        authors = _json.loads(paper["authors_json"]) if paper.get("authors_json") else []
        categories = (
            _json.loads(paper["arxiv_categories_json"])
            if paper.get("arxiv_categories_json")
            else []
        )
        pdf_path = Path(paper["pdf_path"]) if paper.get("pdf_path") else None
        head_text = ""
        if pdf_path is not None:
            head = pdf.read_head(pdf_path, pages=1)
            head_text = head.text

        ctx = PaperContext(
            full_id=paper["full_id"],
            title=paper.get("title"),
            abstract=paper.get("abstract"),
            authors=authors,
            arxiv_categories=categories,
            journal_ref=paper.get("venue"),   # raw hint stored by enrich
            comment=None,
            pdf_head_text=head_text,
        )

        if not has_enough_context(ctx):
            log.info(
                "paper_id=%s lacks abstract/PDF text; skipping classify", paper_id
            )
            return

        client = self._get_llm_client()
        dims = self._get_dimensions()
        rows, model_label = classify(ctx=ctx, config=dims, client=client)

        if not rows:
            log.info("paper_id=%s classifier returned no rows", paper_id)
            return

        repository.replace_classifications(
            self._conn,
            paper_id=paper_id,
            rows=rows,
            model=model_label,
            classification_version=CLASSIFICATION_VERSION,
        )
        log.info(
            "classified paper_id=%s rows=%d model=%s",
            paper_id, len(rows), model_label,
        )

    def _get_llm_client(self) -> llm_module.LLMClient:
        if self._llm_client is not None:
            return self._llm_client
        if self._llm_error is not None:
            # Already tried and failed; rethrow so tasks.fail kicks in.
            raise RuntimeError(self._llm_error)
        try:
            cfg = llm_module.LLMConfig.load(self._cfg.paths.llm_config_file)
            self._llm_client = llm_module.LLMClient(cfg)
        except Exception as exc:
            self._llm_error = f"LLM unavailable: {type(exc).__name__}: {exc}"
            raise
        return self._llm_client

    def _get_dimensions(self) -> dim_module.DimensionsConfig:
        if self._dimensions is None:
            self._dimensions = dim_module.load(self._cfg.paths.dimensions_file)
        return self._dimensions
