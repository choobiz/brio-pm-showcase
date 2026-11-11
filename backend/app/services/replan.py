"""Progress-aware re-plan: a new schedule version from what actually happened.

Same rules the hand-built 1234 versions used (v6/v7 notes), so a re-plan can be
checked against them:
  * finish-to-start links; an activity's `lag_wd` applies after each predecessor
  * Mon-Fri, minus the version's holidays and shutdown ranges
  * finish dates are inclusive: a 2-day job starting Thu finishes Fri
  * P80 keeps the base version's buffer (working days) over P50

Progress on the data date:
  * done      start/finish are the recorded dates; zero float, never critical
  * assumed   no finish on record but later work has started, so it happened;
              keeps its planned dates and is flagged, not hidden
  * started   starts on its recorded start, logic ignored (it is under way);
              the remaining work runs from the data date. Remaining = by the
              recorded % if there is one, else planned duration minus the
              working days already elapsed, never less than 1
  * not done  early start = the later of the data date and its predecessors

Unanswered work (due by the data date per the plan, nothing typed on Record
what happened) follows `unanswered`:
  * "keep" (default, the approved sketch: "unanswered keep their planned
    dates") — taken to have gone to plan: scheduled from its planned start
    (or its recorded start) and its predecessors, with no data-date floor,
    and not before the day after a site report said it had not started.
    Finished before the data date = counted done; otherwise under way
  * "push" — nothing more happened than the records say; it runs from the
    data date
The new version's notes name the rule and every activity it was applied to.

Nothing here touches the database; services/schedule_replan.py loads and saves.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Calendar:
    holidays: set[date] = field(default_factory=set)
    ranges: list[tuple[date, date]] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Iterable[Any] | None) -> "Calendar":
        """Holidays as stored on a version: "2026-10-12", "2026-09-30 Truth &
        Reconciliation", or {"shutdown": {"from": ..., "to": ...}}."""
        cal = cls()
        for h in raw or []:
            if isinstance(h, str):
                m = _ISO.match(h.strip())
                if m:
                    cal.holidays.add(date.fromisoformat(m.group(1)))
            elif isinstance(h, dict):
                for v in h.values():
                    if isinstance(v, dict) and v.get("from") and v.get("to"):
                        cal.ranges.append((date.fromisoformat(str(v["from"])[:10]),
                                           date.fromisoformat(str(v["to"])[:10])))
        return cal

    def working(self, d: date) -> bool:
        if d.weekday() >= 5 or d in self.holidays:
            return False
        return not any(a <= d <= b for a, b in self.ranges)

    def on_or_after(self, d: date) -> date:
        while not self.working(d):
            d += timedelta(days=1)
        return d

    def add(self, d: date, n: int) -> date:
        """The working day n working days after working day d (n may be 0)."""
        d = self.on_or_after(d)
        while n > 0:
            d += timedelta(days=1)
            if self.working(d):
                n -= 1
        return d

    def sub(self, d: date, n: int) -> date:
        d = self.on_or_after(d)
        while n > 0:
            d -= timedelta(days=1)
            if self.working(d):
                n -= 1
        return d

    def offset(self, start: date, d: date) -> int:
        """Working days from start to d (start = 0); negative if d is earlier."""
        a, b = self.on_or_after(start), self.on_or_after(d)
        if b < a:
            return -self.offset(b, a)
        n, x = 0, a
        while x < b:
            x += timedelta(days=1)
            if self.working(x):
                n += 1
        return n


def _d(v: Any) -> date | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _dur(a: dict) -> int:
    m = a.get("most_likely_wd") if a.get("most_likely_wd") is not None else a.get("expected_wd")
    return max(1, int(round(float(m or 0))))


def _topo(acts: dict[str, dict]) -> list[str]:
    indeg = {c: 0 for c in acts}
    succ: dict[str, list[str]] = {c: [] for c in acts}
    for c, a in acts.items():
        for p in a.get("predecessor_codes") or []:
            if p in acts:
                succ[p].append(c)
                indeg[c] += 1
    queue = [c for c in acts if indeg[c] == 0]
    out: list[str] = []
    while queue:
        c = queue.pop(0)
        out.append(c)
        for s in succ[c]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(out) != len(acts):
        raise ValueError("schedule logic has a loop: " + ", ".join(sorted(set(acts) - set(out))))
    return out


def assumed_done(acts: dict[str, dict], actuals: dict[str, dict]) -> set[str]:
    """Predecessors (transitively) of anything started or finished that have
    no finish on record. Same rule as frontend lib/schedulePlan assumedDone."""
    def finished(c: str) -> bool:
        r = actuals.get(c) or {}
        return bool(r.get("actual_finish")) or r.get("status") == "done"

    out: set[str] = set()
    stack: list[str] = []
    for c, a in acts.items():
        r = actuals.get(c) or {}
        if r.get("actual_start") or finished(c):
            stack.extend(a.get("predecessor_codes") or [])
    while stack:
        c = stack.pop()
        if c in out or c not in acts:
            continue
        if not finished(c):
            out.add(c)
        stack.extend(acts[c].get("predecessor_codes") or [])
    return out


def unanswered_codes(acts: dict[str, dict], actuals: dict[str, dict], assumed: set[str],
                     data_date: date) -> set[str]:
    """Due by the data date per the plan, not finished, not assumed done, and
    nothing typed in (site-report facts alone don't answer it)."""
    out = set()
    for c, a in acts.items():
        r = actuals.get(c) or {}
        if c in assumed or r.get("actual_finish") or r.get("status") == "done":
            continue
        if r.get("source") in ("typed", "both"):
            continue
        ps, pf = _d(a.get("start_date")), _d(a.get("finish_date"))
        if (pf and pf < data_date) or (ps and ps < data_date and not r.get("actual_start")):
            out.add(c)
    return out


def replan(base: dict, actuals: dict[str, dict], data_date: date, unanswered: str = "keep") -> dict:
    """base: a schedule version ({project_start, holidays, p50/p80_finish_date,
    activities:[...]}) as GET /schedules/{id} returns it. actuals: per code
    {status, actual_start, actual_finish, percent_complete}. Returns the new
    version's fields plus `changes` for the preview."""
    cal = Calendar.from_json(base.get("holidays"))
    ps = _d(base["project_start"])
    dd = cal.on_or_after(data_date)
    acts = {a["activity_code"]: dict(a) for a in base["activities"]}
    order = _topo(acts)
    assumed = assumed_done(acts, actuals)
    if unanswered not in ("keep", "push"):
        raise ValueError("unanswered must be 'keep' or 'push'")
    kept = unanswered_codes(acts, actuals, assumed, dd) if unanswered == "keep" else set()

    start: dict[str, date] = {}
    finish: dict[str, date] = {}
    remaining: dict[str, int] = {}
    state: dict[str, str] = {}

    for c in order:
        a, r, dur = acts[c], actuals.get(c) or {}, _dur(acts[c])
        a_start, a_finish = _d(r.get("actual_start")), _d(r.get("actual_finish"))
        if a_finish or r.get("status") == "done":
            f = a_finish or _d(a.get("finish_date"))
            s = a_start or (cal.sub(f, dur - 1) if f else _d(a.get("start_date")))
            start[c], finish[c], state[c] = s, f, "done"
        elif c in assumed:
            start[c], finish[c], state[c] = _d(a.get("start_date")), _d(a.get("finish_date")), "assumed"
        elif c in kept:
            # As if the past went to plan: the links still hold, but no data-
            # date floor. A recorded start fixes the start; otherwise the later
            # of the planned start and the predecessors.
            s0 = a_start
            if s0 is None:
                s0 = _d(a.get("start_date")) or dd
                for p in a.get("predecessor_codes") or []:
                    if p in finish and finish[p]:
                        s0 = max(s0, cal.add(finish[p], 1 + int(a.get("lag_wd") or 0)))
                # a site report that said "not started" on day L stands: the
                # earliest it can have started is the next working day
                seen = _d(r.get("last_reported"))
                if r.get("status") == "not_started" and seen:
                    s0 = max(s0, cal.add(seen, 1))
            f0 = cal.add(s0, dur - 1)
            start[c], finish[c] = s0, f0
            if f0 < dd:
                state[c] = "kept"
            else:
                state[c] = "kept_started" if s0 < dd else "planned"
                if s0 < dd:
                    remaining[c] = cal.offset(dd, f0) + 1
        elif a_start:
            pct = r.get("percent_complete")
            if pct:
                rem = max(1, math.ceil(dur * (100 - int(pct)) / 100))
            else:
                elapsed = cal.offset(a_start, dd) if dd > a_start else 0
                rem = max(1, dur - elapsed)
            remaining[c] = rem
            start[c], finish[c], state[c] = a_start, cal.add(dd, rem - 1), "started"
        else:
            es = dd
            for p in a.get("predecessor_codes") or []:
                if p in finish and finish[p]:
                    es = max(es, cal.add(finish[p], 1 + int(a.get("lag_wd") or 0)))
            start[c], finish[c], state[c] = es, cal.add(es, dur - 1), "planned"

    open_codes = [c for c in order if state[c] in ("started", "kept_started", "planned")]
    project_finish = max(finish.values())

    # Backward pass over the work still to do.
    succs: dict[str, list[str]] = {c: [] for c in acts}
    for c, a in acts.items():
        for p in a.get("predecessor_codes") or []:
            if p in acts:
                succs[p].append(c)
    late_finish: dict[str, date] = {}
    late_start: dict[str, date] = {}
    for c in reversed(order):
        if c not in open_codes:
            continue
        lf = project_finish
        for s in succs[c]:
            if s in late_start:
                lf = min(lf, cal.sub(late_start[s], 1 + int(acts[s].get("lag_wd") or 0)))
        span = (remaining[c] if c in remaining else _dur(acts[c])) - 1
        late_finish[c], late_start[c] = lf, cal.sub(lf, span)

    out_acts: list[dict] = []
    critical: list[str] = []
    for c in order:
        a = dict(acts[c])
        s, f = start[c], finish[c]
        a.pop("id", None)
        a["start_date"], a["finish_date"] = s.isoformat(), f.isoformat()
        a["early_start_wd"], a["early_finish_wd"] = cal.offset(ps, s), cal.offset(ps, f)
        if c in late_finish:
            ef = finish[c]
            tf = cal.offset(ef, late_finish[c])
            a["late_start_wd"], a["late_finish_wd"] = cal.offset(ps, late_start[c]), cal.offset(ps, late_finish[c])
            a["total_float_wd"] = a["free_float_wd"] = tf
            a["is_critical"] = tf <= 0
        else:
            a["late_start_wd"], a["late_finish_wd"] = a["early_start_wd"], a["early_finish_wd"]
            a["total_float_wd"] = a["free_float_wd"] = 0
            a["is_critical"] = False
        if a["is_critical"]:
            critical.append(c)
        out_acts.append(a)

    base_p50, base_p80 = _d(base.get("p50_finish_date")), _d(base.get("p80_finish_date"))
    buffer = cal.offset(base_p50, base_p80) if base_p50 and base_p80 else 0
    p80 = cal.add(project_finish, buffer)

    old = {a["activity_code"]: a for a in base["activities"]}
    changes = []
    for a in out_acts:
        o = old[a["activity_code"]]
        os_, of = _d(o.get("start_date")), _d(o.get("finish_date"))
        moved = (os_.isoformat() if os_ else None, of.isoformat() if of else None) != (a["start_date"], a["finish_date"])
        changes.append({
            "activity_code": a["activity_code"], "name": a.get("name"), "state": state[a["activity_code"]],
            "old_start": os_.isoformat() if os_ else None, "old_finish": of.isoformat() if of else None,
            "new_start": a["start_date"], "new_finish": a["finish_date"],
            "finish_moved_days": (_d(a["finish_date"]) - of).days if of else None,
            "moved": moved, "is_critical": a["is_critical"],
            "was_critical": bool(o.get("is_critical")),
        })

    return {
        "project_start": ps.isoformat(),
        "data_date": dd.isoformat(),
        "holidays": base.get("holidays") or [],
        "deterministic_finish_date": project_finish.isoformat(),
        "p50_finish_date": project_finish.isoformat(),
        "p80_finish_date": p80.isoformat(),
        "project_expected_wd": cal.offset(ps, project_finish) + 1,
        "project_std_dev_wd": base.get("project_std_dev_wd"),
        "critical_path_activity_codes": critical,
        "activities": out_acts,
        "changes": changes,
        "assumed": sorted(assumed),
        "unanswered_rule": unanswered,
        "kept": sorted(c for c in state if state[c] in ("kept", "kept_started")),
        "p80_buffer_wd": buffer,
    }
