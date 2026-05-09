"""Daily arXiv RSS feed fetcher for the Atlas star chart.

Fetches new papers from arXiv RSS feeds by category, generates embeddings,
and stores them so the Atlas can display Distant Stars and Nebula (blind spots)
for the current day only.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from paperprism_agent import repository as repo
from paperprism_agent.events import Actor, Event, EventLogger
from paperprism_agent.navigator.embedding import EMB_DIM, _build_embed_text, encode_batch

log = logging.getLogger("paperprism.arxiv_feed")

RSS_BASE = "https://rss.arxiv.org/rss"
RSS_TIMEOUT = 60.0
MAX_PAPERS_PER_CATEGORY = 300  # safety ceiling
USER_AGENT = "PaperPrism-Agent (+https://github.com/paperprism; local)"


@dataclass
class FeedPaper:
    """A single paper parsed from arXiv RSS."""
    arxiv_id: str
    title: str = ""
    abstract: str = ""


def _extract_arxiv_id(link: str) -> str | None:
    """Extract arXiv ID from an RSS entry link or id.

    Examples:
        http://arxiv.org/abs/2401.08281v1  →  2401.08281
        http://arxiv.org/abs/cs.LG/0512001  →  cs.LG/0512001
    """
    m = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", link)
    if m:
        return m.group(1)
    return None


def _clean_title(raw: str) -> str:
    """Collapse whitespace and strip arXiv-appended parentheses."""
    t = " ".join(raw.split())
    # arXiv RSS titles often end with " (arXiv:XXXX.XXXXX ...)"
    t = re.sub(r"\s*\(arXiv:[^)]+\)\s*$", "", t)
    return t.strip()


def _clean_abstract(raw: str) -> str:
    """Collapse whitespace in abstract."""
    return " ".join(raw.split()).strip()


def fetch_daily_feed(categories: list[str]) -> list[FeedPaper]:
    """Fetch and parse today's papers from arXiv RSS for the given categories.

    Returns a deduplicated list of FeedPaper objects.
    """
    import feedparser  # lazy import so install is optional

    seen: dict[str, FeedPaper] = {}

    for cat in categories:
        url = f"{RSS_BASE}/{cat}"
        log.info("Fetching arXiv RSS: %s", url)

        try:
            with httpx.Client(
                timeout=RSS_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Failed to fetch RSS for %s: %s", cat, exc)
            continue

        feed = feedparser.parse(resp.text)

        count = 0
        for entry in getattr(feed, "entries", []):
            # Entry link is typically http://arxiv.org/abs/XXXX.XXXXXvN
            link = getattr(entry, "link", "") or getattr(entry, "id", "")
            arxiv_id = _extract_arxiv_id(link)
            if arxiv_id is None:
                continue
            if arxiv_id in seen:
                continue

            title = _clean_title(getattr(entry, "title", ""))
            # feedparser puts the summary in 'summary' or 'description'
            abstract = _clean_abstract(
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
            )

            seen[arxiv_id] = FeedPaper(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
            )
            count += 1
            if count >= MAX_PAPERS_PER_CATEGORY:
                log.warning(
                    "Hit MAX_PAPERS_PER_CATEGORY=%d for %s, stopping",
                    MAX_PAPERS_PER_CATEGORY, cat,
                )
                break

        log.info("Parsed %d papers from %s RSS", count, cat)

    papers = list(seen.values())
    log.info("Total unique feed papers: %d", len(papers))
    return papers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def refresh_feed(conn: sqlite3.Connection, categories: list[str]) -> int:
    """Main entry: fetch → encode → upsert → cleanup.

    Returns the number of new feed papers stored.
    """
    today = _today()

    # 1. Clean up old feed data from previous days
    _cleanup_old_feed(conn, today)

    # 2. Check if we already have today's feed
    existing = conn.execute(
        "SELECT COUNT(*) FROM arxiv_feed_papers WHERE feed_date = ?",
        (today,),
    ).fetchone()[0]
    if existing > 0:
        log.info("Feed for %s already exists (%d papers), skipping", today, existing)
        return 0

    # 3. Fetch RSS
    papers = fetch_daily_feed(categories)
    if not papers:
        log.warning("No feed papers fetched for %s", today)
        return 0

    # 4. Filter out papers already in the user's library
    arxiv_ids = [p.arxiv_id for p in papers]
    placeholders = ",".join("?" * len(arxiv_ids))
    library_ids = {
        r[0]
        for r in conn.execute(
            f"SELECT arxiv_id FROM papers WHERE deleted_at IS NULL AND arxiv_id IN ({placeholders})",
            arxiv_ids,
        ).fetchall()
    }
    new_papers = [p for p in papers if p.arxiv_id not in library_ids]
    log.info(
        "Filtered %d library papers, %d new feed papers remain",
        len(papers) - len(new_papers), len(new_papers),
    )

    if not new_papers:
        log.info("No new feed papers after filtering library")
        return 0

    # 5. Generate embeddings — reuse _build_embed_text for consistency
    # with library embeddings.  Feed papers have no summary/tags, so they
    # follow the same fallback path as unclassified library papers:
    # title + Abstract: {abstract}.
    texts = []
    for p in new_papers:
        texts.append(_build_embed_text(p.title, "", [], p.abstract or ""))

    embs = encode_batch(texts)

    # 6. Upsert into arxiv_feed_papers + arxiv_feed_embeddings
    now = _now_iso()
    for i, p in enumerate(new_papers):
        conn.execute(
            "INSERT OR REPLACE INTO arxiv_feed_papers(arxiv_id, title, abstract, feed_date, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (p.arxiv_id, p.title, p.abstract, today, now),
        )
        blob = struct.pack(f"{EMB_DIM}f", *embs[i])
        repo.upsert_arxiv_feed_embedding(conn, p.arxiv_id, blob)

    conn.commit()
    log.info("Stored %d feed papers for %s", len(new_papers), today)

    # Emit feed.fetched event so the Memory Ledger can track daily feed coverage.
    filtered_library_count = len(papers) - len(new_papers)
    with conn:
        EventLogger.emit(
            conn,
            Event(
                actor="system",
                event_type="feed.fetched",
                subject_type="feed",
                subject_id=today,
                payload={
                    "categories": categories,
                    "total_fetched": len(papers),
                    "new_papers": len(new_papers),
                    "filtered_library": filtered_library_count,
                },
            ),
        )

    return len(new_papers)


def _cleanup_old_feed(conn: sqlite3.Connection, before_date: str) -> int:
    """Delete feed papers and embeddings from days before `before_date`.

    Returns the number of feed papers deleted.
    """
    # Get old arxiv_ids first so we can delete their embeddings too
    old_ids = [
        r[0]
        for r in conn.execute(
            "SELECT arxiv_id FROM arxiv_feed_papers WHERE feed_date < ?",
            (before_date,),
        ).fetchall()
    ]
    if not old_ids:
        return 0

    # Delete embeddings
    for aid in old_ids:
        conn.execute(
            "DELETE FROM arxiv_feed_embeddings WHERE arxiv_id = ?",
            (aid,),
        )

    # Delete feed papers
    cur = conn.execute(
        "DELETE FROM arxiv_feed_papers WHERE feed_date < ?",
        (before_date,),
    )
    deleted = cur.rowcount
    conn.commit()
    log.info("Cleaned up %d old feed papers (before %s)", deleted, before_date)
    return deleted
