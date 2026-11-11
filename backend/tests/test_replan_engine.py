"""Re-plan engine (services/replan.py) against the real 1234 schedule.

v7 was built by hand on 13 Oct 2026 as a progress update of v6. The engine has
to reproduce it exactly from v7's own progress state before its answers about
later dates mean anything.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.replan import Calendar, replan  # noqa: E402

V7 = json.loads((Path(__file__).parent / "fixtures" / "schedule_sample_v7.json").read_text())
BY = {a["activity_code"]: a for a in V7["activities"]}
D = date


def _done(*codes):
    return {c: {"status": "done", "actual_start": BY[c]["start_date"], "actual_finish": BY[c]["finish_date"]}
            for c in codes}


# What the site reports said for 1234 by 30 Oct (derive_from_reports output).
REPORTS_30_OCT = {
    "A50": {"status": "started", "actual_start": "2026-10-16", "percent_complete": 0, "source": "site_report"},
    "A100": {"status": "started", "actual_start": "2026-10-16", "percent_complete": 0, "source": "site_report"},
    "A60": {"status": "not_started", "last_reported": "2026-10-16", "source": "site_report"},
    "A107": {"status": "not_started", "last_reported": "2026-10-21", "source": "site_report"},
    "A125": {"status": "not_started", "last_reported": "2026-10-20", "source": "site_report"},
}


def test_calendar_reads_labelled_holidays_and_the_christmas_shutdown():
    cal = Calendar.from_json(V7["holidays"] + ["2026-09-30 Truth & Reconciliation"])
    assert not cal.working(D(2026, 10, 12))            # Thanksgiving
    assert not cal.working(D(2026, 9, 30))             # labelled string
    assert not cal.working(D(2026, 12, 23))            # shutdown 22 Dec - 4 Jan
    assert cal.add(D(2026, 12, 21), 1) == D(2027, 1, 5)


def test_replaying_v7_from_its_own_progress_reproduces_every_date():
    out = replan(V7, _done("A10", "A20", "A41", "A40", "A45", "A70"), D(2026, 10, 13))
    assert [c["activity_code"] for c in out["changes"] if c["moved"]] == []
    new = {a["activity_code"]: a for a in out["activities"]}
    for code, a in BY.items():
        assert new[code]["total_float_wd"] == float(a["total_float_wd"]), code
        assert new[code]["is_critical"] == a["is_critical"], code
    assert (out["p50_finish_date"], out["p80_finish_date"]) == ("2027-01-15", "2027-01-25")
    assert out["project_expected_wd"] == 63


def test_keep_rule_unanswered_work_follows_the_links_and_the_reports():
    out = replan(V7, REPORTS_30_OCT, D(2026, 10, 30), "keep")
    ch = {c["activity_code"]: c for c in out["changes"]}
    # forming started a day late (16 Oct) and takes its 2 days
    assert (ch["A100"]["new_start"], ch["A100"]["new_finish"]) == ("2026-10-16", "2026-10-19")
    # the inspection follows it, not the same day (first draft got this wrong)
    assert ch["A105"]["new_start"] == "2026-10-20"
    # "will be poured tomorrow" on 21 Oct: the pour can't be before 22 Oct
    assert ch["A107"]["new_start"] == "2026-10-22"
    # "not started" on 16 Oct stands for the tank
    assert ch["A60"]["new_start"] == "2026-10-19"
    assert out["p50_finish_date"] == "2027-01-19"
    assert "A100" in out["kept"] and out["unanswered_rule"] == "keep"


def test_push_rule_nothing_happened_beyond_the_records():
    out = replan(V7, REPORTS_30_OCT, D(2026, 10, 30), "push")
    ch = {c["activity_code"]: c for c in out["changes"]}
    assert ch["A100"]["state"] == "started" and ch["A100"]["new_finish"] == "2026-10-30"
    assert ch["A105"]["new_start"] == "2026-11-02"
    assert out["p50_finish_date"] == "2027-01-29" and out["kept"] == []


def test_typed_answers_are_not_overridden_by_the_keep_rule():
    act = dict(REPORTS_30_OCT)
    act["A107"] = {"status": "not_started", "reason": "Weather", "source": "both", "last_reported": "2026-10-21"}
    out = replan(V7, act, D(2026, 10, 30), "keep")
    a107 = next(c for c in out["changes"] if c["activity_code"] == "A107")
    assert a107["state"] == "planned" and a107["new_start"] == "2026-10-30"   # from the data date


def test_recorded_percent_sets_the_remaining_work():
    act = {"A250": {"status": "started", "actual_start": "2026-10-26", "percent_complete": 50, "source": "typed"}}
    out = replan(V7, act, D(2026, 10, 30), "push")
    a = next(c for c in out["changes"] if c["activity_code"] == "A250")
    assert a["new_finish"] == "2026-11-05"   # 9 wd at 50% -> 5 left: Fri 30 Oct + 4 wd


def test_done_work_has_no_float_and_is_never_critical():
    out = replan(V7, _done("A10", "A20", "A41", "A40", "A45", "A70"), D(2026, 10, 13))
    a40 = next(a for a in out["activities"] if a["activity_code"] == "A40")
    assert a40["total_float_wd"] == 0 and a40["is_critical"] is False


def test_a_logic_loop_is_refused():
    base = {**V7, "activities": [dict(a) for a in V7["activities"]]}
    base["activities"][0]["predecessor_codes"] = ["A410"]
    with pytest.raises(ValueError, match="loop"):
        replan(base, {}, D(2026, 10, 30))
