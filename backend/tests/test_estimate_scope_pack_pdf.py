"""Estimate PDF: scope + material selections (no prices) + lump sum (Amir, 2026-10-27).

The client gets one longer document: the project's scope, then the finishes
and fixtures we selected with NO prices (to confirm or change), then every
estimate line with no prices and one lump-sum price.

Pinned:
  1. The selections section lists the selected items with a confirm column
     and no dollar amounts; only the closing totals carry money.
  2. Every estimate line is listed; no per-trade price.
  3. The route puts the estimate's own scope in front (estimate_versions.scope_id),
     falls back to the project's only scope, and otherwise refuses (409) rather
     than take the latest: 456 W Sample has a suite scope and a corridor scope.
"""
from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import estimate_selection_pdf as S

PAYLOAD = {
    "reference": "456SA-CORRIDOR", "overhead": 12, "profit": 15,
    "categories": [
        {"name": "09 Finishes", "total": 30000, "items": [
            {"name": "Carpet tile, Interface Human Nature, 687 sf × $9.50/sf", "labor": 4000, "materials": 6500, "qty": 687},
            {"name": "Vinyl wallcovering to match 7th floor", "labor": 3000, "materials": 4000},
            {"name": "Drywall patching", "labor": 2000, "materials": 500},
            {"name": "Contingency", "labor": 0, "materials": 1000},
        ]},
        {"name": "26 Electrical", "total": 12000, "items": [
            {"name": "2x2 LED light fixture, recessed", "labor": 2000, "materials": 5000, "qty": 24},
            {"name": "Branch circuit rework", "labor": 3000, "materials": 2000},
        ]},
    ],
    "exclusions": ["Washroom work"],
}
PROJECT = {"name": "456 W Sample", "address": "456 W Sample St"}


def _section(html: str, title: str) -> str:
    i = html.index(title)
    j = html.find("<section", i)
    return html[i:j if j > 0 else None]


def test_selections_are_listed_with_no_prices():
    html = S.build_selection_html(PAYLOAD, PROJECT, {}, "scopepack")
    sel = _section(html, "Material Selections: Please Confirm")
    assert "Carpet tile, Interface Human Nature" in sel and "2x2 LED light fixture" in sel
    assert "OK, or change to" in sel
    assert "$" not in sel, "no prices next to the selections"
    assert "Drywall patching" not in sel, "commodity work is not a client selection"


def test_every_line_is_listed_and_only_the_lump_sum_carries_money():
    html = S.build_selection_html(PAYLOAD, PROJECT, {}, "scopepack")
    detail = _section(html, "Estimate Detail")
    for line in ("Drywall patching", "Branch circuit rework", "Vinyl wallcovering"):
        assert line in detail
    assert "$" not in detail, "no line or trade prices"
    assert "$9.50" not in html, "unit rates never reach the client"
    totals = html[html.index('class="sec totals"'):]
    assert "Lump-Sum Price" in totals and re.search(r"\$[\d,]+", totals)


def test_the_existing_lump_sum_edition_is_unchanged():
    html = S.build_selection_html(PAYLOAD, PROJECT, {}, "lumpsum")
    assert "Please Confirm" not in html and "Scope of Work" in html


# ── route: which scope goes in front ───────────────────────────────────────
# Amir, 2026-10-27: the estimate already belongs to a scope (the one it was
# generated from, inherited by its revisions), so the PDF uses that one. It asks
# only when the estimate has no scope and the project has several.

PID, VID = uuid4(), uuid4()
SCOPE_A, SCOPE_B, OTHER = uuid4(), uuid4(), uuid4()


class _Res:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, scopes, own=None):
        self.scopes, self.own = scopes, own

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM estimate_versions ev" in sql:
            return _Res([(PAYLOAD, "456SA-CORRIDOR", "456 W Sample", "456 W Sample St", "commercial_ti", 687,
                          None, None, None, None)])
        if "SELECT scope_id FROM estimate_versions" in sql:
            return _Res([self.own])
        if "FROM scopes WHERE id = :s" in sql:
            return _Res([r for r in self.scopes if r[0] == params["s"]])
        if "FROM scopes WHERE project_id" in sql:
            return _Res(self.scopes)
        raise AssertionError(sql[:80])


@pytest.fixture
def rendered(monkeypatch):
    seen = {}
    import app.services.package_pdf as PK
    import app.services.scope_pdf as SP
    monkeypatch.setattr(S, "render_client_selection_pdf", lambda payload, project, client, mode: b"EST:" + mode.encode())
    monkeypatch.setattr(SP, "render_full_scope_pdf",
                        lambda scopes, project, client: (seen.setdefault("scope", scopes[0]["payload"]), b"SCOPE")[1])
    monkeypatch.setattr(PK, "merge_pdfs", lambda parts: b"|".join(parts))
    return seen


def _call(db, scope_id=None):
    from app.routers.workbench import estimate_version_selection_pdf
    return estimate_version_selection_pdf(PID, VID, mode="scopepack", scope_id=scope_id, db=db, _=None)


def _scopes():
    now = datetime(2026, 10, 22)
    return [(SCOPE_B, 2, {"summary": "corridor"}, now), (SCOPE_A, 1, {"summary": "suite"}, now)]


def test_the_estimates_own_scope_goes_in_front_without_asking(rendered):
    resp = _call(FakeDB(_scopes(), own=SCOPE_A))
    assert resp.body == b"SCOPE|EST:scopepack"
    assert rendered["scope"] == {"summary": "suite"}, "its own scope, not the latest"
    assert "Scope_and_Price" in resp.headers["content-disposition"]


def test_an_unlinked_estimate_with_several_scopes_is_asked_not_guessed(rendered):
    with pytest.raises(HTTPException) as e:
        _call(FakeDB(_scopes()))
    assert e.value.status_code == 409 and "choose" in e.value.detail


def test_an_unlinked_estimate_with_one_scope_uses_it(rendered):
    resp = _call(FakeDB(_scopes()[:1]))
    assert resp.body == b"SCOPE|EST:scopepack" and rendered["scope"] == {"summary": "corridor"}


def test_no_scope_is_refused(rendered):
    with pytest.raises(HTTPException) as e:
        _call(FakeDB([]))
    assert e.value.status_code == 409 and "no scope" in e.value.detail


def test_a_chosen_scope_wins_and_must_be_on_the_project(rendered):
    _call(FakeDB(_scopes(), own=SCOPE_A), scope_id=SCOPE_B)
    assert rendered["scope"] == {"summary": "corridor"}
    with pytest.raises(HTTPException) as e:
        _call(FakeDB(_scopes()), scope_id=OTHER)
    assert e.value.status_code == 404


def test_the_page_is_told_the_same_scope_the_pdf_uses():
    from app.routers.workbench import estimate_version_pdf_scope
    out = estimate_version_pdf_scope(PID, VID, db=FakeDB(_scopes(), own=SCOPE_A), _=None)
    assert out["scope"]["id"] == str(SCOPE_A) and out["scope_count"] == 2
    assert estimate_version_pdf_scope(PID, VID, db=FakeDB(_scopes()), _=None) == {"scope": None, "scope_count": 2}


# ── dollar figures inside line names never reach the client ────────────────

@pytest.mark.parametrize("raw,want", [
    ("Elevator surround millwork: fabrication allowance ($3,200 ea) and install",
     "Elevator surround millwork: fabrication allowance and install"),
    ("Framing per owner markup 2026-06-02 (target raw $14,000) [v009]", "Framing per owner markup 2026-06-02 [v009]"),
    ("Tile per owner markup: + (6x8 ft); line total $5,140]", "Tile per owner markup: + (6x8 ft)"),
    ("Appliances install per owner direction ($35–40K range, midpoint $37.5K)", "Appliances install per owner direction"),
    ("Trade 09 value engineering (-$18,000; specific line reductions)", "Trade 09 value engineering"),
    ("Owner allowance budget $2,000 for signage", "Owner allowance for signage"),
    ("Carpet tile — 687 sf × $9.50/sf installed", "Carpet tile — 687 sf installed"),
    ('Paint 2 coats, 24" x 8" @ 24" O/C, ~250 sqft', 'Paint 2 coats, 24" x 8" @ 24" O/C, ~250 sqft'),
])
def test_line_names_lose_their_dollar_figures(raw, want):
    """2026-10-27: 19 lines across the 11 current estimates printed internal
    figures ("target raw $65,000", "line total $1,500") on client PDFs."""
    got = S._client_item_name(raw)
    assert got == want and "$" not in got


def test_the_other_client_editions_drop_them_too():
    from app.services.estimate_pdf import _client_item_name as detailed_name
    assert detailed_name("Framing per owner markup (target raw $14,000) [v009]") == "Framing per owner markup [v009]"
    assert detailed_name("Overhead door, 9x7") == "Overhead door, 9x7"
