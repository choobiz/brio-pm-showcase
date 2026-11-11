"""Filename auto-categorisation heuristics (`app.routers.documents`).

These are pure-logic tests — no DB, no R2. They lock in the consultant /
accounting filename rules added 2026-08 and, just as importantly, the negative
cases: short tokens like "TI", "bid" and sheet codes like "E-1" are easy to make
fire inside unrelated words, and the ordering between the BCBC Schedule rules
and everything else is load-bearing.

Every assertion names the exact expected category. When a filename is expected
to keep its *pre-existing* category (or stay `other`), that is called out — a
change there is a regression, not an improvement.
"""

from __future__ import annotations

import pytest

from app.routers.documents import _safe_filename, _suggest_category


# The inbound-email pipeline (app/services/inbound_attachments.py) categorises
# the *sanitised* filename, where spaces and commas have become "_". "_" is a
# word character, so r"\belec\b" cannot fire on "..._Elec.pdf" — every rule has
# to hold for both spellings. Each case below is checked raw AND sanitised.
def _both(filename: str) -> tuple[str, str]:
    return _suggest_category(filename, None), _suggest_category(_safe_filename(filename), None)


def assert_category(filename: str, expected: str) -> None:
    raw, safe = _both(filename)
    assert raw == expected, f"raw {filename!r} → {raw}, expected {expected}"
    assert safe == expected, (
        f"sanitised {_safe_filename(filename)!r} → {safe}, expected {expected}"
    )


# ── the four real inbound-email filenames (verified misfiled as `other`) ──

REAL_FILENAMES = [
    ("PO-124225-A-ME - 7580 and 7590 6th Street, Burnaby - Elec.pdf", "drawing"),
    ("PO-124225-A-ME - 7580 and 7590 6th Street, Burnaby - MECH.pdf", "drawing"),
    ("PO-124225-A-ME - 7580 and 7590 6th Street, Burnaby - TI.pdf", "drawing"),
    ("BCBC 2024 Schedule B - MECH and ELEC.pdf", "permit"),
]


@pytest.mark.parametrize("filename,expected", REAL_FILENAMES)
def test_real_consultant_filenames(filename, expected):
    assert_category(filename, expected)


# ── rule 1 — discipline tags → drawing ───────────────────────────────────

DISCIPLINE_CASES = [
    "Project - MECH.pdf",
    "Project - Mech.pdf",
    "Project - Mechanical.pdf",
    "Project - ELEC.pdf",
    "Project - Elec.pdf",
    "Project - Electrical.pdf",
    "Project - STRUCT.pdf",
    "Project - Struct.pdf",
    "Project - Structural.pdf",
    "Project - ARCH.pdf",
    "Project - Arch.pdf",
    "Project - Architectural.pdf",
    "Project - PLUMB.pdf",
    "Project - Plumb.pdf",
    "Project - Plumbing.pdf",
    "Project - CIVIL.pdf",
    "Project - Civil.pdf",
    "Project - TI.pdf",
    "Project - Ti.pdf",
    "Project - Tenant Improvement.pdf",
    "Project - Tenant Improvements.pdf",
]


@pytest.mark.parametrize("filename", DISCIPLINE_CASES)
def test_discipline_suffix_is_a_drawing(filename):
    assert_category(filename, "drawing")


def test_discipline_tag_mid_name():
    assert_category("MECH - 123 Main St - Issued Set.pdf", "drawing")
    assert_category("7580 6th St TI package.pdf", "drawing")


# ── rule 2 — sheet codes → drawing ───────────────────────────────────────

SHEET_CODES = ["A-1", "A-101", "S-2", "M-3", "E-1", "P-2", "A-01", "M-100"]


@pytest.mark.parametrize("code", SHEET_CODES)
def test_sheet_code_is_a_drawing(code):
    assert_category(f"{code}.pdf", "drawing")
    assert_category(f"7580 6th Street - {code} Issued.pdf", "drawing")


# ── rule 3 — BCBC letters of assurance → permit ──────────────────────────

SCHEDULE_CASES = [
    "Schedule A.pdf",
    "Schedule B.pdf",
    "Schedule C-1.pdf",
    "Schedule C-2.pdf",
    "BCBC 2024 Schedule B.pdf",
    "Letter of Assurance.pdf",
    "Letters of Assurance - 7580 6th St.pdf",
]


@pytest.mark.parametrize("filename", SCHEDULE_CASES)
def test_schedule_and_assurance_are_permits(filename):
    assert_category(filename, "permit")


# ── ordering — rule 3 beats rules 1, 4 and 5 ─────────────────────────────


def test_schedule_beats_discipline():
    # The critical case: contains "Schedule B" AND "MECH"/"ELEC".
    assert_category("BCBC 2024 Schedule B - MECH and ELEC.pdf", "permit")
    assert_category("Schedule C-2 - Structural Field Review.pdf", "permit")


def test_schedule_beats_quote_and_invoice():
    assert_category("Schedule B - Estimate.pdf", "permit")
    assert_category("Schedule A - Invoice 1042.pdf", "permit")
    assert_category("Schedule B - Contractor Statement.pdf", "permit")
    assert_category("Letter of Assurance - Tender Set.pdf", "permit")


# ── rule 4 — quotes / estimates / bids → report ──────────────────────────

QUOTE_CASES = [
    "Quote.pdf",
    "Quotes - Drywall.pdf",
    "Quotation 8823.pdf",
    "Estimate.pdf",
    "Estimates - Rev A.pdf",
    "Proposal.pdf",
    "Tender.pdf",
    "Invitation to Tender.pdf",
    "Bid.pdf",
    "Bids - Trades.pdf",
    "Rebid.pdf",
    "Re-bid Package.pdf",
]


@pytest.mark.parametrize("filename", QUOTE_CASES)
def test_quotes_and_estimates_are_reports(filename):
    assert_category(filename, "report")


# ── rule 5 — invoices → invoice_legacy ───────────────────────────────────

INVOICE_CASES = [
    "Invoice.pdf",
    "Invoice 1042 - 7580 6th St.pdf",
    "Invoices - March.pdf",
    "INV-1042.pdf",
    "INV 1042.pdf",
    "INV_2291 Trades.pdf",
    "Receipt.pdf",
    "Receipts - Materials.pdf",
    "Bank Statement - July 2026.pdf",
    "Owner Statement.pdf",
]


@pytest.mark.parametrize("filename", INVOICE_CASES)
def test_invoices_and_money_statements(filename):
    assert_category(filename, "invoice_legacy")


def test_tender_beats_inv_prefix():
    # "Invitation to tender" must be a report, never an invoice.
    assert_category("Invitation to Tender - 7580 6th St.pdf", "report")


# ── negatives — the false positives these rules could easily create ──────


@pytest.mark.parametrize(
    "filename,expected",
    [
        # "TI" must not fire inside unrelated words.
        ("TITLE-SHEET.pdf", "other"),
        ("Title Block Standards.pdf", "other"),
        ("Notice of Completion.pdf", "other"),
        ("Multiple Units Summary.pdf", "other"),
        ("Estimating Notes.pdf", "other"),
        # Single-letter sheet codes must not swallow these.
        ("COVID-19 Protocol.pdf", "other"),
        ("Rev-2.pdf", "other"),
        ("Phase-1.pdf", "other"),
        ("Unit-3.pdf", "other"),
        ("CO-4.pdf", "other"),
        ("PO-124225.pdf", "other"),
        ("E-2024 Summary.pdf", "other"),  # 4 digits — not a sheet code
        # "bid" needs a boundary.
        ("BIDDING-SITE.pdf", "other"),
        ("Forbidden Zone Notes.pdf", "other"),
        # "INV-" must not fire on these.
        ("Inventory List.pdf", "other"),
        # "statement" — the two construction senses stay out of billing.
        ("Method Statement.pdf", "other"),
        ("Statement of Work.pdf", "other"),
        # Discipline word boundaries.
        ("Research Notes.pdf", "other"),
        ("Plumber Callout Log.pdf", "other"),
        # "Schedule" alone is a programme, not a letter of assurance.
        ("Construction Schedule.pdf", "other"),
        ("Schedule of Values.pdf", "other"),
        ("Baseline Schedule Rev 3.pdf", "other"),
    ],
)
def test_negative_cases_stay_uncategorised(filename, expected):
    assert_category(filename, expected)


@pytest.mark.parametrize(
    "filename,expected",
    [
        # Pre-existing rules that must keep their current category.
        ("SITE-PLAN.pdf", "drawing"),
        ("Floor Plan Level 2.pdf", "drawing"),
        ("Structural Drawings.dwg", "drawing"),
        ("Geotechnical Report.pdf", "report"),
        ("Investigation Report.pdf", "report"),
        ("Hazmat Survey.pdf", "report"),
        ("CCDC 2 Signed.pdf", "contract"),
        ("Scope of Work.pdf", "scope"),
        ("Scope and Estimate.pdf", "scope"),  # existing `scope` wins over new `estimate`
        ("Building Permit Application.pdf", "permit"),
        ("Business Licence.pdf", "permit"),
    ],
)
def test_existing_rules_unchanged(filename, expected):
    # Raw name only. The pre-existing entries use r"\b", which cannot fire once
    # `_safe_filename` has turned the spaces into underscores — a known,
    # pre-existing gap that this change deliberately does not touch (only the
    # newly appended rules are underscore-safe).
    assert _suggest_category(filename, None) == expected


# ── photo handling must be untouched ─────────────────────────────────────


def test_image_mime_short_circuits_before_any_pattern():
    # Even a filename that would otherwise hit the Schedule → permit rule.
    assert _suggest_category("BCBC Schedule B.jpg", "image/jpeg") == "photo"
    assert _suggest_category("Invoice 1042.png", "image/png") == "photo"


def test_photo_by_extension_still_wins_over_the_new_rules():
    assert_category("site-photo.JPG", "photo")
    assert_category("IMG_2291.png", "photo")
    # The photo-by-extension rule sits above the newly appended rules, so an
    # image extension beats "Invoice" / "Estimate" / a sheet code.
    assert_category("Invoice 1042.jpeg", "photo")
    assert_category("Estimate.png", "photo")
    assert_category("A-101.jpg", "photo")


def test_drawing_keywords_still_outrank_photo_extension():
    # Pre-existing precedence, unchanged: the discipline keyword entries appear
    # above the photo-by-extension entry, so "MECH riser.png" was — and stays —
    # a drawing.
    assert _suggest_category("MECH riser.png", None) == "drawing"
