"""Thin client for the arxiv public API.

Only what P2.2 needs:
  - `fetch_by_id(full_id)` -> parsed dict with title/abstract/authors/...
  - deterministic, cached-free, synchronous (we run it via asyncio.to_thread
    from the worker).

Network etiquette (per arxiv's terms):
  - One connection, sequential; never open many in parallel.
  - Descriptive User-Agent.
  - Generous timeouts; retry with exponential backoff on 5xx / network error.
  - 429 -> honour Retry-After; otherwise back off.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from paperprism_agent import __version__

log = logging.getLogger("paperprism.arxiv_client")

ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
NS = {"atom": ATOM_NS, "arxiv": ARXIV_NS}

BASE_URL = "https://export.arxiv.org/api/query"
USER_AGENT = f"PaperPrism-Agent/{__version__} (+https://github.com/paperprism; local)"

DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 3.0  # arxiv recommends >= 3s between retries


class ArxivNotFound(Exception):
    """Queried id has no matching entry in arxiv's feed."""


class ArxivTransientError(Exception):
    """Network / 5xx / 429; worker will requeue with task-level backoff."""


@dataclass
class ArxivMeta:
    arxiv_id: str                           # as returned (may include version)
    title: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    primary_category: str | None = None
    categories: list[str] = field(default_factory=list)
    published_at: str | None = None          # ISO-8601 from <published>
    updated_at: str | None = None            # ISO-8601 from <updated>
    journal_ref: str | None = None
    comment: str | None = None
    doi: str | None = None
    license: str | None = None
    pdf_link: str | None = None
    abs_link: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "affiliations": self.affiliations,
            "primary_category": self.primary_category,
            "categories": self.categories,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "journal_ref": self.journal_ref,
            "comment": self.comment,
            "doi": self.doi,
            "license": self.license,
            "pdf_link": self.pdf_link,
            "abs_link": self.abs_link,
        }


def fetch_by_id(full_id: str, *, timeout: float = DEFAULT_TIMEOUT) -> ArxivMeta:
    """Fetch and parse metadata for a single paper.

    `full_id` may include a version (e.g. ``2401.08281v2``) or not
    (``2401.08281``). Legacy ids like ``cs.LG/0512001`` are accepted verbatim.

    Raises `ArxivNotFound` if the id returns an empty feed,
    `ArxivTransientError` for retryable failures (worker will re-enqueue).
    """
    xml_bytes = _get_with_retry(full_id, timeout=timeout)
    return _parse(xml_bytes, queried=full_id)


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #

def _get_with_retry(full_id: str, *, timeout: float) -> bytes:
    params = {"id_list": full_id, "max_results": 1}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml"}

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(
                timeout=timeout,
                headers=headers,
                follow_redirects=True,
            ) as client:
                resp = client.get(BASE_URL, params=params)
            if resp.status_code == 200:
                return resp.content
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", BACKOFF_BASE))
                log.warning("arxiv 429, sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                last_err = ArxivTransientError("429 Too Many Requests")
                continue
            if 500 <= resp.status_code < 600:
                last_err = ArxivTransientError(f"{resp.status_code} upstream")
                time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            # 4xx other than 429 is hard-fail
            raise ArxivTransientError(
                f"unexpected status {resp.status_code}: {resp.text[:200]}"
            )
        except httpx.HTTPError as exc:
            last_err = exc
            log.warning(
                "arxiv fetch attempt %s/%s failed: %s", attempt, MAX_ATTEMPTS, exc
            )
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
    raise ArxivTransientError(
        f"arxiv fetch exhausted retries for id={full_id}: {last_err}"
    )


# --------------------------------------------------------------------------- #
# Atom parsing
# --------------------------------------------------------------------------- #

def _parse(xml_bytes: bytes, *, queried: str) -> ArxivMeta:
    root = ET.fromstring(xml_bytes)
    entry = root.find("atom:entry", NS)
    if entry is None:
        raise ArxivNotFound(f"no <entry> in response for id={queried}")

    # arxiv returns a "search info" entry even when nothing is found; detect
    # by missing <arxiv:primary_category> AND missing <title>.
    title_el = entry.find("atom:title", NS)
    if title_el is None:
        raise ArxivNotFound(f"empty entry for id={queried}")

    meta = ArxivMeta(arxiv_id=queried)
    meta.title = _text(title_el)
    meta.abstract = _text(entry.find("atom:summary", NS))
    meta.published_at = _text(entry.find("atom:published", NS))
    meta.updated_at = _text(entry.find("atom:updated", NS))

    # authors (+ optional affiliations)
    for author in entry.findall("atom:author", NS):
        name = _text(author.find("atom:name", NS))
        if name:
            meta.authors.append(name)
        aff = _text(author.find("arxiv:affiliation", NS))
        if aff and aff not in meta.affiliations:
            meta.affiliations.append(aff)

    # categories
    prim = entry.find("arxiv:primary_category", NS)
    if prim is not None:
        meta.primary_category = prim.get("term")
    for cat in entry.findall("atom:category", NS):
        term = cat.get("term")
        if term and term not in meta.categories:
            meta.categories.append(term)

    # misc arxiv-namespaced
    meta.journal_ref = _text(entry.find("arxiv:journal_ref", NS))
    meta.comment = _text(entry.find("arxiv:comment", NS))
    meta.doi = _text(entry.find("arxiv:doi", NS))
    meta.license = _text(entry.find("arxiv:license", NS))

    # links
    for link in entry.findall("atom:link", NS):
        rel = link.get("rel")
        title = link.get("title")
        href = link.get("href")
        if rel == "alternate":
            meta.abs_link = href
        elif title == "pdf":
            meta.pdf_link = href

    return meta


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    # arxiv wraps abstract/title in whitespace + newlines; collapse them.
    return " ".join(t.split()) if t else None
