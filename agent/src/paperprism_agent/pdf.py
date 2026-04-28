"""Minimal PDF inspection via PyMuPDF.

Only what P2.2 needs:
  - Extract the first N pages of text (default: 1) -- used for code URL
    scraping and, later, affiliation extraction.
  - Scan a text blob for a GitHub / GitLab / Bitbucket repo URL.

Everything is wrapped in a defensive try/except so a broken PDF cannot take
the worker down -- we fall back to empty text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf

log = logging.getLogger("paperprism.pdf")

# Capture only the repo root (owner/name), strip trailing punctuation.
# We accept github.com, gitlab.com, bitbucket.org and huggingface.co (very
# common for model cards that ship with papers).
_REPO_RE = re.compile(
    r"""
    \bhttps?://
    (?:www\.)?
    (?:github\.com|gitlab\.com|bitbucket\.org|huggingface\.co)
    /
    [\w.\-]+           # owner / user
    /
    [\w.\-]+           # repo / model
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Catch trailing punctuation that LaTeX PDFs often glue onto URLs.
_TRAILING_JUNK = ".,);]}>"

# Matches modern (YYMM.NNNNN[vN]) or legacy (archive/YYMMNNN[vN]) arxiv ids
# that a PDF header typically stamps on the first page.
_ARXIV_ID_RE = re.compile(
    r"""
    (?:arXiv:\s*)?
    (
        \d{4}\.\d{4,5}(?:v\d+)?          # modern form 2401.08281 / 2401.08281v2
        |
        [a-z][a-z\-\.]+/\d{7}(?:v\d+)?   # legacy form cs.LG/0512001
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Shape-level guard used by the worker to decide whether to hit arxiv.org.
_ARXIV_SHAPE_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z\-\.]+/\d{7}(?:v\d+)?)$",
    re.IGNORECASE,
)


@dataclass
class PdfHead:
    text: str
    n_pages: int
    extracted_pages: int


def read_head(pdf_path: Path, *, pages: int = 1) -> PdfHead:
    """Return plain text from the first `pages` pages. Never raises --
    on failure returns an empty PdfHead."""
    if not pdf_path.exists():
        log.warning("pdf missing: %s", pdf_path)
        return PdfHead(text="", n_pages=0, extracted_pages=0)

    try:
        with fitz.open(str(pdf_path)) as doc:
            n = doc.page_count
            take = min(pages, n)
            parts = []
            for i in range(take):
                try:
                    parts.append(doc.load_page(i).get_text("text") or "")
                except Exception as exc:
                    log.warning("pdf page %s extract failed: %s", i, exc)
            return PdfHead(
                text="\n".join(parts),
                n_pages=n,
                extracted_pages=take,
            )
    except Exception as exc:
        log.warning("pdf open failed for %s: %s", pdf_path, exc)
        return PdfHead(text="", n_pages=0, extracted_pages=0)


def find_code_url(*texts: str) -> str | None:
    """Return the first repo URL found across the given text blobs.

    Accepts any number of sources (abstract, first-page text, comment, ...)
    and returns the earliest match in reading order.
    """
    for blob in texts:
        if not blob:
            continue
        m = _REPO_RE.search(blob)
        if m:
            url = m.group(0).rstrip(_TRAILING_JUNK)
            return url
    return None


def extract_arxiv_id(*texts: str) -> str | None:
    """Return the first arxiv id found across the given text blobs, or None.

    Useful when a user-supplied PDF had its abstract-page arxiv stamp but
    no structured metadata on the caller side.
    """
    for blob in texts:
        if not blob:
            continue
        m = _ARXIV_ID_RE.search(blob)
        if m:
            return m.group(1)
    return None


def looks_like_arxiv_id(full_id: str | None) -> bool:
    """Cheap shape check -- True if ``full_id`` could be queried on arxiv."""
    if not full_id:
        return False
    return bool(_ARXIV_SHAPE_RE.match(full_id.strip()))


def extract_title_abstract(text: str) -> tuple[str | None, str | None]:
    """Heuristic title + abstract extraction from a PDF first-page text blob.

    Intended for papers that don't have an arxiv id (user's legacy folder
    of PDFs). Good enough to feed the LLM classifier; not exact science.
    """
    if not text:
        return None, None

    lines = [ln.strip() for ln in text.splitlines()]
    non_empty = [ln for ln in lines if ln]

    # --- Title: first "headline-ish" line (skip pure running headers). ---
    title: str | None = None
    for ln in non_empty[:15]:
        # Skip lines that look like running headers / submission stamps.
        if len(ln) < 6 or len(ln) > 300:
            continue
        low = ln.lower()
        if low.startswith("arxiv:") or low.startswith("preprint"):
            continue
        # Avoid pure footer page numbers etc.
        if ln.count(" ") == 0 and not any(c.isalpha() for c in ln):
            continue
        title = ln
        break

    # --- Abstract: text after an "Abstract" heading, else first long block. ---
    abstract: str | None = None
    lower_text = text.lower()
    anchor = lower_text.find("abstract")
    if anchor >= 0:
        # Grab up to 3000 chars after the word "abstract" for classifier.
        chunk = text[anchor + len("abstract"):].lstrip(" :.\n\r\t-—")
        # Stop at "1 Introduction" / "Introduction" / references cue.
        for stop in ("\n1 Introduction", "\n1. Introduction", "\nIntroduction",
                     "\nKeywords", "\nReferences"):
            cut = chunk.find(stop)
            if cut > 0:
                chunk = chunk[:cut]
                break
        abstract = " ".join(chunk.split())[:3000] or None
    else:
        # Fall back to the first big paragraph (>=150 chars).
        buf: list[str] = []
        for ln in non_empty[1:]:
            if len(ln) > 30:
                buf.append(ln)
            if sum(len(x) for x in buf) > 400:
                break
        joined = " ".join(buf).strip()
        abstract = joined[:3000] if len(joined) >= 150 else None

    return title, abstract
