"""CRM schemas — queue, contacts, opportunities, interactions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CrmContactBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: Optional[str] = None
    segment: Optional[str] = None
    tier: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    contact_confidence: Optional[str] = None
    #: Up to three of the firm's actual recent permits, newest first.
    #: [{address, value, work, issued, permit}] — see migration 0054.
    recent_projects: Optional[list[dict]] = None
    permits_total: Optional[int] = None
    value_as_applicant: Optional[int] = None
    cities: Optional[str] = None
    top_use_category: Optional[str] = None
    bc_registration: Optional[str] = None
    entity_type: Optional[str] = None
    registry_status: Optional[str] = None
    excluded_reason: Optional[str] = None


class CrmContactDetail(CrmContactBase):
    legal_name: Optional[str] = None
    permit_name: Optional[str] = None
    permits_as_applicant: Optional[int] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = []


class CrmInteractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    direction: str
    occurred_at: datetime
    summary: Optional[str] = None
    outcome: Optional[str] = None
    created_by_id: Optional[UUID] = None


class CrmOpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_id: UUID
    track: str
    status: str
    next_action: Optional[str] = None
    next_action_at: Optional[date] = None
    owner_id: Optional[UUID] = None
    notes: Optional[str] = None
    last_contacted_at: Optional[datetime] = None


class QueueItem(BaseModel):
    """One row of the week — the only screen that matters."""

    opportunity: CrmOpportunityRead
    contact: CrmContactBase
    owner_name: Optional[str] = None
    last_interaction: Optional[CrmInteractionRead] = None
    interaction_count: int = 0
    # why this row is here, so the UI never has to work it out
    reason: str = Field(description="overdue | due_today | no_action | upcoming")
    days_overdue: int = 0

    # How to reach THIS firm, derived from what it actually has. Computed on
    # read rather than stored, so it can never disagree with the contact record
    # — the first version stored "Call" on every Tier A opportunity at import
    # time and then told you to phone firms with no phone number.
    channel: str = Field(description="call | email | website | find_contact")
    channel_target: Optional[str] = None
    channel_hint: Optional[str] = None


class QueueResponse(BaseModel):
    items: list[QueueItem]
    counts: dict[str, int]


class LogInteraction(BaseModel):
    """Log what happened AND set what happens next, in one call.

    Next action is part of this payload on purpose: logging a call without
    deciding the next step is how a CRM turns into a filing cabinet.
    """

    channel: str = "call"
    direction: str = "outbound"
    summary: Optional[str] = None
    outcome: Optional[str] = None
    occurred_at: Optional[datetime] = None

    next_action: Optional[str] = None
    next_action_at: Optional[date] = None
    status: Optional[str] = None


class OpportunityUpdate(BaseModel):
    status: Optional[str] = None
    next_action: Optional[str] = None
    next_action_at: Optional[date] = None
    owner_id: Optional[UUID] = None
    notes: Optional[str] = None


class OpportunityCreate(BaseModel):
    track: str = "construction"
    next_action: Optional[str] = None
    next_action_at: Optional[date] = None
    owner_id: Optional[UUID] = None


class ContactWithOpportunities(CrmContactDetail):
    opportunities: list[CrmOpportunityRead] = []
    interactions: list[CrmInteractionRead] = []
