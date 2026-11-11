"""Compact Critical Path Method engine (server-side) with PERT risk spread.

The web ScheduleEditor computes CPM in the browser; this mirrors the same math
so `schedule_from_estimate` can produce a fully-baked schedule from an
AI-proposed activity list. Durations and floats are in WORKING DAYS; calendar
dates skip weekends (holidays optional).

The AI proposes a single most-likely duration per activity. We derive a
right-skewed PERT triple (optimistic/most-likely/pessimistic) from it —
construction overruns are more likely and larger than speed-ups — compute the
expected duration tₑ = (o + 4m + p)/6 and variance ((p − o)/6)², run the CPM
pass on tₑ, and derive a P80 finish from the critical-path variance so the deck
can show an honest P50/P80 range instead of a single point estimate.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

# Right-skewed spread: optimistic 15% faster, pessimistic 60% slower than most-likely.
_OPTIMISTIC = 0.85
_PESSIMISTIC = 1.6


def _add_working_days(start: date, wd: int, holidays: set[date]) -> date:
    """Return the date `wd` working days after `start` (0 → same day)."""
    d = start
    remaining = wd
    while remaining > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            remaining -= 1
    # ensure start itself is a working day
    while d.weekday() >= 5 or d in holidays:
        d = d + timedelta(days=1)
    return d


def _pert(m: int) -> tuple[int, int, int, float]:
    """From a most-likely duration m (working days) → (optimistic, pessimistic, effective, variance).

    `effective` = round(tₑ) is the integer working-day duration used in the CPM
    pass; `variance` feeds the project standard deviation. Milestones (m=0) carry
    no duration or risk.
    """
    if m <= 0:
        return 0, 0, 0, 0.0
    o = max(1, round(m * _OPTIMISTIC))
    p = max(m + 1, round(m * _PESSIMISTIC))
    te = (o + 4 * m + p) / 6.0
    var = ((p - o) / 6.0) ** 2
    return o, p, max(1, round(te)), var


def compute_cpm(activities: list[dict], project_start: date, holidays: set[date] | None = None) -> dict:
    """activities: [{code, name, duration_wd, predecessor_codes:[...], phase?, is_milestone?}]
    Returns {activities: [...with ES/EF/LS/LF/float/is_critical/PERT/start_date/finish_date],
             project_expected_wd, project_std_dev_wd, p50_finish_date, p80_finish_date,
             finish_date, critical_path_activity_codes}."""
    holidays = holidays or set()
    by_code = {a["code"]: dict(a) for a in activities if a.get("code")}
    order = list(by_code.keys())

    # PERT per activity — effective (rounded tₑ) duration drives the pass.
    for a in by_code.values():
        m = int(a.get("duration_wd") or 0)
        o, p, eff, var = _pert(m)
        a["_m"], a["_o"], a["_p"], a["_eff"], a["_var"] = m, o, p, eff, var

    def dur_of(code: str) -> int:
        return by_code[code]["_eff"]

    # Forward pass (ES/EF in working-day offsets from 0).
    def es_of(code: str, seen: set[str]) -> int:
        a = by_code[code]
        if "_es" in a:
            return a["_es"]
        if code in seen:
            return 0  # cycle guard
        seen = seen | {code}
        preds = [p for p in (a.get("predecessor_codes") or []) if p in by_code]
        es = 0 if not preds else max(es_of(p, seen) + dur_of(p) for p in preds)
        a["_es"] = es
        return es

    for code in order:
        es = es_of(code, set())
        by_code[code]["_ef"] = es + dur_of(code)

    project_wd = max((a["_ef"] for a in by_code.values()), default=0)

    # Backward pass (LF/LS).
    def lf_of(code: str, seen: set[str]) -> int:
        a = by_code[code]
        if "_lf" in a:
            return a["_lf"]
        if code in seen:
            return project_wd
        seen = seen | {code}
        succs = [c for c, x in by_code.items() if code in (x.get("predecessor_codes") or [])]
        lf = project_wd if not succs else min(lf_of(s, seen) - dur_of(s) for s in succs)
        a["_lf"] = lf
        return lf

    for code in order:
        lf_of(code, set())

    out: list[dict] = []
    critical: list[str] = []
    crit_var = 0.0
    for code in order:
        a = by_code[code]
        eff = a["_eff"]
        es, ef, lf = a["_es"], a["_ef"], a["_lf"]
        ls = lf - eff
        float_wd = ls - es
        is_crit = float_wd <= 0
        if is_crit:
            critical.append(code)
            crit_var += a["_var"]
        start_dt = _add_working_days(project_start, es, holidays)
        finish_dt = _add_working_days(project_start, max(ef - 1, es), holidays) if eff > 0 else start_dt
        out.append({
            "activity_code": code, "name": a.get("name"), "phase": a.get("phase"),
            "duration_wd": eff, "predecessor_codes": a.get("predecessor_codes") or [],
            "early_start_wd": es, "early_finish_wd": ef, "late_start_wd": ls, "late_finish_wd": lf,
            "total_float_wd": float_wd, "free_float_wd": float_wd,
            "is_critical": is_crit, "is_milestone": bool(a.get("is_milestone")) or a["_m"] == 0,
            "optimistic_wd": a["_o"], "most_likely_wd": a["_m"], "pessimistic_wd": a["_p"],
            "expected_wd": eff, "variance": round(a["_var"], 3),
            "lag_wd": 0,
            "start_date": start_dt.isoformat(), "finish_date": finish_dt.isoformat(),
        })

    # P80 from critical-path variance (floored at +8% so the buffer is always visible —
    # independent-activity CLT under-states correlated construction risk).
    std_dev = math.sqrt(crit_var)
    p50_wd = project_wd
    p80_wd = max(round(p50_wd + 0.84 * std_dev), round(p50_wd * 1.08))
    p50_finish = _add_working_days(project_start, max(p50_wd - 1, 0), holidays)
    p80_finish = _add_working_days(project_start, max(p80_wd - 1, 0), holidays)
    return {
        "activities": out,
        "project_expected_wd": p50_wd,
        "project_std_dev_wd": round(std_dev, 1),
        "p50_finish_date": p50_finish.isoformat(),
        "p80_finish_date": p80_finish.isoformat(),
        "finish_date": p50_finish.isoformat(),
        "critical_path_activity_codes": critical,
    }
