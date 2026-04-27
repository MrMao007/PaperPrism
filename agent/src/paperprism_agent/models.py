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


class HealthResponse(BaseModel):
    ok: bool = True
    version: str
    home: str
    vault: str
