"""Snapshot a record before it is overwritten, and put it back when asked.

Scopes, estimates and change orders are all edited by wholesale replacement of
a JSONB payload. That was survivable while a person was typing into a form —
they could retype. It stops being survivable once an AI can rewrite the whole
document from one sentence of instruction, so the history lands BEFORE that
does, not alongside it.

A full prior payload, not a diff. A diff says what changed; only the whole
prior state lets you put it back.

`snapshot()` is called INSIDE the caller's transaction and does not commit. If
the edit it precedes is rolled back, so is the snapshot, and the history never
claims an edit that did not happen.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Tables this service knows how to snapshot, mapped to the column holding the
#: state worth restoring. `None` means the whole row is captured field by field.
SNAPSHOTTABLE: dict[str, str | None] = {
    "scopes": "payload",
    "estimates": "payload",
    "estimate_versions": "payload",
    "change_orders": None,
    "invoices": "payload",
}

#: Columns captured for a whole-row snapshot. Deliberately explicit: an
#: unbounded `SELECT *` would sweep in created_at/updated_at and make every
#: restore look like a change.
_ROW_COLUMNS: dict[str, list[str]] = {
    "change_orders": [
        "title", "description", "cost_impact", "schedule_impact_days",
        "status", "detail", "owner_note",
    ],
}


def actor_uuid(user: Any) -> UUID | None:
    """The acting user's id, or None.

    Tolerant on purpose. Attribution is a nice-to-have on a history row; a
    malformed or sentinel id must never be the reason an edit fails to save.
    """
    raw = getattr(user, "id", None)
    if not raw:
        return None
    try:
        parsed = UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None
    return None if parsed.int == 0 else parsed


def _current_state(db: Session, table: str, row_id: UUID | str) -> dict[str, Any] | None:
    column = SNAPSHOTTABLE.get(table)
    if column is not None:
        row = db.execute(
            _T(f"SELECT {column} AS state FROM {table} WHERE id = :i"), {"i": str(row_id)}
        ).mappings().first()
        return dict(row["state"] or {}) if row else None

    cols = _ROW_COLUMNS.get(table)
    if not cols:
        return None
    row = db.execute(
        _T(f"SELECT {', '.join(cols)} FROM {table} WHERE id = :i"), {"i": str(row_id)}
    ).mappings().first()
    if row is None:
        return None
    # Decimal and datetime are not JSON — stringify anything the encoder would
    # choke on rather than losing the field.
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        out[k] = v if isinstance(v, (str, int, float, bool, dict, list, type(None))) else str(v)
    return out


def snapshot(
    db: Session,
    *,
    table: str,
    row_id: UUID | str,
    project_id: UUID | str | None,
    action: str,
    actor_id: UUID | str | None = None,
    reason: str | None = None,
) -> int | None:
    """Record the CURRENT state of a row, before the caller overwrites it.

    Returns the revision number written, or None when there was nothing to
    capture (unknown table, or the row does not exist yet). Never raises into
    the caller: failing to write history must not block the edit itself, and a
    silently missing revision is a smaller problem than a refused save.
    """
    if table not in SNAPSHOTTABLE:
        return None
    # A savepoint, so a failure here really does not reach the caller. Without
    # one, the swallowed error still left the transaction aborted and the edit
    # it was protecting failed on its next statement — the opposite of the
    # promise above. Found 2026-10-12 against a database with no
    # record_revisions table.
    sp = db.begin_nested()
    try:
        state = _current_state(db, table, row_id)
        if state is None:
            sp.commit()
            return None
        nxt = (
            db.execute(
                _T("SELECT COALESCE(MAX(revision), 0) + 1 FROM record_revisions "
                   "WHERE table_name = :t AND row_id = :r"),
                {"t": table, "r": str(row_id)},
            ).scalar()
            or 1
        )
        db.execute(
            _T(
                "INSERT INTO record_revisions "
                "(project_id, table_name, row_id, revision, payload, action, reason, actor_id) "
                "VALUES (:p, :t, :r, :n, CAST(:payload AS jsonb), :a, :why, :u)"
            ),
            {
                "p": str(project_id) if project_id else None,
                "t": table,
                "r": str(row_id),
                "n": nxt,
                "payload": _dumps(state),
                "a": action,
                "why": reason,
                "u": str(actor_id) if actor_id else None,
            },
        )
        sp.commit()
        return int(nxt)
    except Exception:  # noqa: BLE001 — history is best effort, the edit is not
        sp.rollback()
        import logging

        logging.getLogger(__name__).exception(
            "revisions: failed to snapshot %s %s", table, row_id
        )
        return None


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def list_revisions(
    db: Session, *, table: str, row_id: UUID | str, limit: int = 50
) -> list[dict[str, Any]]:
    """History for one row, newest first. Payloads are included — they are the
    thing a reviewer compares against, and these documents are small."""
    rows = db.execute(
        _T(
            "SELECT r.id, r.revision, r.action, r.reason, r.created_at, r.payload, "
            "       r.actor_id, u.name AS actor_name "
            "FROM record_revisions r LEFT JOIN users u ON u.id = r.actor_id "
            "WHERE r.table_name = :t AND r.row_id = :r "
            "ORDER BY r.revision DESC LIMIT :l"
        ),
        {"t": table, "r": str(row_id), "l": limit},
    ).mappings().all()
    return [
        {
            "id": str(x["id"]),
            "revision": x["revision"],
            "action": x["action"],
            "reason": x["reason"],
            "created_at": x["created_at"].isoformat() if x["created_at"] else None,
            "actor_id": str(x["actor_id"]) if x["actor_id"] else None,
            "actor_name": x["actor_name"],
            "payload": x["payload"],
        }
        for x in rows
    ]


def restore(
    db: Session,
    *,
    revision_id: UUID | str,
    actor_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Put a row back to a recorded state.

    The CURRENT state is snapshotted first, so a restore is itself undoable.
    An undo you cannot undo is how a fat-fingered recovery loses more than the
    mistake did.
    """
    rev = db.execute(
        _T("SELECT table_name, row_id, revision, payload, project_id "
           "FROM record_revisions WHERE id = :i"),
        {"i": str(revision_id)},
    ).mappings().first()
    if rev is None:
        raise ValueError("revision not found")

    table, row_id = rev["table_name"], rev["row_id"]
    snapshot(
        db,
        table=table,
        row_id=row_id,
        project_id=rev["project_id"],
        action="restore",
        actor_id=actor_id,
        reason=f"state before restoring revision {rev['revision']}",
    )

    column = SNAPSHOTTABLE.get(table)
    payload = rev["payload"] or {}
    if column is not None:
        db.execute(
            _T(f"UPDATE {table} SET {column} = CAST(:v AS jsonb) WHERE id = :i"),
            {"v": _dumps(payload), "i": str(row_id)},
        )
    else:
        cols = _ROW_COLUMNS.get(table) or []
        present = [c for c in cols if c in payload]
        if not present:
            raise ValueError(f"revision holds no restorable columns for {table}")
        sets = ", ".join(f"{c} = :{c}" for c in present)
        params: dict[str, Any] = {"i": str(row_id)}
        for c in present:
            v = payload[c]
            params[c] = _dumps(v) if isinstance(v, (dict, list)) else v
        # JSONB columns need the cast; the rest are plain.
        sets = sets.replace("detail = :detail", "detail = CAST(:detail AS jsonb)")
        db.execute(_T(f"UPDATE {table} SET {sets} WHERE id = :i"), params)

    return {"table": table, "row_id": str(row_id), "restored_revision": rev["revision"]}
