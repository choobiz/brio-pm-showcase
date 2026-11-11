"""Grade how accurate an estimate can be, from the documents actually uploaded.

"Can we produce an estimate" is the wrong question — the answer is always yes,
and that is exactly the problem. An estimate built from a scope description and a
rate library looks identical to one measured off a CAD model, and costs the same
to produce. What differs is how wrong it can be, and by which trade.

So the grade is per DISCIPLINE, not per project. A set with architectural CAD and
no structural drawings measures its partitions and guesses its footings — and the
footings are the expensive half. Saying "CAD present, measured takeoff" over that
set is true and useless.

Each discipline maps to the BRIO divisions it prices. What is missing is reported
as the divisions that fall back to an allowance, in the language of the estimate
the user is about to generate rather than the language of a file listing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: discipline -> (BRIO divisions it governs, what it contributes, budget weight)
#: Weight is the share of a typical BRIO residential build this discipline's
#: drawings govern. Used to order what to chase, not to compute a price.
DISCIPLINE_IMPACT: dict[str, dict[str, Any]] = {
    "architectural": {
        "divisions": ["04 Rough Carpentry & Framing", "05 Doors, Frames, Hardware & Glazing",
                      "06 Insulation, Drywall & Acoustic", "07 Painting & Finishes",
                      "08 Flooring & Tile", "09 Millwork & Casework",
                      "16 Exterior Cladding, Siding & Trim"],
        "gives": "room areas, opening counts and sizes, partition lengths, envelope area",
        "weight": 45,
    },
    "structural": {
        "divisions": ["03 Foundation & Concrete", "04 Rough Carpentry & Framing"],
        "gives": "footing and wall sizes, engineered members, rebar and connection detail",
        "weight": 20,
    },
    "civil": {
        "divisions": ["17 Site Work", "18 Stormwater Management",
                      "02 Excavation, Demolition & Site Clearing"],
        "gives": "grading, servicing runs, detention volume, excavation depth",
        "weight": 15,
    },
    "mechanical": {
        "divisions": ["12 Mechanical / HVAC"],
        "gives": "equipment schedule, duct runs, ventilation loads",
        "weight": 7,
    },
    "electrical": {
        "divisions": ["14 Electrical & Low-Voltage"],
        "gives": "panel and circuit schedule, device counts, service size",
        "weight": 7,
    },
    "plumbing": {
        "divisions": ["13 Plumbing & Fixtures"],
        "gives": "fixture schedule, supply and drainage runs",
        "weight": 6,
    },
}

#: Disciplines whose absence materially changes a residential budget.
BUDGET_CRITICAL = ("architectural", "structural", "civil")

GRADES = {
    "measured_complete": (
        "MEASURED — every budget-affecting discipline is CAD. Quantities are "
        "measured, not assumed."),
    "measured_partial": (
        "PARTLY MEASURED — the architectural set is CAD so areas and openings are "
        "measured, but one or more budget-affecting disciplines have no drawings. "
        "Those divisions fall back to allowances."),
    "read_from_pdf": (
        "READ, NOT MEASURED — no CAD. Quantities are read off PDF drawings, which "
        "needs a human to confirm every one. Upload the DWG or DXF and the same "
        "drawings produce a measured takeoff."),
    "scope_only": (
        "NOT MEASURED — no drawings at all. The estimate can only price a written "
        "scope against the rate library. Treat the total as an order of magnitude."),
}


@dataclass
class Accuracy:
    grade: str
    headline: str
    disciplines: dict[str, dict[str, Any]] = field(default_factory=dict)
    allowance_divisions: list[str] = field(default_factory=list)
    measured_divisions: list[str] = field(default_factory=list)
    next_uploads: list[dict[str, Any]] = field(default_factory=list)
    coverage_pct: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade, "headline": self.headline,
            "coverage_pct": self.coverage_pct,
            "disciplines": self.disciplines,
            "measured_divisions": self.measured_divisions,
            "allowance_divisions": self.allowance_divisions,
            "next_uploads": self.next_uploads,
            "warnings": self.warnings,
        }


def grade_accuracy(discipline_coverage: dict[str, dict[str, bool]],
                   set_audit: Any = None, unattributed: int = 0) -> Accuracy:
    """`discipline_coverage` is {discipline: {"cad": bool, "pdf": bool}} — the
    shape `estimate_readiness.compute_readiness` already produces.

    `set_audit` is an optional SetAudit. A set can hold architectural CAD and
    still be missing a whole storey; coverage of the DISCIPLINES says nothing
    about coverage of the BUILDING, and both belong in the same verdict.
    """
    disciplines: dict[str, dict[str, Any]] = {}
    measured: list[str] = []
    allowance: list[str] = []
    weight_covered = 0

    for name, impact in DISCIPLINE_IMPACT.items():
        cov = discipline_coverage.get(name) or {}
        has_cad, has_pdf = bool(cov.get("cad")), bool(cov.get("pdf"))
        source = "cad" if has_cad else ("pdf" if has_pdf else "none")
        # PDF counts for half: the drawing exists and can be read, but nothing is
        # measured from it without a human confirming the scale and every figure.
        weight_covered += impact["weight"] * (1.0 if has_cad else 0.5 if has_pdf else 0.0)
        disciplines[name] = {
            "source": source,
            "divisions": impact["divisions"],
            "gives": impact["gives"],
            "budget_critical": name in BUDGET_CRITICAL,
        }
        (measured if has_cad else allowance).extend(impact["divisions"])

    arch = disciplines["architectural"]["source"]
    missing_critical = [d for d in BUDGET_CRITICAL
                        if disciplines[d]["source"] == "none"]
    # A budget-critical discipline that only has PDFs is READ, not measured, and
    # must not count toward "complete". Requiring mere presence let a project with
    # PDF structural drawings grade as MEASURED at 62% coverage — a contradiction
    # on its face, and exactly the overstatement this grading exists to stop.
    pdf_only_critical = [d for d in BUDGET_CRITICAL
                         if disciplines[d]["source"] == "pdf"]

    if arch == "none" and all(v["source"] == "none" for v in disciplines.values()):
        grade = "scope_only"
    elif arch == "cad" and not missing_critical and not pdf_only_critical:
        grade = "measured_complete"
    elif arch == "cad":
        grade = "measured_partial"
    else:
        grade = "read_from_pdf"

    # What to upload next, worst-impact first — the point is to name the one
    # document that moves the estimate most, not to list everything absent.
    nxt = []
    for name, impact in sorted(DISCIPLINE_IMPACT.items(),
                               key=lambda kv: -kv[1]["weight"]):
        src = disciplines[name]["source"]
        if src == "cad":
            continue
        nxt.append({
            "discipline": name,
            "have": src,
            "ask": ("the CAD (DWG/DXF)" if src == "pdf" else "any drawings, CAD preferred"),
            "unlocks": impact["gives"],
            "divisions": impact["divisions"],
            "budget_critical": name in BUDGET_CRITICAL,
        })

    warnings: list[str] = []
    if grade == "measured_complete":
        warnings.append(
            "The takeoff measures GEOMETRY. Mechanical, electrical and plumbing "
            "quantities are counts and lengths, not loads or sizing — those still "
            "come from the consultants' schedules.")
    if missing_critical:
        warnings.append(
            "No drawings for: " + ", ".join(missing_critical) +
            ". Those divisions are allowances and will not respond to the takeoff.")
    if pdf_only_critical:
        warnings.append(
            "PDF only for: " + ", ".join(pdf_only_critical) +
            ". Those quantities have to be read and confirmed by hand — upload "
            "the CAD and they become measured.")

    if unattributed:
        warnings.append(
            f"{unattributed} drawing(s) could not be attributed to a discipline "
            f"from their filename, so they count toward nothing above. Rename or "
            f"recategorise them and the grade may improve.")

    if set_audit is not None:
        for b, d in (getattr(set_audit, "by_building", {}) or {}).items():
            missing = d.get("storeys_missing") or []
            if missing:
                warnings.append(
                    f"{b}: no floor plan for {', '.join(missing)} — every measured "
                    f"quantity covers {len(d.get('storeys_covered') or [])} of "
                    f"{len(d.get('storeys_expected') or [])} storeys.")

    # Deduplicate while keeping order; a division governed by two disciplines is
    # measured if EITHER of them is measured.
    measured_u = list(dict.fromkeys(measured))
    allowance_u = [d for d in dict.fromkeys(allowance) if d not in measured_u]

    return Accuracy(
        grade=grade, headline=GRADES[grade],
        disciplines=disciplines,
        measured_divisions=measured_u,
        allowance_divisions=allowance_u,
        next_uploads=nxt,
        coverage_pct=int(round(weight_covered)),
        warnings=warnings,
    )
