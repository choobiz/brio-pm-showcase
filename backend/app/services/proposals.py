"""A change somebody suggested: what it would do, what else it touches, and
what was decided about it.

⚠️ WHY A REJECTION IS AS IMPORTANT AS AN ACCEPTANCE. §11's acceptance criterion
is "reject a proposal; who proposed, what it would have changed, who rejected
and why are all retrievable." Before this, declining an AI edit left no trace at
all — the plan simply was not applied and the fact that anyone had considered it
disappeared. "We looked at raising that and chose not to" is the answer a
dispute turns on more often than "we did it", so a rejected proposal is kept
permanently and a reason is REQUIRED.

⚠️ IMPACT IS READ FROM THE FOREIGN-KEY GRAPH, NOT WRITTEN DOWN. A hand-kept map
of "what depends on an estimate" is a second source of truth that goes stale the
first time someone adds a table and does not think to update it — and it goes
stale silently, reporting "nothing else is affected" about a record with six
dependents. The graph is queried from information_schema instead, so a new table
appears in the impact the day it is created.

Measured 2026-10-02: estimate_versions is referenced by nine tables — schedules,
contracts, cash_flows, schedules_of_values, presentations, scopes,
drawing_line_item_links, regeneration_approvals and itself. scopes,
estimates and change_orders are referenced by none. That asymmetry is the
feature: editing an estimate version is a far bigger act than editing a scope,
and nothing in the product said so.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.models.proposal import Proposal, TERMINAL_STATUSES
from app.services.edit_plan import apply as apply_changes
from app.services.edit_plan import verify
from app.services.editable_records import TARGETS, _current_document, _write
from app.services.revisions import snapshot


class ProposalError(ValueError):
    """Something about this proposal cannot be honoured. Message is for a person."""


#: Tables a proposal may target, keyed by the `kind` the caller names. Reused
#: from the ai_edit path rather than re-listed, so "what is editable" has one
#: definition.
ALLOWED_KINDS = tuple(TARGETS.keys())

#: Dependents worth naming to a reviewer. A reference that is bookkeeping rather
#: than consequence — a self-referencing parent_version_id, an approval row —
#: adds noise to a decision without informing it.
_IMPACT_WORDS: dict[str, str] = {
    "schedules": "the schedule built from it",
    "schedules_of_values": "the schedule of values",
    "cash_flows": "the cash-flow forecast",
    "contracts": "the contract based on it",
    "presentations": "a client presentation built from it",
    "scopes": "a scope derived from it",
    "invoices": "an invoice billing from it",
    "payments": "payments recorded against it",
    "drawing_line_item_links": "lines linked back to drawings",
}

#: Self-references and internal approval bookkeeping. Excluded deliberately and
#: NAMED here rather than filtered silently, so the exclusion is reviewable.
_NOT_WORTH_REPORTING = {"regeneration_approvals"}


def _dependents(db: Session, table: str) -> list[tuple[str, str]]:
    """(child_table, child_column) pairs that point at `table`, from the schema."""
    rows = db.execute(_T("""
        SELECT tc.table_name AS child, kcu.column_name AS col
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
         WHERE tc.constraint_type = 'FOREIGN KEY'
           AND ccu.table_name = :t
    """), {"t": table}).fetchall()
    out = []
    for r in rows:
        if r.child == table:          # self-reference: a version's parent
            continue
        if r.child in _NOT_WORTH_REPORTING:
            continue
        out.append((r.child, r.col))
    return out


def impact_of(db: Session, table: str, row_id: UUID | str) -> list[dict[str, Any]]:
    """What else would be affected, counted from rows that actually exist.

    Returns only dependents with at least one real row. "The schedule built from
    it" is worth a reviewer's attention; "0 presentations" is noise, and listing
    every possible consequence trains people to skip the list.
    """
    out: list[dict[str, Any]] = []
    for child, col in _dependents(db, table):
        try:
            n = db.execute(
                _T(f"SELECT count(*) FROM {child} WHERE {col} = :i"), {"i": str(row_id)},
            ).scalar() or 0
        except Exception:  # noqa: BLE001 — a table we cannot read is not a blocker
            continue
        if not n:
            continue
        out.append({
            "table": child,
            "count": int(n),
            "what": _IMPACT_WORDS.get(child, f"{n} row(s) in {child}"),
        })
    return sorted(out, key=lambda d: -d["count"])


def create(
    db: Session,
    *,
    project_id: UUID,
    kind: str,
    target_id: UUID | str,
    summary: str,
    change_set: list[dict[str, Any]],
    origin: str,
    ai_job_id: UUID | None = None,
    proposed_by_id: UUID | None = None,
) -> Proposal:
    """Record a proposal. Writes nothing to the target."""
    if kind not in ALLOWED_KINDS:
        raise ProposalError(f"{kind!r} is not something a proposal can change.")
    if not (summary or "").strip():
        raise ProposalError("A proposal needs a one-line summary of what it would do.")

    spec = TARGETS[kind]
    p = Proposal(
        project_id=project_id,
        target_table=spec["table"],
        target_id=target_id if isinstance(target_id, UUID) else UUID(str(target_id)),
        origin=origin,
        ai_job_id=ai_job_id,
        proposed_by_id=proposed_by_id,
        summary=summary.strip(),
        change_set=change_set or [],
        impact=impact_of(db, spec["table"], target_id),
        status="pending",
    )
    db.add(p)
    db.flush()
    return p


def _kind_for_table(table: str) -> str:
    for k, spec in TARGETS.items():
        if spec["table"] == table:
            return k
    raise ProposalError(f"No editable record is mapped to {table!r}.")


def recheck(db: Session, p: Proposal) -> list[dict[str, Any]]:
    """Re-verify the change set against the record as it stands NOW.

    The plan may be minutes or days old. A change whose `before` no longer
    matches must not land — either the path was invented or the record moved
    underneath the proposal, and both are cases where guessing is worse than
    stopping.
    """
    spec = TARGETS[_kind_for_table(p.target_table)]
    doc = _current_document(db, spec, str(p.target_id))
    return verify(doc, list(p.change_set or []))


def is_stale(verified: list[dict[str, Any]]) -> bool:
    """True when nothing in the set can still be applied.

    ⚠️ The flag is `applicable`, not `stale`. An earlier version of this read
    `c.get("stale")`, which `edit_plan.verify` never sets — so every change
    looked fine, staleness detection did nothing at all, and `apply` silently
    dropped the unapplicable ones leaving "applied 0" with no explanation."""
    return bool(verified) and not any(c.get("applicable") for c in verified)


def reject(db: Session, p: Proposal, *, user_id: UUID | None, reason: str) -> Proposal:
    """Decline it, permanently, with the reason attached.

    ⚠️ THE REASON IS NOT OPTIONAL. A rejection with no reason answers three of
    the four audit questions and leaves blank the one anybody would ask later.
    """
    if p.status in TERMINAL_STATUSES:
        raise ProposalError(f"This proposal was already {p.status}.")
    text_reason = (reason or "").strip()
    if not text_reason:
        raise ProposalError("Say why this is being rejected — the reason is the record.")
    p.status = "rejected"
    p.decision_reason = text_reason
    p.decided_by_id = user_id
    p.decided_at = datetime.now(timezone.utc)
    db.flush()
    return p


def accept(
    db: Session,
    p: Proposal,
    *,
    user_id: UUID | None,
    accept_paths: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Apply it, after re-checking, with the prior state snapshotted first.

    `accept_paths` lets a reviewer take some changes and not others — unticking
    one in the review screen has to mean something, otherwise the checkboxes are
    decoration.
    """
    if p.status in TERMINAL_STATUSES:
        raise ProposalError(f"This proposal was already {p.status}.")

    kind = _kind_for_table(p.target_table)
    spec = TARGETS[kind]
    doc = _current_document(db, spec, str(p.target_id))
    verified = verify(doc, list(p.change_set or []))

    wanted = verified
    if accept_paths is not None:
        chosen = set(accept_paths)
        wanted = [c for c in verified if c.get("path") in chosen]

    applicable = [c for c in wanted if c.get("applicable")]
    if not applicable:
        # Distinguish "you picked nothing" from "the record moved" — they call
        # for different actions and look identical from a blank result.
        if is_stale(verified):
            p.status = "stale"
            db.flush()
            raise ProposalError(
                "The record changed since this was proposed, so none of it can be "
                "applied. Nothing was written — look at it again."
            )
        raise ProposalError("Nothing was selected to apply.")

    new_doc, applied = apply_changes(doc, applicable)

    # History BEFORE the write, inside this transaction. A rolled-back apply
    # must leave no revision claiming it happened.
    rev = snapshot(
        db, table=spec["table"], row_id=str(p.target_id), project_id=p.project_id,
        action="proposal", actor_id=user_id,
        reason=(reason or p.summary)[:500] or None,
    )

    recomputed = _write(db, spec, str(p.target_id), new_doc)

    p.status = "accepted"
    p.decided_by_id = user_id
    p.decided_at = datetime.now(timezone.utc)
    p.decision_reason = (reason or "").strip() or None
    p.revision_number = rev
    db.flush()

    return {
        "applied": len(applied),
        "skipped": len(verified) - len(applied),
        "recomputed": recomputed,
        "revision_number": p.revision_number,
    }


def supersede_pending(db: Session, project_id: UUID, table: str, row_id: UUID | str) -> int:
    """Mark earlier pending proposals on the same record as superseded.

    Two pending proposals for one record is a trap: accepting both applies the
    second to a document the first already changed, and the second's `before`
    values were read from the original. Marking rather than deleting keeps the
    earlier suggestion in the history.
    """
    rows = db.execute(_T("""
        UPDATE proposals SET status = 'superseded', updated_at = now()
         WHERE project_id = :p AND target_table = :t AND target_id = :r
           AND status = 'pending'
        RETURNING id"""), {"p": str(project_id), "t": table, "r": str(row_id)}).fetchall()
    return len(rows)
