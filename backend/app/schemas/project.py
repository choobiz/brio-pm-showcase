from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import TimestampedRead


ProjectStatus = Literal["lead", "preconstruction", "in_progress", "closeout", "archived"]

#: Drives which cost-division set an estimate is organised into — residential
#: types resolve to BRIO's 18 numbered divisions, commercial types to CSI
#: MasterFormat (see services.divisions). Required on CREATE; still nullable on
#: read and update because 19 existing projects are deliberately unclassified.
ProjectType = Literal["residential_new", "residential_reno", "commercial_ti", "commercial_new"]

FinishLevel = Literal["builder_grade", "mid", "high"]
DeliveredAs = Literal["finished", "shell", "part_shell"]
DrawingDecision = Literal["allowance", "not_in_scope"]


class ProjectBase(BaseModel):
    slug: str
    name: str
    address: str | None = None
    summary: str | None = None
    type: ProjectType | None = None
    status: ProjectStatus = "lead"
    phase: str | None = None
    client_id: UUID | None = None
    building_age: int | None = None
    building_type: str | None = None
    sqft: int | None = None
    finish_level: FinishLevel | None = None
    delivered_as: DeliveredAs | None = None
    drawing_decisions: dict[str, DrawingDecision] = {}
    google_drive_url: str | None = None
    # When false, the document indexer skips this project. Defaults true.
    index_documents: bool = True
    # True for a "new home" (dwelling) under the BC Homeowner Protection Act —
    # drives the 2-5-10 home-warranty disclosure in the contract. Off for
    # accessory buildings (garage/shop/office).
    home_warranty_required: bool = False
    # Per-project client-discussion notes: [{title, body, computed?}].
    discussion_points: list[dict] = []


class ProjectCreate(ProjectBase):
    pass


class ProjectCreateRequest(BaseModel):
    """Operator-facing create — the server derives a unique slug from the name."""

    name: str
    address: str | None = None
    # Mandatory from 2026-09-27. The type selects the cost-division set, so a
    # project created without one would silently estimate against the generic
    # fallback list. Legacy rows stay nullable; new ones must decide.
    type: ProjectType
    status: ProjectStatus = "lead"
    client_id: UUID | None = None
    # Mandatory from 2026-09-27 — year built. Hazmat exposure is a function of
    # building age (pre-1990 stock), and it cannot be inferred later: all 40
    # existing projects have it null, which is exactly the gap being closed.
    building_age: int = Field(ge=1850, le=2100)
    building_type: str | None = None
    sqft: int | None = None
    google_drive_url: str | None = None


class ProjectUpdate(BaseModel):
    """Operator-facing patch — every field optional; only supplied keys mutate."""

    name: str | None = None
    address: str | None = None
    summary: str | None = None
    type: ProjectType | None = None
    status: ProjectStatus | None = None
    client_id: UUID | None = None
    # Same range as create. Unchecked here until 2026-10-12.
    building_age: int | None = Field(default=None, ge=1850, le=2100)
    building_type: str | None = None
    sqft: int | None = None
    finish_level: FinishLevel | None = None
    delivered_as: DeliveredAs | None = None
    # Replaces the whole map. Send {} to clear every decision.
    drawing_decisions: dict[str, DrawingDecision] | None = None
    google_drive_url: str | None = None
    index_documents: bool | None = None
    home_warranty_required: bool | None = None
    discussion_points: list[dict] | None = None


class ProjectRead(TimestampedRead, ProjectBase):
    # The status retained before archiving (for Unarchive); NULL when not archived.
    pre_archive_status: ProjectStatus | None = None
