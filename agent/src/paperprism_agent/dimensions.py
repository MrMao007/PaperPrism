"""Dimension loader.

Reads ``~/.paperprism/dimensions.yaml`` and turns it into typed dataclasses
the classifier can reason about.

We do *lightweight* validation only: enough to keep the LLM prompt correct
and to reject broken config on startup, without turning into a schema
framework. Unknown fields are preserved verbatim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

log = logging.getLogger("paperprism.dimensions")

# What a dimension can be fed from.
SourceKind = Literal["metadata", "pdf", "llm", "llm+metadata"]

# Shape of the value we'll persist.
ValueKind = Literal["enum", "multi_enum", "number", "free_text", "date", "url", "text"]

ALLOWED_SOURCES: set[str] = {"metadata", "pdf", "llm", "llm+metadata"}
ALLOWED_KINDS: set[str] = {
    "enum", "multi_enum", "number", "free_text", "date", "url", "text",
}


@dataclass
class Dimension:
    name: str
    kind: ValueKind
    source: SourceKind
    description: str = ""
    options: list[str] = field(default_factory=list)
    max_items: int | None = None
    max_chars: int | None = None
    range: list[float] | None = None
    prompt: str | None = None

    @property
    def llm_involved(self) -> bool:
        return self.source in ("llm", "llm+metadata")

    @property
    def is_closed_set(self) -> bool:
        return self.kind == "enum" and bool(self.options)


@dataclass
class DimensionsConfig:
    version: int
    dimensions: list[Dimension]

    def llm_dimensions(self) -> list[Dimension]:
        return [d for d in self.dimensions if d.llm_involved]

    def by_name(self, name: str) -> Dimension | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


class DimensionsError(Exception):
    pass


def load(path: Path) -> DimensionsConfig:
    """Read and validate a dimensions YAML from disk."""
    if not path.exists():
        raise DimensionsError(f"dimensions file missing: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DimensionsError("dimensions YAML must be a mapping")

    version = int(raw.get("version", 1))
    dims_raw = raw.get("dimensions")
    if not isinstance(dims_raw, list) or not dims_raw:
        raise DimensionsError("dimensions YAML must contain a non-empty `dimensions` list")

    dims: list[Dimension] = []
    seen: set[str] = set()
    for i, item in enumerate(dims_raw):
        if not isinstance(item, dict):
            raise DimensionsError(f"dimensions[{i}] must be a mapping")
        d = _parse_one(item, idx=i)
        if d.name in seen:
            raise DimensionsError(f"duplicate dimension name: {d.name!r}")
        seen.add(d.name)
        dims.append(d)

    log.info(
        "loaded %d dimensions (%d LLM-involved) from %s",
        len(dims), sum(1 for d in dims if d.llm_involved), path,
    )
    return DimensionsConfig(version=version, dimensions=dims)


def _parse_one(raw: dict[str, Any], *, idx: int) -> Dimension:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise DimensionsError(f"dimensions[{idx}].name missing or not a string")
    kind = raw.get("kind")
    if kind not in ALLOWED_KINDS:
        raise DimensionsError(
            f"dimensions[{idx}].kind={kind!r} not in {sorted(ALLOWED_KINDS)}"
        )
    source = raw.get("source")
    if source not in ALLOWED_SOURCES:
        raise DimensionsError(
            f"dimensions[{idx}].source={source!r} not in {sorted(ALLOWED_SOURCES)}"
        )

    options_raw = raw.get("options") or []
    options: list[str] = []
    if options_raw:
        if not isinstance(options_raw, list):
            raise DimensionsError(f"dimensions[{idx}].options must be a list")
        for o in options_raw:
            if not isinstance(o, (str, int, float)):
                raise DimensionsError(
                    f"dimensions[{idx}].options items must be scalars"
                )
            options.append(str(o))

    rng = raw.get("range")
    if rng is not None:
        if (
            not isinstance(rng, list)
            or len(rng) != 2
            or not all(isinstance(x, (int, float)) for x in rng)
        ):
            raise DimensionsError(
                f"dimensions[{idx}].range must be [min, max] of numbers"
            )

    return Dimension(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        description=str(raw.get("description", "")),
        options=options,
        max_items=raw.get("max_items"),
        max_chars=raw.get("max_chars"),
        range=[float(x) for x in rng] if rng else None,
        prompt=raw.get("prompt"),
    )
