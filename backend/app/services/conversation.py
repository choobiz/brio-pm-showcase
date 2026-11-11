"""Client › Conversation: the client portal thread and the logged emails,
calls and notes, as one timeline (round 2, approved 2026-10-23).

Nothing is copied: portal messages stay in project_messages (the only part the
client sees) and the rest stays in project_communications. Each item is put in
a group by who it is with:
  * client: portal messages, and mail/calls with the project client's emails;
  * engineers & inspectors: project team members in a design/inspection role,
    and anything the classifier filed as permit/inspection;
  * suppliers & trades: suppliers, trade contacts, team members in a trade or
    supply role, and quotes;
  * other: everything else.
"Needs a reply": client portal messages after our last portal message, and an
inbound email/call with action items that has nothing sent back to that
address since.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

GROUPS = ("client", "engineers", "suppliers", "other")
TRADE_ROLES = {"electrician", "plumber", "concrete_supplier", "truss_supplier", "contractor"}


def _email(s: str | None) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", s or "")
    return m.group(0).lower() if m else None


def _name(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return str(v)


def _directory(db: Session, pid: str) -> tuple[dict[str, str], dict[str, str], dict]:
    """email → group, name → group, and the client's name/emails."""
    by_email: dict[str, str] = {}
    by_name: dict[str, str] = {}
    client = db.execute(text("""
        SELECT c.name, c.email, c.emails FROM projects p JOIN clients c ON c.id = p.client_id
         WHERE p.id = :p"""), {"p": pid}).mappings().first()
    client_emails: list[str] = []
    if client:
        for e in [client["email"], *(client["emails"] or [])]:
            if _email(e):
                client_emails.append(_email(e))
                by_email[_email(e)] = "client"
        if client["name"]:
            by_name[_name(client["name"])] = "client"
    for r in db.execute(text("SELECT name, contact_name, email FROM suppliers WHERE active IS NOT FALSE")).mappings():
        if _email(r["email"]):
            by_email.setdefault(_email(r["email"]), "suppliers")
        for n in (r["name"], r["contact_name"]):
            if n:
                by_name.setdefault(_name(n), "suppliers")
    for r in db.execute(text("SELECT company_name, contact_name, email, emails FROM trades")).mappings():
        for e in [r["email"], *(r["emails"] or [])]:
            if _email(e):
                by_email.setdefault(_email(e), "suppliers")
        for n in (r["company_name"], r["contact_name"]):
            if n:
                by_name.setdefault(_name(n), "suppliers")
    for r in db.execute(text("SELECT role, name, company, email FROM project_team WHERE project_id = :p"),
                        {"p": pid}).mappings():
        g = "suppliers" if r["role"] in TRADE_ROLES else "engineers"
        if _email(r["email"]):
            by_email[_email(r["email"])] = g
        for n in (r["name"], r["company"]):
            if n:
                by_name.setdefault(_name(n), g)
    info = {"name": client["name"] if client else None, "emails": client_emails}
    return by_email, by_name, info


def _group(email: str | None, name: str | None, category: str | None,
           by_email: dict[str, str], by_name: dict[str, str]) -> str:
    if email and email in by_email:
        return by_email[email]
    if name and _name(name) in by_name:
        return by_name[_name(name)]
    if category == "permit_inspection":
        return "engineers"
    if category == "quote":
        return "suppliers"
    return "other"


def timeline(db: Session, project_id: UUID | str) -> dict[str, Any]:
    pid = str(project_id)
    by_email, by_name, client = _directory(db, pid)
    items: list[dict[str, Any]] = []

    msgs = db.execute(text("""SELECT id, sender_role, body, read_at, created_at FROM project_messages
                               WHERE project_id = :p ORDER BY created_at"""), {"p": pid}).mappings().all()
    last_ours = max((m["created_at"] for m in msgs if m["sender_role"] == "office"), default=None)
    for m in msgs:
        theirs = m["sender_role"] == "owner"
        items.append({
            "kind": "portal", "id": str(m["id"]), "at": _iso(m["created_at"]), "group": "client",
            "ours": not theirs, "author": (client["name"] or "Client") if theirs else "BRIO",
            "channel": "portal", "subject": None, "body": m["body"], "summary": None, "category": None,
            "action_items": [], "read": m["read_at"] is not None,
            "needs_reply": theirs and (last_ours is None or m["created_at"] > last_ours),
        })

    comms = db.execute(text("""
        SELECT id, source, direction, channel, from_name, from_email, to_emails, subject, body,
               occurred_at, created_at, category, summary, action_items, urgency
          FROM project_communications WHERE project_id = :p"""), {"p": pid}).mappings().all()
    sent_to: dict[str, datetime] = {}
    for c in comms:
        if c["direction"] == "outbound":
            at = c["occurred_at"] or c["created_at"]
            for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", c["to_emails"] or ""):
                e = e.lower()
                sent_to[e] = max(sent_to.get(e, at), at)
    for c in comms:
        ours = c["direction"] in ("outbound", "internal")
        who_email = _email(c["to_emails"]) if ours else _email(c["from_email"])
        who_name = None if ours else c["from_name"]
        at = c["occurred_at"] or c["created_at"]
        actions = list(c["action_items"] or [])
        answered = who_email is not None and sent_to.get(who_email) is not None and sent_to[who_email] > at
        items.append({
            "kind": "comm", "id": str(c["id"]), "at": _iso(at),
            "group": _group(who_email, who_name, c["category"], by_email, by_name),
            "ours": ours, "author": "BRIO" if ours else (c["from_name"] or c["from_email"] or "Unknown"),
            "counterpart": c["to_emails"] if ours else None,
            "channel": c["channel"] or "note", "source": c["source"], "subject": c["subject"],
            "body": c["body"], "summary": c["summary"], "category": c["category"], "urgency": c["urgency"],
            "action_items": actions, "read": True,
            "needs_reply": (not ours) and bool(actions) and not answered,
        })

    # A reply filed against a booking that's still waiting on its party
    # (R2Client): offer to mark the booking booked from here.
    waiting = {}
    for m in db.execute(text("""SELECT id, ref, title, status, thread FROM project_milestones
                                 WHERE project_id = :p AND status = 'requested'"""), {"p": pid}).mappings():
        for t in m["thread"] or []:
            if t.get("direction") == "in" and t.get("communication_id"):
                waiting[str(t["communication_id"])] = {"id": str(m["id"]), "ref": m["ref"], "title": m["title"]}
    for i in items:
        if i["kind"] == "comm" and i["id"] in waiting:
            i["booking"] = waiting[i["id"]]

    items.sort(key=lambda i: i["at"] or "", reverse=True)
    counts = {g: sum(1 for i in items if i["group"] == g) for g in GROUPS}
    open_questions = [{"item": a, "id": i["id"], "kind": i["kind"], "at": i["at"], "author": i["author"]}
                      for i in items if i["needs_reply"] for a in (i["action_items"] or [i["body"][:160]])]
    return {
        "client": client,
        "items": items,
        "counts": counts,
        "needs_reply": sum(1 for i in items if i["needs_reply"]),
        "open_questions": open_questions,
        "decisions": decisions(db, pid),
    }


def decisions(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    """What the client has decided, from the records that hold it: change
    orders approved or declined, selections chosen, a proposal accepted. A
    decision said only on a call isn't here until it's recorded as one of
    these (R2Client "Decisions on record")."""
    pid = str(project_id)
    out: list[dict[str, Any]] = []
    for r in db.execute(text("""SELECT number, title, status, decided_at, cost_impact FROM change_orders
                                 WHERE project_id = :p AND status IN ('approved', 'rejected')"""),
                        {"p": pid}).mappings():
        out.append({"what": f"CO-{r['number']} {r['title']} {'approved' if r['status'] == 'approved' else 'declined'}",
                    "where": "change order", "at": _iso(r["decided_at"])})
    for r in db.execute(text("""SELECT title, decision_type, selected_option_id, options, status, decided_at
                                 FROM client_selections WHERE project_id = :p
                                 AND status IN ('submitted', 'confirmed')"""), {"p": pid}).mappings():
        label = next((o.get("label") for o in (r["options"] or []) if o.get("id") == r["selected_option_id"]),
                     {"approved": "approved", "changes_requested": "asked for changes"}.get(r["selected_option_id"] or "", ""))
        out.append({"what": f"{r['title']}: {label}", "where": "selection" + (" (confirmed)" if r["status"] == "confirmed" else ""),
                    "at": _iso(r["decided_at"])})
    if db.execute(text("SELECT to_regclass('proposal_acceptances')")).scalar():  # migration 0086
        for r in db.execute(text("""SELECT version_number, total, typed_name, accepted_at FROM proposal_acceptances
                                     WHERE project_id = :p"""), {"p": pid}).mappings():
            out.append({"what": f"Proposal v{r['version_number']} accepted by {r['typed_name']}",
                        "where": "portal", "at": _iso(r["accepted_at"])})
    out.sort(key=lambda d: d["at"] or "", reverse=True)
    return out


def schedule_reply(db: Session, project_id: UUID | str) -> str:
    """A reply draft from the schedule THE CLIENT SEES (R2Client "Suggest a
    reply from the schedule"): what's next and the finish date. Plain facts,
    no AI; the office edits it before sending."""
    from datetime import timedelta

    from app.services.actuals import site_today

    pid = str(project_id)
    s = db.execute(text("""SELECT id, p50_finish_date FROM schedules WHERE project_id = :p AND client_visible
                            ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).mappings().first()
    if not s:
        return ""
    today = site_today()
    acts = db.execute(text("""SELECT name, start_date, finish_date FROM activities WHERE schedule_id = :s
                               AND NOT COALESCE(is_milestone, false) AND finish_date >= :t AND start_date <= :e
                               ORDER BY start_date LIMIT 4"""),
                      {"s": s["id"], "t": today, "e": today + timedelta(days=14)}).mappings().all()
    fmt = lambda d: f"{d:%a %b} {d.day}"  # noqa: E731
    lines = ["Here's where things stand on the schedule:"]
    for a in acts:
        lines.append(f"- {a['name']}: {fmt(a['start_date'])} to {fmt(a['finish_date'])}")
    if s["p50_finish_date"]:
        lines.append(f"We're working to substantial completion around {fmt(s['p50_finish_date'])}.")
    return "\n".join(lines)
