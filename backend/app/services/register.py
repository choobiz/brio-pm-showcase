"""The register — what needs a person's attention, as typed items.

⚠️ WHY ITEMS AND NOT A CHAT PANE. Construction already has a grammar for this
and it is not conversation: the RFI log, the submittal log, the punch list, the
deficiency list. Typed entries, a state, an owner, a date. Staff read those
fluently with no training, they survive being interrupted for three days, and
they answer "what needs me" without anyone having to think of the question.

Nothing here writes. Every item is DERIVED on read from records that already
exist, which is why Phase 1 needs no table of its own: a stored register is a
second copy of a fact, and two copies of one fact is the failure this codebase
has spent a lot of effort removing elsewhere.

TWO RULES, both load-bearing:

  1. **Every item states its consequence — ONCE, and briefly.** "9 of 15
     unreviewed" is trivia; "estimating is blocked on this project" is
     actionable. An item that cannot name what it is blocking probably should
     not be an item.

     ⚠️ The first build got this half right and half wrong. Each card carried the
     full rationale, so one explanatory sentence about sign-off appeared FOUR
     times on one screen and the advisory half was three identical cards. Every
     word was true and the page was unreadable. Hence `summary` (one short line,
     always shown) and `why` (the reasoning, on demand). Repetition is noise even
     when every instance is correct.

  1b. **One item per FACT, not per row.** Seventeen untyped projects are one
     item that says seventeen, not seventeen items. The same must hold for
     anything else that recurs across projects without differing.

  2. **Every item carries its evidence.** `evidence` names the records the item
     was derived from, so a reader can check the claim rather than trust it.
     This is also what the source-accuracy acceptance check samples.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Blocking = someone is actively trying to do the work and a gate is refusing.
#: Attention = worth knowing, but nobody is stopped.
#:
#: ⚠️ THE DISTINCTION IS NOT "is a gate closed". Run against live data, a naive
#: reading produced nine blocking items, six of which were the same "not
#: analysed yet" notice on projects nobody was estimating — including two
#: occupancy permits from 2021 and 2023 and a test fixture. Technically true,
#: useless to read. A gate is only BLOCKING when someone has reached it.
BLOCKING, ATTENTION = "blocking", "attention"

#: Archived projects never appear. An archived project is by definition not
#: being worked on, so every item on it is noise.
_LIVE = "p.status <> 'archived'"


def _rows(db: Session, sql: str, **params) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(_T(sql), params).mappings().all()]


def build_register(
    db: Session, project_ids: list[UUID], *, project_id: UUID | None = None,
    company_wide: bool = False,
) -> list[dict[str, Any]]:
    """Every open item across the caller's projects, blocking first.

    ``project_ids`` is the caller's already-authorised scope. An empty list
    yields nothing — never everything.
    """
    if not project_ids:
        return []
    pids = [str(p) for p in (([project_id]) if project_id else project_ids)]
    items: list[dict[str, Any]] = []
    #: Projects with drawings and no work started — collected into one item.
    ready: list[dict[str, Any]] = []

    # ── 1. Quantities awaiting review. The first half of the estimate gate. ──
    for r in _rows(db, """
        SELECT q.project_id, p.name AS project_name,
               count(*) FILTER (WHERE q.status = 'candidate') AS pending,
               count(*) AS total,
               min(q.created_at)::date AS since
          FROM quantity_overrides q JOIN projects p ON p.id = q.project_id
         WHERE q.project_id = ANY(:pids) AND p.status <> 'archived'
      GROUP BY q.project_id, p.name
        HAVING count(*) FILTER (WHERE q.status = 'candidate') > 0
      ORDER BY 3 DESC""", pids=pids):
        items.append({
            "key": f"quantities-unreviewed:{r['project_id']}",
            "severity": BLOCKING,
            "kind": "quantities_unreviewed",
            "project_id": str(r["project_id"]),
            "project_name": r["project_name"],
            "title": "Quantities not yet reviewed",
            "count": int(r["pending"]),
            "summary": "Estimating is blocked on this project.",
            "why": (
                f"{r['pending']} of {r['total']} measurements taken from the drawings have not "
                "been looked at. Each one has to be accepted, corrected or marked as not "
                "counted before an estimate is allowed to price it."
            ),
            "since": str(r["since"]) if r["since"] else None,
            "evidence": {"table": "quantity_overrides", "status": "candidate",
                         "pending": int(r["pending"]), "total": int(r["total"])},
            "actions": [{"label": "Review", "href": f"/projects/{r['project_id']}/takeoff"}],
        })

    # ── 2. Drawing analysis unsigned. The second half of the same gate. ─────
    # Mirrors quantity_gate._assert_pack_approved: CAD is CAD wherever filed,
    # and only the LATEST analysis counts, so a rebuild re-blocks.
    from app.services.estimate_readiness import IS_CAD_SQL
    for r in _rows(db, f"""
        SELECT p.id AS project_id, p.name AS project_name,
               (SELECT count(*) FROM documents d
                 WHERE d.project_id = p.id AND d.archived = false AND {IS_CAD_SQL}) AS cad_files,
               (SELECT k.status FROM drawing_analysis_packs k
                 WHERE k.project_id = p.id ORDER BY k.version_number DESC LIMIT 1) AS latest_status,
               -- Has anyone actually started here? A gate nobody has reached is a
               -- next step, not a blocker.
               --
               -- An existing analysis COUNTS, even a draft one: building it is
               -- starting. Leaving it out treated "ran the analysis and has not
               -- signed it" as nobody-has-begun, which is exactly backwards —
               -- that is the case where someone is standing at the gate.
               ((SELECT count(*) FROM quantity_overrides q WHERE q.project_id = p.id)
              + (SELECT count(*) FROM estimates e WHERE e.project_id = p.id)
              + (SELECT count(*) FROM drawing_analysis_packs k WHERE k.project_id = p.id)
               ) AS work_started
          FROM projects p
         WHERE p.id = ANY(:pids) AND {_LIVE}
           AND EXISTS (SELECT 1 FROM documents d
                        WHERE d.project_id = p.id AND d.archived = false AND {IS_CAD_SQL})
      ORDER BY p.name""", pids=pids):
        status = r["latest_status"]
        if status == "approved":
            continue
        never = status is None
        if int(r["work_started"]) > 0:
            items.append({
                "key": f"analysis-unsigned:{r['project_id']}",
                "severity": BLOCKING,
                "kind": "analysis_unsigned",
                "project_id": str(r["project_id"]),
                "project_name": r["project_name"],
                "title": ("Drawings not analysed yet" if never
                          else "Drawing analysis not signed off"),
                "count": int(r["cad_files"]),
                "summary": "Estimating is blocked on this project.",
                "why": (
                    f"{r['cad_files']} CAD drawing(s) "
                    + ("have not been read yet. " if never
                       else f"were read, and the analysis is still {status}. ")
                    + "Signing off is a separate act from reviewing each measurement: it is "
                      "what catches a missing storey or a sheet that failed to read."
                ),
                "evidence": {"table": "drawing_analysis_packs", "latest_status": status,
                             "cad_files": int(r["cad_files"]), "work_started": True},
                "actions": [{"label": "Open the analysis",
                             "href": f"/projects/{r['project_id']}/takeoff"}],
            })
        else:
            # Collected, not repeated. Three identical cards saying "nothing is
            # being held up" is three times the words and none of the meaning.
            ready.append({"project_id": str(r["project_id"]),
                          "project_name": r["project_name"],
                          "cad_files": int(r["cad_files"])})

    if ready:
        names = [r["project_name"] for r in ready]
        items.append({
            "key": "drawings-ready",
            "severity": ATTENTION,
            "kind": "drawings_ready",
            "project_id": ready[0]["project_id"] if len(ready) == 1 else None,
            "project_name": names[0] if len(names) == 1 else None,
            "title": "Drawings ready to analyse",
            "count": len(ready),
            "summary": (
                f"{len(names)} projects have CAD drawings nobody has read yet."
                if len(names) > 1 else "This project has CAD drawings nobody has read yet."
            ),
            "why": (
                "Nothing is held up — no one has started estimating these. Reading the "
                "drawings is the first step when one is picked up. "
                + ", ".join(f"{r['project_name']} ({r['cad_files']})" for r in ready)
            ),
            "evidence": {"table": "drawing_analysis_packs", "projects": names,
                         "work_started": False},
            "actions": ([{"label": "Open the analysis",
                          "href": f"/projects/{ready[0]['project_id']}/takeoff"}]
                        if len(ready) == 1 else
                        [{"label": "Open projects", "href": "/projects"}]),
        })

    # ── 3. No project type. Decides which division breakdown applies. ───────
    untyped = _rows(db, f"""
        SELECT p.id, p.name FROM projects p
         WHERE p.id = ANY(:pids) AND p.type IS NULL AND {_LIVE} ORDER BY p.name""", pids=pids)
    if untyped:
        items.append({
            "key": "project-type-missing",
            "severity": BLOCKING,
            "kind": "project_type_missing",
            "project_id": str(untyped[0]["id"]) if len(untyped) == 1 else None,
            "project_name": untyped[0]["name"] if len(untyped) == 1 else None,
            "title": "Project type not set",
            "count": len(untyped),
            "summary": (
                f"{len(untyped)} projects would estimate against the wrong division list."
                if len(untyped) > 1 else
                "This project would estimate against the wrong division list."
            ),
            "why": (
                "Without a type there is no way to tell whether an estimate should use Brio's "
                "18 residential divisions or the commercial breakdown, so it falls back to a "
                "generic category list that is not your standard."
            ),
            "evidence": {"table": "projects", "column": "type", "null_count": len(untyped),
                         "projects": [p["name"] for p in untyped[:10]]},
            "actions": [{"label": "Set project types", "href": "/projects"}],
        })

    # ── 4. No year built. Hazmat screening is already built and cannot fire. ─
    noyear = _rows(db, f"""
        SELECT p.id, p.name FROM projects p
         WHERE p.id = ANY(:pids) AND p.building_age IS NULL AND {_LIVE} ORDER BY p.name""", pids=pids)
    if noyear:
        items.append({
            "key": "building-age-missing",
            "severity": ATTENTION,
            "kind": "building_age_missing",
            "project_id": str(noyear[0]["id"]) if len(noyear) == 1 else None,
            "project_name": noyear[0]["name"] if len(noyear) == 1 else None,
            "title": "Year built not recorded",
            "count": len(noyear),
            "summary": (
                f"Asbestos and lead screening cannot run on {len(noyear)} projects."
                if len(noyear) > 1 else
                "Asbestos and lead screening cannot run on this project."
            ),
            "why": (
                "Scope generation already checks pre-1990 buildings for asbestos and pre-1978 "
                "for lead paint. Without a year it cannot run either check, so the absence is "
                "silent rather than flagged."
            ),
            "evidence": {"table": "projects", "column": "building_age", "null_count": len(noyear)},
            "actions": [{"label": "Add years built", "href": "/projects"}],
        })

    # ── 5. AI output nobody has judged. Company-wide, one item. ─────────────
    # Not project-scoped, so it is left out when ONE project is asked for — on a
    # project page it read as that project's own backlog.
    # Superadmin only (Amir, 2026-10-15): a manager saw "3,360 AI results unjudged"
    # on Home and in Ask, a count across every project, linking to a page
    # that is now superadmin-only. Callers pass company_wide for a superadmin.
    r = [] if project_id or not company_wide else _rows(db, """
        SELECT count(*) AS pending,
               count(DISTINCT sv.skill_id) AS skills,
               min(sr.created_at)::date AS since
          FROM skill_runs sr JOIN skill_versions sv ON sv.id = sr.skill_version_id
         WHERE sr.acceptance_status = 'pending'""")
    if r and int(r[0]["pending"]) > 0:
        n = int(r[0]["pending"])
        items.append({
            "key": "ai-output-unreviewed",
            "severity": ATTENTION,
            "kind": "ai_output_unreviewed",
            "project_id": None, "project_name": None,
            "title": "AI output nobody has judged",
            "count": n,
            "summary": f"{n:,} results are unjudged, so the prompts cannot improve.",
            "why": (
                f"The oldest is from {r[0]['since']}. Nothing is wrong with them — but until "
                "someone accepts or edits a result it produces no signal to learn from."
            ),
            "evidence": {"table": "skill_runs", "acceptance_status": "pending", "count": n,
                         "distinct_skills": int(r[0]["skills"])},
            "actions": [{"label": "Review", "href": "/skills"}],
        })

    order = {BLOCKING: 0, ATTENTION: 1}
    items.sort(key=lambda i: (order[i["severity"]], -(i.get("count") or 0)))
    return items


def register_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts for the navigation badge. Blocking is what the badge shows —
    a badge that counts advisory items trains people to ignore it."""
    return {
        "total": len(items),
        "blocking": sum(1 for i in items if i["severity"] == BLOCKING),
        "attention": sum(1 for i in items if i["severity"] == ATTENTION),
        "projects_blocked": len({i["project_id"] for i in items
                                 if i["severity"] == BLOCKING and i["project_id"]}),
    }
