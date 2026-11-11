"""Turning what a supervisor said into the day's report, without inventing a day.

THE CASE FOR DOING THIS AT ALL, from §11b: a daily site report carries eight
sections — conditions, manpower, tasks, deliveries, visitors, safety, delays,
notes. Filling eight sections as a form, on a phone, at the end of a site day,
is exactly the kind of task that quietly stops happening. Speech is how people
describe a day anyway.

⚠️ THE DISTINCTION THIS WHOLE MODULE EXISTS TO PRESERVE: "nothing happened" and
"nobody asked" are different facts, and a form cannot tell them apart either.

    safety: {"incident": false}      -> someone asked, and there was no incident
    safety absent, "safety" in not_recorded -> nobody asked; unknown

The second must never be written as the first. A report that silently records
"no safety incident" because the conversation never reached safety is a false
record on a document that gets read after an accident. So the model fills only
what was actually said and NAMES every section it could not fill.

⚠️ IT PROPOSES. IT DOES NOT WRITE. The output is shown beside the transcript and
a person confirms before anything is PATCHed onto the report. Speech recognition
mangles site vocabulary and a misheard percentage becomes a progress claim.

⚠️ FLAGS, NEVER ACTIONS. A delay may move the schedule and a rejected delivery
may be a supplier back-charge. Both are raised for the office and neither is
acted on — re-baselining a schedule and raising a back-charge are decisions with
money attached, taken by people.
"""

from __future__ import annotations

import json
from typing import Any

#: Enough for eight sections of a busy day plus the reasoning about what was
#: not said. Not enough to write an essay.
MAX_TOKENS = 4000

#: The sections of the payload, which the model must use verbatim as keys.
SECTIONS = ("conditions", "manpower", "tasks", "deliveries",
            "visitors", "safety", "delays", "notes")

#: Per-task status values the model may use — the DAILY_TASK_STATUSES enum.
TASK_STATUSES = ("not_started", "in_progress", "completed", "delayed", "not_worked")

SYSTEM = """You turn a site supervisor's spoken account of a construction day \
into a structured daily report for a BC builder.

You are given the day's SCHEDULED ACTIVITIES and a TRANSCRIPT of what the \
supervisor said. The transcript came from speech recognition, so expect mangled \
trade vocabulary, missing punctuation and false starts.

THE RULE THAT OVERRIDES EVERY OTHER RULE

Record only what was actually said. "Nothing happened" and "nobody mentioned it" \
are different facts and must never be conflated.

  · If the supervisor said there were no safety incidents, record that.
  · If safety never came up, DO NOT record "no incident". Put "safety" in \
not_recorded instead.

The same applies to every section. An empty section you invented is a false \
entry on a document that is read after accidents and during disputes.

MATCHING TASKS

Each scheduled activity has an activity_code. When the supervisor clearly refers \
to a scheduled activity, use that exact activity_code. Only create an entry with \
"ad_hoc": true for work that is genuinely not on the schedule. If you cannot tell \
which activity was meant, leave it out of tasks and put the sentence in "unclear".

Set "status" to one of: not_started, in_progress, completed, delayed, not_worked. \
Set "percent_complete" ONLY if a figure was actually spoken or is unambiguous \
("about two thirds done" -> 65 is acceptable; "going well" is not — leave it null).

WHAT TO FLAG, AND WHAT NOT TO DO ABOUT IT

Some things a supervisor mentions have consequences outside the report: a delay \
that may move the schedule, a rejected delivery that may be a supplier \
back-charge, a safety incident, an instruction from an owner or consultant that \
may be a change. Put these in "flagged_for_office" with what was said and why it \
may matter.

Do NOT act on them. Do not re-baseline anything, do not raise a charge, do not \
write a change order, and do not tell the office what to do. You are noting that \
somebody should look.

NEVER DO THESE

1. Never invent a number — a crew count, a percentage, a temperature, a delivery \
quantity — that was not spoken.
2. Never add a trade, a visitor or a delivery that was not mentioned.
3. Never resolve a contradiction in the transcript. Put it in "unclear".
4. Never write a safety entry unless safety was discussed.
5. Never include prices, rates, or cost estimates. This is a record of \
observation, not a commercial document.

OUTPUT

Return ONE JSON object and nothing else. No markdown fence, no commentary.

{
  "payload": {
    "conditions": {"weather": str, "temp_c": number, "ground": str},
    "manpower":   [{"trade": str, "company": str|null, "count": number|null, "hours": number|null}],
    "tasks":      [{"activity_code": str|null, "name": str, "ad_hoc": bool,
                    "status": str, "percent_complete": number|null,
                    "crew": number|null, "notes": str}],
    "deliveries": [{"item": str, "supplier": str|null, "notes": str}],
    "visitors":   [{"name": str|null, "company": str|null, "purpose": str}],
    "safety":     {"incident": bool, "notes": str},
    "delays":     [{"reason": str, "impact": str, "notes": str}],
    "notes":      str
  },
  "not_recorded": [ "<section name>", ... ],
  "flagged_for_office": [ {"what": str, "why": str} ],
  "unclear": [ str ]
}

OMIT any section of "payload" that was not discussed, and name it in \
"not_recorded" instead. Do not include a section as an empty object or empty \
array to mean "nothing" — absence plus not_recorded is how you say "unknown", \
and an empty array means "asked, and there were none"."""


def _agenda_lines(tasks: list[dict[str, Any]]) -> str:
    """The scheduled activities, as the model's agenda for the conversation."""
    rows = []
    for t in tasks or []:
        if t.get("removed"):
            continue
        code = t.get("activity_code") or "—"
        name = t.get("name") or "(unnamed activity)"
        bits = [f"- [{code}] {name}"]
        if t.get("phase"):
            bits.append(f"phase {t['phase']}")
        if t.get("is_critical"):
            bits.append("CRITICAL PATH")
        if t.get("is_milestone"):
            bits.append("milestone")
        rows.append("  ".join(bits))
    return "\n".join(rows) if rows else "- (no activities were scheduled for this day)"


PHOTO_NOTE = ("\nLines like [photo: name.jpg] mark photos the supervisor attached. They are saved with "
              "the report already: do not say they are missing or ask whether to attach them.")


def build_daily_capture_messages(
    input_payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """(system, messages, max_tokens) for a daily_report_capture job.

    Expected input:
      {"report_date": "2026-10-02",
       "project_metadata": {...},
       "scheduled_tasks": [ ...payload["tasks"] from the synced report... ],
       "transcript": "<what the supervisor said>"}
    """
    payload = input_payload or {}
    meta = payload.get("project_metadata") or {}
    transcript = (payload.get("transcript") or "").strip()
    tasks = payload.get("scheduled_tasks") or []
    day = payload.get("report_date") or "(date not supplied)"

    head = [f"PROJECT: {meta.get('name') or '(unnamed project)'}"]
    if meta.get("address"):
        head.append(f"ADDRESS: {meta['address']}")
    head.append(f"REPORT DATE: {day}")

    body = [
        "\n".join(head),
        f"\nSCHEDULED FOR THIS DAY:\n{_agenda_lines(tasks)}",
    ]

    if not transcript:
        # Refusing rather than letting the model write a plausible day from the
        # schedule alone. A report generated from what was PLANNED, with no
        # account of what happened, is the most dangerous output this could
        # produce: it looks like a record and contains no observation.
        body.append(
            "\nNO TRANSCRIPT WAS PROVIDED.\n"
            "Return the JSON object with an empty \"payload\", every section "
            "listed in \"not_recorded\", and nothing in the other arrays. Do NOT "
            "describe what was scheduled as though it happened."
        )
    else:
        body.append(
            "\nWHAT THE SUPERVISOR SAID (speech-to-text, may be garbled):\n"
            f"\"\"\"\n{transcript}\n\"\"\""
        )
        if "[photo:" in transcript:
            # The app writes these markers when a photo is attached; the photo is
            # saved with the report. Without this the model reported attached
            # photos as missing (rollout test 2026-10-14).
            body.append(PHOTO_NOTE)

    return SYSTEM, [{"role": "user", "content": "\n".join(body)}], MAX_TOKENS


def parse_capture_result(text: str) -> dict[str, Any]:
    """Pull the proposal out of the model's reply.

    Tolerates a markdown fence because models add them despite instructions,
    but does NOT tolerate a missing or malformed object — a half-parsed report
    is worse than a failed one, because the half that parsed looks complete.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else ""
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model did not return a JSON object.")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("The model returned JSON that is not an object.")

    proposal = data.get("payload")
    if not isinstance(proposal, dict):
        proposal = {}

    # Normalise, so the UI can rely on the shape. A section the model omitted
    # stays omitted — that absence is the signal, not an oversight to fill in.
    not_recorded = [s for s in (data.get("not_recorded") or []) if s in SECTIONS]
    # Anything absent from the payload AND unnamed by the model is still
    # unknown, so it is added rather than silently treated as answered.
    for s in SECTIONS:
        if s not in proposal and s not in not_recorded:
            not_recorded.append(s)

    flagged = [f for f in (data.get("flagged_for_office") or []) if isinstance(f, dict)]
    unclear = [u for u in (data.get("unclear") or []) if isinstance(u, str)]

    return {
        "payload": proposal,
        "not_recorded": not_recorded,
        "flagged_for_office": flagged,
        "unclear": unclear,
    }
