"""FastAPI HTTP server exposing the Agent to the Chrome extension.

Endpoints:
  GET  /api/health  -> health probe
  POST /api/ingest  -> archive event handler

Auth:
  Optional. If `Config.token` is non-empty, requests must carry the same
  value in the `X-PaperPrism-Token` header. Empty token disables auth --
  acceptable because we only bind to 127.0.0.1.

CORS:
  chrome-extension://<id> origins need a permissive allowance. We accept
  any origin *on the loopback interface only*; combined with the 127.0.0.1
  bind, this is safe enough for a single-user local tool.
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from importlib import resources as pkg_resources
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from paperprism_agent import __version__
from paperprism_agent import db as db_module
from paperprism_agent import repository, tasks
from paperprism_agent.config import Config
from paperprism_agent.ingest import handle_ingest
from paperprism_agent.models import HealthResponse, IngestRequest, IngestResponse
from paperprism_agent.worker import Worker

log = logging.getLogger("paperprism.server")


def create_app(cfg: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Bootstrap local state that has to exist before any request.
        cfg.paths.ensure()
        _load_secrets_into_env(cfg)
        _materialize_default_dimensions(cfg)
        _materialize_default_llm(cfg)
        conn = db_module.connect(cfg.paths.db_file)
        reset_n = tasks.reset_stale_running(conn)
        if reset_n:
            log.info("crash recovery: %s stale running tasks reset", reset_n)

        worker: Worker | None = None
        if cfg.worker_enabled:
            worker = Worker(
                cfg=cfg,
                conn=conn,
                poll_interval=cfg.worker_poll_interval,
            )
            worker.start()
            app.state.worker = worker
        else:
            log.info("worker disabled via config; queue will not drain")
            app.state.worker = None

        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            db_module.close()

    app = FastAPI(
        title="PaperPrism Agent",
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Stash config on app state so route handlers can reach it without globals.
    app.state.cfg = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    def require_token(
        request: Request,
        x_paperprism_token: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = request.app.state.cfg.token
        if not expected:
            return  # auth disabled
        if x_paperprism_token != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing token")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            version=__version__,
            home=str(cfg.paths.home),
            vault=str(cfg.paths.vault),
        )

    @app.post(
        "/api/ingest",
        response_model=IngestResponse,
        dependencies=[Depends(require_token)],
    )
    def ingest(req: IngestRequest, request: Request) -> IngestResponse:
        cfg_local: Config = request.app.state.cfg
        return handle_ingest(cfg_local, req)

    @app.get("/")
    def root() -> dict:
        return {
            "name": "PaperPrism Agent",
            "version": __version__,
            "endpoints": ["/api/health", "/api/ingest", "/api/papers", "/api/tasks/stats", "/docs"],
        }

    @app.get("/api/papers")
    def list_papers(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        q: str | None = Query(None, description="FTS5 full-text search"),
        sort: str = Query("ingested_at", description="Sort column"),
        order: str = Query("desc", description="asc or desc"),
        domain: str | None = Query(None, description="Filter by domain"),
        affiliations: str | None = Query(None, description="Filter by affiliation"),
    ) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        items, total = repository.list_papers(
            conn, limit=limit, offset=offset,
            q=q, sort=sort, order=order,
            domain=domain, affiliations=affiliations,
        )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/dimensions/values")
    def dimension_values() -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        return repository.list_dimension_values(conn)

    @app.get("/api/tasks/stats")
    def task_stats() -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        return tasks.stats(conn)

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: int) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
        import json as _json
        for key in ("authors_json", "arxiv_categories_json", "affiliations_json"):
            raw = paper.pop(key, None)
            paper[key.removesuffix("_json")] = _json.loads(raw) if raw else []
        rows = conn.execute(
            """
            SELECT dim_name, value, numeric_value, confidence, model, classified_at
            FROM classifications WHERE paper_id = ?
            ORDER BY dim_name, value
            """,
            (paper_id,),
        ).fetchall()
        classifications: dict[str, list] = {}
        for r in rows:
            classifications.setdefault(r["dim_name"], []).append(
                {
                    "value": r["value"],
                    "numeric_value": r["numeric_value"],
                    "confidence": r["confidence"],
                    "model": r["model"],
                    "classified_at": r["classified_at"],
                }
            )
        paper["classifications"] = classifications
        return paper

    @app.get("/api/papers/{paper_id}/pdf")
    def get_paper_pdf(paper_id: int) -> FileResponse:
        """Stream the PDF file for a paper inline.

        Path must resolve inside the configured vault; otherwise 403.
        """
        from pathlib import Path as _Path

        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
        pdf_path_str = paper.get("pdf_path")
        if not pdf_path_str:
            raise HTTPException(status_code=404, detail="paper has no pdf")
        pdf_path = _Path(pdf_path_str)
        # Safety: refuse to serve files outside the vault root.
        try:
            pdf_path.resolve().relative_to(cfg.paths.vault.resolve())
        except (ValueError, OSError):
            log.warning("refusing to serve pdf outside vault: %s", pdf_path)
            raise HTTPException(status_code=403, detail="pdf outside vault")
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail=f"pdf file missing: {pdf_path}")
        filename = f"{paper.get('full_id') or paper_id}.pdf"
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=filename,
            content_disposition_type="inline",
        )

    @app.delete(
        "/api/papers/{paper_id}",
        dependencies=[Depends(require_token)],
    )
    def delete_paper(
        paper_id: int,
        remove_files: bool = Query(True, description="Also remove the vault directory on disk"),
    ) -> dict:
        import shutil as _shutil
        from pathlib import Path as _Path

        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")

        full_id = paper.get("full_id")
        vault_dir = paper.get("vault_dir")

        # Delete DB rows first (classifications/tasks cascade via FK).
        repository.delete_paper(conn, paper_id)

        files_removed = False
        if remove_files and vault_dir:
            vd = _Path(vault_dir)
            # Safety: refuse to delete anything outside the configured vault root.
            try:
                vd.resolve().relative_to(cfg.paths.vault.resolve())
            except (ValueError, OSError):
                log.warning(
                    "refusing to rm vault_dir outside of %s: %s", cfg.paths.vault, vd
                )
            else:
                if vd.exists():
                    try:
                        _shutil.rmtree(vd)
                        files_removed = True
                    except OSError:
                        log.exception("failed to rm %s", vd)

        log.info(
            "paper deleted id=%s full_id=%s files_removed=%s",
            paper_id, full_id, files_removed,
        )
        return {
            "deleted": True,
            "paper_id": paper_id,
            "full_id": full_id,
            "files_removed": files_removed,
        }

    return app


def _materialize_default_dimensions(cfg: Config) -> None:
    """On first run, copy the packaged dimensions YAML into PAPERPRISM_HOME
    so the user has an editable file to tweak."""
    target = cfg.paths.dimensions_file
    if target.exists():
        return
    try:
        src = pkg_resources.files("paperprism_agent.resources").joinpath(
            "dimensions.default.yaml"
        )
        with pkg_resources.as_file(src) as real:
            shutil.copy2(real, target)
        log.info("wrote default dimensions YAML to %s", target)
    except Exception:
        log.exception("failed to write default dimensions YAML")


def _materialize_default_llm(cfg: Config) -> None:
    """On first run, copy the packaged LLM YAML into PAPERPRISM_HOME."""
    target = cfg.paths.llm_config_file
    if target.exists():
        return
    try:
        src = pkg_resources.files("paperprism_agent.resources").joinpath(
            "llm.default.yaml"
        )
        with pkg_resources.as_file(src) as real:
            shutil.copy2(real, target)
        log.info("wrote default LLM YAML to %s", target)
    except Exception:
        log.exception("failed to write default LLM YAML")


def _load_secrets_into_env(cfg: Config) -> None:
    """Populate process env from ~/.paperprism/secrets.env if present.

    Under launchd the plist already bakes these in, but when `serve` is
    launched by the CLI (foreground or via subprocess), this is the only
    way the Agent sees keys like DASHSCOPE_API_KEY without the user
    having to export them in every shell.

    Never overrides an already-exported env var: the shell wins.
    """
    import os
    # Imported lazily to avoid a hard dep cycle at module import.
    from paperprism_agent.launchd import load_secrets

    secrets, warnings = load_secrets(cfg.paths.secrets_file)
    for w in warnings:
        log.warning("%s", w)
    applied: list[str] = []
    for key, value in secrets.items():
        if os.environ.get(key):
            continue  # existing env wins
        os.environ[key] = value
        applied.append(key)
    if applied:
        log.info("loaded %d secret(s) from %s: %s",
                 len(applied), cfg.paths.secrets_file, ", ".join(sorted(applied)))
