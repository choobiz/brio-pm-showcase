"""The questions that have to be answered before an estimate or scope is worth
much — each one carrying what the answer actually buys.

§11b's argument, which is the whole reason this is an interview rather than a
list of blockers: a list still leaves the estimator to go and fix three things,
whereas an interview fixes them in the same sitting. "Do you have mechanical
drawings" is a chore. "Those two divisions are guessed rather than measured" is
a decision.

⚠️ EVERY QUESTION IS COMPUTED FROM THE RECORD. Nothing here is a model asking
whatever occurs to it. A question appears because something is demonstrably
missing — no project type, unreviewed quantities, an unsigned analysis, a
discipline with no drawings — and it disappears when that is no longer true.
That is what makes the set bounded and the end of it meaningful.

⚠️ AND WHERE A FIGURE CANNOT BE COMPUTED, NONE IS GIVEN. The approved wireframe
shows lines like "about $19,700 I am guessing rather than measuring" and "swings
roughly $14,000". Those numbers cannot be derived before an estimate exists —
there is nothing to take a percentage of — and printing a plausible one would be
a fabricated figure on the screen where an estimator decides what to chase. So
the consequence is stated in the terms that ARE known: which divisions stop
being measured, what stops being checkable, what cannot be raised. A dollar
amount appears only once there is an estimate to read it from.

⚠️ IT TERMINATES WITH A CHOICE, NOT A WALL. Stopping is always allowed; an
unanswered question stays on the list until it is answered.

⚠️ ANSWERS LIVE ON THE PROJECT. Finish level, delivered-as and the decisions
about disciplines with no drawings are project columns (migration 0066), written
through PATCH /projects/{id} like type and year built. A question disappears
because its column is filled, not because this module remembers it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Project types where the building's age decides whether hazardous materials
#: are raised. A new build has no existing fabric, so the question is not asked
#: rather than asked and ignored.
_RENOVATION_TYPES = ("residential_reno", "commercial_ti")

#: Before this year, BC renovation work routinely triggers a hazardous materials
#: survey. It decides whether the interview RAISES the question, nothing more.
HAZMAT_YEAR = 1990

#: Decisions that swing a budget and that nothing else asks. Each is answered by
#: writing its project column; `column` names it and `values` maps each option
#: to the stored value, in the same order as `options`.
_JUDGEMENT: list[dict[str, Any]] = [
    # "finish_level" (builder/mid/high) is no longer asked (2026-10-13): a tier
    # applied defaults nobody chose. Finishes are now asked one by one on the
    # Estimate page — see the finishes question below and services/finish_spec.
    {
        "id": "shell_or_finished",
        "column": "delivered_as",
        "values": ["finished", "shell", "part_shell"],
        "question": "Is the space delivered as a finished space, or as a shell?",
        "why": ("It decides whether several divisions are priced at all. A shell "
                "and a finished space are different projects at the same square "
                "footage."),
        "options": ["Finished", "Shell", "Part shell, part finished"],
    },
]


def _q(
    qid: str, question: str, why: str, *, blocking: bool, kind: str,
    options: list[str] | None = None, action: str | None = None,
    current: Any = None, answer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`answer`, when present, says how to answer in place: the project field to
    PATCH and the value each option writes ({"field", "values"}, or for a
    drawing decision {"field": "drawing_decisions", "key", "values"}, where a
    None value means the option is answered elsewhere, e.g. by uploading)."""
    return {
        "id": qid, "question": question, "why": why, "blocking": blocking,
        "kind": kind, "options": options, "action": action, "current": current,
        "answer": answer,
    }


def _project(db: Session, project_id: UUID) -> dict[str, Any] | None:
    row = db.execute(
        _T("SELECT id, name, type, building_age, status, finish_level, delivered_as, "
           "drawing_decisions, finish_selections FROM projects WHERE id = :p"),
        {"p": str(project_id)},
    ).mappings().first()
    return dict(row) if row else None


def _blocking_questions(db: Session, project_id: UUID, proj: dict[str, Any]) -> list[dict]:
    """Things that stop an estimate being produced at all."""
    out: list[dict] = []

    if not proj.get("type"):
        out.append(_q(
            "project_type",
            "Is this a new build, a renovation, or a commercial fit-out?",
            "It decides whether the estimate uses Brio's 18 residential divisions "
            "or the CSI breakdown. With no type the project resolves to NO "
            "division set at all, and the estimate falls back to a generic list "
            "that is not Brio's standard. Nothing on the estimate says so.",
            blocking=True, kind="project_type",
            options=["New build — residential", "Renovation — residential",
                     "Commercial tenant improvement", "New build — commercial"],
            action=f"/projects/{project_id}/overview",
            answer={"field": "type", "values": [
                "residential_new", "residential_reno", "commercial_ti", "commercial_new"]},
        ))

    # Unreviewed quantities and an unsigned analysis are separate gates and are
    # asked separately: "review these" and "sign this off" are different acts,
    # and collapsing them hides which one is outstanding.
    try:
        from app.services.quantity_gate import gate_status

        # GateStatus is a dataclass: `open` means work is outstanding, and
        # `unresolved` lists it. Read by its real names rather than guessed at —
        # a getattr fallback would have silently found nothing and reported the
        # gate as clear.
        st = gate_status(db, project_id)
        if st.open:
            n = len(st.unresolved)
            if n:
                out.append(_q(
                    "quantities_unreviewed",
                    f"{n} measurement{'s' if n != 1 else ''} from the drawings "
                    f"{'have' if n != 1 else 'has'} not been reviewed.",
                    "An estimate cannot price a number nobody has checked. These "
                    "are what the machine read; until a person agrees or corrects "
                    "each one, the gate stays shut.",
                    blocking=True, kind="quantities",
                    action=f"/projects/{project_id}/takeoff",
                    current=n,
                ))
    except Exception:  # noqa: BLE001 — the gate is advisory to the interview
        pass

    # ⚠️ MIRRORS quantity_gate._assert_pack_approved — CAD present and the latest
    # pack not "approved" is a refusal. This used to test for "signed", a status
    # that does not exist (PACK_STATUSES is draft | approved | superseded), so an
    # approved analysis still read as blocking; and it skipped the project with
    # CAD and NO pack at all, which is the case the gate refuses first.
    from app.services.estimate_readiness import IS_CAD_SQL

    has_cad = db.execute(_T(
        "SELECT 1 FROM documents WHERE project_id = :p AND archived = false "
        "AND {cad} LIMIT 1".format(cad=IS_CAD_SQL)), {"p": str(project_id)}).first()
    row = db.execute(_T(
        "SELECT status FROM drawing_analysis_packs WHERE project_id = :p "
        "ORDER BY version_number DESC LIMIT 1"), {"p": str(project_id)}).first()
    if has_cad and (row is None or row[0] != "approved"):
        never = row is None
        out.append(_q(
            "analysis_unsigned",
            ("The CAD drawings have not been analysed." if never
             else "The drawing analysis has not been signed off."),
            ("An estimate is refused until the drawings are read and the analysis "
             "approved. " if never else "") +
            "Confirming individual numbers and signing the analysis are different "
            "acts. Without the signature an estimate could price a set whose "
            "missing storey or failed sheet nobody looked at.",
            blocking=True, kind="analysis",
            action=f"/projects/{project_id}/takeoff",
            current=None if never else row[0],
        ))

    return out


def _material_questions(
    db: Session, project_id: UUID, proj: dict[str, Any],
) -> list[dict]:
    """Things that change the number, or change how much of it is measured."""
    out: list[dict] = []

    # Disciplines with no drawings. The consequence is stated in divisions, not
    # dollars — see the module docstring for why no figure is invented.
    try:
        from app.services.estimate_readiness import compute_readiness

        r = compute_readiness(db, project_id) or {}
        acc = r.get("accuracy") or {}
        discs = acc.get("disciplines") or {}
        absent = [d for d, i in discs.items()
                  if (i or {}).get("source") in (None, "none", "missing")]

        # ⚠️ ONE QUESTION, NOT NINE, WHEN NOTHING HAS BEEN UPLOADED. A project
        # with no drawings produced nine near-identical questions — "are there
        # architectural drawings", "are there structural drawings" — which is
        # exactly the open-ended interrogation §11b warns is worse than a form.
        # When EVERY discipline is missing the useful question is the single
        # one; per-discipline questions are for a set with gaps IN it.
        decided = proj.get("drawing_decisions") or {}
        if discs and len(absent) == len(discs) and decided.get("all"):
            absent = []  # "there are none — price from scope" was the answer
        elif discs and len(absent) == len(discs):
            out.append(_q(
                "drawings_none",
                "Are there drawings for this project at all?",
                (f"Nothing has been uploaded, so all {len(discs)} disciplines are "
                 f"priced from the scope and rates rather than measured. That is "
                 f"the lowest-confidence path available, and every division on the "
                 f"estimate carries it."),
                blocking=False, kind="documents",
                options=["Upload them", "There are none — price from scope"],
                action=f"/projects/{project_id}/documents",
                answer={"field": "drawing_decisions", "key": "all",
                        "values": [None, "allowance"]},
            ))
            absent = []

        absent = [d for d in absent if not decided.get(d)]

        for disc in absent:
            info = discs.get(disc) or {}
            divs = info.get("divisions") or []
            named = ", ".join(divs[:3]) + ("…" if len(divs) > 3 else "")
            out.append(_q(
                f"drawings_{disc}",
                f"Are there {disc} drawings for this project?",
                (f"Without them {len(divs)} division{'s' if len(divs) != 1 else ''} "
                 f"({named}) are priced as allowances rather than measured. "
                 f"That is a guess carried into the total, and the estimate "
                 f"will say so."),
                blocking=False, kind="documents",
                options=["Upload them", "Price as an allowance", "Not in scope"],
                action=f"/projects/{project_id}/documents",
                answer={"field": "drawing_decisions", "key": disc,
                        "values": [None, "allowance", "not_in_scope"]},
            ))
    except Exception:  # noqa: BLE001
        pass

    if proj.get("type") in _RENOVATION_TYPES and not proj.get("building_age"):
        out.append(_q(
            "year_built",
            "What year was the building built?",
            f"This is work on existing fabric. Before {HAZMAT_YEAR}, hazardous "
            f"materials should be raised before demolition; after it they are not "
            f"routinely. With no year recorded, neither is said.",
            blocking=False, kind="year_built",
            action=f"/projects/{project_id}/overview",
            answer={"field": "building_age"},
        ))

    for j in _JUDGEMENT:
        if proj.get(j["column"]):
            continue
        out.append(_q(j["id"], j["question"], j["why"], blocking=False,
                      kind="judgement", options=j["options"],
                      answer={"field": j["column"], "values": j["values"]}))

    # A shell has no finishes; anything else is asked finish by finish.
    if proj.get("delivered_as") in ("finished", "part_shell"):
        from app.services.finish_spec import QUESTIONS

        sel = proj.get("finish_selections") or {}
        if isinstance(sel, str):
            import json as _json
            sel = _json.loads(sel or "{}")
        open_ = [q["label"] for q in QUESTIONS if not (sel.get(q["key"]) or {}).get("confirmed")]
        if open_:
            out.append(_q(
                "finishes",
                f"Confirm the finishes: {len(QUESTIONS) - len(open_)} of {len(QUESTIONS)} done.",
                ("Each unconfirmed finish is priced as a typical choice and listed on the "
                 "estimate as an assumption. Still open: " + ", ".join(open_) + "."),
                blocking=False, kind="judgement",
                action=f"/projects/{project_id}/estimate#finishes",
            ))

    return out


def build_interview(
    db: Session, project_id: UUID, answered: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The questions outstanding for this project, blocking first.

    `answered` carries ids already answered in this sitting, so the set shrinks
    as it is worked through without anything being written yet.
    """
    proj = _project(db, project_id)
    if proj is None:
        raise ValueError("That project is no longer here.")

    done = set((answered or {}).keys())
    blocking = [q for q in _blocking_questions(db, project_id, proj) if q["id"] not in done]
    material = [q for q in _material_questions(db, project_id, proj) if q["id"] not in done]

    confidence = None
    coverage = None
    try:
        from app.services.estimate_readiness import compute_readiness

        r = compute_readiness(db, project_id) or {}
        confidence = r.get("confidence")
        coverage = (r.get("accuracy") or {}).get("coverage_pct")
    except Exception:  # noqa: BLE001
        pass

    return {
        "project_id": str(project_id),
        "project_name": proj.get("name"),
        "blocking": blocking,
        "material": material,
        "answered": sorted(done),
        # Declared up front. An open-ended interrogation is worse than a form —
        # a reader needs to know it ends before they will start it.
        "summary": (
            f"{len(blocking)} thing{'s' if len(blocking) != 1 else ''} "
            f"{'block' if len(blocking) != 1 else 'blocks'}, "
            f"{len(material)} would change the number."
            if (blocking or material) else
            "Nothing outstanding — everything this can check is answered."
        ),
        "confidence": confidence,
        "coverage_pct": coverage,
        "can_estimate": not blocking,
        # The stopping rule, stated rather than implied.
        "stopping_rule": (
            "Stop whenever you like. Anything blocking must be cleared before an "
            "estimate can be produced at all; everything else is optional and "
            "stays on this list until it is answered."
        ),
        "no_figures_note": (
            "Consequences are stated in divisions and confidence, not dollars. A "
            "dollar figure cannot be derived before an estimate exists, and a "
            "plausible invented one is worse than none."
        ),
    }


def outstanding_for_scope(
    db: Session, project_id: UUID, answered: dict[str, Any] | None = None,
) -> list[str]:
    """What should be carried into the scope's `informationRequired` list.

    Reuses the field the scope already has and already renders, rather than
    inventing a parallel place for open questions to be forgotten in.
    """
    iv = build_interview(db, project_id, answered)
    lines = []
    for q in [*iv["blocking"], *iv["material"]]:
        lines.append(f"{q['question']} — {q['why']}")
    return lines
