"""Turning speech into a day's report — what the request promises, and what the
parser refuses to let through.

The property under test is the one the whole feature turns on: "nothing
happened" and "nobody asked" must stay distinguishable. A report that records
"no safety incident" because the conversation never reached safety is a false
entry on a document read after accidents and during disputes — and it is exactly
the entry a careless implementation produces, because an empty section looks
like a clean day.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.ai_job import AI_JOB_KINDS  # noqa: E402
from app.models.daily_site_report import DAILY_TASK_STATUSES  # noqa: E402
from app.services.ai import build_messages_for_job  # noqa: E402
from app.services.daily_report_capture import (  # noqa: E402
    SECTIONS,
    TASK_STATUSES,
    build_daily_capture_messages,
    parse_capture_result,
)

KIND = "daily_report_capture"


def _tasks():
    return [
        {"activity_code": "A-110", "name": "Wall framing", "phase": "structure",
         "is_critical": True, "removed": False},
        {"activity_code": "A-120", "name": "Window delivery", "phase": "envelope",
         "removed": False},
        {"activity_code": "A-130", "name": "Plumbing rough-in inspection",
         "is_milestone": True, "removed": False},
        {"activity_code": "A-999", "name": "Removed activity", "removed": True},
    ]


def _input(**kw):
    base = {
        "report_date": "2026-10-02",
        "project_metadata": {"name": "1234 Example Way", "address": "1234 Example Way"},
        "scheduled_tasks": _tasks(),
        "transcript": "Got the north and east walls up, about two thirds done, four guys.",
    }
    base.update(kw)
    return base


def _text(messages):
    return "\n".join(
        m["content"] if isinstance(m["content"], str) else "" for m in messages
    )


# --- the request ------------------------------------------------------------

def test_the_kind_is_registered_and_routed():
    assert KIND in AI_JOB_KINDS
    system, messages, _ = build_messages_for_job(KIND, _input())
    assert system and messages


def test_the_schedule_is_the_agenda():
    """The agent opens with what was planned, which is what removes the
    "what do I say" problem — the conversation is a walkthrough, not a blank box."""
    _, messages, _ = build_daily_capture_messages(_input())
    t = _text(messages)
    assert "A-110" in t and "Wall framing" in t
    assert "A-130" in t and "Plumbing rough-in inspection" in t
    assert "CRITICAL PATH" in t


def test_removed_activities_are_not_in_the_agenda():
    _, messages, _ = build_daily_capture_messages(_input())
    assert "Removed activity" not in _text(messages)


def test_the_transcript_is_carried_verbatim():
    t = "north and east walls up, about two thirds, four guys, windows wrong size"
    _, messages, _ = build_daily_capture_messages(_input(transcript=t))
    assert t in _text(messages)


def test_no_transcript_forbids_writing_the_plan_as_though_it_happened():
    """The most dangerous output available: a report generated from the SCHEDULE
    with no account of the day. It looks like a record and contains no
    observation."""
    _, messages, _ = build_daily_capture_messages(_input(transcript="   "))
    t = _text(messages)
    assert "NO TRANSCRIPT WAS PROVIDED" in t
    assert "Do NOT describe what was scheduled as though it happened" in t


def test_the_prompt_separates_nothing_happened_from_nobody_asked():
    system, _, _ = build_daily_capture_messages(_input())
    assert '"nobody mentioned it" are different facts' in system
    assert 'DO NOT record "no incident"' in system


def test_the_prompt_flags_without_acting():
    system, _, _ = build_daily_capture_messages(_input())
    low = system.lower()
    assert "flagged_for_office" in system
    assert "do not act on them" in low
    assert "do not re-baseline anything" in low


def test_the_prompt_forbids_invented_numbers_and_prices():
    system, _, _ = build_daily_capture_messages(_input())
    low = system.lower()
    assert "never invent a number" in low
    assert "never include prices" in low


def test_task_statuses_match_the_database_enum():
    """A status the model is told to use but the column rejects would fail on
    save, after the supervisor thought they were done."""
    assert set(TASK_STATUSES) == set(DAILY_TASK_STATUSES)
    system, _, _ = build_daily_capture_messages(_input())
    for s in TASK_STATUSES:
        assert s in system


# --- the parser -------------------------------------------------------------

def test_a_section_the_model_omitted_is_reported_as_not_recorded():
    """The headline property. Only tasks were discussed; the other seven
    sections must come back as unknown, not as empty-and-therefore-fine."""
    out = parse_capture_result(json.dumps({
        "payload": {"tasks": [{"activity_code": "A-110", "name": "Wall framing",
                               "status": "in_progress", "percent_complete": 65}]},
        "not_recorded": ["conditions"],
    }))
    assert "tasks" not in out["not_recorded"]
    for s in SECTIONS:
        if s != "tasks":
            assert s in out["not_recorded"], f"{s} silently treated as answered"


def test_an_empty_array_means_asked_and_none_not_unknown():
    """The distinction in the other direction: the supervisor was asked about
    visitors and there were none. That is a recorded fact and must survive."""
    out = parse_capture_result(json.dumps({
        "payload": {"visitors": [], "safety": {"incident": False, "notes": "none"}},
        "not_recorded": [],
    }))
    assert "visitors" not in out["not_recorded"]
    assert "safety" not in out["not_recorded"]
    assert out["payload"]["visitors"] == []
    assert out["payload"]["safety"]["incident"] is False


def test_a_markdown_fence_is_tolerated():
    body = json.dumps({"payload": {"notes": "quiet day"}, "not_recorded": []})
    out = parse_capture_result(f"```json\n{body}\n```")
    assert out["payload"]["notes"] == "quiet day"


def test_prose_around_the_object_is_tolerated():
    body = json.dumps({"payload": {"notes": "ok"}, "not_recorded": []})
    out = parse_capture_result(f"Here is the report:\n{body}\nLet me know.")
    assert out["payload"]["notes"] == "ok"


@pytest.mark.parametrize("bad", ["", "   ", "no json here", "[1,2,3]", "{broken"])
def test_an_unparseable_reply_raises_rather_than_half_succeeding(bad):
    """A half-parsed report is worse than a failed one, because the half that
    parsed looks complete."""
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_capture_result(bad)


def test_unknown_section_names_are_discarded():
    out = parse_capture_result(json.dumps({
        "payload": {}, "not_recorded": ["conditions", "invented_section"],
    }))
    assert "invented_section" not in out["not_recorded"]
    assert set(out["not_recorded"]) == set(SECTIONS)


def test_flags_and_unclear_survive_and_are_type_checked():
    out = parse_capture_result(json.dumps({
        "payload": {},
        "not_recorded": [],
        "flagged_for_office": [
            {"what": "Windows rejected", "why": "may be a supplier back-charge"},
            "not a dict",
        ],
        "unclear": ["was the inspection today or tomorrow", 42],
    }))
    assert len(out["flagged_for_office"]) == 1
    assert out["flagged_for_office"][0]["what"] == "Windows rejected"
    assert out["unclear"] == ["was the inspection today or tomorrow"]


def test_a_missing_payload_key_does_not_crash():
    out = parse_capture_result(json.dumps({"not_recorded": []}))
    assert out["payload"] == {}
    assert set(out["not_recorded"]) == set(SECTIONS)


def test_photo_markers_are_explained_as_attached_photos():
    """Rollout test 2026-10-14: with two photos attached the write-up said
    "photos referenced ... not included in this report"."""
    from app.services.daily_report_capture import PHOTO_NOTE, build_daily_capture_messages

    _, messages, _ = build_daily_capture_messages(
        {"report_date": "2026-10-14", "transcript": "Framed the east wall.\n[photo: a.jpg]"})
    assert PHOTO_NOTE in messages[0]["content"]


def test_a_completed_task_is_saved_as_fully_done():
    """Rollout test 2026-10-14 saved a task as Complete at 0%."""
    from app.routers.daily_reports import complete_means_done

    out = complete_means_done({"tasks": [{"name": "Frame", "status": "completed", "percent_complete": 0},
                                         {"name": "Sheath", "status": "in_progress", "percent_complete": 0}]})
    assert [t["percent_complete"] for t in out["tasks"]] == [100, 0]


def test_the_report_pdf_carries_raised_items_what_was_not_mentioned_and_photos():
    """Rollout test 2026-10-14: the report dropped what was raised for the office
    and its photos, and blank sections read as "None"."""
    from app.services.daily_report_pdf import daily_report_html

    html = daily_report_html(
        report={"report_date": "2026-10-14", "status": "draft",
                "payload": {"raised_for_office": [{"what": "Two split studs", "why": "possible back-charge"}],
                            "not_recorded": ["visitors", "delays"]}},
        project={"name": "ZZ", "address": ""}, photos=["data:image/jpeg;base64,AAAA"])
    assert "Two split studs" in html and "possible back-charge" in html
    assert "Not mentioned in this report" in html and "visitors, delays" in html
    assert '<img src="data:image/jpeg;base64,AAAA"/>' in html
