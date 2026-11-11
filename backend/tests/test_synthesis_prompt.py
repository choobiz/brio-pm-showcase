"""What the synthesis prompt promises, held in place by tests.

These assert the SHAPE of the request, not the model's answer. That is the only
half that is deterministic — but it is the half that decides whether a wrong
answer is even possible. A prompt that forgets to attach the documents, or that
leaves the model free to describe "a typical permit set" when nothing was
supplied, produces a confident fabrication every time and no amount of reading
the output will tell you why.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.ai_job import AI_JOB_KINDS  # noqa: E402
from app.services.ai import build_messages_for_job  # noqa: E402
from app.services.quantity_gate import GATED_JOB_KINDS  # noqa: E402
from app.services.synthesis import build_synthesis_messages  # noqa: E402

KIND = "document_synthesis"


def _payload(**kw):
    base = {
        "project_metadata": {"name": "1234 Example Way", "address": "1234 Example Way"},
        "documents": [
            {"caption": "A-200 Plan.pdf", "media_type": "application/pdf", "data": "QQ=="},
            {"caption": "S-101 Structural.pdf", "media_type": "application/pdf", "data": "Qg=="},
        ],
        "images": [{"caption": "site.jpg", "media_type": "image/jpeg", "data": "Qw=="}],
        "text_documents": [{"caption": "Spec.docx", "text": "Section 09 finishes."}],
    }
    base.update(kw)
    return base


def _text(messages):
    out = []
    for m in messages:
        for b in m["content"]:
            if b.get("type") == "text":
                out.append(b["text"])
    return "\n".join(out)


def test_the_kind_is_registered_and_routed():
    assert KIND in AI_JOB_KINDS, "kind missing from the enum tuple"
    system, messages, _ = build_messages_for_job(KIND, _payload())
    assert system and "missing from this set" in system
    assert messages, "no messages built"


def test_synthesis_is_not_behind_the_estimate_gate():
    """It is read-only prose. Gating it would mean you cannot ask what is
    missing until you have finished reviewing quantities — exactly backwards."""
    assert KIND not in GATED_JOB_KINDS


def test_every_supplied_file_is_actually_attached():
    """The failure that looks like a thoughtful answer: the model is asked about
    documents it was never sent, and obliges."""
    _, messages, _ = build_synthesis_messages(_payload())
    blocks = messages[0]["content"]
    assert sum(1 for b in blocks if b.get("type") == "document") == 2
    assert sum(1 for b in blocks if b.get("type") == "image") == 1
    assert "Section 09 finishes." in _text(messages)


def test_the_manifest_names_every_file_and_counts_them():
    """So a reader can check "read 9 of 11" against what they selected. A file
    dropped on the way in is otherwise invisible."""
    _, messages, _ = build_synthesis_messages(_payload())
    t = _text(messages)
    assert "FILES PROVIDED (4)" in t
    for name in ("A-200 Plan.pdf", "S-101 Structural.pdf", "site.jpg", "Spec.docx"):
        assert name in t, f"{name} not named in the manifest"


def test_an_empty_set_forbids_describing_a_typical_package():
    """The highest-risk case. With nothing attached, a general-knowledge answer
    about what a permit set normally contains reads exactly like a finding."""
    _, messages, _ = build_synthesis_messages(_payload(documents=[], images=[], text_documents=[]))
    t = _text(messages)
    assert "FILES PROVIDED (0)" in t
    assert "No files were provided" in t
    assert "Do not describe a typical package" in t
    blocks = messages[0]["content"]
    assert not [b for b in blocks if b.get("type") in ("document", "image")]


def test_a_question_is_carried_through_verbatim():
    q = "Does the structural package cover the garage slab?"
    _, messages, _ = build_synthesis_messages(_payload(question=q))
    assert q in _text(messages)


def test_absence_is_phrased_as_not_in_this_set_never_as_missing():
    """"Missing" asserts the file does not exist. The office may hold it and
    simply not have selected it here — a distinction that decides whether
    someone phones a consultant to complain."""
    system, _, _ = build_synthesis_messages(_payload())
    assert "NOT IN THIS SET" in system
    assert 'NEVER as "missing"' in system


def test_findings_must_name_their_sources():
    system, _, _ = build_synthesis_messages(_payload())
    assert "names the documents it came from" in system
    assert "cannot source is not a finding" in system


def test_the_prompt_forbids_inventing_numbers_and_leaving_its_lane():
    system, _, _ = build_synthesis_messages(_payload())
    low = system.lower()
    assert "never state a dimension, quantity, specification or price as fact" in low
    assert "not writing a scope, an estimate, or a quantity takeoff" in low
    # It reports; it does not tell the office what to do commercially.
    assert "do not recommend commercial action" in low


def test_missing_project_metadata_is_stated_not_omitted():
    """Consistent with Correction 4 — unknown is said, not left blank for the
    model to fill in with an assumption."""
    _, messages, _ = build_synthesis_messages(_payload(project_metadata={}))
    assert "No project metadata was recorded" in _text(messages)


def test_a_file_that_extracted_to_nothing_is_named_as_unread():
    """Found by this test: the manifest listed it as provided while the content
    block skipped it, so the model was told about a document it never got. It is
    now excluded from the count and named explicitly as unreadable."""
    _, messages, _ = build_synthesis_messages(
        _payload(documents=[], images=[],
                 text_documents=[{"caption": "Scan.docx", "text": "   "},
                                 {"caption": "Spec.docx", "text": "Real content."}])
    )
    t = _text(messages)
    assert "FILES PROVIDED (1)" in t, "an unreadable file must not be counted"
    assert "SELECTED BUT UNREADABLE (1): Scan.docx" in t
    assert "Scan.docx (extracted text)" not in t
    assert "Real content." in t
