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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from paperprism_agent import __version__
from paperprism_agent import arxiv_client
from paperprism_agent import db as db_module
from paperprism_agent import llm as llm_module
from paperprism_agent import pdf as pdf_module
from paperprism_agent import repository, tasks
from paperprism_agent.config import Config
from paperprism_agent.events import Actor, Event, EventLogger
from paperprism_agent.models import IngestRequest, IngestResponse, UploadIngestResponse
from paperprism_agent.paths import resolve_vault

log = logging.getLogger("paperprism.ingest")

META_SCHEMA_VERSION = 1
CHUNK = 1 << 20  # 1 MiB for sha256 streaming


def handle_ingest(cfg: Config, req: IngestRequest, *, actor: Actor = "user") -> IngestResponse:
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

    return _handle_completed(cfg, req, actor=actor)


def _handle_completed(cfg: Config, req: IngestRequest, *, actor: Actor = "user") -> IngestResponse:
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
        conn.execute("BEGIN")
        try:
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
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="paper.ingested.downloaded",
                    subject_type="paper",
                    subject_id=req.arxivId.id,
                    payload={
                        "source_url": req.sourceUrl,
                        "filename": dest_pdf.name,
                        "vault_path": str(dest_pdf),
                        "sha256": src_hash,
                    },
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
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


def handle_upload(
    cfg: Config,
    *,
    file_bytes: bytes,
    filename: str,
    source_hint: str | None = None,
    actor: Actor = "user",
) -> UploadIngestResponse:
    """Ingest a user-supplied PDF (bulk-folder import path).

    Flow:
      1. Write the bytes to a temp file in the vault root and sha256 them.
      2. If an existing paper already has this sha256 -> return duplicate.
      3. Peek at the first page text; if it contains an arxiv id we
         re-use the arxiv-flavoured vault layout + enrich path; otherwise
         we file it under ``vault/local/YYYY/MM/local-<sha>/`` and use a
         synthetic ``full_id`` so the rest of the pipeline still works.
      4. Enqueue an enrich task (worker handles the non-arxiv branch).
    """
    if not filename.lower().endswith(".pdf"):
        return UploadIngestResponse(
            accepted=False,
            status="rejected",
            message="Only .pdf files are accepted",
        )
    if not file_bytes:
        return UploadIngestResponse(
            accepted=False,
            status="rejected",
            message="Empty file",
        )

    vault_root = cfg.paths.vault
    vault_root.mkdir(parents=True, exist_ok=True)

    # 1) stage the bytes to a temp file so we can hash / peek without
    #    committing to a final vault path yet.
    staging_dir = vault_root / ".uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    tmp_path = staging_dir / f"upload-{stamp}.pdf"
    try:
        tmp_path.write_bytes(file_bytes)

        sha = _sha256(tmp_path)

        # 2) duplicate check ------------------------------------------------
        conn = db_module.connect(cfg.paths.db_file)
        existing = repository.find_paper_by_sha256(conn, sha)
        if existing is not None:
            return UploadIngestResponse(
                accepted=True,
                duplicate=True,
                paperId=existing["id"],
                fullId=existing.get("full_id"),
                arxivId=existing.get("arxiv_id"),
                vaultPath=existing.get("pdf_path"),
                title=existing.get("title"),
                status="duplicate",
                message="File already present in the library; skipped.",
            )

        # 3) peek at first page ---------------------------------------------
        head = pdf_module.read_head(tmp_path, pages=1)
        arxiv_hit = _resolve_arxiv_id(
            cfg=cfg,
            filename=filename,
            pdf_path=tmp_path,
            head_text=head.text,
        )

        now = datetime.now(timezone.utc)
        if arxiv_hit:
            full_id = arxiv_hit
            arxiv_id_plain = arxiv_hit.split("v")[0] if "v" in arxiv_hit else arxiv_hit
            version = arxiv_hit[len(arxiv_id_plain):] or None
            is_legacy = "/" in arxiv_hit
            safe_id = full_id.replace("/", "_")
            dest_dir = vault_root / f"{now.year:04d}" / f"{now.month:02d}" / safe_id
        else:
            full_id = f"local-{sha[:12]}"
            arxiv_id_plain = full_id
            version = None
            is_legacy = False
            dest_dir = vault_root / "local" / f"{now.year:04d}" / f"{now.month:02d}" / full_id

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_pdf = dest_dir / "paper.pdf"
        # If another sha-collision-free upload already landed here (rare),
        # we still overwrite -- the DB-level sha dedupe above is authoritative.
        shutil.move(str(tmp_path), str(dest_pdf))

        # 4) meta.json sidecar ---------------------------------------------
        meta = {
            "schema_version": META_SCHEMA_VERSION,
            "id": arxiv_id_plain,
            "version": version,
            "fullId": full_id,
            "legacy": is_legacy,
            "source_url": None,
            "abs_url": None,
            "original_download_path": source_hint or filename,
            "vault_path": str(dest_pdf),
            "sha256": sha,
            "size_bytes": dest_pdf.stat().st_size if dest_pdf.exists() else None,
            "ingested_at": now.isoformat(),
            "agent_version": __version__,
            "copied": True,
            "classification": None,
            "imported": True,
        }
        (dest_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 5) register + queue enrich ---------------------------------------
        conn.execute("BEGIN")
        try:
            paper = repository.upsert_paper(
                conn,
                full_id=full_id,
                arxiv_id=arxiv_id_plain,
                version=version,
                is_legacy=is_legacy,
                pdf_path=str(dest_pdf),
                vault_dir=str(dest_dir),
                source_url=None,
                abs_url=None,
                sha256=sha,
                size_bytes=dest_pdf.stat().st_size if dest_pdf.exists() else None,
            )
            tasks.enqueue(conn, paper_id=paper.id, kind="enrich")
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="paper.ingested.uploaded",
                    subject_type="paper",
                    subject_id=arxiv_id_plain,
                    payload={
                        "filename": filename,
                        "size_bytes": len(file_bytes),
                        "vault_path": str(dest_pdf),
                        "sha256": sha,
                    },
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        log.info(
            "upload ingested paper_id=%s full_id=%s dest=%s",
            paper.id, full_id, dest_pdf,
        )
        return UploadIngestResponse(
            accepted=True,
            duplicate=False,
            paperId=paper.id,
            fullId=full_id,
            arxivId=arxiv_hit,
            vaultPath=str(dest_pdf),
            title=None,
            status="queued",
            message="Queued for enrichment + classification.",
        )
    finally:
        # Clean up the staging file on failure (on success it was moved).
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


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


# --------------------------------------------------------------------------- #
# arxiv id resolution for bulk-folder upload
# --------------------------------------------------------------------------- #
#
# Strategy (per user spec):
#   Step 1: treat the filename as an arxiv id candidate; verify via arxiv API.
#   Step 2: if step 1 fails, ask the LLM to extract an arxiv id from the PDF
#           first-page text; verify via arxiv API.
# If both steps fail we fall back to the local-<sha> path upstream.

_ARXIV_ID_STR_RE = re.compile(
    r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z\-\.]+/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)

_LLM_EXTRACT_SYSTEM = (
    "You extract the arXiv identifier from a paper's first-page text. "
    "Return a single JSON object exactly matching the schema "
    '{"arxiv_id": string | null}. '
    "The value must be the canonical arxiv id (e.g. \"2504.19413\", "
    "\"2504.19413v1\", or the legacy form \"cs.LG/0512001\"). "
    "Use null when no arxiv id is present or you are not confident. "
    "Output ONLY the JSON object, no prose."
)


def _candidate_from_filename(filename: str) -> str | None:
    """Pull an arxiv-id-shaped token out of the upload's filename.

    Strips the directory prefix and ``.pdf`` suffix, then:
      1. tries the whole stem (so ``2504.19413v1.pdf`` maps cleanly),
      2. otherwise scans for the first arxiv-shape substring
         (so ``Attention_1706.03762.pdf`` still works).
    Returns None when no candidate is found.
    """
    if not filename:
        return None
    stem = Path(filename).name
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = stem.strip()
    if pdf_module.looks_like_arxiv_id(stem):
        return stem
    m = _ARXIV_ID_STR_RE.search(stem)
    return m.group(1) if m else None


def _verify_on_arxiv(full_id: str) -> bool:
    """Return True iff arxiv API actually returns an entry for ``full_id``.

    Transient errors (network, 5xx) are logged and treated as failure --
    the caller will fall back to the next resolution step.
    """
    if not pdf_module.looks_like_arxiv_id(full_id):
        return False
    try:
        arxiv_client.fetch_by_id(full_id)
        return True
    except arxiv_client.ArxivNotFound:
        return False
    except arxiv_client.ArxivTransientError as exc:
        log.warning("arxiv verify failed for %s (transient): %s", full_id, exc)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("arxiv verify crashed for %s: %s", full_id, exc)
        return False


def _llm_extract_arxiv_id(cfg: Config, head_text: str) -> str | None:
    """Ask the configured LLM to pull the arxiv id from the PDF head text.

    Any config / network / parse error is swallowed (returns None); the
    caller will fall back to the synthetic ``local-<sha>`` path.
    """
    if not head_text or not head_text.strip():
        return None
    try:
        llm_cfg = llm_module.LLMConfig.load(cfg.paths.llm_config_file)
        client = llm_module.LLMClient(llm_cfg)
    except Exception as exc:  # noqa: BLE001
        log.info("LLM unavailable for arxiv-id extraction: %s", exc)
        return None
    # Cap head_text so we don't blow the prompt budget on huge first pages.
    snippet = head_text[: llm_cfg.pdf_head_char_limit]
    try:
        raw = client.chat_json(
            system=_LLM_EXTRACT_SYSTEM,
            user=f"Paper first-page text (truncated):\n\n{snippet}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM arxiv-id extraction failed: %s", exc)
        return None
    candidate = _parse_llm_arxiv_id(raw)
    if not candidate:
        return None
    return candidate if pdf_module.looks_like_arxiv_id(candidate) else None


def _parse_llm_arxiv_id(raw: str) -> str | None:
    """Best-effort JSON extraction tolerant of stray text around the object."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("arxiv_id")
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _resolve_arxiv_id(
    *,
    cfg: Config,
    filename: str,
    pdf_path: Path,  # kept for future signature growth; not used today
    head_text: str,
) -> str | None:
    """Two-step arxiv id resolution.

    1. Derive a candidate from the filename and verify it on arxiv.org.
    2. Ask the LLM to extract a candidate from the PDF first-page text
       and verify it on arxiv.org.
    Returns the verified id, or None if both steps fail.
    """
    del pdf_path  # reserved

    # Step 1: filename -> arxiv API ------------------------------------------
    cand = _candidate_from_filename(filename)
    if cand:
        log.info("arxiv resolve step1: filename candidate=%s", cand)
        if _verify_on_arxiv(cand):
            log.info("arxiv resolve step1 confirmed via arxiv API: %s", cand)
            return cand
        log.info("arxiv resolve step1 rejected by arxiv API: %s", cand)

    # Step 2: LLM extraction -> arxiv API ------------------------------------
    cand = _llm_extract_arxiv_id(cfg, head_text)
    if cand:
        log.info("arxiv resolve step2: llm candidate=%s", cand)
        if _verify_on_arxiv(cand):
            log.info("arxiv resolve step2 confirmed via arxiv API: %s", cand)
            return cand
        log.info("arxiv resolve step2 rejected by arxiv API: %s", cand)

    log.info("arxiv resolve: no verified id for filename=%s", filename)
    return None


# --------------------------------------------------------------------------- #
# Feed ingest: add a feed paper to the library by downloading its PDF
# --------------------------------------------------------------------------- #

_FEED_PDF_TIMEOUT = 120.0
_FEED_USER_AGENT = "PaperPrism-Agent (+https://github.com/paperprism; local)"


def handle_ingest_feed(
    cfg: Config,
    *,
    arxiv_id: str,
    actor: Actor = "user",
) -> UploadIngestResponse:
    """Ingest a feed paper by downloading its PDF from arXiv.

    Flow:
      1. Check if the paper already exists in the library (return duplicate).
      2. Download PDF from ``https://arxiv.org/pdf/<arxiv_id>``.
      3. Save to vault, compute sha256.
      4. ``upsert_paper`` + enqueue enrich + emit ``paper.ingested.from_feed``.
    """
    import httpx

    conn = db_module.connect(cfg.paths.db_file)

    # 1) duplicate check
    existing = repository.find_paper_by_arxiv_id(conn, arxiv_id)
    if existing is not None and existing["deleted_at"] is None:
        return UploadIngestResponse(
            accepted=True,
            duplicate=True,
            paperId=existing["id"],
            fullId=existing.get("full_id"),
            arxivId=arxiv_id,
            vaultPath=existing.get("pdf_path"),
            title=existing.get("title"),
            status="duplicate",
            message="Paper already in your library.",
        )

    # 2) download PDF from arXiv
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    log.info("Downloading feed paper PDF: %s", pdf_url)
    try:
        with httpx.Client(
            timeout=_FEED_PDF_TIMEOUT,
            headers={"User-Agent": _FEED_USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(pdf_url)
            resp.raise_for_status()
            pdf_bytes = resp.content
    except httpx.HTTPError as exc:
        log.error("Failed to download feed PDF %s: %s", pdf_url, exc)
        return UploadIngestResponse(
            accepted=False,
            status="rejected",
            message=f"Failed to download PDF from arXiv: {exc}",
        )

    if not pdf_bytes or len(pdf_bytes) < 100:
        return UploadIngestResponse(
            accepted=False,
            status="rejected",
            message="Downloaded PDF is empty or too small.",
        )

    # 3) save to vault
    vault_root = cfg.paths.vault
    vault_root.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    safe_id = arxiv_id.replace("/", "_")
    dest_dir = vault_root / f"{now.year:04d}" / f"{now.month:02d}" / safe_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_pdf = dest_dir / "paper.pdf"

    _atomic_copy_from_bytes(pdf_bytes, dest_pdf)
    sha = _sha256(dest_pdf)

    # Write meta.json sidecar
    full_id = arxiv_id
    arxiv_id_plain = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
    version = arxiv_id[len(arxiv_id_plain):] or None
    is_legacy = "/" in arxiv_id

    meta = {
        "schema_version": META_SCHEMA_VERSION,
        "id": arxiv_id_plain,
        "version": version,
        "fullId": full_id,
        "legacy": is_legacy,
        "source_url": pdf_url,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "original_download_path": pdf_url,
        "vault_path": str(dest_pdf),
        "sha256": sha,
        "size_bytes": dest_pdf.stat().st_size if dest_pdf.exists() else None,
        "ingested_at": now.isoformat(),
        "agent_version": __version__,
        "copied": True,
        "classification": None,
        "imported": True,
        "source": "feed",
    }
    (dest_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4) register in DB + enqueue enrich + emit event
    try:
        conn.execute("BEGIN")
        try:
            paper = repository.upsert_paper(
                conn,
                full_id=full_id,
                arxiv_id=arxiv_id_plain,
                version=version,
                is_legacy=is_legacy,
                pdf_path=str(dest_pdf),
                vault_dir=str(dest_dir),
                source_url=pdf_url,
                abs_url=f"https://arxiv.org/abs/{arxiv_id}",
                sha256=sha,
                size_bytes=dest_pdf.stat().st_size if dest_pdf.exists() else None,
            )
            tasks.enqueue(conn, paper_id=paper.id, kind="enrich")
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="paper.ingested.from_feed",
                    subject_type="paper",
                    subject_id=arxiv_id_plain,
                    payload={
                        "source_url": pdf_url,
                        "filename": dest_pdf.name,
                        "vault_path": str(dest_pdf),
                        "sha256": sha,
                    },
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except Exception:
        log.exception("Failed to register feed paper in DB; file is still on disk")
        return UploadIngestResponse(
            accepted=False,
            status="rejected",
            message="Failed to register paper in database.",
        )

    log.info(
        "Feed paper ingested paper_id=%s arxiv_id=%s dest=%s",
        paper.id, arxiv_id, dest_pdf,
    )
    return UploadIngestResponse(
        accepted=True,
        duplicate=False,
        paperId=paper.id,
        fullId=full_id,
        arxivId=arxiv_id,
        vaultPath=str(dest_pdf),
        title=None,
        status="queued",
        message="Downloaded from arXiv; queued for enrichment.",
    )


def _atomic_copy_from_bytes(data: bytes, dest: Path) -> None:
    """Write bytes to a tmp file next to dest, then rename; crash-safe."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
