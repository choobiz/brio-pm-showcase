"""Client presentation generator — data assembly + AI framing.

The presentation deck is a HYBRID artifact:
  • NUMBERS are pulled verbatim from the workbench (active estimate, schedule,
    cash flow). The AI never computes or invents a financial/date value.
  • The AI does judgment/prose only: project narrative, the technical/logistic
    challenges of major work areas, identification of early-approval /
    long-lead decisions, and schedule/cash-flow framing. It never frames the
    work as an "investment" or justifies the price.

Client-facing money uses the SAME formula as the client estimate PDF:
  margin = (1 + overhead/100) * (1 + profit/100); overhead+profit folded into
  category totals (never shown as line items); Subtotal → Allowances → GST(5%)
  → Total. (BRIO rule: hide margins from the client.)

gather_context() → dict of everything the deck needs (numbers + drawings +
staleness). build_presentation_messages() → the AI call (tool-use). Then
assemble_payload() merges the deterministic data with the AI prose.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.estimate_pdf import _num, _qty_is_zero  # shared number coercion / de-scoped test


# ── discipline heuristic (documents have no per-discipline type — filename only)
def discipline_of_filename(name: str | None) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("storm", "swm", "drainage", "civil", "grading", "servicing")):
        return "stormwater"
    if any(k in n for k in ("struct", "framing", "foundation", "/s-", " s-", "s1", "s2", "s3")):
        return "structural"
    if any(k in n for k in ("arch", "floor plan", "elevation", "section", "a-1", "a1", "a2", "a-2", "a10", "a20")):
        return "architectural"
    if any(k in n for k in ("mech", "hvac", "m-1", "m1")):
        return "mechanical"
    if any(k in n for k in ("elec", "e-1", "e1", "lighting", "power")):
        return "electrical"
    if any(k in n for k in ("plumb", "p-1", "p1", "sanitary")):
        return "plumbing"
    return "other"


import re as _re

_PRICE_TOKENS = _re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*/\s*\w+)?|@\s*")


def _scope_label(name) -> str:
    """Client-facing scope descriptor: drop the internal pricing/quantity tail.

    Estimate item names read 'Scope — qty/rate detail' (e.g. 'Slab concrete —
    4" × 750 SF (9 CY @ $310/CY) + finish'); keep the scope before the em/en-dash
    and scrub any leftover dollar figures so unit rates never reach the client."""
    s = str(name or "")
    for sep in ("—", "–"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    s = _PRICE_TOKENS.sub("", s)
    return " ".join(s.split()).strip(" ·-—–,")


def compute_client_totals(payload: dict) -> dict:
    """Fold overhead+profit into category totals exactly as the client estimate
    PDF does, then roll up to Subtotal → Allowances → GST → Total."""
    overhead = _num(payload.get("overhead"))
    profit = _num(payload.get("profit"))
    margin = (1 + overhead / 100) * (1 + profit / 100)
    cats: list[dict] = []
    for c in payload.get("categories") or []:
        if not isinstance(c, dict):
            continue
        base = _num(c.get("total"))
        # Top line items by cost — the client-facing "what's included" breakdown.
        # Exclude contingency lines: they're a cost buffer, not a scope item.
        # Exclude qty=0 lines: de-scoped work the client would read as included.
        items = [it for it in (c.get("items") or [])
                 if isinstance(it, dict) and it.get("name")
                 and "contingency" not in str(it.get("name")).lower()
                 and not _qty_is_zero(it)]
        items = sorted(items, key=lambda it: _num(it.get("total")) or _num(it.get("materials")) + _num(it.get("labor")), reverse=True)
        cats.append({
            "name": c.get("name") or "Untitled",
            "discipline": c.get("discipline"),
            "description": c.get("description") or "",
            "displayTotal": round(base * margin, 2),
            "optional": bool(c.get("optional")),
            "itemCount": len(items),
            "includes": [lbl for it in items[:8] if (lbl := _scope_label(it.get("name")))],
        })
    subtotal = sum(c["displayTotal"] for c in cats)
    allowances = _num(payload.get("allowances"))
    taxable = subtotal + allowances
    gst = taxable * 0.05
    total = taxable + gst
    return {
        "categories": cats,
        "subtotal": round(subtotal, 2),
        "allowances": round(allowances, 2),
        "gst": round(gst, 2),
        "total": round(total, 2),
    }


def high_cost_drivers(cats: list[dict], subtotal: float, frac: float = 0.65) -> list[dict]:
    """Top cost drivers that together make up ~`frac` of the subtotal."""
    ranked = sorted([c for c in cats if not c.get("optional")],
                    key=lambda c: c["displayTotal"], reverse=True)
    out: list[dict] = []
    acc = 0.0
    for c in ranked:
        out.append(c)
        acc += c["displayTotal"]
        if subtotal and acc / subtotal >= frac:
            break
    return out


def _row(db: Session, sql: str, **params) -> dict | None:
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def _rows(db: Session, sql: str, **params) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def gather_context(db: Session, project_id, estimate_version_id: str | None = None) -> dict:
    """Pull every input the deck needs + compute the deterministic pieces.
    Raises ValueError if the project has no estimate to present."""
    pid = str(project_id)
    project = _row(db, "SELECT * FROM projects WHERE id = :pid", pid=pid)
    if not project:
        raise ValueError("Project not found.")
    client = None
    if project.get("client_id"):
        client = _row(db, "SELECT * FROM clients WHERE id = :cid", cid=str(project["client_id"]))

    # Estimate: explicit version, else the active one.
    if estimate_version_id:
        est = _row(db, "SELECT * FROM estimate_versions WHERE id = :eid AND project_id = :pid",
                   eid=str(estimate_version_id), pid=pid)
    else:
        est = _row(db, "SELECT * FROM estimate_versions WHERE project_id = :pid AND is_active = true", pid=pid)
        if not est:
            est = _row(db, "SELECT * FROM estimate_versions WHERE project_id = :pid "
                           "ORDER BY version_number DESC LIMIT 1", pid=pid)
    if not est:
        raise ValueError("No estimate exists for this project — generate an estimate first.")
    est_payload = est.get("payload") or {}
    if isinstance(est_payload, str):
        est_payload = json.loads(est_payload)
    totals = compute_client_totals(est_payload)
    drivers = high_cost_drivers(totals["categories"], totals["subtotal"])

    # Scope — prefer the scope derived FROM the estimate we're presenting (so it
    # matches the estimate exactly); fall back to the latest scope if none yet.
    scope = _row(db, "SELECT * FROM scopes WHERE project_id = :pid AND based_on_estimate_id = :eid "
                     "ORDER BY version DESC LIMIT 1", pid=pid, eid=str(est["id"]))
    if not scope:
        scope = _row(db, "SELECT * FROM scopes WHERE project_id = :pid ORDER BY version DESC LIMIT 1", pid=pid)
    scope_payload = (scope or {}).get("payload") or {}
    if isinstance(scope_payload, str):
        scope_payload = json.loads(scope_payload)

    # Schedule (latest) + its activities.
    schedule = _row(db, "SELECT * FROM schedules WHERE project_id = :pid ORDER BY created_at DESC LIMIT 1", pid=pid)
    activities: list[dict] = []
    if schedule:
        activities = _rows(db, "SELECT * FROM activities WHERE schedule_id = :sid "
                               "ORDER BY early_start_wd NULLS LAST, activity_code",
                           sid=str(schedule["id"]))

    # Cash flow (latest).
    cash_flow = _row(db, "SELECT * FROM cash_flows WHERE project_id = :pid ORDER BY created_at DESC LIMIT 1", pid=pid)

    # Drawings — PDF documents in the 'drawing' category (exclude CAD source
    # files like .dwg/.dxf: browsers can't render them inline and would download
    # them), grouped by filename discipline.
    drawings = _rows(db, "SELECT id, filename, r2_key, category FROM documents "
                         "WHERE project_id = :pid AND category = 'drawing' AND archived = false "
                         "AND (mime = 'application/pdf' OR lower(filename) LIKE '%.pdf') "
                         "ORDER BY filename LIMIT 12", pid=pid)
    for d in drawings:
        d["discipline"] = discipline_of_filename(d.get("filename"))

    # Staleness: is a downstream artifact based on an estimate other than the one we're presenting?
    active_eid = str(est["id"])
    stale = []
    if cash_flow and cash_flow.get("based_on_estimate_id") and str(cash_flow["based_on_estimate_id"]) != active_eid:
        stale.append("cash_flow")
    if schedule and schedule.get("based_on_estimate_id") and str(schedule["based_on_estimate_id"]) != active_eid:
        stale.append("schedule")

    critical = []
    milestones = []
    for a in activities:
        if a.get("is_critical"):
            critical.append({"code": a.get("activity_code"), "name": a.get("name"),
                             "start": _iso(a.get("start_date")), "finish": _iso(a.get("finish_date"))})
        if a.get("is_milestone"):
            milestones.append({"code": a.get("activity_code"), "name": a.get("name"),
                               "date": _iso(a.get("start_date") or a.get("finish_date"))})

    return {
        "project": {"id": pid, "name": project.get("name"), "address": project.get("address"),
                    "type": project.get("type"), "sqft": project.get("sqft")},
        "client": {"name": (client or {}).get("name"), "company": (client or {}).get("company")} if client else None,
        "estimate": {"id": active_eid, "version": est.get("version_number"),
                     "reference": est.get("reference"), "label": est.get("label"),
                     "detailLevel": est_payload.get("detailLevel"),
                     "assumptions": est_payload.get("assumptions") or [],
                     "exclusions": est_payload.get("exclusions") or [],
                     "paymentSchedule": est_payload.get("paymentSchedule") or [],
                     "validDays": est_payload.get("validDays")},
        "totals": totals,
        "highCostDrivers": drivers,
        "scope": {"summary": scope_payload.get("summary"),
                  "categories": scope_payload.get("categories") or [],
                  "potentialIssues": scope_payload.get("potentialIssues") or [],
                  "permits": scope_payload.get("permits") or []},
        "schedule": {"present": bool(schedule),
                     "projectStart": _iso((schedule or {}).get("project_start")),
                     "finishP50": _iso((schedule or {}).get("p50_finish_date")),
                     "finishP80": _iso((schedule or {}).get("p80_finish_date")),
                     "finishDeterministic": _iso((schedule or {}).get("deterministic_finish_date")),
                     "critical": critical, "milestones": milestones,
                     "activities": [{"code": a.get("activity_code"), "name": a.get("name"),
                                     "phase": a.get("phase"), "duration": _num(a.get("expected_wd")),
                                     "start": _iso(a.get("start_date")), "finish": _iso(a.get("finish_date")),
                                     "critical": bool(a.get("is_critical")), "milestone": bool(a.get("is_milestone"))}
                                    for a in activities],
                     "id": str(schedule["id"]) if schedule else None},
        "cashFlow": {"present": bool(cash_flow),
                     "bridgeFinancing": _num((cash_flow or {}).get("bridge_financing_required")),
                     "minCashPosition": _num((cash_flow or {}).get("min_cash_position")),
                     "finalCashPosition": _num((cash_flow or {}).get("final_cash_position")),
                     "phaseCosts": (cash_flow or {}).get("phase_costs"),
                     "milestoneDates": (cash_flow or {}).get("milestone_dates"),
                     "id": str(cash_flow["id"]) if cash_flow else None},
        "drawings": drawings,
        "stale": stale,
    }


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)[:10]


# ── AI framing call (plain-JSON output; shape described in _SYSTEM) ──────────

_SYSTEM = """You are a senior construction project manager at BRIO writing the narrative for a CLIENT presentation of a renovation or commercial construction project. You are given the project's real, already-calculated data (estimate, scope, schedule, cash flow, drawings).

PURPOSE: inform the client — clearly, honestly, technically — about HOW the project will be executed: the work involved, its technical difficulties and logistic challenges, the schedule and its critical steps, the cash-flow requirement, and the decisions we need from them EARLY to keep the project on schedule. This is NOT a sales pitch and it is NOT our job to justify the cost or persuade the client the price is worthwhile. Focus on the PROJECT and its execution.

ABSOLUTE RULES:
- Do NOT use the word "investment", and do NOT frame the work as an investment, a value proposition, or money well spent. Never defend, sell, or editorialize about the price.
- You write PROSE and JUDGEMENT only. NEVER state, compute, or alter a dollar figure, date, quantity, or percentage — those are rendered separately. Refer to cost only neutrally where unavoidable ("one of the larger work packages", "a major portion of the work") — never as something to be justified.
- Ground everything in the supplied scope/estimate/schedule. Do not invent scope, materials, or commitments not present in the data.
- Tone: professional, direct, technical — speaking to the client as a competent partner who needs to understand what's involved and what's required from them.

TASKS:
- headline: one factual sentence stating what the project is.
- overview: 2-3 short paragraphs — what the project involves and how BRIO will execute it, including the main technical and logistic realities (site access, existing conditions, sequencing, occupied-space or phasing constraints, code/permit dependencies) where relevant.
- scopeSections: one per major scope area. clientDescription = plain-language explanation of the work AND any technical difficulty or logistic challenge it carries. featureDrawingDisciplines = which drawing types (architectural/structural/stormwater/etc.) best illustrate this area.
- workAreaNotes: return EXACTLY one entry for EACH item in the `highCostCategories` list in the data below — cover every one. Set `i` to that item's `i` value, and `note` to 1-2 sentences on the TECHNICAL DIFFICULTY and/or LOGISTIC CHALLENGE of that specific work area (complexity, sequencing, site constraints, coordination, code, existing conditions). Inform the client what makes THAT work involved — do NOT justify or defend its cost, and do not invent work areas not in the list.
- ownerDecisions: ONLY decisions the OWNER must actively make that affect construction and the schedule — the material / finish / product SELECTIONS for work that ACTUALLY APPEARS in the scope and estimate provided (e.g. if the estimate includes windows, cladding, a garage door, cabinetry, or flooring, those selections belong here). CRITICAL GROUNDING: every decision must correspond to a scope/estimate line. Do NOT introduce a system, product, feature, or option that is not present in the data (for example, do not raise an HVAC/heating decision, a radiant-slab option, or any equipment choice unless that work is clearly in the scope/estimate). For each: title, whatToDecide, affectsSchedule (what work it holds up if the decision is late), leadTimeWeeks (procurement/decision lead time in weeks, integer, or null). Do NOT include permits, our own tasks, or anything we can determine ourselves by site inspection.
- ourAssumptions: things WE are assuming to be adequate or as-expected and will VERIFY ourselves during site visits / excavation / demolition — e.g. existing electrical service & panel capacity, existing plumbing, soil / ground conditions, existing structure, concealed conditions. These are NOT owner decisions; they are assumptions we carry into the estimate. For each: title, assumption (what we are assuming), ifWrong (the change-order / extra-cost consequence if a site inspection proves it wrong). This section tells the client up front, honestly, where change orders could arise.
- scheduleNarrative: brief plain-language framing of the timeline, sequence, and critical steps.
- cashFlowNarrative: brief plain-language framing of the cash-flow requirement and payment cadence — the funding rhythm the project needs, stated factually (not as a selling point).
- estimateChange: ONLY if `priorEstimate` appears in the data. Write a clear, honest 2-4 sentence explanation of why the current total is higher than the earlier figure the client already received. Ground it strictly in the real reason: scope that was NOT in the original estimate — specifically the City-required stormwater management / site-servicing work and changes arising from City feedback and the updated drawings. Acknowledge plainly that it is a meaningful increase, and frame it as ADDED SCOPE now required by the City (new work that must be built), not a price increase on the same work. Do NOT invent or state any dollar figures — the numbers are rendered separately.
- closingNote: a short, practical forward-looking close.

OUTPUT: return ONLY a single valid JSON object (no markdown fences, no prose around it) with exactly these keys:
  headline (string), overview (string),
  scopeSections (array of {title, discipline, clientDescription, featureDrawingDisciplines:[string]}),
  workAreaNotes (array of {i:int, note:string} — one per highCostCategories item, by its i),
  ownerDecisions (array of {title, whatToDecide, affectsSchedule, leadTimeWeeks:int|null}),
  ourAssumptions (array of {title, assumption, ifWrong}),
  scheduleNarrative (string), cashFlowNarrative (string), closingNote (string),
  estimateChange (string — include ONLY if priorEstimate is in the data; the honest why-higher explanation).
Escape all quotes/newlines inside string values so the JSON parses."""


def build_presentation_messages(ctx: dict) -> tuple[str, list[dict], int]:
    """Returns (system, messages, max_tokens) for the framing call (plain JSON)."""
    # Compact, number-light context for the model (numbers rendered elsewhere).
    slim = {
        "project": ctx["project"],
        "scope": {"summary": ctx["scope"]["summary"],
                  "categories": [{"name": c.get("name"), "description": c.get("description")}
                                 for c in ctx["scope"]["categories"] if isinstance(c, dict)],
                  "potentialIssues": ctx["scope"]["potentialIssues"],
                  "permits": ctx["scope"]["permits"]},
        "estimateCategories": [c["name"] for c in ctx["totals"]["categories"]],
        "highCostCategories": [{"i": i, "name": c["name"]} for i, c in enumerate(ctx["highCostDrivers"])],
        "scheduleMilestones": ctx["schedule"]["milestones"],
        "scheduleCriticalActivities": [c["name"] for c in ctx["schedule"]["critical"]],
        "hasCashFlow": ctx["cashFlow"]["present"],
        "drawingDisciplinesAvailable": sorted({d["discipline"] for d in ctx["drawings"]}),
    }
    if ctx.get("priorEstimate"):
        slim["priorEstimate"] = ctx["priorEstimate"]
    user = ("Write the client presentation narrative for this project. Data:\n\n"
            + json.dumps(slim, indent=1, default=str))
    return _SYSTEM, [{"role": "user", "content": user}], 8000


def _decide_by(ctx: dict, item: dict) -> str | None:
    """Compute a concrete 'decide by' date: target milestone/start minus lead time."""
    lead_weeks = item.get("leadTimeWeeks")
    start = ctx["schedule"].get("projectStart")
    # default target = project start (most early-approval items gate early work)
    target = None
    gates = (item.get("gates") or item.get("affectsSchedule") or "").lower()
    for m in ctx["schedule"].get("milestones") or []:
        if m.get("name") and m["name"].lower() in gates and m.get("date"):
            target = m["date"]
            break
    target = target or start
    if not target:
        return None
    try:
        d = datetime.fromisoformat(target).date()
    except Exception:
        return None
    if isinstance(lead_weeks, int) and lead_weeks > 0:
        d = d - timedelta(weeks=lead_weeks)
    return d.isoformat()


def assemble_payload(ctx: dict, ai: dict) -> dict:
    """Merge deterministic data (numbers) with AI prose into the deck payload."""
    owner_decisions = []
    for it in ai.get("ownerDecisions") or []:
        owner_decisions.append({**it, "decideByDate": _decide_by(ctx, it)})

    # Work-area notes are keyed by the index we handed the model (robust to
    # any category-name drift). Accept legacy categoryName matches as a fallback.
    _notes_by_i: dict[int, str] = {}
    _by_name = {c["name"]: i for i, c in enumerate(ctx["highCostDrivers"])}
    for j in ai.get("workAreaNotes") or []:
        if not isinstance(j, dict) or not j.get("note"):
            continue
        idx = j.get("i")
        if not isinstance(idx, int) and j.get("categoryName") in _by_name:
            idx = _by_name[j["categoryName"]]
        if isinstance(idx, int):
            _notes_by_i[idx] = j["note"]

    prior = ctx.get("priorEstimate") or {}
    estimate_change = None
    if prior.get("total"):
        cur = ctx["totals"]["total"]
        estimate_change = {
            "priorTotal": prior.get("total"), "priorDate": prior.get("date"),
            "priorNote": prior.get("note"), "currentTotal": cur,
            "delta": round(cur - float(prior["total"]), 2),
            "explanation": ai.get("estimateChange"),
        }

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "audience": "client",
        "project": ctx["project"],
        "client": ctx["client"],
        "headline": ai.get("headline"),
        "overview": ai.get("overview"),
        "scopeSections": ai.get("scopeSections") or [],
        "budget": {**ctx["totals"], "reference": ctx["estimate"]["reference"],
                   "detailLevel": ctx["estimate"]["detailLevel"],
                   "validDays": ctx["estimate"]["validDays"],
                   "paymentSchedule": ctx["estimate"]["paymentSchedule"]},
        "estimateChange": estimate_change,
        "highCostItems": [
            {"categoryName": c["name"], "displayTotal": c["displayTotal"], "note": _notes_by_i.get(i)}
            for i, c in enumerate(ctx["highCostDrivers"])
        ],
        "schedule": {**ctx["schedule"], "narrative": ai.get("scheduleNarrative")},
        "cashFlow": {**ctx["cashFlow"], "narrative": ai.get("cashFlowNarrative")},
        "ownerDecisions": owner_decisions,
        "ourAssumptions": ai.get("ourAssumptions") or [],
        "drawings": ctx["drawings"],
        "assumptions": ctx["estimate"]["assumptions"],
        "exclusions": ctx["estimate"]["exclusions"],
        "closingNote": ai.get("closingNote"),
        "provenance": {"estimateId": ctx["estimate"]["id"], "scheduleId": ctx["schedule"]["id"],
                       "cashFlowId": ctx["cashFlow"]["id"], "stale": ctx["stale"]},
    }
