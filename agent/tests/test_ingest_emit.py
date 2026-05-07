"""Integration tests for ingest → event emission (T06)."""

from __future__ import annotations

from paperprism_agent import db
from paperprism_agent.ingest import handle_ingest, handle_upload
from paperprism_agent.models import ArxivId, IngestRequest


def test_handle_ingest_emits_downloaded_event(tmp_home, tmp_path):
    """archive.completed produces a paper.ingested.downloaded event."""
    src = tmp_path / "source.pdf"
    src.write_bytes(b"%PDF-1.4 test content for ingest")

    req = IngestRequest(
        event="archive.completed",
        arxivId=ArxivId(id="2501.00001", fullId="2501.00001v1", legacy=False),
        sourceUrl="https://arxiv.org/abs/2501.00001",
        downloadPath=str(src),
        emittedAt="2024-01-01T00:00:00Z",
    )

    resp = handle_ingest(tmp_home, req, actor="user")
    assert resp.accepted is True

    conn = db.connect(tmp_home.paths.db_file)
    ev = conn.execute(
        "SELECT * FROM events WHERE event_type = 'paper.ingested.downloaded'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "2501.00001"
    assert ev["actor"] == "user"
    import json
    payload = json.loads(ev["payload"])
    assert payload["source_url"] == "https://arxiv.org/abs/2501.00001"
    assert "sha256" in payload


def test_handle_upload_emits_uploaded_event(tmp_home):
    """User PDF upload produces a paper.ingested.uploaded event."""
    pdf_bytes = b"%PDF-1.4 uploaded paper"
    resp = handle_upload(
        tmp_home,
        file_bytes=pdf_bytes,
        filename="my-paper.pdf",
        actor="user",
    )
    assert resp.accepted is True
    assert resp.duplicate is False

    conn = db.connect(tmp_home.paths.db_file)
    ev = conn.execute(
        "SELECT * FROM events WHERE event_type = 'paper.ingested.uploaded'"
    ).fetchone()
    assert ev is not None
    assert ev["actor"] == "user"
    import json
    payload = json.loads(ev["payload"])
    assert payload["filename"] == "my-paper.pdf"
    assert payload["size_bytes"] == len(pdf_bytes)
