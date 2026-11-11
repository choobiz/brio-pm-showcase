"""Load, preview and save a progress re-plan (Schedule › Update the plan).

The engine is services/replan.py (pure). This module reads the latest version
and the actuals, and writes the new version: immutable, `parent_version_id` =
the version it was built on, notes that say what was recorded, assumed and
kept, and `client_visible` only when the office ticked "Tell the client".
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import actuals as actuals_svc
from app.services.replan import replan

_SCHED_COLS = ("id, version_number, project_start, holidays, p50_finish_date, p80_finish_date, "
               "project_std_dev_wd, client_visible, working_hours")
_ACT_COLS = ("activity_code, name, phase, predecessor_codes, lag_wd, optimistic_wd, most_likely_wd, "
             "pessimistic_wd, expected_wd, variance, start_date, finish_date, is_critical, is_milestone, "
             "total_float_wd")


class StaleBase(Exception):
    """The version the preview was built on is no longer the latest."""


def latest_version(db: Session, project_id: UUID | str) -> dict | None:
    row = db.execute(
        text(f"SELECT {_SCHED_COLS} FROM schedules WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"),
        {"p": str(project_id)},
    ).mappings().first()
    if row is None:
        return None
    base = dict(row)
    base["activities"] = [dict(r) for r in db.execute(
        text(f"SELECT {_ACT_COLS} FROM activities WHERE schedule_id = :s ORDER BY early_start_wd, activity_code"),
        {"s": row["id"]},
    ).mappings().all()]
    return base


def _fmt(d: str | date | None) -> str:
    if not d:
        return "-"
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return d.strftime("%a %-d %b %Y")


def compute(db: Session, project_id: UUID | str, unanswered: str, data_date: date | None = None) -> dict:
    base = latest_version(db, project_id)
    if base is None:
        raise LookupError("no schedule for this project")
    acts = actuals_svc.project_actuals(db, project_id)
    dd = data_date or actuals_svc.site_today()
    out = replan(base, acts, dd, unanswered)
    out["base"] = {k: base[k] for k in ("id", "version_number", "p50_finish_date", "p80_finish_date", "client_visible")}
    out["actuals"] = acts
    return out


def preview(db: Session, project_id: UUID | str, unanswered: str = "keep") -> dict[str, Any]:
    """Both rules' finish dates (so the screen can show the range), and the
    chosen rule's changes. Nothing is written."""
    out = compute(db, project_id, unanswered)
    other = compute(db, project_id, "push" if unanswered == "keep" else "keep")
    names = {a["activity_code"]: a.get("name") for a in out["activities"]}
    client_finish = db.execute(
        text("SELECT p50_finish_date FROM schedules WHERE project_id = :p AND client_visible "
             "ORDER BY created_at DESC LIMIT 1"), {"p": str(project_id)},
    ).scalar()
    return {
        "base": out["base"],
        "client_p50_finish_date": client_finish,
        "data_date": out["data_date"],
        "unanswered_rule": unanswered,
        "p50_finish_date": out["p50_finish_date"],
        "p80_finish_date": out["p80_finish_date"],
        "other_rule": {"unanswered_rule": other["unanswered_rule"],
                       "p50_finish_date": other["p50_finish_date"], "p80_finish_date": other["p80_finish_date"]},
        "changes": [c for c in out["changes"] if c["moved"] or c["is_critical"] != c["was_critical"]],
        "unchanged": sum(1 for c in out["changes"] if not c["moved"]),
        "assumed": [{"activity_code": c, "name": names.get(c)} for c in out["assumed"]],
        "kept": [{"activity_code": c, "name": names.get(c)} for c in out["kept"]],
        "critical_path_activity_codes": out["critical_path_activity_codes"],
    }


def _notes(out: dict, user_note: str | None) -> str:
    b = out["base"]
    names = {a["activity_code"]: a.get("name") for a in out["activities"]}
    lines = [
        f"v{b['version_number'] + 1} - PROGRESS UPDATE from Record what happened (data date {_fmt(out['data_date'])}). "
        f"Built on v{b['version_number']}, which is left unchanged. "
        f"Finish {_fmt(b['p50_finish_date'])} -> {_fmt(out['p50_finish_date'])} "
        f"(P80 {_fmt(b['p80_finish_date'])} -> {_fmt(out['p80_finish_date'])}).",
    ]
    rec = []
    for code, r in sorted(out["actuals"].items()):
        if code not in names:
            continue
        src = {"typed": "entered", "both": "site report + entered"}.get(r.get("source"), "site report")
        draft = ", draft" if r.get("from_draft") else ""
        if r.get("actual_finish"):
            what = f"done {_fmt(r['actual_finish'])}"
        elif r.get("actual_start"):
            what = f"started {_fmt(r['actual_start'])}" + (f", {r['percent_complete']}%" if r.get("percent_complete") else "")
        else:
            what = "not started" + (f" ({r['reason']})" if r.get("reason") else "")
        rec.append(f"{code} {names[code]}: {what} [{src}{draft}]")
    lines.append("RECORDED: " + ("; ".join(rec) if rec else "nothing") + ".")
    if out["assumed"]:
        lines.append("ASSUMED DONE on planned dates, because later work has started: " + ", ".join(out["assumed"]) + ".")
    if out["unanswered_rule"] == "keep":
        lines.append("UNANSWERED, taken to have gone to plan (links kept, not before a site report said not started): "
                     + (", ".join(out["kept"]) or "none") + ".")
    else:
        lines.append("UNANSWERED work was taken NOT to have happened beyond the records; it runs from the data date.")
    lines.append(f"METHOD: services/replan.py - finish-to-start + lag, Mon-Fri, same holidays and shutdown; "
                 f"started work runs its remaining days from the data date; done work has no float. "
                 f"P80 keeps v{b['version_number']}'s {out['p80_buffer_wd']}-working-day buffer over P50. "
                 f"{len(out['activities'])} activities, {len(out['critical_path_activity_codes'])} critical.")
    lines.append("CLIENT: shown in the portal (Tell the client ticked)." if out.get("client_visible")
                 else f"CLIENT: NOT shown; the portal still shows the latest version shared with the client.")
    if user_note and user_note.strip():
        lines.append("NOTE: " + user_note.strip())
    return "\n".join(lines)


def save(db: Session, project_id: UUID | str, *, base_schedule_id: UUID | str, unanswered: str,
         client_visible: bool, note: str | None, user_id: UUID | None) -> dict[str, Any]:
    out = compute(db, project_id, unanswered)
    if str(out["base"]["id"]) != str(base_schedule_id):
        raise StaleBase(f"v{out['base']['version_number']} is now the latest version; review the update again")
    out["client_visible"] = client_visible
    sid = uuid4()
    ver = int(out["base"]["version_number"]) + 1
    db.execute(text("""
        INSERT INTO schedules (id, project_id, version_number, project_start, working_hours, holidays,
            deterministic_finish_date, p50_finish_date, p80_finish_date, project_expected_wd,
            project_std_dev_wd, critical_path_activity_codes, notes, parent_version_id, created_by_id,
            client_visible)
        SELECT :id, project_id, :ver, project_start, working_hours, holidays,
            :df, :p50, :p80, :ewd, project_std_dev_wd, CAST(:cpc AS jsonb), :notes, id, :uid, :cv
        FROM schedules WHERE id = :base
    """), {
        "id": sid, "ver": ver, "df": out["deterministic_finish_date"], "p50": out["p50_finish_date"],
        "p80": out["p80_finish_date"], "ewd": out["project_expected_wd"],
        "cpc": json.dumps(out["critical_path_activity_codes"]), "notes": _notes(out, note),
        "uid": user_id, "cv": client_visible, "base": out["base"]["id"],
    })
    new_ids: dict[str, UUID] = {}
    for a in out["activities"]:
        new_ids[a["activity_code"]] = aid = uuid4()
        db.execute(text("""
            INSERT INTO activities (id, schedule_id, activity_code, name, phase, predecessor_codes, lag_wd,
                optimistic_wd, most_likely_wd, pessimistic_wd, expected_wd, variance,
                early_start_wd, early_finish_wd, late_start_wd, late_finish_wd, total_float_wd, free_float_wd,
                start_date, finish_date, is_critical, is_milestone)
            VALUES (:id, :sid, :code, :name, :phase, CAST(:preds AS jsonb), :lag, :o, :m, :p, :e, :var,
                :es, :ef, :ls, :lf, :tf, :ff, :sd, :fd, :crit, :ms)
        """), {
            "id": aid, "sid": sid, "code": a["activity_code"], "name": a.get("name"), "phase": a.get("phase"),
            "preds": json.dumps(a.get("predecessor_codes") or []), "lag": int(a.get("lag_wd") or 0),
            "o": a.get("optimistic_wd"), "m": a.get("most_likely_wd"), "p": a.get("pessimistic_wd"),
            "e": a.get("expected_wd"), "var": a.get("variance"),
            "es": a["early_start_wd"], "ef": a["early_finish_wd"], "ls": a["late_start_wd"],
            "lf": a["late_finish_wd"], "tf": a["total_float_wd"], "ff": a["free_float_wd"],
            "sd": a["start_date"], "fd": a["finish_date"], "crit": a["is_critical"], "ms": bool(a.get("is_milestone")),
        })
    # Inspections ride along, re-linked to the new activity rows by code.
    for i in db.execute(text("""
        SELECT i.authority, i.inspection_type, i.lead_time_days, i.gates, i.notes, a.activity_code
        FROM inspections i LEFT JOIN activities a ON a.id = i.activity_id WHERE i.schedule_id = :base
    """), {"base": out["base"]["id"]}).mappings().all():
        db.execute(text("""
            INSERT INTO inspections (id, schedule_id, activity_id, authority, inspection_type, lead_time_days, gates, notes)
            VALUES (:id, :sid, :aid, :auth, :it, :lead, :gates, :n)
        """), {"id": uuid4(), "sid": sid, "aid": new_ids.get(i["activity_code"]), "auth": i["authority"],
               "it": i["inspection_type"], "lead": i["lead_time_days"], "gates": i["gates"], "n": i["notes"]})
    db.execute(text("""
        INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
        VALUES (:pid, 'schedules', :rid, 'progress-replan', :uid, CAST(:diff AS jsonb))
    """), {"pid": str(project_id), "rid": sid, "uid": user_id, "diff": json.dumps({
        "version_number": ver, "parent_schedule_id": str(out["base"]["id"]), "data_date": out["data_date"],
        "unanswered_rule": unanswered, "client_visible": client_visible,
        "p50": [str(out["base"]["p50_finish_date"]), out["p50_finish_date"]],
    })})
    return {"id": str(sid), "version_number": ver, "p50_finish_date": out["p50_finish_date"],
            "p80_finish_date": out["p80_finish_date"], "client_visible": client_visible,
            "previous_p50_finish_date": str(out["base"]["p50_finish_date"])}
