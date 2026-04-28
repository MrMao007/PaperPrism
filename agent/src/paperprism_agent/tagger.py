"""Auto-tag a batch of papers using the configured LLM.

The public surface is three pure functions so the job-manager can drive them:

    plan_batches(paper_ids, batch_size) -> list[list[int]]
    run_batch(client, paper_ctxs, existing_top_tags) -> BatchResult
    summarize_topic(client, titles, ranked_tags) -> TopicDraft

Design choices:
  - No DB access in this module. Callers pass in paper context dicts.
  - Tag output is normalised (lowercase, spaces/underscores -> hyphens,
    trimmed) so duplicates collapse in the `tags` table UNIQUE index.
  - Per-paper tag budget is enforced here so the LLM can't tag-spam us.
  - The optional ``existing_top_tags`` hint is injected into later batches
    to keep tag vocabulary consistent across batches of the same job.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from paperprism_agent.llm import LLMClient

log = logging.getLogger("paperprism.tagger")

# Per-paper cap: default; callers can override per-job via
# ``max_tags_per_paper`` on run_batch/parse_batch_response. We still
# clamp to HARD_MAX_TAGS_PER_PAPER so a bad UI can't tag-spam.
MAX_TAGS_PER_PAPER = 5
HARD_MAX_TAGS_PER_PAPER = 10
# Total hint tags injected into a batch prompt.
HINT_TAG_LIMIT = 30
# Characters of abstract fed into the prompt per paper (cheaper than full).
ABSTRACT_SNIPPET_CHARS = 900

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
_SLUG_KEEP_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# Tag name normalisation
# --------------------------------------------------------------------------- #

def normalize_tag(name: str) -> str:
    """Canonical form stored in ``tags.name``.

    Lowercased, stripped, whitespace/underscores collapsed into single
    hyphens. Non-ascii letters are preserved so we don't mangle CJK labels.
    """
    s = (name or "").strip().lower()
    # unify separators first
    s = re.sub(r"[\s_/]+", "-", s)
    # collapse runs of hyphens
    s = re.sub(r"-{2,}", "-", s)
    # strip leading/trailing punctuation
    s = s.strip("-.,;:!?()[]{}\"'`")
    return s


def slugify(text: str, *, fallback: str = "topic") -> str:
    """ASCII-only, url-safe slug. Multi-hyphen collapsed, short."""
    s = (text or "").strip().lower()
    s = _SLUG_KEEP_RE.sub("-", s).strip("-")
    if not s:
        s = fallback
    return s[:60]


# --------------------------------------------------------------------------- #
# Batching
# --------------------------------------------------------------------------- #

def plan_batches(paper_ids: list[int], batch_size: int) -> list[list[int]]:
    if batch_size <= 0:
        batch_size = 15
    # stable order; callers shuffle beforehand if they want.
    return [
        paper_ids[i : i + batch_size]
        for i in range(0, len(paper_ids), batch_size)
    ]


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #

_BATCH_SYSTEM_TEMPLATE = (
    "You are a research librarian tagging papers for a personal paper manager. "
    "You respond with strict JSON only, no prose, no markdown fences. "
    "Each paper should receive between {min_tags} and {max_tags} short tags "
    "describing its topic, method family, or application area. "
    "Prefer 1-3 word kebab-case tags (e.g. 'mixture-of-experts', "
    "'speech-synthesis'). Avoid author names, arxiv ids, or venue names. "
    "Reuse tags across papers when the papers are closely related."
)


def _batch_system(max_tags: int) -> str:
    min_tags = 2 if max_tags >= 2 else 1
    return _BATCH_SYSTEM_TEMPLATE.format(min_tags=min_tags, max_tags=max_tags)

TOPIC_SYSTEM = (
    "You are a research librarian summarising a themed collection of papers. "
    "You respond with strict JSON only, no prose, no markdown. "
    "The `name` should be concise (<= 60 chars), Title Case, and capture the "
    "shared theme. `summary` is 1-3 sentences, plain prose, describing what "
    "ties these papers together."
)


def build_batch_prompt(
    *,
    paper_ctxs: list[dict[str, Any]],
    existing_top_tags: list[str] | None = None,
    max_tags_per_paper: int = MAX_TAGS_PER_PAPER,
) -> tuple[str, str]:
    """Given paper ctx dicts (title, abstract, categories, first_author),
    build the (system, user) pair that instructs the LLM to output tags for
    each paper_id."""

    lines: list[str] = []
    if existing_top_tags:
        hint = ", ".join(existing_top_tags[:HINT_TAG_LIMIT])
        lines.append(
            "## Already-used tags in this batch run (prefer reusing these when they fit):\n"
            f"{hint}\n"
        )

    lines.append("## Papers")
    for ctx in paper_ctxs:
        pid = ctx["paper_id"]
        title = (ctx.get("title") or "").strip() or ctx.get("full_id") or f"paper {pid}"
        abstract = (ctx.get("abstract") or "").strip()
        if len(abstract) > ABSTRACT_SNIPPET_CHARS:
            abstract = abstract[:ABSTRACT_SNIPPET_CHARS].rsplit(" ", 1)[0] + " [...]"
        cats = ", ".join(ctx.get("arxiv_categories") or [])
        block = [f"### paper_id={pid}", f"Title: {title}"]
        if cats:
            block.append(f"arXiv categories: {cats}")
        if abstract:
            block.append(f"Abstract: {abstract}")
        else:
            block.append("Abstract: (unavailable)")
        lines.append("\n".join(block))

    lines.append(
        "\n## Output schema (return this exact JSON shape):\n"
        "{\n"
        '  "papers": [\n'
        '    {"paper_id": <int>, "tags": ["tag-one", "tag-two"]}\n'
        "  ],\n"
        '  "batch_hint_tags": ["..."]   // up to 8 representative tags for this batch\n'
        "}\n"
        "Output ONLY the JSON. Do not invent paper_ids; only use those listed above."
    )

    return _batch_system(max_tags_per_paper), "\n\n".join(lines)


def build_topic_prompt(
    *,
    titles: list[str],
    ranked_tags: list[tuple[str, int]],
) -> tuple[str, str]:
    tags_blob = ", ".join(f"{t} ({c})" for t, c in ranked_tags[:25]) or "(none)"
    sample_titles = "\n".join(f"- {t[:180]}" for t in titles[:30])
    user = (
        "The following papers were batch-tagged together as one themed collection.\n\n"
        f"## Most common tags (with frequency)\n{tags_blob}\n\n"
        f"## Representative titles\n{sample_titles}\n\n"
        "Return JSON:\n"
        "{\n"
        '  "name": "...",           // Title Case, <= 60 chars\n'
        '  "summary": "...",        // 1-3 sentences\n'
        '  "slug_hint": "..."       // 3-5 ASCII words, lowercase, hyphenated\n'
        "}"
    )
    return TOPIC_SYSTEM, user


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _parse_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        raise ValueError("LLM response was not JSON")
    return json.loads(m.group(0))


@dataclass
class BatchResult:
    """Output of a single LLM batch call."""
    per_paper: dict[int, list[str]] = field(default_factory=dict)
    hint_tags: list[str] = field(default_factory=list)
    raw_preview: str = ""


def parse_batch_response(
    raw: str,
    *,
    allowed_paper_ids: set[int],
    max_tags_per_paper: int = MAX_TAGS_PER_PAPER,
) -> BatchResult:
    """Strict-ish parser: we trust the JSON shape but re-clamp the tag
    counts, discard unknown paper_ids, and normalise tag names."""
    data = _parse_json(raw)
    out = BatchResult(raw_preview=raw[:400])
    clamp = max(1, min(HARD_MAX_TAGS_PER_PAPER, max_tags_per_paper))

    papers = data.get("papers") or []
    if not isinstance(papers, list):
        raise ValueError("`papers` field must be a list")

    for entry in papers:
        if not isinstance(entry, dict):
            continue
        try:
            pid = int(entry.get("paper_id"))
        except (TypeError, ValueError):
            continue
        if pid not in allowed_paper_ids:
            continue
        raw_tags = entry.get("tags") or []
        if not isinstance(raw_tags, list):
            continue
        seen: list[str] = []
        for t in raw_tags:
            norm = normalize_tag(str(t))
            if not norm or norm in seen:
                continue
            seen.append(norm)
            if len(seen) >= clamp:
                break
        if seen:
            out.per_paper[pid] = seen

    hints_raw = data.get("batch_hint_tags") or []
    if isinstance(hints_raw, list):
        for h in hints_raw:
            norm = normalize_tag(str(h))
            if norm and norm not in out.hint_tags:
                out.hint_tags.append(norm)

    return out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def run_batch(
    *,
    client: LLMClient,
    paper_ctxs: list[dict[str, Any]],
    existing_top_tags: list[str] | None = None,
    max_tags_per_paper: int = MAX_TAGS_PER_PAPER,
) -> BatchResult:
    """One LLM call; returns parsed+validated tags. Exceptions propagate."""
    system, user = build_batch_prompt(
        paper_ctxs=paper_ctxs,
        existing_top_tags=existing_top_tags,
        max_tags_per_paper=max_tags_per_paper,
    )
    raw = client.chat_json(system=system, user=user)
    allowed = {c["paper_id"] for c in paper_ctxs}
    result = parse_batch_response(
        raw,
        allowed_paper_ids=allowed,
        max_tags_per_paper=max_tags_per_paper,
    )
    log.info(
        "batch tagged %s/%s papers (cap=%s), %s hint tags",
        len(result.per_paper), len(allowed), max_tags_per_paper, len(result.hint_tags),
    )
    return result


@dataclass
class TopicDraft:
    name: str
    summary: str
    slug_hint: str


def summarize_topic(
    *,
    client: LLMClient,
    titles: list[str],
    tag_counts: Counter[str],
) -> TopicDraft:
    """Second-stage LLM call: name + one-liner summary for the collection.

    Never raises: on failure we fall back to using the top tag as a name
    (so the job can still `done` instead of `failed`).
    """
    ranked = tag_counts.most_common()
    system, user = build_topic_prompt(titles=titles, ranked_tags=ranked)
    try:
        raw = client.chat_json(system=system, user=user)
        data = _parse_json(raw)
        name = str(data.get("name") or "").strip()[:80]
        summary = str(data.get("summary") or "").strip()
        slug_hint = str(data.get("slug_hint") or "").strip()
        if not name:
            raise ValueError("topic name missing")
        return TopicDraft(
            name=name,
            summary=summary,
            slug_hint=slugify(slug_hint or name),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("topic summarisation failed, using fallback: %s", exc)
        fallback_name = (
            ranked[0][0].replace("-", " ").title() if ranked else "Untitled Topic"
        )
        return TopicDraft(
            name=fallback_name,
            summary="",
            slug_hint=slugify(fallback_name),
        )
