"""Pydantic models mirroring the Chrome extension's ingest contract.

The shapes here MUST stay backwards compatible with
`extension/lib/agent.ts`. If a field is renamed on one side, the other
must add a migration alias.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ArxivId(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Canonical id without version, e.g. 2604.01234")
    version: Optional[str] = Field(default=None, description="Version tag like v2")
    fullId: str = Field(description="Id with version, e.g. 2604.01234v2")
    legacy: bool = Field(default=False)


EventType = Literal["archive.requested", "archive.completed"]


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: EventType
    arxivId: ArxivId
    sourceUrl: str
    downloadPath: Optional[str] = None
    vaultPathHint: Optional[str] = None
    downloadId: Optional[int] = None
    triggerClassification: bool = False
    absUrl: Optional[str] = None
    emittedAt: str


class IngestResponse(BaseModel):
    accepted: bool
    vaultPath: Optional[str] = None
    status: Optional[Literal["queued", "classified", "needs_review"]] = None
    message: Optional[str] = None


class UploadIngestResponse(BaseModel):
    """Response for the dashboard's bulk-folder import endpoint."""

    accepted: bool
    paperId: Optional[int] = None
    fullId: Optional[str] = None
    arxivId: Optional[str] = None
    duplicate: bool = False
    vaultPath: Optional[str] = None
    title: Optional[str] = None
    status: Optional[Literal["queued", "duplicate", "rejected"]] = None
    message: Optional[str] = None


class HealthResponse(BaseModel):
    ok: bool = True
    version: str
    home: str
    vault: str


class EventItem(BaseModel):
    """One row from the Memory Ledger events table."""

    id: int
    ts: str
    actor: str
    event_type: str
    subject_type: str
    subject_id: str
    related_ids: list[str] | None = None
    payload: dict | None = None
    schema_v: int


class EventsListResponse(BaseModel):
    items: list[EventItem]
    next_cursor: int | None = None


class TimelineResponse(BaseModel):
    paper_id: int
    arxiv_id: str | None = None
    events: list[EventItem]


class TrackEventBody(BaseModel):
    """Request body for POST /api/events/track (L1 read-behaviour events)."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    subject_type: str
    subject_id: str
    actor: str = "user"
    payload: dict | None = None
