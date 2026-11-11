"""Home and the project overview: what is read from where.

Home (wireframe round 1, approved 2026-10-12) answers "what needs me, across
every job" on login. The project overview answers "how far is this one from an
estimate". Both are read-only views over records that already exist; nothing
here writes.

⚠️ ACTIVE IS NOT THE STATUS COLUMN. On 2026-10-12 all 35 live projects were
`preconstruction`, including 1234 Example, which was framing. Selecting
active jobs by status would have shown none. So a job is active when its status
says it is under way OR something was done on it in the last ACTIVE_DAYS days,
and each card says which of the two put it there.

⚠️ CONTRACT VALUE HAS TWO SOURCES (Amir, 2026-10-12): the contract record when
one exists, otherwise the active estimate, labelled as such. Both are BEFORE
GST, which is the basis invoices bill "% of contract" against.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

ACTIVE_DAYS = 30
UNDER_WAY = ("in_progress", "closeout")

#: Rendered by the app, not uploaded by anyone. "Documents in" means a person
#: put something here.
GENERATED_CATEGORIES = ("scope_pdf", "project_index", "invoice_pdf", "change_order_pdf", "prepared")


def today() -> date:
    """Vancouver's date. The UTC date turns over at 5pm here, which put Home's
    "this week" (and the assistant's "as of") a day ahead every evening."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Vancouver")).date()


def _payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def active_estimate(db: Session, pid: str) -> dict[str, Any] | None:
    """The active estimate version and its total before GST.

    Same arithmetic as the manager portfolio and the owner portal: non-optional
    categories, then overhead, then profit, each rounded where they round.
    """
    row = db.execute(_T(
        "SELECT id, version_number, label, created_at, payload FROM estimate_versions "
        "WHERE project_id = :p AND is_active = true LIMIT 1"), {"p": pid}).mappings().first()
    if not row:
        return None
    from app.services.estimate_totals import version_totals

    est = _payload(row["payload"])
    t = version_totals(est)
    before_gst = t["before_gst"]
    return {
        "id": str(row["id"]),
        "version_number": int(row["version_number"]),
        "label": row["label"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "detail_level": est.get("detailLevel"),
        "total_before_gst": before_gst,
        "total_with_gst": t["with_gst"],
    }


def contract_value(db: Session, pid: str, est: dict | None) -> dict[str, Any] | None:
    row = db.execute(_T(
        "SELECT base_contract_value, option_contract_value, status FROM contracts "
        "WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"), {"p": pid}).mappings().first()
    if row and row["base_contract_value"] is not None:
        return {"amount": round(float(row["base_contract_value"])), "source": "contract",
                "status": row["status"], "before_gst": True}
    if est:
        return {"amount": est["total_before_gst"], "source": "estimate",
                "version_number": est["version_number"], "before_gst": True}
    return None


def _latest_schedule_id(db: Session, pid: str):
    return db.execute(_T(
        "SELECT id FROM schedules WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"),
        {"p": pid}).scalar()


def next_milestone(db: Session, pid: str, on: date) -> dict[str, Any] | None:
    """First milestone on or after today in the latest schedule. Same rule as
    the owner portal, minus its fallback to the last past milestone: on an
    office card a past date reads as overdue, which it may not be."""
    sid = _latest_schedule_id(db, pid)
    if not sid:
        return None
    r = db.execute(_T(
        "SELECT name, start_date FROM activities WHERE schedule_id = :s AND is_milestone "
        "AND start_date >= :d ORDER BY start_date LIMIT 1"), {"s": sid, "d": on}).mappings().first()
    return {"label": r["name"], "date": str(r["start_date"])} if r else None


def last_activity(db: Session, pids: list[str]) -> dict[str, datetime]:
    """When anyone last did something on each project."""
    if not pids:
        return {}
    rows = db.execute(_T("""
        SELECT project_id, max(at) AS at FROM (
            SELECT project_id, created_at AS at FROM documents WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM estimate_versions WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM scopes WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM daily_site_reports WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM invoices WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM change_orders WHERE project_id = ANY(:p)
            UNION ALL SELECT project_id, created_at FROM quantity_overrides WHERE project_id = ANY(:p)
        ) t GROUP BY project_id"""), {"p": pids}).mappings().all()
    return {str(r["project_id"]): r["at"] for r in rows if r["at"]}


def active_jobs(db: Session, pids: list[str], on: date) -> list[dict[str, Any]]:
    if not pids:
        return []
    projects = db.execute(_T(
        "SELECT id, name, address, status, type FROM projects "
        "WHERE id = ANY(:p) AND status <> 'archived'"), {"p": pids}).mappings().all()
    last = last_activity(db, [str(p["id"]) for p in projects])
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)
    out = []
    for p in projects:
        pid = str(p["id"])
        at = last.get(pid)
        under_way = p["status"] in UNDER_WAY
        recent = at is not None and at >= cutoff
        if not (under_way or recent):
            continue
        est = active_estimate(db, pid)
        out.append({
            "project_id": pid, "name": p["name"], "address": p["address"],
            "status": p["status"], "type": p["type"],
            "why_active": "status" if under_way else "recent_work",
            "last_activity_at": at.isoformat() if at else None,
            "next_milestone": next_milestone(db, pid, on),
            "estimate": ({k: est[k] for k in ("version_number", "total_with_gst", "total_before_gst")}
                         if est else None),
            "contract": contract_value(db, pid, est),
        })
    out.sort(key=lambda j: (0 if j["why_active"] == "status" else 1,
                            -(datetime.fromisoformat(j["last_activity_at"]).timestamp()
                              if j["last_activity_at"] else 0)))
    return out


def this_week(db: Session, pids: list[str], on: date) -> list[dict[str, Any]]:
    """Schedule activities starting, finishing or falling due in the next 7 days,
    from each project's latest schedule."""
    if not pids:
        return []
    end = on + timedelta(days=6)
    rows = db.execute(_T("""
        WITH latest AS (
            SELECT DISTINCT ON (s.project_id) s.id, s.project_id
              FROM schedules s JOIN projects p ON p.id = s.project_id
             WHERE s.project_id = ANY(:p) AND p.status <> 'archived'
          ORDER BY s.project_id, s.created_at DESC)
        SELECT l.project_id, pr.name AS project_name, a.name, a.phase, a.start_date,
               a.finish_date, a.is_milestone
          FROM latest l JOIN activities a ON a.schedule_id = l.id
          JOIN projects pr ON pr.id = l.project_id
         WHERE (a.start_date BETWEEN :d AND :e) OR (a.finish_date BETWEEN :d AND :e)
      ORDER BY coalesce(a.start_date, a.finish_date), pr.name
         LIMIT 40"""), {"p": pids, "d": on, "e": end}).mappings().all()
    out = []
    for r in rows:
        starts = r["start_date"] is not None and on <= r["start_date"] <= end
        out.append({
            "project_id": str(r["project_id"]), "project_name": r["project_name"],
            "name": r["name"], "phase": r["phase"], "milestone": bool(r["is_milestone"]),
            "event": "milestone" if r["is_milestone"] else ("starts" if starts else "finishes"),
            "date": str(r["start_date"] if (starts or r["is_milestone"]) else r["finish_date"]),
        })
    # In the order of the date each line shows: the SQL orders by start, and an
    # activity that started weeks ago is listed on its finish day (rollout test 2026-10-14).
    out.sort(key=lambda i: (i["date"], i["project_name"] or "", i["name"] or ""))
    return out


# ── The road to an estimate (project overview) ───────────────────────────

def road(db: Session, project_id: UUID) -> dict[str, Any]:
    """Six steps from nothing to an estimate, each done / to do / not needed,
    and the first one not done is `current`.

    Steps 3-5 are the drawing-analysis gate. A project with no CAD has nothing
    for that gate to read, so those steps are 'not needed' rather than left
    looking undone forever.
    """
    from app.services.estimate_readiness import IS_CAD_SQL

    pid = str(project_id)
    one = lambda sql, **kw: db.execute(_T(sql), {"p": pid, **kw}).scalar()  # noqa: E731

    docs = int(one(
        "SELECT count(*) FROM documents WHERE project_id = :p AND archived = false "
        "AND category <> ALL(:g)", g=list(GENERATED_CATEGORIES)) or 0)
    scopes = int(one("SELECT count(*) FROM scopes WHERE project_id = :p") or 0)
    cad = int(one(
        f"SELECT count(*) FROM documents WHERE project_id = :p AND archived = false AND {IS_CAD_SQL}") or 0)
    pack = one("SELECT status FROM drawing_analysis_packs WHERE project_id = :p "
               "ORDER BY version_number DESC LIMIT 1")
    q = db.execute(_T(
        "SELECT count(*) AS total, count(*) FILTER (WHERE status = 'candidate') AS pending "
        "FROM quantity_overrides WHERE project_id = :p"), {"p": pid}).mappings().one()
    q_total, q_pending = int(q["total"]), int(q["pending"])
    est = active_estimate(db, pid)
    versions = int(one("SELECT count(*) FROM estimate_versions WHERE project_id = :p") or 0)

    base = f"/projects/{pid}"
    analysis = f"{base}/takeoff"
    steps: list[dict[str, Any]] = [
        {"key": "documents", "label": "Documents in",
         "state": "done" if docs else "todo",
         "detail": f"{docs} file{'s' if docs != 1 else ''}" if docs else "Nothing uploaded yet",
         "href": f"{base}/documents"},
        {"key": "scope", "label": "Scope written",
         "state": "done" if scopes else "todo",
         "detail": f"{scopes} version{'s' if scopes != 1 else ''}" if scopes else "No scope yet",
         "href": f"{base}/scope"},
    ]
    if cad:
        steps.append({"key": "drawings_read", "label": "Drawings read",
                      "state": "done" if pack else "todo",
                      "detail": (f"{cad} CAD file{'s' if cad != 1 else ''} read" if pack
                                 else f"{cad} CAD file{'s' if cad != 1 else ''} not read yet"),
                      "href": analysis})
    else:
        steps.append({"key": "drawings_read", "label": "Drawings read", "state": "not_needed",
                      "detail": "No CAD drawings", "href": analysis})
    if q_total:
        steps.append({"key": "checked", "label": "Measurements checked",
                      "state": "done" if q_pending == 0 else "todo",
                      "detail": f"{q_total - q_pending} of {q_total} checked",
                      "href": analysis})
    elif cad:
        steps.append({"key": "checked", "label": "Measurements checked", "state": "todo",
                      "detail": "Nothing measured yet", "href": analysis})
    else:
        steps.append({"key": "checked", "label": "Measurements checked", "state": "not_needed",
                      "detail": "Nothing measured from drawings", "href": analysis})
    if cad:
        steps.append({"key": "signed_off", "label": "Signed off",
                      "state": "done" if pack == "approved" else "todo",
                      "detail": {"approved": "Approved", None: "Not analysed yet"}.get(
                          pack, f"Analysis is {pack}"),
                      "href": analysis})
    else:
        steps.append({"key": "signed_off", "label": "Signed off", "state": "not_needed",
                      "detail": "No CAD analysis to sign", "href": analysis})
    steps.append({"key": "estimate", "label": "Estimate",
                  "state": "done" if est else "todo",
                  "detail": (f"v{est['version_number']} · ${est['total_with_gst']:,} with GST"
                             if est else ("Drafts only, none active" if versions else "Not yet")),
                  "href": f"{base}/estimate"})

    current = next((s["key"] for s in steps if s["state"] == "todo"), None)
    for s in steps:
        s["current"] = s["key"] == current

    invoiced = db.execute(_T(
        "SELECT count(*) AS n, coalesce(sum((payload->>'total')::numeric), 0) AS total, "
        "coalesce(sum(paid_amount), 0) AS paid "
        "FROM invoices WHERE project_id = :p AND status <> 'void'"), {"p": pid}).mappings().one()
    return {
        "steps": steps,
        "current": current,
        "snapshot": {
            "documents": docs,
            "scope_versions": scopes,
            "estimate": est,
            "estimate_versions": versions,
            "contract": contract_value(db, pid, est),
            "invoices": {"count": int(invoiced["n"]), "total": round(float(invoiced["total"])),
                         "paid": round(float(invoiced["paid"]))},
        },
    }
