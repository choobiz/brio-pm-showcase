from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import TimestampedRead


class DocumentSummaryRead(TimestampedRead):
    """Per-document summary surface returned alongside Document rows."""

    document_id: UUID
    project_id: UUID
    skill_version: str
    status: str  # queued | running | succeeded | failed | skipped
    summary_text: str | None = None
    structured_fields: dict[str, Any] = {}
    error_message: str | None = None
    indexed_at: datetime | None = None


class DocumentIndexBadge(BaseModel):
    """Compact status surface for the Documents page badge."""

    document_id: UUID
    status: str  # not_indexed | queued | running | succeeded | failed | skipped
    summary_short: str | None = None  # first sentence of summary_text
    indexed_at: datetime | None = None
    #: The whole summary, for the preview pane's "What the AI read".
    summary_text: str | None = None
    #: Why reading failed, in the reader's words, for "Couldn't be read".
    error_message: str | None = None


class ProjectIndexRead(BaseModel):
    """Materialised project index: markdown body + freshness metadata."""

    project_id: UUID
    project_name: str
    document_count: int
    summarized_count: int
    pending_count: int
    failed_count: int
    last_indexed_at: datetime | None = None
    generated_at: datetime
    markdown: str
