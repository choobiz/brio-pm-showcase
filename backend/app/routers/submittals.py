"""Submittal review (wireframes approved by Amir 2026-10-17).

  GET    /projects/{pid}/submittals                       the list (screen A)
  GET    /projects/{pid}/submittals/checklists            the five trade checklists
  POST   /projects/{pid}/submittals/suggest               governing files for a trade + the cost
  POST   /projects/{pid}/submittals                       start a review (screen B; queues the job)
  GET    /projects/{pid}/submittals/{sid}                 findings, quote, questions, gate (C + D)
  POST   /projects/{pid}/submittals/{sid}/run             run it again (after a failed reply)
  PATCH  /projects/{pid}/submittals/{sid}/findings/{fid}  the PM's call: ask | fine | answered
  PATCH  /projects/{pid}/submittals/{sid}                 outcome, note, signed copy, milestone
  GET    /projects/{pid}/submittals/{sid}/emails/{party}  the question email (made from the asks)
  PUT    /projects/{pid}/submittals/{sid}/emails/{party}  save edits
  POST   /projects/{pid}/submittals/{sid}/emails/{party}/copied   Copy to Gmail was pressed
  POST   /projects/{pid}/submittals/{sid}/revision        REV n+1, checked against this one

Nothing here sends email or places an order: emails leave by Copy to Gmail, and
"cleared to order" is a marker only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.submittal import OUTCOMES, Submittal
from app.services import submittal_checklists as checklists
from app.services import submittal_review as review

router = APIRouter(prefix="/projects/{project_id}", tags=["submittals"], dependencies=[Depends(require_office)])

OUTCOME_LABEL = {"no_exceptions": "Reviewed, no exceptions", "as_noted": "Reviewed as noted",
                 "revise_resubmit": "Revise and resubmit", "rejected": "Rejected"}
#: Measured on Opus 5.5 rates ($4 in / $20 out per million): a PDF page is about
#: 3k tokens in; a ten-check reply runs about 8k out. Replaced by the median of
#: real reviews once there are three. The first live run (1234 tank, 2026-10-17):
#: 45k in, 12k out and still cut off, so the reply allowance is 16k.
TOKENS_PER_PAGE, REPLY_TOKENS, HEADROOM = 3000, 16000, Decimal("1.5")
PAGES_IF_UNKNOWN = 10
#: Same ceiling as the other approved AI jobs (routers/ai_jobs.MAX_APPROVE_USD).
MAX_APPROVE_USD = 50.0


def _check_approval(db: Session, project_id: UUID, doc_ids: list, approve_usd: float) -> None:
    """The amount approved must cover what the page quoted for these files: a
    stale or hand-made request for less is refused, not run (audit 2026-10-17)."""
    quote = _cost(db, _docs(db, project_id, doc_ids))["usd"]
    if approve_usd + 1e-9 < quote:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"approve at least ${quote:.2f} for these files (up to ${approve_usd:.2f} was approved)")


def _same_file_both_sides(sub: list, gov: list) -> None:
    both = {str(i) for i in sub} & {str(i) for i in gov}
    if both:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "the same file is picked as the submittal and as a governing document")


_REV = re.compile(r"\(?\b(?:R|REV\.?\s*)(\d{1,2})\b\)?", re.I)


def latest_revisions(rows: list) -> list:
    """Of files that differ only by their revision mark ("SMP (R2)" and "SMP (R3)"),
    keep the highest: on 1234 the suggestion offered both, and a superseded plan
    read as governing would pass a submittal against the wrong drawing."""
    best: dict[str, tuple[int, Any]] = {}
    order: list[str] = []
    for r in rows:
        name = r.filename or ""
        revs = [int(m.group(1)) for m in _REV.finditer(name)]
        stem = re.sub(r"\s+", " ", _REV.sub("", name)).strip().lower()
        if stem not in best:
            order.append(stem)
        if stem not in best or (max(revs) if revs else -1) > best[stem][0]:
            best[stem] = (max(revs) if revs else -1, r)
    return [best[k][1] for k in order]


def _get(db: Session, project_id: UUID, sid: UUID) -> Submittal:
    s = db.get(Submittal, sid)
    if s is None or s.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")
    return s


def _docs(db: Session, project_id: UUID, ids: list) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = db.execute(_T("""
        SELECT d.id, d.filename, d.mime,
               (SELECT count(*) FROM document_text_pages t WHERE t.document_id = d.id) AS pages
          FROM documents d WHERE d.project_id = :p AND d.id = ANY(:ids) AND NOT d.archived"""),
                      {"p": str(project_id), "ids": [str(i) for i in ids]}).all()
    found = {str(r.id): {"id": str(r.id), "filename": r.filename, "mime": r.mime, "pages": int(r.pages or 0)}
             for r in rows}
    return [found[str(i)] for i in ids if str(i) in found]


def _cost(db: Session, docs: list[dict]) -> dict[str, Any]:
    from app.services import cost_estimate

    hist = cost_estimate.ai_job(db, "submittal_review")
    pages = sum(d["pages"] or PAGES_IF_UNKNOWN for d in docs)
    if "median" in hist["how"]:
        return {"usd": hist["usd"], "how": hist["how"], "pages": pages}
    usd = (Decimal(pages * TOKENS_PER_PAGE) * 4 + Decimal(REPLY_TOKENS) * 20) / Decimal(1_000_000) * HEADROOM
    usd = (max(usd, Decimal("0.25")) / Decimal("0.05")).quantize(Decimal(1), rounding=ROUND_UP) * Decimal("0.05")
    return {"usd": float(usd), "how": f"{pages} pages at Opus rates x {HEADROOM} (no history yet)", "pages": pages}


def _team(db: Session, project_id: UUID, role: str) -> dict[str, Any]:
    from app.models.milestone import ProjectTeamMember

    t = db.query(ProjectTeamMember).filter(ProjectTeamMember.project_id == project_id,
                                           ProjectTeamMember.role == role).first()
    return {"role": role, "name": t.name, "company": t.company, "email": t.email} if t else {"role": role}


def _checklist(db: Session, s: Submittal) -> checklists.Checklist:
    """The checklist this review RAN with (a copy on its job), so editing the
    checklist later never changes what an old review means. Falls back to the
    current one, then to an empty list named after the trade."""
    from app.models.ai_job import AIJob

    job = db.get(AIJob, s.ai_job_id) if s.ai_job_id else None
    snap = (job.input or {}).get("checklist") if job else None
    if snap:
        try:
            return checklists.from_dict(snap)
        except (KeyError, TypeError):
            pass
    try:
        return checklists.get(s.trade, db)
    except KeyError:
        return checklists.Checklist(s.trade, s.trade, "other", ())


def _row(db: Session, s: Submittal) -> dict[str, Any]:
    gov = _docs(db, s.project_id, s.governing_doc_ids)
    ms = None
    if s.milestone_id:
        ms = db.execute(_T("SELECT ref, title FROM project_milestones WHERE id = :i"), {"i": str(s.milestone_id)}).first()
    label = _checklist(db, s).label
    return {"id": str(s.id), "ref": s.ref, "name": s.name, "trade": s.trade, "trade_label": label,
            "supplier": s.supplier, "revision": s.revision, "status": review.derive_status(s),
            "counts": review.counts(s.findings or []), "checked_against": [d["filename"] for d in gov],
            "outcome": s.outcome, "outcome_label": OUTCOME_LABEL.get(s.outcome or ""),
            "milestone": {"ref": ms.ref, "title": ms.title} if ms else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
            "cleared": review.gate(s)["cleared"], "error": s.error}


@router.get("/submittals/checklists")
def list_checklists(project_id: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    return checklists.listing(db)


@router.get("/submittals")
def list_submittals(project_id: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    rows = db.query(Submittal).filter(Submittal.project_id == project_id).order_by(Submittal.created_at.desc()).all()
    return [_row(db, s) for s in rows]


class SuggestBody(BaseModel):
    trade: str
    submittal_doc_ids: list[UUID] = []
    governing_doc_ids: list[UUID] | None = None


@router.post("/submittals/suggest")
def suggest(project_id: UUID, body: SuggestBody, db: Session = Depends(get_db),
            user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Governing files the trade usually needs (a suggestion: the PM's ticks
    decide what is read) and the up-to cost of reading the ticked files."""
    assert_project_access(db, user, project_id)
    try:
        cl = checklists.get(body.trade, db)
    except KeyError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown trade checklist")
    rows = db.execute(_T("SELECT id, filename FROM documents WHERE project_id = :p AND NOT archived"),
                      {"p": str(project_id)}).all()
    skip = {str(i) for i in body.submittal_doc_ids}
    hits = [r for r in rows if str(r.id) not in skip
            and any(re.search(p, r.filename or "", re.I) for p in cl.governing)]
    suggested = [str(r.id) for r in latest_revisions(hits)]
    gov = [str(i) for i in body.governing_doc_ids] if body.governing_doc_ids is not None else suggested
    docs = _docs(db, project_id, [*body.submittal_doc_ids, *gov])
    return {"suggested": suggested, "cost": _cost(db, docs)}


class StartBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trade: str
    supplier: str | None = Field(default=None, max_length=200)
    revision: str = Field(default="REV 0", max_length=20)
    submittal_doc_ids: list[UUID] = Field(min_length=1)
    governing_doc_ids: list[UUID] = Field(min_length=1)
    instructions: str | None = Field(default=None, max_length=2000)
    approve_usd: float = Field(gt=0, le=MAX_APPROVE_USD)
    milestone_id: UUID | None = None


def _queue(db: Session, s: Submittal, approve_usd: float, user: CurrentUser) -> None:
    from app.models.ai_job import AIJob

    try:
        cl = checklists.get(s.trade, db)
    except KeyError:  # the checklist was removed since: run with the copy it last ran with
        cl = _checklist(db, s)
    previous = None
    if s.parent_id:
        p = db.get(Submittal, s.parent_id)
        previous = [{k: f.get(k) for k in ("check_key", "check", "shop", "plan", "result", "question", "call",
                                           "call_note")} for f in (p.findings if p else [])]
    job = AIJob(project_id=s.project_id, kind="submittal_review", status="queued",
                input={"submittal_id": str(s.id), "name": s.name, "supplier": s.supplier, "revision": s.revision,
                       "checklist": checklists.to_dict(cl),
                       "submittal_doc_ids": list(s.submittal_doc_ids), "governing_doc_ids": list(s.governing_doc_ids),
                       "instructions": s.instructions, "previous_findings": previous, "approve_usd": approve_usd})
    db.add(job)
    db.flush()
    s.ai_job_id, s.status, s.error, s.approved_usd = job.id, "reviewing", None, approve_usd


@router.post("/submittals", status_code=status.HTTP_201_CREATED)
def start(project_id: UUID, body: StartBody, db: Session = Depends(get_db),
          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        checklists.get(body.trade, db)
    except KeyError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown trade checklist")
    _same_file_both_sides(body.submittal_doc_ids, body.governing_doc_ids)
    ids = list(dict.fromkeys([*body.submittal_doc_ids, *body.governing_doc_ids]))
    if len(_docs(db, project_id, ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a picked file is not on this project")
    _check_approval(db, project_id, ids, body.approve_usd)
    # One S-number per submittal, even when two are started at once.
    db.execute(_T("SELECT pg_advisory_xact_lock(hashtext('submittal-ref:' || :p))"), {"p": str(project_id)})
    n = db.execute(_T("SELECT coalesce(max(substring(ref from 2)::int), 0) FROM submittals WHERE project_id = :p"),
                   {"p": str(project_id)}).scalar()
    s = Submittal(project_id=project_id, ref=f"S{n + 1}", name=body.name.strip(), trade=body.trade,
                  supplier=(body.supplier or "").strip() or None, revision=body.revision.strip() or "REV 0",
                  submittal_doc_ids=[str(i) for i in body.submittal_doc_ids],
                  governing_doc_ids=[str(i) for i in body.governing_doc_ids],
                  instructions=(body.instructions or "").strip() or None,
                  milestone_id=body.milestone_id, created_by=user.email)
    db.add(s)
    db.flush()
    review.note(s, user.email, f"{s.revision} received{' from ' + s.supplier if s.supplier else ''}")
    _queue(db, s, body.approve_usd, user)
    db.commit()
    return _row(db, s)


@router.get("/submittals/{sid}")
def detail(project_id: UUID, sid: UUID, db: Session = Depends(get_db),
           user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.services.milestones import project_address

    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    cl = _checklist(db, s)
    groups = review.question_groups(s.findings or [])
    engineer = _team(db, project_id, cl.engineer_role)
    revisions = db.query(Submittal).filter(Submittal.project_id == project_id, Submittal.ref == s.ref).all()
    by_id = {r.id: r for r in revisions}

    def depth(r: Submittal) -> int:  # by the parent chain: timestamps can tie
        n, seen = 0, set()
        while r.parent_id in by_id and r.id not in seen:
            seen.add(r.id)
            r, n = by_id[r.parent_id], n + 1
        return n

    revisions.sort(key=lambda r: (depth(r), r.created_at))
    out = {**_row(db, s),
           "summary": s.summary, "findings": s.findings or [], "quote": s.quote or {},
           "submittal_docs": _docs(db, project_id, s.submittal_doc_ids),
           "governing_docs": _docs(db, project_id, s.governing_doc_ids),
           "instructions": s.instructions, "cost_usd": float(s.cost_usd) if s.cost_usd is not None else None,
           "approved_usd": float(s.approved_usd) if s.approved_usd is not None else None,
           "outcome_note": s.outcome_note, "outcome_by": s.outcome_by,
           "outcome_at": s.outcome_at.isoformat() if s.outcome_at else None,
           "signed_received": s.signed_received, "gate": review.gate(s),
           "questions": {k: [f["id"] for f in v] for k, v in groups.items()},
           "parties": {"engineer": engineer, "supplier": {"role": "supplier", "company": s.supplier}},
           "emails": s.emails or [], "thread": s.thread or [], "history": s.history or [],
           "revisions": [{"id": str(r.id), "revision": r.revision, "status": review.derive_status(r)}
                         for r in revisions],
           "milestone_id": str(s.milestone_id) if s.milestone_id else None,
           "project_address": project_address(db, project_id)}
    db.commit()  # project_address may have minted the token
    return out


class RunBody(BaseModel):
    approve_usd: float = Field(gt=0, le=MAX_APPROVE_USD)


@router.post("/submittals/{sid}/run", status_code=status.HTTP_202_ACCEPTED)
def rerun(project_id: UUID, sid: UUID, body: RunBody, db: Session = Depends(get_db),
          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    if s.status != "review_failed":
        # A finished review is not run again over the PM's calls; a new file is a
        # new revision (audit 2026-10-17: a rerun replaced every Ask and Fine).
        raise HTTPException(status.HTTP_409_CONFLICT, "only a review that didn't finish can be run again")
    _check_approval(db, project_id, [*s.submittal_doc_ids, *s.governing_doc_ids], body.approve_usd)
    review.note(s, user.email, "Review run again")
    _queue(db, s, body.approve_usd, user)
    db.commit()
    return _row(db, s)


class CallBody(BaseModel):
    call: str | None
    call_note: str | None = Field(default=None, max_length=1000)
    question: str | None = Field(default=None, max_length=2000)
    ask_who: str | None = None


@router.patch("/submittals/{sid}/findings/{fid}")
def set_call(project_id: UUID, sid: UUID, fid: str, body: CallBody, db: Session = Depends(get_db),
             user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    if s.status in ("reviewing", "review_failed", "superseded", "draft"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"calls can't be made while the submittal is {s.status}")
    if s.outcome:
        raise HTTPException(status.HTTP_409_CONFLICT, "the outcome is recorded; clear it to change a call")
    if body.call is not None and body.call not in review.CALLS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"call must be one of {', '.join(review.CALLS)}")
    if body.ask_who is not None and body.ask_who not in ("engineer", "supplier"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ask_who must be engineer or supplier")
    findings = [dict(f) for f in s.findings or []]
    f = next((x for x in findings if x["id"] == fid), None)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("call") == "ask" and not (data.get("question") or f.get("question")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "write the question to ask")
    f.update({k: v for k, v in data.items()})
    if f.get("call") == "ask" and f.get("ask_who") not in ("engineer", "supplier"):
        f["ask_who"] = "supplier"
    s.findings = findings
    word = {"ask": "ask", "fine": "fine as is", "answered": "answered", None: "undecided"}[f.get("call")]
    review.note(s, user.email, f"{f['check']}: {word}{' · ' + f['call_note'] if f.get('call_note') else ''}")
    db.commit()
    return {"finding": f, "status": review.derive_status(s), "gate": review.gate(s)}


class SubmittalPatch(BaseModel):
    outcome: str | None = None
    outcome_note: str | None = Field(default=None, max_length=2000)
    signed_received: bool | None = None
    milestone_id: UUID | None = None


@router.patch("/submittals/{sid}")
def patch(project_id: UUID, sid: UUID, body: SubmittalPatch, db: Session = Depends(get_db),
          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    data = body.model_dump(exclude_unset=True)
    if s.status == "superseded" and ({"outcome", "signed_received"} & set(data)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a newer revision replaced this one; decide on that one")
    if "outcome" in data:
        if data["outcome"] is not None and data["outcome"] not in OUTCOMES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"outcome must be one of {', '.join(OUTCOMES)}")
        if data["outcome"] and s.status in ("draft", "reviewing", "review_failed"):
            raise HTTPException(status.HTTP_409_CONFLICT, "there are no findings to decide on yet")
        s.outcome = data["outcome"]
        s.outcome_by = user.email if s.outcome else None
        s.outcome_at = datetime.now(timezone.utc) if s.outcome else None
        if s.outcome and s.status not in ("superseded",):
            s.status = "closed"
        elif not s.outcome and s.status == "closed":
            s.status = "needs_call"
        review.note(s, user.email, f"Outcome: {OUTCOME_LABEL[s.outcome]}" if s.outcome else "Outcome cleared")
    if "outcome_note" in data:
        s.outcome_note = (data["outcome_note"] or "").strip() or None
    if "signed_received" in data and data["signed_received"] is not None:
        s.signed_received = data["signed_received"]
        review.note(s, user.email, "Engineer's signed review received" if s.signed_received
                    else "Signed review un-ticked")
    if "milestone_id" in data:
        if data["milestone_id"] is not None:
            ok = db.execute(_T("SELECT 1 FROM project_milestones WHERE id = :i AND project_id = :p"),
                            {"i": str(data["milestone_id"]), "p": str(project_id)}).first()
            if not ok:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "that milestone is not on this project")
        s.milestone_id = data["milestone_id"]
    if review.gate(s)["cleared"] and not any(h["text"] == "Cleared to order" for h in s.history or []):
        review.note(s, user.email, "Cleared to order")
    db.commit()
    return detail(project_id, sid, db, user)


def _email(db: Session, s: Submittal, party: str, user: CurrentUser) -> dict[str, Any]:
    from app.routers.milestones import _sender

    if party not in ("engineer", "supplier"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such party")
    saved = next((e for e in s.emails or [] if e["party"] == party), None)
    questions = review.question_groups(s.findings or []).get(party, [])
    if saved is not None and saved.get("finding_ids") == [q["id"] for q in questions]:
        return saved
    if not questions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"nothing is marked Ask for the {party}")
    p = db.execute(_T("SELECT name, address FROM projects WHERE id = :i"), {"i": str(s.project_id)}).first()
    to = _team(db, s.project_id, _checklist(db, s).engineer_role) if party == "engineer" \
        else {"name": "", "email": ""}
    gov = [d["filename"] for d in _docs(db, s.project_id, s.governing_doc_ids)]
    draft = review.draft_email(s, party, questions, project={"name": p.name, "address": p.address}, to=to,
                               sender=_sender(db, user), governing=gov)
    if saved is not None:  # the asks changed since it was saved: rebuilt, recipient kept
        draft.update({k: saved[k] for k in ("to_name", "to_email") if saved.get(k)})
    return draft


@router.get("/submittals/{sid}/emails/{party}")
def get_email(project_id: UUID, sid: UUID, party: str, db: Session = Depends(get_db),
              user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.services.milestones import project_address, tag_subject

    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    e = _email(db, s, party, user)
    out = {**e, "subject_as_sent": tag_subject(e["subject"], s.ref), "project_address": project_address(db, project_id)}
    db.commit()
    return out


class EmailPut(BaseModel):
    to_name: str | None = None
    to_email: str | None = Field(default=None, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.put("/submittals/{sid}/emails/{party}")
def save_email(project_id: UUID, sid: UUID, party: str, body: EmailPut, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    e = {**_email(db, s, party, user), **body.model_dump()}
    s.emails = [*[x for x in s.emails or [] if x["party"] != party], e]
    db.commit()
    return get_email(project_id, sid, party, db, user)


@router.post("/submittals/{sid}/emails/{party}/copied")
def copied(project_id: UUID, sid: UUID, party: str, db: Session = Depends(get_db),
           user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Copy to Gmail was pressed: the questions count as asked (the PM sends it)."""
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    e = {**_email(db, s, party, user), "copied_at": datetime.now(timezone.utc).isoformat()}
    s.emails = [*[x for x in s.emails or [] if x["party"] != party], e]
    review.note(s, user.email, f"Questions copied to Gmail for the {party}")
    db.commit()
    return {"status": review.derive_status(s)}


class RevisionBody(BaseModel):
    revision: str = Field(min_length=1, max_length=20)
    submittal_doc_ids: list[UUID] = Field(min_length=1)
    governing_doc_ids: list[UUID] | None = None
    instructions: str | None = Field(default=None, max_length=2000)
    approve_usd: float = Field(gt=0, le=MAX_APPROVE_USD)


@router.post("/submittals/{sid}/revision", status_code=status.HTTP_201_CREATED)
def new_revision(project_id: UUID, sid: UUID, body: RevisionBody, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """REV n+1 of the same submittal, checked against this one's findings
    (Amir, 2026-10-17: "keep the trail")."""
    assert_project_access(db, user, project_id)
    parent = _get(db, project_id, sid)
    if parent.status in ("reviewing", "draft", "review_failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, "this revision has no findings to compare against yet")
    if parent.status == "superseded":
        raise HTTPException(status.HTTP_409_CONFLICT, "a newer revision already replaced this one; start from it")
    gov = [str(i) for i in body.governing_doc_ids] if body.governing_doc_ids is not None else parent.governing_doc_ids
    _same_file_both_sides(body.submittal_doc_ids, gov)
    ids = list(dict.fromkeys(str(i) for i in [*body.submittal_doc_ids, *gov]))
    if len(_docs(db, project_id, ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a picked file is not on this project")
    _check_approval(db, project_id, ids, body.approve_usd)
    s = Submittal(project_id=project_id, ref=parent.ref, name=parent.name, trade=parent.trade,
                  supplier=parent.supplier, revision=body.revision.strip(), parent_id=parent.id,
                  submittal_doc_ids=[str(i) for i in body.submittal_doc_ids], governing_doc_ids=list(gov),
                  instructions=(body.instructions or "").strip() or None, milestone_id=parent.milestone_id,
                  history=list(parent.history or []), created_by=user.email)
    db.add(s)
    db.flush()
    parent.status = "superseded"
    review.note(parent, user.email, f"Superseded by {s.revision}")
    review.note(s, user.email, f"{s.revision} received; checked against {parent.revision}")
    _queue(db, s, body.approve_usd, user)
    db.commit()
    return _row(db, s)


class ToLibraryBody(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    unit: str
    unit_cost: float = Field(gt=0)
    trade: str | None = None
    sku: str | None = None


@router.post("/submittals/{sid}/quote-lines/{line}/to-library", status_code=status.HTTP_201_CREATED)
def quote_line_to_library(project_id: UUID, sid: UUID, line: int, body: ToLibraryBody, db: Session = Depends(get_db),
                          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """One priced quote line into the rate library as a supplier material, by the
    PM pressing Add with the values in front of them (pricing never changes on its
    own). Marked on the line so it isn't added twice."""
    from app.routers.rates import MaterialCreate, create_material

    assert_project_access(db, user, project_id)
    _get(db, project_id, sid)
    # Row lock: two quick presses must not add the material twice.
    s = db.query(Submittal).filter(Submittal.id == sid).with_for_update().one()
    quote = dict(s.quote or {})
    lines = [dict(x) for x in quote.get("lines") or []]
    ln = next((x for x in lines if x.get("i") == line), None)
    if ln is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such quote line")
    if ln.get("material_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "this line is already in the rate library")
    date_ = quote.get("date")
    try:
        from datetime import date as _d

        as_of = _d.fromisoformat(str(date_)) if date_ else None
    except ValueError:
        as_of = None
    created = create_material(MaterialCreate(
        name=body.name.strip(), unit=body.unit, unit_cost=body.unit_cost, source="supplier", sku=body.sku or ln.get("sku"),
        trade=body.trade, supplier=quote.get("supplier") or s.supplier, as_of_date=as_of,
        source_ref=f"Quote {quote.get('number') or ''} · submittal {s.ref} {s.revision}".strip()), db, user)
    s = _get(db, project_id, sid)  # create_material committed
    quote = dict(s.quote or {})
    quote["lines"] = [{**x, "material_id": created["id"]} if x.get("i") == line else x for x in quote.get("lines") or []]
    s.quote = quote
    review.note(s, user.email, f"Added to the rate library: {created['name']} at ${body.unit_cost:,.2f}/{body.unit}")
    db.commit()
    return {"material_id": created["id"], "line": line}


@router.get("/submittals/{sid}/findings/{fid}/cite")
def cite(project_id: UUID, sid: UUID, fid: str, side: str, db: Session = Depends(get_db),
         user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The cited page with what the finding quotes boxed: the label words and the
    values (e.g. 211.50). A JPEG cropped around the matches, so a 24 x 36 sheet
    stays small. A scan has no words to find: the page comes back unmarked."""
    from app.services import submittal_cite

    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    f = next((x for x in s.findings or [] if x["id"] == fid), None)
    if f is None or side not in ("shop", "plan"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    pool = _docs(db, project_id, s.submittal_doc_ids if side == "shop" else s.governing_doc_ids)
    if not pool:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no file on that side")
    ref = f.get(f"{side}_ref") or ""
    doc = submittal_cite.pick_doc(pool, ref)
    from app.models.document import Document
    from app.services.r2 import get_bytes

    d = db.get(Document, UUID(doc["id"]))
    return {"document": doc, "ref": ref, **submittal_cite.render(get_bytes(d.r2_key), d.mime or "", ref=ref,
                                                                    value=f.get(side) or "")}


checklist_router = APIRouter(prefix="/submittal-checklists", tags=["submittals"],
                             dependencies=[Depends(require_office)])


class ChecklistBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    engineer_role: str = "other"
    checks: list[dict[str, str]] = Field(min_length=1)
    governing: list[str] = []
    governing_words: list[str] = []


@checklist_router.get("")
def all_checklists(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return checklists.listing(db)


@checklist_router.put("/{key}")
def save_checklist(key: str, body: ChecklistBody, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Edit a checklist, or add one, for every project. A review that already ran
    keeps the copy it ran with."""
    from app.models.milestone import TEAM_ROLES

    if not re.fullmatch(r"[a-z0-9_]{2,40}", key):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "key: 2-40 lowercase letters, digits or _")
    if body.engineer_role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown role")
    try:
        out = checklists.save(db, {"key": key, **body.model_dump()}, user_id=user.id)
    except (ValueError, re.error) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.commit()
    return out


@checklist_router.delete("/{key}")
def reset_checklist(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    checklists.reset(db, key)
    db.commit()
    return {"reset": key, "builtin": key in checklists.CHECKLISTS}
