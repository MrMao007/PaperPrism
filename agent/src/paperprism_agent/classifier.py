"""Classifier: turn one paper + one dimensions config into classification rows.

Strategy (P2.3):
  - One LLM call per paper covers every LLM-involved dimension at once.
    Saves tokens, yields consistent outputs.
  - The prompt describes every LLM dimension plus its constraints (closed
    set, max_items, ...). The model must return a JSON object keyed by
    dimension name.
  - We post-validate: enum values are coerced to the closed set (unknown
    -> "Other" if available, else dropped); multi_enum is capped at
    max_items; URLs are lightly sanity-checked.
  - Results are flattened into `classifications` rows ready for
    ``repository.replace_classifications``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from paperprism_agent.dimensions import Dimension, DimensionsConfig
from paperprism_agent.llm import LLMClient

log = logging.getLogger("paperprism.classifier")

CLASSIFICATION_VERSION = 1

SYSTEM_PROMPT = (
    "You are a careful scientific paper classifier. "
    "Given a paper's title, abstract and metadata, you return a single JSON "
    "object describing it along the requested dimensions. "
    "You must use the exact dimension names as JSON keys. "
    "Use null when the evidence is insufficient -- never guess. "
    "Output ONLY the JSON object, no prose, no markdown."
)

# Extract the first balanced {...} block from mixed text, as a fallback
# when providers ignore `response_format`.
_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

# Light URL sanity check for url-typed dimensions.
_URL_RE = re.compile(r"^https?://[^\s]+$")


@dataclass
class PaperContext:
    """Everything the classifier needs about the paper."""
    full_id: str
    title: str | None
    abstract: str | None
    authors: list[str]
    arxiv_categories: list[str]
    journal_ref: str | None
    comment: str | None
    pdf_head_text: str | None


def has_enough_context(ctx: PaperContext) -> bool:
    """Refuse to burn tokens on nothing: require at least abstract or PDF text."""
    return bool((ctx.abstract or "").strip() or (ctx.pdf_head_text or "").strip())


def classify(
    *,
    ctx: PaperContext,
    config: DimensionsConfig,
    client: LLMClient,
) -> tuple[list[dict[str, Any]], str]:
    """Run the LLM and return (classification rows, model label).

    Each row is a dict matching ``repository.replace_classifications`` schema:
    ``{"dim_name", "value", "numeric_value", "confidence"}``.
    A multi-value dimension (multi_enum / affiliations) yields multiple rows.
    """
    dims = config.llm_dimensions()
    if not dims:
        return [], client.provider_label

    system, user = _build_prompt(ctx, dims, char_limits=(
        client.cfg.abstract_char_limit, client.cfg.pdf_head_char_limit,
    ))
    raw = client.chat_json(system=system, user=user)
    parsed = _parse_json(raw)
    log.debug("LLM raw output (first 300 chars): %s", raw[:300])

    rows: list[dict[str, Any]] = []
    for d in dims:
        value = parsed.get(d.name)
        rows.extend(_materialize(d, value))
    return rows, client.provider_label


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #

def _build_prompt(
    ctx: PaperContext,
    dims: list[Dimension],
    *,
    char_limits: tuple[int, int],
) -> tuple[str, str]:
    abstract_limit, pdf_limit = char_limits

    dim_specs: list[str] = []
    schema_sample: dict[str, Any] = {}
    for d in dims:
        spec = [f"- {d.name}: ({d.kind}) {d.description or ''}"]
        if d.options:
            spec.append(f"    Allowed values: {d.options}")
        if d.max_items:
            spec.append(f"    Max items: {d.max_items}")
        if d.max_chars:
            spec.append(f"    Max chars: {d.max_chars}")
        if d.prompt:
            spec.append(f"    Guidance: {d.prompt.strip()}")
        dim_specs.append("\n".join(spec))
        schema_sample[d.name] = _schema_hint(d)

    schema_blob = json.dumps(schema_sample, indent=2, ensure_ascii=False)

    user_parts: list[str] = []
    user_parts.append("## Paper")
    if ctx.title:
        user_parts.append(f"Title: {ctx.title}")
    if ctx.authors:
        user_parts.append(f"Authors: {', '.join(ctx.authors[:20])}")
    if ctx.arxiv_categories:
        user_parts.append(f"arXiv categories: {', '.join(ctx.arxiv_categories)}")
    if ctx.journal_ref:
        user_parts.append(f"arxiv journal_ref: {ctx.journal_ref}")
    if ctx.comment:
        user_parts.append(f"arxiv comment: {ctx.comment}")
    if ctx.abstract:
        user_parts.append(f"\nAbstract:\n{_truncate(ctx.abstract, abstract_limit)}")
    if ctx.pdf_head_text:
        user_parts.append(
            "\nPDF first-page text (may contain affiliations / code URL):\n"
            + _truncate(ctx.pdf_head_text, pdf_limit)
        )

    user_parts.append("\n## Dimensions to fill")
    user_parts.append("\n".join(dim_specs))

    user_parts.append(
        "\n## Output schema (return this exact shape, filling values; use null "
        "when unsure)"
    )
    user_parts.append(schema_blob)

    return SYSTEM_PROMPT, "\n".join(user_parts)


def _schema_hint(d: Dimension) -> Any:
    if d.kind == "multi_enum":
        return []
    if d.kind == "number":
        return None
    return None


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " [...]"


# --------------------------------------------------------------------------- #
# Parsing + validation
# --------------------------------------------------------------------------- #

def _parse_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        raise ValueError("LLM response did not contain a JSON object")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM JSON still invalid after recovery: {exc}") from exc


def _materialize(d: Dimension, value: Any) -> list[dict[str, Any]]:
    """Translate one LLM output value into zero-or-more DB rows."""
    if value is None or value == "" or value == []:
        return []

    if d.kind == "multi_enum":
        if not isinstance(value, list):
            value = [value]
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for v in value:
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            if d.options and s not in d.options:
                s = _coerce_enum(s, d.options)
                if s is None:
                    continue
            seen.add(s)
            rows.append({"dim_name": d.name, "value": s})
            if d.max_items and len(rows) >= d.max_items:
                break
        return rows

    if d.kind == "enum":
        s = str(value).strip()
        if d.options and s not in d.options:
            coerced = _coerce_enum(s, d.options)
            if coerced is None:
                return []
            s = coerced
        return [{"dim_name": d.name, "value": s}]

    if d.kind == "number":
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return []
        if d.range and not (d.range[0] <= numeric <= d.range[1]):
            return []
        return [
            {
                "dim_name": d.name,
                "value": str(numeric),
                "numeric_value": numeric,
            }
        ]

    if d.kind == "url":
        s = str(value).strip()
        if not _URL_RE.match(s):
            return []
        return [{"dim_name": d.name, "value": s}]

    # text / free_text / date
    s = str(value).strip()
    if d.max_chars and len(s) > d.max_chars:
        s = s[: d.max_chars]
    if not s:
        return []
    return [{"dim_name": d.name, "value": s}]


def _coerce_enum(val: str, options: list[str]) -> str | None:
    """Map an off-list value into the closed set, case-insensitively, else
    fall back to 'Other' if present, else drop."""
    low = val.lower()
    for opt in options:
        if opt.lower() == low:
            return opt
    if "Other" in options:
        return "Other"
    return None
