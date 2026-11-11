from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import TimestampedRead


EstimateDetail = Literal["summary", "standard", "detailed", "fully_itemized"]
EstimateStatus = Literal["draft", "sent", "accepted", "rejected", "expired"]


class EstimateBase(BaseModel):
    project_id: UUID
    ref: str
    version: int = 1
    detail_level: EstimateDetail = "standard"
    payload: dict[str, Any] = {}
    status: EstimateStatus = "draft"


class EstimateCreate(EstimateBase):
    pass


class EstimatePatch(BaseModel):
    """Review-and-edit flow — full-payload replacement; status optional."""

    payload: dict[str, Any]
    status: EstimateStatus | None = None


class EstimateRead(TimestampedRead, EstimateBase):
    ai_job_id: UUID | None = None


class EstimatePdfResult(BaseModel):
    """Result of generating + storing an estimate PDF."""

    document_id: UUID
    filename: str
    size_bytes: int
    download_url: str
