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

import json as _json
import logging
import shutil
from contextlib import asynccontextmanager
from importlib import resources as pkg_resources
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from paperprism_agent import __version__
from paperprism_agent import db as db_module
from paperprism_agent import auto_tag_jobs, repository, tasks
from paperprism_agent.config import Config
from paperprism_agent.events import Actor
from paperprism_agent.ingest import handle_ingest, handle_ingest_feed, handle_upload
from paperprism_agent.llm import LLMClient, LLMConfig, LLMConfigError, LLMError
from paperprism_agent.models import (
    EventItem,
    EventsListResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    IngestFeedRequest,
    TimelineResponse,
    TrackEventBody,
    UploadIngestResponse,
)
from paperprism_agent.navigator import map_data as navigator_map_data
from paperprism_agent.weekly_digest import maybe_generate_digest
from paperprism_agent.worker import Worker

log = logging.getLogger("paperprism.server")


def _actor_from_request(request: Request) -> Actor:
    """Read the X-PaperPrism-Actor header; default to 'agent'."""
    raw = (request.headers.get("X-PaperPrism-Actor") or "").strip().lower()
    if raw in {"user", "agent", "llm", "system"}:
        return raw  # type: ignore[return-value]
    return "agent"


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

        # Check if a weekly digest needs to be generated.
        try:
            maybe_generate_digest(cfg, conn)
        except Exception:
            log.exception("weekly digest generation failed (non-fatal)")

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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
        actor = _actor_from_request(request)
        return handle_ingest(cfg_local, req, actor=actor)

    @app.post(
        "/api/ingest/upload",
        response_model=UploadIngestResponse,
        dependencies=[Depends(require_token)],
    )
    async def ingest_upload(
        request: Request,
        file: UploadFile = File(...),
        source_hint: str | None = Form(default=None),
    ) -> UploadIngestResponse:
        """Ingest a single user-supplied PDF (Dashboard bulk-folder import).

        Content-Type: multipart/form-data. The browser can't give us an OS
        path, so we accept the raw bytes and dedupe by sha256.
        """
        cfg_local: Config = request.app.state.cfg
        actor = _actor_from_request(request)
        try:
            data = await file.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"could not read upload: {exc}")
        filename = file.filename or "upload.pdf"
        return handle_upload(
            cfg_local,
            file_bytes=data,
            filename=filename,
            source_hint=source_hint,
            actor=actor,
        )

    @app.post(
        "/api/ingest/feed",
        response_model=UploadIngestResponse,
        dependencies=[Depends(require_token)],
    )
    def ingest_feed(request: Request, body: IngestFeedRequest) -> UploadIngestResponse:
        """Ingest a feed paper by downloading its PDF from arXiv.

        This is the Atlas "Add to Library" action.  The Agent downloads
        the PDF, registers it in the vault, and emits a
        ``paper.ingested.from_feed`` Ledger event.
        """
        cfg_local: Config = request.app.state.cfg
        actor = _actor_from_request(request)
        return handle_ingest_feed(cfg_local, arxiv_id=body.arxiv_id.strip(), actor=actor)

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
        tag: str | None = Query(None, description="Filter by tag name"),
        topic: str | None = Query(None, description="Filter by topic slug"),
    ) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        items, total = repository.list_papers(
            conn, limit=limit, offset=offset,
            q=q, sort=sort, order=order,
            domain=domain, affiliations=affiliations,
            tag=tag, topic_slug=topic,
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
        request: Request,
        remove_files: bool = Query(True, description="Also remove the vault directory on disk"),
    ) -> dict:
        import shutil as _shutil
        from pathlib import Path as _Path

        actor = _actor_from_request(request)
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")

        full_id = paper.get("full_id")
        vault_dir = paper.get("vault_dir")

        # Delete DB rows first (classifications/tasks cascade via FK).
        repository.delete_paper(conn, paper_id, actor=actor)

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

    # ---------------- LLM configuration ----------------

    @app.get("/api/llm/config")
    def get_llm_config() -> dict:
        """Return current ``llm.yaml`` values. Never exposes the actual
        api key, only whether the named env var is populated."""
        import os as _os
        import yaml as _yaml
        from paperprism_agent.launchd import secret_allowlist

        path = cfg.paths.llm_config_file
        raw: dict = {}
        if path.exists():
            try:
                loaded = _yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception as exc:
                raise HTTPException(500, detail=f"llm.yaml unreadable: {exc}")
        api_key_env = raw.get("api_key_env") or None
        api_key_has_value = bool(
            api_key_env and _os.environ.get(api_key_env)
        )
        return {
            "version": int(raw.get("version", 1)),
            "provider": str(raw.get("provider", "openai")).lower(),
            "model": raw.get("model", ""),
            "api_base": raw.get("api_base") or "",
            "api_key_env": api_key_env or "",
            "api_key_has_value": api_key_has_value,
            "temperature": float(raw.get("temperature", 0.0)),
            "max_output_tokens": int(raw.get("max_output_tokens", 600)),
            "timeout_seconds": float(raw.get("timeout_seconds", 60)),
            "max_retries": int(raw.get("max_retries", 2)),
            "abstract_char_limit": int(raw.get("abstract_char_limit", 2000)),
            "pdf_head_char_limit": int(raw.get("pdf_head_char_limit", 1500)),
            "auto_tag_on_ingest": bool(raw.get("auto_tag_on_ingest", True)),
            "feed_categories": raw.get("feed_categories") or [],
            "allowed_api_key_envs": sorted(secret_allowlist()),
            "path": str(path),
        }

    @app.put("/api/llm/config", dependencies=[Depends(require_token)])
    def put_llm_config(payload: dict) -> dict:
        """Overwrite ``llm.yaml`` with the provided config.

        If ``api_key`` is supplied and ``api_key_env`` is in the allowlist,
        the key is upserted into ``secrets.env`` (mode 600) and injected
        into the running process env so the next worker load picks it up.
        The api_key itself is never written into llm.yaml.
        """
        import os as _os
        import yaml as _yaml
        from paperprism_agent.launchd import upsert_secret

        if not isinstance(payload, dict):
            raise HTTPException(400, detail="body must be a JSON object")

        api_key_plain = payload.pop("api_key", None)

        # Validate required fields minimally.
        try:
            doc = {
                "version": int(payload.get("version", 1)),
                "provider": str(payload.get("provider", "openai")).lower(),
                "model": str(payload["model"]),
                "api_base": (payload.get("api_base") or None),
                "api_key_env": (payload.get("api_key_env") or None),
                "temperature": float(payload.get("temperature", 0.0)),
                "max_output_tokens": int(payload.get("max_output_tokens", 600)),
                "timeout_seconds": float(payload.get("timeout_seconds", 60)),
                "max_retries": int(payload.get("max_retries", 2)),
                "abstract_char_limit": int(payload.get("abstract_char_limit", 2000)),
                "pdf_head_char_limit": int(payload.get("pdf_head_char_limit", 1500)),
                "auto_tag_on_ingest": bool(payload.get("auto_tag_on_ingest", True)),
            }

            # feed_categories: optional list of arXiv categories for daily feed
            fc = payload.get("feed_categories")
            if fc is not None:
                if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
                    doc["feed_categories"] = fc
                else:
                    raise HTTPException(400, detail="feed_categories must be a list of strings")
        except KeyError as exc:
            raise HTTPException(400, detail=f"missing field: {exc}")
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, detail=f"invalid field: {exc}")

        path = cfg.paths.llm_config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                _yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise HTTPException(500, detail=f"failed to write llm.yaml: {exc}")

        secret_written = False
        if api_key_plain and doc["api_key_env"]:
            try:
                upsert_secret(
                    cfg.paths.secrets_file, doc["api_key_env"], str(api_key_plain)
                )
                _os.environ[doc["api_key_env"]] = str(api_key_plain)
                secret_written = True
            except ValueError as exc:
                # Key not in allowlist: we still saved llm.yaml.
                raise HTTPException(400, detail=str(exc))

        log.info(
            "llm.yaml updated: provider=%s model=%s api_base=%s key_env=%s secret_written=%s",
            doc["provider"], doc["model"], doc["api_base"], doc["api_key_env"], secret_written,
        )
        return {"saved": True, "secret_written": secret_written, "path": str(path)}

    @app.post("/api/llm/test", dependencies=[Depends(require_token)])
    def test_llm_config() -> dict:
        """Load the current ``llm.yaml`` and make a tiny chat request to
        confirm the provider is reachable and the api key works."""
        try:
            llm_cfg = LLMConfig.load(cfg.paths.llm_config_file)
            client = LLMClient(llm_cfg)
            content = client.chat_json(
                system='You respond with strict JSON.',
                user='Respond with exactly {"ok":true}.',
            )
            return {
                "ok": True,
                "provider_label": client.provider_label,
                "sample": content[:200],
            }
        except LLMConfigError as exc:
            return {"ok": False, "error": f"config: {exc}"}
        except LLMError as exc:
            return {"ok": False, "error": f"llm: {exc}"}
        except Exception as exc:  # noqa: BLE001
            log.exception("llm test failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---------------- Tags ----------------

    @app.get("/api/tags")
    def list_tags_endpoint() -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        return {"items": repository.list_tags(conn)}

    @app.get("/api/papers/{paper_id}/tags")
    def get_paper_tags_endpoint(paper_id: int) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
        return {"items": repository.get_tags_for_paper(conn, paper_id)}

    @app.post(
        "/api/papers/{paper_id}/tags",
        dependencies=[Depends(require_token)],
    )
    def edit_paper_tags_endpoint(paper_id: int, payload: dict, request: Request) -> dict:
        """Body: ``{"add": ["tag-a"], "remove": ["tag-b"]}``.

        Both arrays are optional. Tags are normalised (lowercase /
        hyphenated). User-edited tags are stored with source='user'
        (upgrading any prior llm-added row).
        """
        if not isinstance(payload, dict):
            raise HTTPException(400, detail="body must be a JSON object")
        add = payload.get("add") or []
        remove = payload.get("remove") or []
        if not isinstance(add, list) or not isinstance(remove, list):
            raise HTTPException(400, detail="'add' / 'remove' must be lists of strings")
        actor = _actor_from_request(request)
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
        added = 0
        if add:
            added = repository.add_paper_tags(
                conn, paper_id=paper_id, tag_names=add, source="user", topic_id=None, actor=actor,
            )
        removed = 0
        for name in remove:
            if repository.remove_paper_tag(conn, paper_id=paper_id, tag_name=str(name), actor=actor):
                removed += 1
        return {
            "paper_id": paper_id,
            "added": added,
            "removed": removed,
            "tags": repository.get_tags_for_paper(conn, paper_id),
        }

    # ---------------- Topics ----------------

    @app.get("/api/topics")
    def list_topics_endpoint() -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        return {"items": repository.list_topics(conn)}

    @app.get("/api/topics/{slug}")
    def get_topic_endpoint(slug: str) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        topic = repository.get_topic_by_slug(conn, slug)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"topic {slug} not found")
        items, total = repository.list_papers(
            conn,
            limit=500,
            offset=0,
            topic_slug=slug,
            sort="published_at",
            order="desc",
        )
        topic["papers"] = items
        topic["paper_count"] = total
        return topic

    @app.delete(
        "/api/topics/{topic_id}",
        dependencies=[Depends(require_token)],
    )
    def delete_topic_endpoint(topic_id: int, request: Request) -> dict:
        conn = db_module.connect(cfg.paths.db_file)
        actor = _actor_from_request(request)
        ok = repository.delete_topic(conn, topic_id, actor=actor)
        if not ok:
            raise HTTPException(status_code=404, detail=f"topic {topic_id} not found")
        return {"deleted": True, "topic_id": topic_id}

    # ---------------- Memory Ledger (events) ----------------

    @app.post("/api/events/track")
    def track_event_route(
        body: TrackEventBody,
        request: Request,
    ) -> dict[str, Any]:
        """Append a single L1 read-behaviour event (e.g. paper.opened).

        Unlike mutation events, this does not wrap a business write;
        it is safe to call directly from server.py.
        """
        conn = db_module.connect(cfg.paths.db_file)
        actor = _actor_from_request(request)
        repository.track_event(
            conn,
            actor=actor,
            event_type=body.event_type,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            payload=body.payload,
        )
        return {"ok": True}

    @app.get("/api/events")
    def list_events(
        subject_type: str | None = Query(None),
        subject_id: str | None = Query(None),
        event_type: str | None = Query(None),
        actor: str | None = Query(None),
        since: str | None = Query(None, description="ISO8601 timestamp, inclusive"),
        limit: int = Query(50, ge=1, le=200),
        cursor: int | None = Query(None, description="events.id offset for pagination"),
    ) -> EventsListResponse:
        """Query the Memory Ledger. Results are newest-first."""
        conn = db_module.connect(cfg.paths.db_file)
        where_parts: list[str] = []
        params: list[object] = []

        if subject_type:
            where_parts.append("subject_type = ?")
            params.append(subject_type)
        if subject_id:
            where_parts.append("subject_id = ?")
            params.append(subject_id)
        if event_type:
            where_parts.append("event_type = ?")
            params.append(event_type)
        if actor:
            where_parts.append("actor = ?")
            params.append(actor)
        if since:
            where_parts.append("ts >= ?")
            params.append(since)
        if cursor is not None:
            where_parts.append("id < ?")
            params.append(cursor)

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(
            f"""
            SELECT id, ts, actor, event_type, subject_type, subject_id,
                   related_ids, payload, schema_v
            FROM events
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            [*params, limit + 1],
        ).fetchall()

        items: list[EventItem] = []
        for r in rows[:limit]:
            payload = _json.loads(r["payload"]) if r["payload"] else None
            related = _json.loads(r["related_ids"]) if r["related_ids"] else None
            items.append(
                EventItem(
                    id=r["id"],
                    ts=r["ts"],
                    actor=r["actor"],
                    event_type=r["event_type"],
                    subject_type=r["subject_type"],
                    subject_id=r["subject_id"],
                    related_ids=related,
                    payload=payload,
                    schema_v=r["schema_v"],
                )
            )

        next_cursor = rows[limit]["id"] if len(rows) > limit else None
        return EventsListResponse(items=items, next_cursor=next_cursor)

    @app.get("/api/papers/{paper_id}/timeline")
    def get_paper_timeline(paper_id: int) -> TimelineResponse:
        """Return every ledger event for a given paper, newest-first."""
        conn = db_module.connect(cfg.paths.db_file)
        paper = repository.get_paper(conn, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")

        arxiv_id = paper.get("arxiv_id")
        rows = conn.execute(
            """
            SELECT id, ts, actor, event_type, subject_type, subject_id,
                   related_ids, payload, schema_v
            FROM events
            WHERE subject_type = 'paper' AND subject_id = ?
            ORDER BY id DESC
            """,
            (arxiv_id,),
        ).fetchall()

        events: list[EventItem] = []
        for r in rows:
            payload = _json.loads(r["payload"]) if r["payload"] else None
            related = _json.loads(r["related_ids"]) if r["related_ids"] else None
            events.append(
                EventItem(
                    id=r["id"],
                    ts=r["ts"],
                    actor=r["actor"],
                    event_type=r["event_type"],
                    subject_type=r["subject_type"],
                    subject_id=r["subject_id"],
                    related_ids=related,
                    payload=payload,
                    schema_v=r["schema_v"],
                )
            )

        return TimelineResponse(
            paper_id=paper_id,
            arxiv_id=arxiv_id,
            events=events,
        )

    # ---------------- Auto-tag jobs ----------------

    @app.post(
        "/api/tags/auto",
        dependencies=[Depends(require_token)],
    )
    async def create_auto_tag_job(payload: dict) -> dict:
        """Body: ``{"paper_ids": [1,2,3]}``.

        ``batch_size`` (optional, advanced) also accepted but not exposed in the
        UI; defaults to 15. The resulting topic surfaces every tag its papers
        received (no truncation).

        Returns the initial job snapshot; clients poll GET /api/tags/auto/{id}.
        """
        if not isinstance(payload, dict):
            raise HTTPException(400, detail="body must be a JSON object")
        raw_ids = payload.get("paper_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(400, detail="'paper_ids' must be a non-empty list")
        try:
            paper_ids = [int(x) for x in raw_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, detail="paper_ids must be integers")
        batch_size = payload.get("batch_size")
        if batch_size is not None:
            try:
                batch_size = int(batch_size)
            except (TypeError, ValueError):
                raise HTTPException(400, detail="batch_size must be an integer")
            if batch_size < 1 or batch_size > 100:
                raise HTTPException(400, detail="batch_size must be in [1, 100]")

        conn = db_module.connect(cfg.paths.db_file)
        try:
            job = auto_tag_jobs.create_job(
                cfg=cfg,
                conn=conn,
                paper_ids=paper_ids,
                batch_size=batch_size,
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc))
        return job.snapshot()

    @app.get("/api/tags/auto/{job_id}")
    def get_auto_tag_job(job_id: str) -> dict:
        job = auto_tag_jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        return job.snapshot()

    @app.delete(
        "/api/tags/auto/{job_id}",
        dependencies=[Depends(require_token)],
    )
    async def cancel_auto_tag_job(job_id: str) -> dict:
        ok = await auto_tag_jobs.cancel_job(job_id)
        if not ok:
            job = auto_tag_jobs.get_job(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            return {"cancelled": False, "status": job.status}
        return {"cancelled": True}

    @app.post(
        "/api/tags/auto/{job_id}/retry",
        dependencies=[Depends(require_token)],
    )
    async def retry_auto_tag_job(job_id: str) -> dict:
        job = await auto_tag_jobs.retry_failed(cfg=cfg, job_id=job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        return job.snapshot()

    # ---------------- Navigator (map) ----------------

    @app.get("/api/feed/status")
    def get_feed_status() -> dict:
        """Return today's arXiv feed status.

        Response:
          date  -- ISO date string (e.g. '2026-05-08')
          count -- number of feed papers available for today
          ready -- true if count > 0 (feed has been fetched for today)
        """
        conn = db_module.connect(cfg.paths.db_file)
        row = conn.execute(
            "SELECT COUNT(*) FROM arxiv_feed_papers WHERE feed_date = date('now')"
        ).fetchone()
        count = row[0] if row else 0
        import datetime
        return {
            "date": datetime.date.today().isoformat(),
            "count": count,
            "ready": count > 0,
        }

    @app.get("/api/map")
    def get_map() -> dict:
        """Return the 2D embedding map for the user's library + arXiv feed."""
        conn = db_module.connect(cfg.paths.db_file)
        payload = navigator_map_data.build_map_data(conn)
        return payload

    # ---------------- Weekly Digest ----------------

    @app.get("/api/weekly-digests")
    def list_weekly_digests(limit: int = Query(8, ge=1, le=52)) -> list[dict]:
        """Return the most recent weekly research digests."""
        conn = db_module.connect(cfg.paths.db_file)
        return repository.list_digests(conn, limit=limit)

    @app.put("/api/weekly-digests/{digest_id}")
    def update_weekly_digest(digest_id: int, body: dict) -> dict:
        """Update the user_note field of a digest."""
        user_note = body.get("user_note", "")
        if not isinstance(user_note, str):
            raise HTTPException(400, detail="user_note must be a string")
        conn = db_module.connect(cfg.paths.db_file)
        ok = repository.update_digest_user_note(conn, digest_id, user_note)
        if not ok:
            raise HTTPException(404, detail=f"digest {digest_id} not found")
        return {"ok": True}

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
