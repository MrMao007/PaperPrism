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
