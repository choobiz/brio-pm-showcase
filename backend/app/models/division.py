"""Division sets — the cost breakdown an estimate is organised into.

BRIO does not have one breakdown, it has one PER PROJECT TYPE: residential work
uses BRIO's own 18 numbered divisions in construction sequence, commercial work
uses CSI MasterFormat. Before this, estimate generation hard-coded a single list
of 19 generic categories inherited from a retired reference file, so every
residential estimate had to be recategorised by hand after the model produced it.

Sets are data so that adding or revising one is a row change rather than a
deploy, and each set is addressed by a stable ``code`` so an estimate generated
last month remains explainable.

⚠️ Changing a set does NOT retroactively change stored estimates. Estimate
payloads carry their own category names; these rows only shape what the next
generation is told to produce. That is deliberate — Amir's instruction is that
existing records stay exactly as they are.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin, UUIDPKMixin

#: Recognised project types. NULL is also legal on ``projects.type`` — a project
#: nobody has classified is honest; a guessed one silently mis-seeds every future
#: estimate on it.
PROJECT_TYPES = ("residential_new", "residential_reno", "commercial_ti", "commercial_new")

#: Which standard a set follows. ``brio`` is our own; ``csi`` is MasterFormat.
DIVISION_STANDARDS = ("brio", "csi")


class DivisionSet(UUIDPKMixin, TimestampMixin, Base):
    """One named breakdown, e.g. ``brio-residential-18`` or ``csi-masterformat``."""

    __tablename__ = "division_sets"

    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    standard: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)

    divisions: Mapped[list["Division"]] = relationship(
        back_populates="set", order_by="Division.sequence", cascade="all, delete-orphan"
    )


class Division(UUIDPKMixin, Base):
    """One division within a set.

    ``number`` is TEXT, not an integer, because the leading zero is part of the
    name — the division is "01 General Conditions", never "1 General Conditions".

    ``active_by_default`` exists because CSI records all 50 numbers while BRIO
    plausibly prices about 21 of them. BRIO's convention is to KEEP an empty
    division at $0 so absent scope stays visible (an empty Specialties division
    is what surfaced unpriced smoke/CO alarms on 960 W Willow). That signal only
    works where the division is possible at all; carrying "Waterways and Marine
    Construction" at $0 on a daycare would bury it in noise. So empty-but-kept is
    scoped to the active set, and the rest stay available without being offered.
    """

    __tablename__ = "divisions"
    __table_args__ = (UniqueConstraint("set_id", "number", name="uq_divisions_set_number"),)

    set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("division_sets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    number: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Seeds each category's ``contingencyPct``. 0 on general conditions/permits,
    #: 5 on trade divisions — set per division, never globally.
    contingency_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5"), server_default="5"
    )
    active_by_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                                    server_default="true")
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)

    set: Mapped[DivisionSet] = relationship(back_populates="divisions")

    @property
    def label(self) -> str:
        """"01 General Conditions, Permits & Closeout" — number and title are one
        name, and this is the exact string an estimate category carries."""
        return f"{self.number} {self.title}"


class ProjectTypeDivisionSet(Base):
    """Maps a project type to its set.

    A mapping table rather than a column on the set, because one set serves
    several types — both residential types share the 18 divisions.
    """

    __tablename__ = "project_type_division_sets"

    project_type: Mapped[str] = mapped_column(Text, primary_key=True)
    set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("division_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
