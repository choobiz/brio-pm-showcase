"""The register: derived items, blocking first, never across a project boundary.

Two properties matter more than the rest:

  · an item must name its CONSEQUENCE, not just a count — a register full of
    trivia is a register nobody reads;
  · an item must never mention a project the caller cannot open.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.register import build_register, register_summary, BLOCKING, ATTENTION  # noqa: E402


def _project(db, name, *, ptype="residential_new", year=1995, status="preconstruction"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, type, building_age) "
                  "values (:i, :n, :s, :st, :t, :y)"),
               {"i": str(pid), "n": name, "s": f"p-{str(pid)[:8]}", "st": status,
                "t": ptype, "y": year})
    return pid


def _quantity(db, pid, status="candidate", qtype="floor_area_sf"):
    db.execute(_T("insert into quantity_overrides (id, project_id, discipline, quantity_type, "
                  "value, unit, source, status, machine_value) "
                  "values (:i, :p, 'architectural', :q, 100, 'sf', 'cad_extract', :s, 100)"),
               {"i": str(uuid4()), "p": str(pid), "q": qtype, "s": status})


def _cad(db, pid, filename="Plan.dxf", category="drawing"):
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, phase, size_bytes) "
                  "values (:i, :p, :f, :k, :c, 'design', 1024)"),
               {"i": str(uuid4()), "p": str(pid), "f": filename, "k": f"t/{uuid4()}", "c": category})


def _pack(db, pid, status="draft", version=1):
    db.execute(_T("insert into drawing_analysis_packs (id, project_id, version_number, status, payload) "
                  "values (:i, :p, :v, :s, '{}'::jsonb)"),
               {"i": str(uuid4()), "p": str(pid), "v": version, "s": status})


def _kinds(items):
    return {i["kind"] for i in items}


def _of(items, kind):
    return next(i for i in items if i["kind"] == kind)


# ── isolation ─────────────────────────────────────────────────────────────

def test_an_empty_scope_yields_nothing_not_everything(db_session):
    db = db_session
    pid = _project(db, "Hidden")
    _quantity(db, pid)
    db.flush()
    assert build_register(db, []) == []


def test_a_project_outside_scope_is_never_mentioned(db_session):
    db = db_session
    mine, theirs = _project(db, "Mine"), _project(db, "Theirs")
    _quantity(db, mine)
    _quantity(db, theirs)
    db.flush()

    items = build_register(db, [mine])
    assert len(items) == 1
    assert items[0]["project_id"] == str(mine)
    blob = repr(items)
    assert "Theirs" not in blob and str(theirs) not in blob


def test_narrowing_to_one_project_excludes_its_siblings(db_session):
    db = db_session
    a, b = _project(db, "Alpha"), _project(db, "Bravo")
    _quantity(db, a)
    _quantity(db, b)
    db.flush()

    items = build_register(db, [a, b], project_id=a)
    assert {i["project_id"] for i in items} == {str(a)}


# ── the estimate gate, both halves ────────────────────────────────────────

def test_unreviewed_quantities_block_and_say_what_they_block(db_session):
    db = db_session
    pid = _project(db, "Gate A")
    _quantity(db, pid, "candidate")
    _quantity(db, pid, "confirmed", "door_count")
    db.flush()

    it = _of(build_register(db, [pid]), "quantities_unreviewed")
    assert it["severity"] == BLOCKING
    assert it["count"] == 1
    assert "1 of 2" in it["why"]
    assert "blocked" in it["summary"].lower(), "an item must name its consequence"
    assert len(it["summary"]) < 70, "the always-visible line must stay scannable"
    assert it["evidence"]["pending"] == 1 and it["evidence"]["total"] == 2


def test_reviewed_quantities_raise_nothing(db_session):
    db = db_session
    pid = _project(db, "All reviewed")
    for s, q in (("confirmed", "a"), ("corrected", "b"), ("excluded", "c")):
        _quantity(db, pid, s, q)
    db.flush()
    assert "quantities_unreviewed" not in _kinds(build_register(db, [pid]))


def test_cad_with_no_analysis_is_raised_and_blocks_once_work_has_begun(db_session):
    """Superseded an earlier version of this test which asserted BLOCKING
    unconditionally. Severity now depends on whether anyone has reached the
    gate — see test_a_gate_nobody_has_reached_is_a_next_step_not_a_blocker."""
    db = db_session
    pid = _project(db, "Never analysed")
    _cad(db, pid)
    _quantity(db, pid, "confirmed")          # someone has started
    db.flush()

    it = _of(build_register(db, [pid]), "analysis_unsigned")
    assert it["severity"] == BLOCKING
    assert it["evidence"]["latest_status"] is None
    assert "have not been read" in it["why"]


def test_a_draft_analysis_still_blocks(db_session):
    db = db_session
    pid = _project(db, "Draft pack")
    _cad(db, pid)
    _pack(db, pid, "draft")
    db.flush()
    assert _of(build_register(db, [pid]), "analysis_unsigned")["evidence"]["latest_status"] == "draft"


def test_an_approved_analysis_clears_the_item(db_session):
    db = db_session
    pid = _project(db, "Signed")
    _cad(db, pid)
    _pack(db, pid, "approved")
    db.flush()
    kinds = _kinds(build_register(db, [pid]))
    assert "analysis_unsigned" not in kinds and "drawings_ready" not in kinds


def test_a_rebuild_re_blocks_because_only_the_latest_counts(db_session):
    """Matches the gate exactly: a newer draft supersedes an approved version."""
    db = db_session
    pid = _project(db, "Rebuilt")
    _cad(db, pid)
    _pack(db, pid, "approved", version=1)
    _pack(db, pid, "draft", version=2)
    db.flush()
    assert _of(build_register(db, [pid]), "analysis_unsigned")["evidence"]["latest_status"] == "draft"


def test_cad_filed_in_another_drawer_still_blocks(db_session):
    """Same predicate as the gate and the builder — they must not drift apart."""
    db = db_session
    pid = _project(db, "Misfiled")
    _cad(db, pid, "Structural.dxf", category="other")
    _quantity(db, pid, "confirmed")          # work started, so it blocks
    db.flush()
    assert "analysis_unsigned" in _kinds(build_register(db, [pid]))


def test_a_project_with_no_cad_is_not_asked_to_sign_anything(db_session):
    db = db_session
    pid = _project(db, "PDF only")
    _cad(db, pid, "A1.04 Floor Plan.pdf")
    db.flush()
    kinds = _kinds(build_register(db, [pid]))
    assert "analysis_unsigned" not in kinds and "drawings_ready" not in kinds


# ── metadata gaps ─────────────────────────────────────────────────────────

def test_a_missing_type_blocks_and_explains_the_consequence(db_session):
    db = db_session
    pid = _project(db, "Untyped", ptype=None)
    db.flush()
    it = _of(build_register(db, [pid]), "project_type_missing")
    assert it["severity"] == BLOCKING
    assert "generic category list" in it["why"]
    assert it["evidence"]["projects"] == ["Untyped"]


def test_a_missing_year_is_advisory_not_blocking(db_session):
    db = db_session
    pid = _project(db, "No year", year=None)
    db.flush()
    it = _of(build_register(db, [pid]), "building_age_missing")
    assert it["severity"] == ATTENTION
    assert "asbestos" in it["why"] and "lead paint" in it["why"]


def test_metadata_items_collapse_across_projects(db_session):
    """One item saying '3 projects', not three items saying 'one project'."""
    db = db_session
    pids = [_project(db, f"Untyped {i}", ptype=None) for i in range(3)]
    db.flush()
    it = _of(build_register(db, pids), "project_type_missing")
    assert it["count"] == 3 and it["project_id"] is None


# ── ordering and the badge ────────────────────────────────────────────────

def test_blocking_items_come_first(db_session):
    db = db_session
    pid = _project(db, "Mixed", year=None)
    _quantity(db, pid)
    db.flush()
    sev = [i["severity"] for i in build_register(db, [pid])]
    assert sev == sorted(sev, key=lambda s: 0 if s == BLOCKING else 1)
    assert sev[0] == BLOCKING


def test_the_badge_counts_blocking_only(db_session):
    db = db_session
    pid = _project(db, "Badge", year=None)
    _quantity(db, pid)
    db.flush()
    items = build_register(db, [pid])
    s = register_summary(items)
    assert s["blocking"] >= 1 and s["attention"] >= 1
    assert s["blocking"] < s["total"], "the badge must not count advisory items"
    assert s["projects_blocked"] == 1


def test_a_clean_project_produces_an_empty_register(db_session):
    db = db_session
    pid = _project(db, "Clean")
    _quantity(db, pid, "confirmed")
    db.flush()
    items = build_register(db, [pid])
    assert [i for i in items if i["project_id"] == str(pid)] == []


def test_every_item_carries_evidence_and_a_consequence(db_session):
    """The two rules the module is built on, asserted rather than assumed."""
    db = db_session
    pid = _project(db, "Contract", ptype=None, year=None)
    _quantity(db, pid)
    _cad(db, pid)
    db.flush()

    for it in build_register(db, [pid]):
        assert it["evidence"], f"{it['kind']} has no evidence"
        assert it["actions"], f"{it['kind']} offers nothing to do"
        assert it["severity"] in (BLOCKING, ATTENTION)
        assert 0 < len(it["summary"]) <= 80, f"{it['kind']} summary is not scannable"
        assert len(it["why"]) > 60, f"{it['kind']} does not explain itself when asked"


# ── signal quality: found by running against live data, not by reasoning ──

def test_an_archived_project_never_appears(db_session):
    """Live data produced blocking items on two occupancy permits from 2021 and
    2023 and a test fixture. An archived project is not being worked on, so
    every item on it is noise."""
    db = db_session
    live = _project(db, "Live one")
    dead = _project(db, "Archived one", ptype=None, year=None, status="archived")
    _quantity(db, dead)
    _cad(db, dead)
    _quantity(db, live)
    db.flush()

    items = build_register(db, [live, dead])
    blob = repr(items)
    assert "Archived one" not in blob and str(dead) not in blob
    assert any(i["project_id"] == str(live) for i in items)


def test_a_gate_nobody_has_reached_is_a_next_step_not_a_blocker(db_session):
    """CAD present, nothing started. Technically the gate is shut; nobody is
    standing at it. Calling that 'estimating is on hold' is true and useless."""
    db = db_session
    pid = _project(db, "Not started")
    _cad(db, pid)
    db.flush()

    it = _of(build_register(db, [pid]), "drawings_ready")
    assert it["severity"] == ATTENTION
    assert it["title"] == "Drawings ready to analyse"
    assert it["evidence"]["work_started"] is False
    assert "nothing is held up" in it["why"].lower()


def test_a_gate_someone_has_reached_is_blocking(db_session):
    db = db_session
    pid = _project(db, "Started")
    _cad(db, pid)
    _quantity(db, pid, "confirmed")          # work has begun
    db.flush()

    it = _of(build_register(db, [pid]), "analysis_unsigned")
    assert it["severity"] == BLOCKING
    assert it["evidence"]["work_started"] is True
    assert "blocked" in it["summary"].lower()


def test_metadata_sweeps_ignore_archived_projects(db_session):
    db = db_session
    live = _project(db, "Live untyped", ptype=None)
    _project(db, "Dead untyped", ptype=None, status="archived")
    db.flush()

    it = _of(build_register(db, [live]), "project_type_missing")
    assert it["count"] == 1 and it["evidence"]["projects"] == ["Live untyped"]


def test_identical_advisory_items_are_collected_not_repeated(db_session):
    """Three projects with unread drawings must be ONE item saying three, not
    three cards carrying the same sentence. The first build shipped the latter,
    and the advisory half of the page was 60% duplicate prose."""
    db = db_session
    pids = []
    for i in range(3):
        pid = _project(db, f"Ready {i}")
        _cad(db, pid)
        pids.append(pid)
    db.flush()

    ready = [i for i in build_register(db, pids) if i["kind"] == "drawings_ready"]
    assert len(ready) == 1, "one item per fact, not one per project"
    assert ready[0]["count"] == 3
    assert "3 projects" in ready[0]["summary"]
    for n in ("Ready 0", "Ready 1", "Ready 2"):
        assert n in ready[0]["why"], "the projects must still be named somewhere"


def test_global_items_never_repeat_a_visible_line(db_session):
    db = db_session
    pids = []
    for i in range(3):
        pid = _project(db, f"P{i}", ptype=None, year=None)
        _cad(db, pid)
        _quantity(db, pid)
        pids.append(pid)
    db.flush()

    globals_ = [i["summary"] for i in build_register(db, pids) if i["project_id"] is None]
    assert len(globals_) == len(set(globals_)), f"repeated global summaries: {globals_}"


def test_company_wide_items_stay_off_a_single_project_view(db_session):
    """AI output nobody has judged is not any one project's. Asked for one
    project, it read on that project's page as its own 3,348-item backlog."""
    db = db_session
    pid = _project(db, "Scoped")
    sv = uuid4()
    db.execute(_T("insert into skill_versions (id, skill_id, version, owner_namespace, status, "
                  "prompt_template, output_schema, examples, created_by) "
                  "values (:i, 'test.skill', '1', 'test', 'active', 'x', '{}'::jsonb, '[]'::jsonb, 'test')"),
               {"i": str(sv)})
    db.execute(_T("insert into skill_runs (skill_version_id, input, acceptance_status, edit_count) "
                  "values (:v, '{}'::jsonb, 'pending', 0)"), {"v": str(sv)})
    # Precondition: the item exists company-wide, so its absence below means something.
    assert "ai_output_unreviewed" in _kinds(build_register(db, [pid], company_wide=True))
    items = build_register(db, [pid], project_id=pid, company_wide=True)
    assert all(i["project_id"] == str(pid) for i in items), \
        [i["kind"] for i in items if i["project_id"] != str(pid)]


def test_a_manager_never_sees_the_company_wide_ai_backlog(db_session):
    """Amir, 2026-10-15: "3,360 AI results unjudged" showed on a manager's Home
    and in their Ask answer. It is a superadmin item."""
    db = db_session
    pid = _project(db, "Manager")
    sv = uuid4()
    db.execute(_T("insert into skill_versions (id, skill_id, version, owner_namespace, status, "
                  "prompt_template, output_schema, examples, created_by) "
                  "values (:i, 'test.skill2', '1', 'test', 'active', 'x', '{}'::jsonb, '[]'::jsonb, 'test')"),
               {"i": str(sv)})
    db.execute(_T("insert into skill_runs (skill_version_id, input, acceptance_status, edit_count) "
                  "values (:v, '{}'::jsonb, 'pending', 0)"), {"v": str(sv)})
    assert "ai_output_unreviewed" not in _kinds(build_register(db, [pid]))
    assert "ai_output_unreviewed" in _kinds(build_register(db, [pid], company_wide=True))
