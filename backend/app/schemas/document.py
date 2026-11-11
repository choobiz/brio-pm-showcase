from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import TimestampedRead

# Track B document categories (matches the brio-admin scope-analyzer inputs).
# Extended 2026-06-16 — added contract / scope / report for ZIP-import
# auto-categorisation of typical project drops (CCDC contracts, SoW
# documents, SMP / hazmat / geotech reports).
DocumentCategory = Literal[
    "photo", "drawing", "contract", "scope", "report",
    "client_note", "invoice_legacy", "permit", "other",
    # Present in the store since the 2026-08-31 rebuild and offered by the
    # frontend picker, but missing here until 2026-10-12, so choosing any of
    # them on upload or on a category change was refused with a 422.
    "invoice", "quote", "survey", "correspondence", "video", "financial",
    # Orders, takeoffs and sheets finalised from Ask → Prepare. The picker has
    # offered it since 2026-10-13 but it was missing here, so choosing it was
    # refused with a 422 (found in the rollout test, 2026-10-14).
    "prepared",
    # Generated artifacts — surfaced via dedicated pages, hidden from
    # the user-facing Documents grid:
    "scope_pdf",       # rendered scope PDFs (per-scope + full + client editions)
    "project_index",   # cached project-wide MD index — one per project
    "invoice_pdf",     # rendered invoice PDFs
    "change_order_pdf",  # rendered change-order PDFs
]


class DocumentBase(BaseModel):
    project_id: UUID
    category: str
    phase: str
    filename: str
    r2_key: str
    mime: str | None = None
    size_bytes: int | None = None


class DocumentCreate(DocumentBase):
    pass


class DocumentRead(TimestampedRead, DocumentBase):
    uploaded_by_id: UUID | None = None
    archived: bool = False
    client_visible: bool = False
    #: Set when a person confirmed the category; NULL means nobody has.
    category_confirmed_at: datetime | None = None


class DocumentPresignRequest(BaseModel):
    """Step 1 — ask for a presigned PUT URL before the browser uploads to R2."""

    project_id: UUID
    filename: str
    content_type: str
    category: DocumentCategory = "other"
    phase: str = "preconstruction"
    #: True when the uploader chose or checked the category before uploading
    #: (the upload review). A category nobody looked at stays unconfirmed.
    category_confirmed: bool = False


class DocumentPresignResponse(BaseModel):
    document_id: UUID
    r2_key: str
    upload_url: str  # short-lived presigned R2 PUT URL


class DocumentFinalizeRequest(BaseModel):
    """Step 2 — called after the browser PUT succeeds, with the real size."""

    size_bytes: int


class DocumentUpdate(BaseModel):
    """Partial update — category (ZIP-import review) + client_visible (share
    a doc/photo to the owner portal)."""

    category: DocumentCategory | None = None
    client_visible: bool | None = None
    #: Rename. The stored object keeps its key; downloads use this name.
    filename: str | None = Field(None, min_length=1, max_length=255)
    #: Confirm the category as it stands, without changing it. Setting
    #: `category` confirms it too: choosing one is a person saying so.
    category_confirmed: bool | None = None


class DocumentBulkUpdate(BaseModel):
    """One change applied to several documents of one project."""

    project_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=500)
    category: DocumentCategory | None = None
    client_visible: bool | None = None
    archived: bool | None = None


class DocumentBulkResult(BaseModel):
    updated: int
    unchanged: int


class CategorySuggestRequest(BaseModel):
    filenames: list[str] = Field(min_length=1, max_length=1000)


class DocumentPreviewResponse(BaseModel):
    """A URL the browser renders in place, or a plain reason why it cannot."""

    filename: str
    previewable: bool
    content_type: str | None = None
    preview_url: str | None = None
    reason: str | None = None


class DocumentDownloadResponse(BaseModel):
    filename: str
    download_url: str  # short-lived presigned R2 GET URL


class DocumentThumbnailResponse(BaseModel):
    thumbnail_url: str  # short-lived presigned R2 GET URL for the resized JPEG
