"""File-ingest pipeline: copy downloaded PDF into the hidden vault.

Current scope (MVP):
  - On `archive.completed`: verify the source file exists, copy it into
    `<vault>/YYYY/MM/<arxivId>/paper.pdf`, write a `meta.json` sidecar,
    and return the resolved vault path.
  - On `archive.requested`: no-op, just log. Reserved for future metadata
    prefetch (arxiv API, embedding, etc.).

Deliberately NOT done here yet:
  - LLM classification (Phase 2)
  - SQLite indexing (Phase 2)
  - Symlink view building (Phase 2)
  - arxiv API enrichment (Phase 2)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from paperprism_agent import __version__
from paperprism_agent import db as db_module
from paperprism_agent import repository, tasks
from paperprism_agent.config import Config
from paperprism_agent.models import IngestRequest, IngestResponse
from paperprism_agent.paths import resolve_vault

log = logging.getLogger("paperprism.ingest")

META_SCHEMA_VERSION = 1
CHUNK = 1 << 20  # 1 MiB for sha256 streaming


def handle_ingest(cfg: Config, req: IngestRequest) -> IngestResponse:
    """Dispatch on event type. Pure sync -- copying is fast enough for MVP."""
    if req.event == "archive.requested":
        log.info(
            "archive.requested arxivId=%s sourceUrl=%s",
            req.arxivId.fullId,
            req.sourceUrl,
        )
        return IngestResponse(
            accepted=True,
            status="queued",
            message="Noted; waiting for archive.completed to pull the file.",
        )

    return _handle_completed(cfg, req)


def _handle_completed(cfg: Config, req: IngestRequest) -> IngestResponse:
    if not req.downloadPath:
        return IngestResponse(
            accepted=False,
            message="archive.completed requires downloadPath",
        )

    src = Path(req.downloadPath).expanduser()
    try:
        src = src.resolve(strict=True)
    except FileNotFoundError:
        log.warning("source missing: %s", src)
        return IngestResponse(
            accepted=False,
            message=f"Source file not found on disk: {src}",
        )
    if not src.is_file():
        return IngestResponse(
            accepted=False,
            message=f"Source path is not a regular file: {src}",
        )

    vault_root = resolve_vault(req.vaultPathHint, cfg.paths.vault)
    vault_root.mkdir(parents=True, exist_ok=True)

    # Partition by time so a single directory never grows unbounded.
    now = datetime.now(timezone.utc)
    safe_id = req.arxivId.fullId.replace("/", "_")
    dest_dir = vault_root / f"{now.year:04d}" / f"{now.month:02d}" / safe_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_pdf = dest_dir / "paper.pdf"

    # De-duplicate by content hash: if the same file is already in the
    # vault we skip the copy and keep the existing meta.json.
    src_hash = _sha256(src)
    already = dest_pdf.exists() and _sha256(dest_pdf) == src_hash
    if not already:
        _atomic_copy(src, dest_pdf)

    meta_path = dest_dir / "meta.json"
    meta = _build_meta(
        req=req,
        src=src,
        dest=dest_pdf,
        sha256=src_hash,
        copied=not already,
    )
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log.info(
        "archive.completed arxivId=%s copied=%s dest=%s",
        req.arxivId.fullId,
        not already,
        dest_pdf,
    )

    # --- DB side: register the paper and enqueue the enrich task ------------
    # Failure here should NOT make ingest appear broken to the plugin -- the
    # file is already safely on disk. Log and move on.
    try:
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.upsert_paper(
            conn,
            full_id=req.arxivId.fullId,
            arxiv_id=req.arxivId.id,
            version=req.arxivId.version,
            is_legacy=req.arxivId.legacy,
            pdf_path=str(dest_pdf),
            vault_dir=str(dest_dir),
            source_url=req.sourceUrl,
            abs_url=req.absUrl,
            sha256=src_hash,
            size_bytes=dest_pdf.stat().st_size if dest_pdf.exists() else None,
        )
        tasks.enqueue(conn, paper_id=paper.id, kind="enrich")
    except Exception:
        log.exception("failed to register paper in DB; file is still on disk")

    return IngestResponse(
        accepted=True,
        vaultPath=str(dest_pdf),
        status="queued",
        message=(
            "File mirrored to vault; classification pending (Phase 2)."
            if not already
            else "File already present; metadata refreshed."
        ),
    )


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy into a tmp file next to dest, then rename; crash-safe."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        shutil.copy2(src, tmp)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_meta(
    *,
    req: IngestRequest,
    src: Path,
    dest: Path,
    sha256: str,
    copied: bool,
) -> dict:
    return {
        "schema_version": META_SCHEMA_VERSION,
        "id": req.arxivId.id,
        "version": req.arxivId.version,
        "fullId": req.arxivId.fullId,
        "legacy": req.arxivId.legacy,
        "source_url": req.sourceUrl,
        "abs_url": req.absUrl,
        "original_download_path": str(src),
        "vault_path": str(dest),
        "sha256": sha256,
        "size_bytes": dest.stat().st_size if dest.exists() else None,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "agent_version": __version__,
        "copied": copied,
        # Classification placeholder; to be filled by Phase 2.
        "classification": None,
    }
