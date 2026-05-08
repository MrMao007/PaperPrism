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
from paperprism_agent import tagger as tagger_module
from paperprism_agent.weekly_digest import maybe_generate_digest
from paperprism_agent import arxiv_feed

log = logging.getLogger("paperprism.worker")

DEFAULT_POLL_INTERVAL = 5.0   # seconds between empty-queue polls
IDLE_SLEEP_ON_ERROR = 10.0    # after an unexpected worker loop error
DIGEST_CHECK_INTERVAL = 3600.0  # seconds between weekly-digest checks (1h)
FEED_CHECK_INTERVAL = 3600.0     # seconds between arXiv feed checks (1h)


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
        self._last_digest_check: float = 0.0  # monotonic timestamp
        self._last_feed_check: float = 0.0      # monotonic timestamp

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
                # Periodic weekly-digest check (independent of task queue).
                await self._maybe_check_digest()
                # Periodic arXiv feed refresh (independent of task queue).
                await self._maybe_refresh_feed()
                processed = await self._tick()
            except Exception:
                log.exception("worker loop crashed; sleeping before retry")
                await self._sleep(IDLE_SLEEP_ON_ERROR)
                continue
            if not processed:
                await self._sleep(self._poll_interval)

    async def _maybe_check_digest(self) -> None:
        """Check once per hour whether a new weekly digest is due."""
        import time
        now = time.monotonic()
        if now - self._last_digest_check < DIGEST_CHECK_INTERVAL:
            return
        self._last_digest_check = now
        try:
            await asyncio.to_thread(maybe_generate_digest, self._cfg, self._conn)
        except Exception:
            log.exception("weekly digest check failed (non-fatal)")

    async def _maybe_refresh_feed(self) -> None:
        """Check once per hour whether today's arXiv feed needs refreshing."""
        import time
        now = time.monotonic()
        if now - self._last_feed_check < FEED_CHECK_INTERVAL:
            return
        self._last_feed_check = now
        try:
            categories = self._get_feed_categories()
            if not categories:
                return
            await asyncio.to_thread(
                arxiv_feed.refresh_feed, self._conn, categories
            )
        except Exception:
            log.exception("arXiv feed refresh failed (non-fatal)")

    def _get_feed_categories(self) -> list[str]:
        """Read feed_categories from llm.yaml; defaults to empty (disabled)."""
        try:
            llm_cfg = self._get_llm_config()
        except Exception:
            return []
        cats = getattr(llm_cfg, "feed_categories", None)
        if cats and isinstance(cats, list):
            return [c for c in cats if isinstance(c, str) and c.strip()]
        return []

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
            elif kind == "tag":
                await asyncio.to_thread(self._run_tag, paper_id)
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

        pdf_path = Path(paper["pdf_path"])
        head = pdf.read_head(pdf_path, pages=2)

        # 1) arxiv API (skipped for user-uploaded non-arxiv PDFs) ----------
        query_id = paper["full_id"]   # versioned if we have one
        meta = None
        if pdf.looks_like_arxiv_id(query_id):
            try:
                meta = arxiv_client.fetch_by_id(query_id)
            except Exception as exc:
                # Non-fatal: fall through to PDF-only enrichment.
                log.warning(
                    "arxiv fetch failed for %s (%s); falling back to PDF-only",
                    query_id, exc,
                )
                meta = None

        if meta is None:
            # PDF-only path: derive title / abstract from the first page.
            title, abstract = pdf.extract_title_abstract(head.text)
            authors: list[str] = []
            categories: list[str] = []
            published_at = None
            updated_at = None
            journal_ref = None
            affiliations: list[str] = []
            comment = None
        else:
            title = meta.title
            abstract = meta.abstract
            authors = meta.authors
            categories = meta.categories or []
            published_at = meta.published_at
            updated_at = meta.updated_at
            journal_ref = meta.journal_ref
            affiliations = meta.affiliations or []
            comment = meta.comment

        # 2) code URL: scan all the text we have --------------------------
        code_url = pdf.find_code_url(
            abstract or "",
            comment or "",
            head.text or "",
        )

        # 3) venue hint -----------------------------------------------------
        venue = journal_ref or comment or None

        # 4) write ----------------------------------------------------------
        repository.mark_enriched(
            self._conn,
            paper_id=paper_id,
            title=title,
            authors=authors or None,
            abstract=abstract,
            categories=categories or None,
            published_at=published_at,
            updated_at_arxiv=updated_at,
            venue=venue,
            code_url=code_url,
            affiliations=affiliations or None,
        )

        # 5) queue classify step -------------------------------------------
        tasks.enqueue(self._conn, paper_id=paper_id, kind="classify")

        log.info(
            "enriched paper_id=%s arxiv=%s title=%r code_url=%s",
            paper_id,
            meta is not None,
            (title or "")[:60],
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
        full_text = ""
        if pdf_path is not None:
            head = pdf.read_head(pdf_path, pages=1)
            head_text = head.text
            # Read full PDF text for richer LLM context (summary etc.)
            if head.n_pages > 1:
                full = pdf.read_head(pdf_path, pages=head.n_pages)
                full_text = full.text

        ctx = PaperContext(
            full_id=paper["full_id"],
            title=paper.get("title"),
            abstract=paper.get("abstract"),
            authors=authors,
            arxiv_categories=categories,
            journal_ref=paper.get("venue"),   # raw hint stored by enrich
            comment=None,
            pdf_head_text=head_text,
            pdf_full_text=full_text or None,
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

        # Queue a tag step if the user opted in (default: True). Kept
        # behind the same LLM config so the on/off switch lives next to
        # provider/model in Options.
        try:
            llm_cfg = self._get_llm_config()
        except Exception as exc:
            log.warning("could not load llm config for auto-tag gate: %s", exc)
            llm_cfg = None
        if llm_cfg is None or getattr(llm_cfg, "auto_tag_on_ingest", True):
            tasks.enqueue(self._conn, paper_id=paper_id, kind="tag")
            # tag 完成后会调用 embed_paper，此处不再重复
        else:
            # auto-tag 关闭，classify 是最后一步，在此生成 embedding
            try:
                from paperprism_agent.navigator import embedding as emb_mod
                emb_mod.embed_paper(self._conn, paper_id)
            except Exception as exc:
                log.warning("embed after classify failed for paper_id=%s (non-fatal): %s", paper_id, exc)

    # ------------------------------------------------------------------ #
    # tag handler (single-paper auto-tag, driven by worker queue)
    # ------------------------------------------------------------------ #

    def _run_tag(self, paper_id: int) -> None:
        """Blocking single-paper auto-tag (called via to_thread).

        Uses the same LLM + prompt as the batch tagger by running the
        batch tagger over a 1-element list. Produces 2-5 LLM-sourced tags
        with ``topic_id=NULL`` (single-paper auto-tag is not part of any
        topic).
        """
        # Honour the user's kill switch: if the operator has turned auto-tag
        # off since this task was enqueued, just mark it done and skip the
        # LLM call. This keeps the queue drained without burning tokens.
        try:
            llm_cfg = self._get_llm_config()
        except Exception:
            llm_cfg = None
        if llm_cfg is not None and not getattr(llm_cfg, "auto_tag_on_ingest", True):
            log.info("auto-tag disabled; skipping tag task for paper_id=%s", paper_id)
            return

        paper = repository.get_paper(self._conn, paper_id)
        if paper is None:
            raise RuntimeError(f"paper {paper_id} vanished before tagging")

        import json as _json
        categories = (
            _json.loads(paper["arxiv_categories_json"])
            if paper.get("arxiv_categories_json")
            else []
        )

        title = paper.get("title") or ""
        abstract = paper.get("abstract") or ""
        if not title and not abstract:
            log.info(
                "paper_id=%s lacks title+abstract; skipping auto-tag", paper_id
            )
            return

        ctx = {
            "paper_id": paper_id,
            "full_id": paper["full_id"],
            "title": title,
            "abstract": abstract,
            "arxiv_categories": categories,
        }

        client = self._get_llm_client()
        result = tagger_module.run_batch(
            client=client,
            paper_ctxs=[ctx],
            existing_top_tags=None,
        )
        tags = result.per_paper.get(paper_id) or []
        if not tags:
            log.info("auto-tag: LLM returned no tags for paper_id=%s", paper_id)
            return

        inserted = repository.add_paper_tags(
            self._conn,
            paper_id=paper_id,
            tag_names=tags,
            source="llm",
            topic_id=None,
        )
        log.info(
            "auto-tagged paper_id=%s tags=%s inserted=%d",
            paper_id, tags, inserted,
        )

        # Re-embed: tags are now available → richer embedding
        try:
            from paperprism_agent.navigator import embedding as emb_mod
            emb_mod.embed_paper(self._conn, paper_id)
        except Exception as exc:
            log.warning("re-embed after tag failed for paper_id=%s (non-fatal): %s", paper_id, exc)

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

    def _get_llm_config(self) -> llm_module.LLMConfig:
        """Always re-read llm.yaml (cheap) so operator toggles take effect
        without restarting the agent."""
        return llm_module.LLMConfig.load(self._cfg.paths.llm_config_file)

    def _get_dimensions(self) -> dim_module.DimensionsConfig:
        if self._dimensions is None:
            self._dimensions = dim_module.load(self._cfg.paths.dimensions_file)
        return self._dimensions
