"""The milestone library: what each stage of a job needs booked and on site.

Approved by Amir 2026-10-16 ("Plan: Milestone Bookings and Letters"). The rows
come from primary sources, cited on each one:

- DNV GEN 110, "Single Family New Construction Required Inspection Stages"
  (https://docs.dnv.org/documents/required-inspection-stages.pdf);
- the BC Building Code's letters of assurance (Schedules B and C-B);
- the City of Vancouver's Low-Density Housing Inspection Stages Guide, which
  names the geotechnical bearing letter and the structural rebar letter outright.

A project's list is built from these rows and then confirmed by its PM. Rows
marked `optional` start unconfirmed with a question ("only if..."), because
whether they apply is the PM's call, not the library's.

`match` lists regexes tried against schedule activity names, in order; the
first activity that matches anchors the item's dates. Put the specific pattern
first: "infiltration" alone matched the tank INSTALL (13 Oct on 1234) before the
District's "infiltration field, before cover" inspection (20 Oct). `lead_days` is in
business days, counted back from that activity's start.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DNV_GEN110 = "DNV GEN 110 (Required Inspection Stages)"
BCBC_LOA = "BC Building Code, Division C 2.2.7 (letters of assurance)"
COV_GUIDE = "City of Vancouver Low-Density Housing Inspection Stages Guide (Oct 2026)"


@dataclass(frozen=True)
class Item:
    key: str
    stage: str
    order: int
    title: str
    role: str
    kind: str = "booking"          # booking | document_request | submittal | notice
    lead_days: int | None = None
    lead_note: str | None = None
    docs: tuple[str, ...] = ()
    match: tuple[str, ...] = ()
    why: str = ""
    source: str = DNV_GEN110
    optional: bool = False
    #: Only when the project has this (e.g. a stormwater plan).
    needs: str | None = None
    tags: tuple[str, ...] = field(default=())


DNV: tuple[Item, ...] = (
    Item("pre.good_neighbour", "Before start", 10, "Pre-construction (Good Neighbour) meeting", "municipality",
         lead_days=5, docs=("Hazardous materials clearance letter",), match=(r"demolition|site clearing",),
         why="Required before the demolition permit; attended by the inspector, the builder and the owner."),
    Item("pre.locate", "Before start", 20, "Utility locate (BC One Call)", "contractor", kind="notice",
         lead_days=3, match=(r"\blocate\b", r"excavat"), source="BC One Call",
         why="Locates are required before any machine digs."),
    Item("pre.arborist", "Before start", 30, "Tree protection review", "arborist", optional=True,
         docs=("Arborist report",), match=(r"site clearing|excavat",),
         why="Only where trees on or near the site are protected."),
    Item("layout.survey", "Layout", 40, "Layout and setback survey", "surveyor", lead_days=5,
         docs=("Foundation layout sketch", "Benchmark (TBM)"), match=(r"setback survey|set-?out|layout",),
         source="BC land surveyor practice", why="Sets the building on the lot before forming."),
    Item("exc.esc", "Excavation", 50, "Erosion and sediment control inspection", "municipality", lead_days=1,
         lead_note="Book online by 2:30 pm for the next business day",
         docs=("Erosion and sediment control measures in place",), match=(r"^excavation|bulk dig", r"excavat"),
         why="The first inspection after demolition. It must pass before footings and forms can be booked."),
    Item("exc.bearing", "Excavation", 60, "Soil bearing review", "geotechnical_engineer", lead_days=3,
         docs=("Geotechnical field memo confirming bearing capacity",), match=(r"soil bearing", r"^excavation|bulk dig"),
         source=f"{BCBC_LOA}, Schedule B 8.1; {COV_GUIDE}",
         why="The geotechnical engineer confirms the soil carries the footings before forming."),
    Item("exc.letter", "Excavation", 65, "Excavation letter", "geotechnical_engineer", kind="document_request",
         optional=True, docs=("Excavation letter (P.Eng.)",), match=(r"^excavation|bulk dig", r"excavat"),
         source=f"{DNV_GEN110}; {BCBC_LOA}, Schedule B 7.1",
         why="Only if the excavation is not to WorkSafeBC norms; the inspector asks for it at footings and forms."),
    Item("ff.structural", "Footings and forms", 70, "Formed footing review before pour", "structural_engineer",
         lead_days=3, docs=("Structural field memo approving rebar and forms",),
         match=(r"formed footing", r"pour footing", r"form footing"),
         source=f"{BCBC_LOA}, Schedule B 2.1; {COV_GUIDE}",
         why="The engineer of record reviews the rebar and forms before concrete is placed."),
    Item("ff.survey", "Footings and forms", 75, "Forming survey and non-encroachment certificate", "surveyor",
         lead_days=3, docs=("Site survey", "Non-encroachment certificate"),
         match=(r"pour footing", r"formed footing", r"form footing"),
         why="DNV requires both on site at the footings and forms inspection."),
    Item("ff.compaction", "Footings and forms", 80, "Compaction test report", "geotechnical_engineer",
         kind="document_request", optional=True, docs=("Compaction test report",), match=(r"pour footing",),
         source=f"{DNV_GEN110}; {BCBC_LOA}, Schedule B 8.3",
         why="Only if more than 1 ft of fill was placed under the footings."),
    Item("ff.concrete", "Footings and forms", 85, "Concrete and pump booking: footings", "concrete_supplier",
         kind="notice", lead_days=3, docs=("Mix design from the structural drawings",), match=(r"pour footing",),
         source="BRIO practice", why="Ready-mix and pump, to the mix on the structural drawings."),
    Item("ff.dnv", "Footings and forms", 90, "DNV footings and forms inspection: footings", "municipality",
         lead_days=1, lead_note="Book online by 2:30 pm for the next business day",
         docs=("Non-encroachment certificate", "Site survey", "Excavation letter (if applicable)",
               "Approved plans with engineer field memos", "Compaction report for fill over 1 ft (if any)"),
         match=(r"pour footing",),
         why="After the engineer's review and the survey, before the pour. "
             "Footings placed before wall forms need their own inspection."),
    Item("walls.structural", "Foundation walls", 100, "Formed wall review before pour", "structural_engineer",
         lead_days=3, docs=("Structural field memo approving wall rebar and forms",),
         match=(r"formed walls", r"pour walls", r"form foundation walls"), source=f"{BCBC_LOA}, Schedule B 2.1",
         why="A second review when the walls are poured separately from the footings."),
    Item("walls.concrete", "Foundation walls", 105, "Concrete and pump booking: walls", "concrete_supplier",
         kind="notice", lead_days=3, docs=("Mix design from the structural drawings",), match=(r"pour walls",),
         source="BRIO practice", why="Ready-mix and pump for the wall pour."),
    Item("walls.dnv", "Foundation walls", 110, "DNV forms inspection: foundation walls", "municipality",
         lead_days=1, lead_note="Book online by 2:30 pm for the next business day",
         docs=("Approved plans with engineer field memos",), match=(r"pour walls",),
         why="Separate inspection when walls are formed after the footings are poured."),
    Item("storm.engineer", "Stormwater", 120, "Stormwater field review before cover", "stormwater_engineer",
         lead_days=3, docs=("Stormwater engineer's field memo",),
         match=(r"infiltration field|before cover", r"infiltration", r"ecobloc|stormwater"),
         source="Project stormwater management plan", needs="stormwater",
         why="The stormwater plan requires the engineer's review before the District inspects."),
    Item("storm.dnv", "Stormwater", 125, "District inspection of the infiltration field before cover", "municipality",
         lead_days=1, docs=("Stormwater engineer's field memo",), match=(r"infiltration field|before cover", r"infiltration"),
         source="Project stormwater management plan", needs="stormwater",
         why="Prior to cover and after the engineer's review (stormwater plan notes)."),
    Item("below.dnv", "Below grade", 130, "Drain tile, damp-proofing and underground services inspection",
         "municipality", lead_days=1, docs=("Backflow prevention test certificate",),
         match=(r"damp ?proof|drain tile", r"under-?slab plumbing"),
         why="Before backfilling against the foundation."),
    Item("below.backfill", "Below grade", 135, "Backfill review", "geotechnical_engineer", lead_days=3,
         docs=("Geotechnical field memo on backfill",), match=(r"backfill",), source=f"{BCBC_LOA}, Schedule B 8.5",
         why="The geotechnical engineer covers backfill under the Schedule B."),
    Item("slab.fill", "Slab", 140, "Fill review before poly", "structural_engineer", lead_days=3,
         docs=("Engineer's letter on soil compaction or slab reinforcement",),
         match=(r"slab prep|vapou?r barrier", r"pour concrete slab"), source=DNV_GEN110,
         why="Engineers commonly note: no poly until the fill below the slab is approved."),
    Item("slab.dnv", "Slab", 145, "Underslab inspection", "municipality", lead_days=1,
         docs=("Engineer's letter on soil compaction or slab reinforcement",),
         match=(r"pour concrete slab", r"slab prep"), why="Before the slab is poured."),
    Item("frame.trusses", "Framing", 150, "Truss or joist drawings to the engineer", "truss_supplier",
         kind="submittal", lead_days=10, optional=True, docs=("Sealed truss drawings and letter",),
         match=(r"roof framing", r"wall framing"), source=f"{DNV_GEN110}; {BCBC_LOA}, Schedule B 2.3",
         why="Only where manufactured trusses or engineered joists are used; reviewed before fabrication."),
    Item("frame.height", "Framing", 155, "Height / framing survey", "surveyor", lead_days=3,
         docs=("Height survey",), match=(r"height", r"roof framing"), source="BC land surveyor practice",
         why="Confirms the building height against zoning."),
    Item("frame.structural", "Framing", 160, "Framing review before covering", "structural_engineer",
         lead_days=3, docs=("Professional engineer certificate for framing and sheathing",),
         match=(r"framing, before covering|inspection .{0,3}framing", r"wall framing"),
         source=f"{BCBC_LOA}, Schedule B 2.1", why="The engineer reviews framing and sheathing before it is covered."),
    Item("frame.dnv", "Framing", 170, "DNV frame inspection", "municipality", lead_days=1,
         docs=("Professional engineer certificate for framing and sheathing", "Truss letter and drawings (if any)",
               "Mechanical ventilation checklist", "Building envelope field memo"),
         match=(r"insulation",),
         why="After the electrical and mechanical rough-ins, before insulation."),
    Item("ins.energy", "Insulation", 180, "Mid-construction energy check", "energy_advisor", lead_days=5,
         optional=True, docs=("Step Code mid-construction checklist",), match=(r"insulation",),
         source="BC Energy Step Code", why="Where the permit carries Energy Step Code requirements."),
    Item("final.schedule_c", "Finals", 190, "Schedule C-B letters of assurance", "structural_engineer",
         kind="document_request", lead_days=10, docs=("Schedule C-B from every registered professional",),
         match=(r"final inspection",), source=f"{BCBC_LOA}, Schedule C-B",
         why="Each registered professional confirms field review; DNV needs them before the final."),
    Item("final.dnv", "Finals", 200, "DNV final building inspection", "municipality", lead_days=1,
         docs=("All final letters of assurance", "Storm pump covenant on title (if a pump)"),
         match=(r"final inspection",), why="After every trade's final has passed."),
    Item("final.boulevard", "Close-out", 210, "Boulevard restoration review", "municipality", lead_days=1,
         match=(r"site restoration|final grading",), why="Releases the security deposit."),
)

LIBRARIES = {"dnv": DNV}


def library_for(municipality: str | None) -> tuple[Item, ...]:
    """The library for a municipality; DNV's is the only one so far, and the
    default, since it is the strictest set we hold."""
    return LIBRARIES.get((municipality or "dnv").lower(), DNV)
