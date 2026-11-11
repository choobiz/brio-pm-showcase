"""Client › Proposal: a presentation sent to the client portal, and the
client's acceptance (round 2 sketch R2Proposal, approved 2026-10-27).

Rules:
  * the office sends one version at a time; sending another replaces it in the
    portal; a sent version can be withdrawn until it is accepted;
  * the client accepts by typing their name; we record who, when, the IP and
    browser, and the price they saw (the presentation's own budget figures);
  * a proposal past its "valid for N days" (from when it was sent) can't be
    accepted: the client is told to ask for an updated one;
  * accepting does NOT create or change a contract. The office is told and
    prepares the contract as today.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.actuals import site_today


class ProposalError(ValueError):
    pass


def _payload(v: Any) -> dict:
    return v if isinstance(v, dict) else json.loads(v or "{}")


def _f(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _valid_until(sent_at: datetime | None, payload: dict) -> date | None:
    days = (payload.get("budget") or {}).get("validDays")
    if not sent_at or not days:
        return None
    return sent_at.astimezone(timezone.utc).date() + timedelta(days=int(days))


def _acceptance(db: Session, prid) -> dict | None:
    a = db.execute(text("""SELECT typed_name, accepted_at, total, ip, options FROM proposal_acceptances
                            WHERE presentation_id = :i"""), {"i": prid}).mappings().first()
    return {"typed_name": a["typed_name"], "accepted_at": a["accepted_at"].isoformat(),
            "total": _f(a["total"]), "ip": a["ip"], "options": a["options"] or []} if a else None


def _prices(payload: dict) -> dict[str, Any]:
    """Base (everything not optional, plus allowances) and the optional items
    the client may keep or drop. The budget's subtotal includes the options."""
    b = payload.get("budget") or {}
    cats = b.get("categories") or []
    opts = [{"name": c.get("name"), "amount": round(_f(c.get("displayTotal")) or 0, 2)}
            for c in cats if c.get("optional")]
    sub = _f(b.get("subtotal")) or 0.0
    allow = _f(b.get("allowances")) or 0.0
    return {"base": round(sub - sum(o["amount"] for o in opts) + allow, 2), "options": opts}


def _total(base: float, kept: list[dict]) -> tuple[float, float, float]:
    sub = round(base + sum(o["amount"] for o in kept), 2)
    gst = round(sub * 0.05, 2)
    return sub, gst, round(sub + gst, 2)


def _active_estimate_total(db: Session, pid: str) -> tuple[int | None, float | None, str | None]:
    """The active estimate's version, price incl. GST (as a proposal states it) and id."""
    r = db.execute(text("""SELECT id, version_number, payload FROM estimate_versions
                            WHERE project_id = :p AND is_active = true LIMIT 1"""), {"p": pid}).mappings().first()
    if not r:
        return None, None, None
    p = _payload(r["payload"])
    mk = (1 + (_f(p.get("overhead")) or 0) / 100) * (1 + (_f(p.get("profit")) or 0) / 100)
    sub = sum((_f(c.get("total")) or 0) for c in (p.get("categories") or [])) * mk + (_f(p.get("allowances")) or 0)
    return r["version_number"], round(sub * 1.05, 2), str(r["id"])


def office_status(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT id, version_number, label, status, payload, portal_sent_at, portal_withdrawn_at, created_at,
               based_on_estimate_id, portal_opened_at, portal_open_count
          FROM presentations WHERE project_id = :p ORDER BY version_number"""),
        {"p": str(project_id)}).mappings().all()
    est_v, est_total, est_id = _active_estimate_total(db, str(project_id))
    out = []
    prev_total = None
    for r in rows:
        p = _payload(r["payload"])
        vu = _valid_until(r["portal_sent_at"], p)
        live = r["portal_sent_at"] is not None and r["portal_withdrawn_at"] is None
        out.append({
            "id": str(r["id"]), "version_number": r["version_number"], "label": r["label"],
            "total": _f((p.get("budget") or {}).get("total")),
            "sent_at": r["portal_sent_at"].isoformat() if r["portal_sent_at"] else None,
            "withdrawn_at": r["portal_withdrawn_at"].isoformat() if r["portal_withdrawn_at"] else None,
            "in_portal": live, "valid_until": str(vu) if vu else None,
            "expired": bool(vu and vu < site_today()),
            "accepted": _acceptance(db, r["id"]),
            "opened_at": r["portal_opened_at"].isoformat() if r["portal_opened_at"] else None,
            "open_count": r["portal_open_count"] or 0,
            "change_from_previous": (round(_f((p.get("budget") or {}).get("total")) - prev_total, 2)
                                     if prev_total is not None and _f((p.get("budget") or {}).get("total")) is not None
                                     else None),
            "headline": p.get("headline"), "overview": p.get("overview"), "closingNote": p.get("closingNote"),
            # Stale: an estimate other than this proposal's is now active.
            "stale": ({"estimate_version": est_v, "estimate_total": est_total}
                      if est_id and r["based_on_estimate_id"] and str(r["based_on_estimate_id"]) != est_id else None),
        })
        prev_total = _f((p.get("budget") or {}).get("total"))
    return out


TEXT_FIELDS = ("headline", "overview", "closingNote")


def edit_text(db: Session, project_id: UUID | str, presentation_id: UUID | str, fields: dict[str, str],
              uid: UUID | None) -> None:
    """Edit the proposal's own words (R2Proposal "click a page to edit its
    words"). Not while it's in the client's portal or accepted: withdraw it
    first, so the client never sees text change under them."""
    pid = str(project_id)
    r = db.execute(text("""SELECT payload, portal_sent_at, portal_withdrawn_at FROM presentations
                            WHERE id = :i AND project_id = :p"""), {"i": str(presentation_id), "p": pid}).mappings().first()
    if not r:
        raise ProposalError("Proposal not found.")
    if _acceptance(db, str(presentation_id)):
        raise ProposalError("The client accepted this proposal; its words can't change.")
    if r["portal_sent_at"] and not r["portal_withdrawn_at"]:
        raise ProposalError("It's in the client's portal. Withdraw it first, edit, then send it again.")
    p = _payload(r["payload"])
    before = {k: p.get(k) for k in TEXT_FIELDS}
    for k, v in fields.items():
        if k in TEXT_FIELDS and v is not None:
            p[k] = v.strip()
    db.execute(text("UPDATE presentations SET payload = CAST(:j AS jsonb), updated_at = now() WHERE id = :i"),
               {"j": json.dumps(p), "i": str(presentation_id)})
    _audit(db, pid, presentation_id, "proposal-text-edited", uid,
           {"before": before, "after": {k: p.get(k) for k in TEXT_FIELDS}})


def mark_opened(db: Session, project_id: UUID | str, presentation_id: UUID | str) -> None:
    db.execute(text("""UPDATE presentations SET portal_open_count = portal_open_count + 1,
                              portal_opened_at = COALESCE(portal_opened_at, now())
                        WHERE id = :i AND project_id = :p"""), {"i": str(presentation_id), "p": str(project_id)})


def _audit(db: Session, pid, prid, action: str, uid, diff: dict) -> None:
    db.execute(text("""INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
                       VALUES (:p, 'presentations', :r, :a, :u, CAST(:d AS jsonb))"""),
               {"p": str(pid), "r": str(prid), "a": action, "u": uid, "d": json.dumps(diff, default=str)})


def send(db: Session, project_id: UUID | str, presentation_id: UUID | str, uid: UUID | None) -> None:
    pid = str(project_id)
    row = db.execute(text("SELECT version_number FROM presentations WHERE id = :i AND project_id = :p"),
                     {"i": str(presentation_id), "p": pid}).first()
    if not row:
        raise ProposalError("Proposal not found.")
    if db.execute(text("SELECT 1 FROM proposal_acceptances WHERE project_id = :p"), {"p": pid}).first():
        raise ProposalError("The client has already accepted a proposal on this project.")
    now = datetime.now(timezone.utc)
    # One version in the portal at a time.
    db.execute(text("""UPDATE presentations SET portal_withdrawn_at = :n
                        WHERE project_id = :p AND id <> :i AND portal_sent_at IS NOT NULL
                          AND portal_withdrawn_at IS NULL"""), {"n": now, "p": pid, "i": str(presentation_id)})
    db.execute(text("""UPDATE presentations SET portal_sent_at = :n, portal_sent_by_id = :u,
                              portal_withdrawn_at = NULL WHERE id = :i"""),
               {"n": now, "u": uid, "i": str(presentation_id)})
    db.execute(text("""INSERT INTO project_messages (id, project_id, sender_id, sender_role, body, created_at)
                       VALUES (gen_random_uuid(), :p, :u, 'office', :b, now())"""),
               {"p": pid, "u": uid,
                "b": f"Your proposal (version {row[0]}) is ready to review under Proposal in your portal."})
    _audit(db, pid, presentation_id, "proposal-sent-to-portal", uid, {"version": row[0]})


def withdraw(db: Session, project_id: UUID | str, presentation_id: UUID | str, uid: UUID | None) -> None:
    if _acceptance(db, str(presentation_id)):
        raise ProposalError("The client has accepted this proposal; it can't be withdrawn.")
    n = db.execute(text("""UPDATE presentations SET portal_withdrawn_at = now()
                            WHERE id = :i AND project_id = :p AND portal_sent_at IS NOT NULL
                              AND portal_withdrawn_at IS NULL"""),
                   {"i": str(presentation_id), "p": str(project_id)}).rowcount
    if not n:
        raise ProposalError("That proposal isn't in the client's portal.")
    _audit(db, project_id, presentation_id, "proposal-withdrawn", uid, {})


def _live(db: Session, pid: str):
    return db.execute(text("""
        SELECT id, version_number, label, payload, portal_sent_at FROM presentations
         WHERE project_id = :p AND portal_sent_at IS NOT NULL AND portal_withdrawn_at IS NULL
         ORDER BY portal_sent_at DESC LIMIT 1"""), {"p": pid}).mappings().first()


def portal_view(db: Session, project_id: UUID | str) -> dict[str, Any] | None:
    """What the client sees: the proposal in their portal, or an accepted one."""
    pid = str(project_id)
    r = _live(db, pid)
    if not r:
        acc = db.execute(text("""SELECT presentation_id FROM proposal_acceptances WHERE project_id = :p"""),
                         {"p": pid}).scalar()
        if not acc:
            return None
        r = db.execute(text("""SELECT id, version_number, label, payload, portal_sent_at FROM presentations
                                WHERE id = :i"""), {"i": acc}).mappings().first()
    from app.services.share_token import sign_presentation_token
    p = _payload(r["payload"])
    b = p.get("budget") or {}
    vu = _valid_until(r["portal_sent_at"], p)
    pr = _prices(p)
    return {
        "id": str(r["id"]), "version_number": r["version_number"],
        "headline": p.get("headline"), "subtotal": _f(b.get("subtotal")), "gst": _f(b.get("gst")),
        "total": _f(b.get("total")), "valid_until": str(vu) if vu else None,
        "base": pr["base"], "options": pr["options"],
        "expired": bool(vu and vu < site_today()),
        "pdf_path": f"/public/presentation/{sign_presentation_token(str(r['id']))}",
        "accepted": _acceptance(db, r["id"]),
    }


def accept(db: Session, project_id: UUID | str, presentation_id: UUID | str, uid: UUID | None,
           typed_name: str, ip: str | None, user_agent: str | None,
           kept_options: list[str] | None = None) -> dict[str, Any]:
    pid = str(project_id)
    name = " ".join((typed_name or "").split())
    if len(name) < 2:
        raise ProposalError("Type your full name to accept.")
    r = _live(db, pid)
    if not r or str(r["id"]) != str(presentation_id):
        raise ProposalError("This proposal is no longer open for acceptance. Ask BRIO for the current one.")
    if _acceptance(db, r["id"]):
        raise ProposalError("This proposal has already been accepted.")
    p = _payload(r["payload"])
    b = p.get("budget") or {}
    vu = _valid_until(r["portal_sent_at"], p)
    if vu and vu < site_today():
        raise ProposalError(f"This proposal's price was valid until {vu:%b %d, %Y}. "
                            "Ask BRIO for an updated one.")
    # Options the client kept (all, unless they unticked some); the price is
    # worked out here from the proposal's own figures, never taken from the page.
    pr = _prices(p)
    names = {o["name"] for o in pr["options"]}
    if kept_options is None:
        kept = pr["options"]
    else:
        unknown = [k for k in kept_options if k not in names]
        if unknown:
            raise ProposalError("That option isn't in this proposal.")
        kept = [o for o in pr["options"] if o["name"] in kept_options]
    sub, gst, total = _total(pr["base"], kept) if pr["options"] else (
        _f(b.get("subtotal")), _f(b.get("gst")), _f(b.get("total")))
    db.execute(text("""
        INSERT INTO proposal_acceptances (presentation_id, project_id, user_id, typed_name, version_number,
               subtotal, gst, total, valid_until, ip, user_agent, options)
        VALUES (:i, :p, :u, :n, :v, :s, :g, :t, :vu, :ip, :ua, CAST(:o AS jsonb))"""),
        {"i": str(r["id"]), "p": pid, "u": uid, "n": name, "v": r["version_number"],
         "s": sub, "g": gst, "t": total, "vu": vu,
         "ip": (ip or "")[:100] or None, "ua": (user_agent or "")[:400] or None, "o": json.dumps(kept)})
    db.execute(text("""INSERT INTO project_messages (id, project_id, sender_id, sender_role, body, created_at)
                       VALUES (gen_random_uuid(), :p, :u, 'owner', :b, now())"""),
               {"p": pid, "u": uid,
                "b": f"I accept proposal version {r['version_number']}"
                     + (f" (${total:,.2f} including GST)" if total else "") + f". — {name}"})
    _audit(db, pid, r["id"], "proposal-accepted", uid, {"typed_name": name, "total": total, "ip": ip})
    return portal_view(db, pid)
