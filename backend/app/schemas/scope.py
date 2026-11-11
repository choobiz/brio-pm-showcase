from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import TimestampedRead


class ScopeBase(BaseModel):
    project_id: UUID
    version: int = 1
    payload: dict[str, Any] = {}


class ScopeCreate(ScopeBase):
    pass


class ScopePatch(BaseModel):
    """Full-payload replacement for the review-and-edit flow."""

    payload: dict[str, Any]


class ScopeSourceImage(BaseModel):
    """One image Keystone analyzed — metadata only; base64 bytes are stripped."""

    caption: str | None = None
    media_type: str | None = None


class ScopeSourceDocument(BaseModel):
    """A library file the scope run was asked to read, and whether it could."""

    id: str
    filename: str | None = None
    read: bool = True
    #: Why it was not read, in words. None when it was.
    reason: str | None = None


class ScopeSource(BaseModel):
    """Provenance for a generated scope: what Keystone was given, and what the
    run cost. Derived from the originating `scope_analysis` AIJob + the
    project row. The review UI shows this so a reader can judge how much
    documentation actually informed the scope."""

    notes: str | None = None
    images: list[ScopeSourceImage] = []
    image_count: int = 0
    project_metadata: dict[str, Any] = {}
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: str | None = None
    generated_at: datetime | None = None
    #: The library files ticked for this run, each marked read or not.
    documents: list[ScopeSourceDocument] = []


class ScopeRead(TimestampedRead, ScopeBase):
    ai_job_id: UUID | None = None
    # Populated by the router from the linked AIJob; None for hand-created or
    # brio-admin-imported scopes that had no Keystone run.
    source: ScopeSource | None = None
    #: The project's name and address from its own record, for the document
    #: header. Set for every version: an edited version has no source, and its
    #: header read "—" (rollout test 2026-10-14).
    project: dict[str, Any] = {}


class ScopePdfResult(BaseModel):
    """Result of generating + storing a scope PDF."""

    document_id: UUID
    filename: str
    size_bytes: int
    download_url: str  # short-lived presigned R2 GET URL
