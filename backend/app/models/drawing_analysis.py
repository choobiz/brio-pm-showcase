from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class DrawingAnalysis(UUIDPKMixin, TimestampMixin, Base):
    """Cached PyMuPDF/OCR parse of a Document drawing.

    One per document. Re-parsed when the R2 object's SHA-256 changes (i.e.
    when the user uploads a revised drawing set). The review UI consumes
    `payload` to render linear candidates, scales, and dimensions; the AI
    worker reads it during scope_analysis to ground quantities in real
    geometry instead of pixel-counting.
    """

    __tablename__ = "drawing_analyses"

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_vector: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
