"""Selections planned against the schedule (round 2 board R2Selections).

  * each decision is tied to the schedule activity that needs it; it must be
    made by that activity's start (latest schedule, a recorded start wins)
    minus the supplier's lead time, in calendar days;
  * a selection is a draft until the office sends it; the client sees only
    sent ones;
  * confirming a client's choice that costs more (or less) than the allowance
    makes a DRAFT change order for the difference. Nothing goes to the client
    and nothing is ordered until the office acts on that change order.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.change_order import ChangeOrder
from app.models.client_selection import ClientSelection
from app.services.actuals import project_actuals, site_today


class SelectionError(ValueError):
    pass


# Typical residential decisions, the schedule work that needs each (word
# patterns over the activity's name and phase), and a default supplier lead
# time in calendar days (editable per selection). "Drain tile" is site work,
# not a finish (found by test on 1234's A127).
SUGGESTIONS: list[tuple[str, str, int]] = [
    ("Windows & exterior doors", r"\bwindows?\b", 42),
    ("Garage door", r"\bgarage door", 28),
    ("Exterior cladding colour", r"\b(cladding|siding)\b", 21),
    ("Roofing colour", r"\b(roof membrane|roofing|shingles?)\b", 21),
    ("Interior doors & hardware", r"\binterior doors?\b", 21),
    ("Millwork & cabinets", r"\b(millwork|casework|cabinets?)\b", 35),
    ("Tile", r"(?<!drain )\btil(e|es|ing)\b", 21),
    ("Flooring", r"\bflooring\b", 21),
    ("Interior paint colours", r"\b(interior )?paint", 7),
    ("Plumbing fixtures", r"\bplumbing fixtures?\b", 21),
    ("Light fixtures", r"\b(electrical devices|light fixtures?)\b", 14),
    ("Appliances", r"\bappliances?\b", 28),
]


def _activities(db: Session, pid: str) -> list[dict]:
    sid = db.execute(text("SELECT id FROM schedules WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"),
                     {"p": pid}).scalar()
    if not sid:
        return []
    rows = db.execute(text("""SELECT activity_code, name, phase, start_date, finish_date, is_milestone
                               FROM activities WHERE schedule_id = :s ORDER BY start_date, activity_code"""),
                      {"s": sid}).mappings().all()
    return [dict(r) for r in rows]


def _start(code: str | None, acts: dict[str, dict], actual: dict[str, dict]) -> date | None:
    if not code or code not in acts:
        return None
    a = actual.get(code) or {}
    return a.get("actual_start") or acts[code]["start_date"]


def _state(s: ClientSelection) -> str:
    if s.status == "confirmed":
        return "confirmed"
    if s.status == "submitted":
        return "chose"
    return "waiting" if s.sent_at else "not_sent"


def plan(db: Session, project_id: UUID | str) -> dict[str, Any]:
    from app.routers.selections import sel_read

    pid = str(project_id)
    today = site_today()
    acts_list = _activities(db, pid)
    acts = {a["activity_code"]: a for a in acts_list}
    actual = project_actuals(db, pid)
    rows = db.execute(select(ClientSelection).where(ClientSelection.project_id == UUID(pid))).scalars().all()
    out = []
    for s in rows:
        start = _start(s.activity_code, acts, actual)
        decide_by = (start - timedelta(days=s.lead_time_days or 0)) if start else s.due_date
        chosen = next((o for o in (s.options or []) if o.get("id") == s.selected_option_id), None)
        r = sel_read(s)
        r.update({
            "activity": ({"code": s.activity_code, "name": acts[s.activity_code]["name"], "start": str(start)}
                         if s.activity_code in acts else None),
            "decide_by": str(decide_by) if decide_by else None,
            "late": bool(decide_by and decide_by < today and s.status == "pending"),
            "state": _state(s),
            "days_waiting": (today - s.sent_at.astimezone(timezone.utc).date()).days
            if s.sent_at and s.status == "pending" else None,
            "choice_delta": (chosen or {}).get("price_delta"),
        })
        out.append(r)
    out.sort(key=lambda r: (r["decide_by"] or "9999", r["display_order"], r["title"]))
    return {
        "selections": out,
        "waiting": sum(1 for r in out if r["state"] in ("waiting", "chose")),
        "activities": [{"code": a["activity_code"], "name": a["name"], "start": str(a["start_date"])}
                       for a in acts_list if not a["is_milestone"] and a["start_date"]],
    }


def suggestions(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    """Typical decisions this project's schedule has work for, not yet listed."""
    pid = str(project_id)
    have = {(t or "").strip().lower() for t in db.execute(
        text("SELECT title FROM client_selections WHERE project_id = :p"), {"p": pid}).scalars()}
    acts = [a for a in _activities(db, pid) if not a["is_milestone"]]
    out = []
    for title, pattern, lead in SUGGESTIONS:
        if title.lower() in have:
            continue
        hit = next((a for a in acts if re.search(pattern, f"{a['name']} {a['phase'] or ''}".lower())), None)
        if hit:
            out.append({"title": title, "activity_code": hit["activity_code"], "activity_name": hit["name"],
                        "start": str(hit["start_date"]), "lead_time_days": lead})
    return out


def send(db: Session, project_id: UUID | str, s: ClientSelection) -> None:
    if s.status != "pending":
        raise SelectionError("The client has already answered this one.")
    if s.decision_type == "select" and len(s.options or []) < 1:
        raise SelectionError("Add at least one option before sending.")
    s.sent_at = datetime.now(timezone.utc)


def confirm(db: Session, project_id: UUID | str, s: ClientSelection, uid: UUID | None) -> ChangeOrder | None:
    """Lock in the client's choice; a price difference becomes a draft CO."""
    if s.status != "submitted":
        raise SelectionError("Only a choice the client has made can be confirmed.")
    co = None
    chosen = next((o for o in (s.options or []) if o.get("id") == s.selected_option_id), None)
    delta = Decimal(str((chosen or {}).get("price_delta") or 0)).quantize(Decimal("0.01"))
    if s.decision_type == "select" and delta != 0 and s.change_order_id is None:
        nxt = (db.execute(select(ChangeOrder.number).where(ChangeOrder.project_id == s.project_id)
                          .order_by(ChangeOrder.number.desc()).limit(1)).scalar() or 0) + 1
        label = chosen.get("label") if chosen else ""
        co = ChangeOrder(
            project_id=s.project_id, number=nxt,
            title=f"Selection: {s.title} — {label}"[:200],
            description=(f"The client chose \"{label}\" for {s.title}"
                         + (f" ({'over' if delta > 0 else 'under'} the allowance by ${abs(delta):,.2f})")
                         + (f". Their note: {s.client_comment}" if s.client_comment else ".")),
            cost_impact=delta, schedule_impact_days=0, status="draft", created_by_id=uid,
            detail={"source": "selection", "selection_id": str(s.id)},
        )
        db.add(co)
        db.flush()
        s.change_order_id = co.id
    s.status = "confirmed"
    return co
