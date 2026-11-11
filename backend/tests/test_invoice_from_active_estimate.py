"""Invoices derive from the ACTIVE estimate version only.

Amir's rule, 2026-09-04: an invoice is generated from the active estimate;
change orders are added and invoiced separately.

Two failure modes this guards:

1. Billing a superseded estimate. Estimate versions are immutable and a new one
   supersedes the last. An invoice seeded from v1 and created after v2 was
   activated bills a price the client never agreed to, and nothing in the data
   would show it later.
2. Leaking margin. BRIO estimates show the client subtotal, GST and total only.
   Overhead and profit must never appear as invoice lines.
"""
import pytest

from app.routers.invoices import (
    ESTIMATE_DERIVED_TYPES,
    PercentageWithoutBasis,
    _compute_totals,
    _line_items_from_estimate,
)


def test_change_order_is_exempt_from_the_active_estimate_rule():
    # A change order bills work beyond the estimated scope by definition.
    assert "change_order" not in ESTIMATE_DERIVED_TYPES
    assert {"progress", "tm", "retainer"} == ESTIMATE_DERIVED_TYPES


def test_lump_sum_estimate_bills_as_a_single_line():
    # The recovered 880 Harbour estimate: no per-category totals, one lump sum.
    payload = {
        "subtotal": 6655.0,
        "gst": 332.75,
        "categories": [
            {"name": "Demolition & Removal", "items": [{"name": "demo", "unitCost": None}]},
            {"name": "Drywall & Finishing", "items": []},
        ],
    }
    items = _line_items_from_estimate(payload)
    assert len(items) == 1, "a lump sum must not be split into an invented breakdown"
    assert items[0]["amount"] == 6655.0


def test_categories_with_totals_become_line_items():
    payload = {
        "subtotal": 300.0,
        "categories": [
            {"name": "Demolition", "total": 100.0},
            {"name": "Drywall", "total": 200.0},
            {"name": "Empty", "total": 0},
            {"name": "Unpriced", "total": None},
        ],
    }
    items = _line_items_from_estimate(payload)
    assert [i["description"] for i in items] == ["Demolition", "Drywall"]
    assert sum(i["amount"] for i in items) == 300.0


def test_overhead_and_profit_never_become_invoice_lines():
    payload = {
        "subtotal": 1000.0,
        "overhead": 150.0,
        "profit": 100.0,
        "categories": [{"name": "Framing", "total": 1000.0}],
    }
    items = _line_items_from_estimate(payload)
    descriptions = " ".join((i["description"] or "").lower() for i in items)
    assert "overhead" not in descriptions
    assert "profit" not in descriptions
    assert sum(i["amount"] for i in items) == 1000.0


def test_seeded_totals_reproduce_the_estimate_arithmetic():
    # 880 Harbour: 6655.00 subtotal, 332.75 GST, 6987.75 total.
    payload = {"lineItems": _line_items_from_estimate({"subtotal": 6655.0, "categories": []})}
    _compute_totals("progress", payload)
    assert payload["subtotal"] == 6655.0
    assert payload["gst"] == 332.75
    assert payload["total"] == 6987.75


def test_cedar_estimate_arithmetic():
    # 710 Cedar: 1856.25 subtotal, 92.81 GST, 1949.06 total.
    payload = {"lineItems": _line_items_from_estimate({"subtotal": 1856.25, "categories": []})}
    _compute_totals("progress", payload)
    assert payload["subtotal"] == 1856.25
    assert payload["gst"] == 92.81
    assert payload["total"] == 1949.06


def test_an_estimate_with_no_billable_amount_yields_nothing():
    assert _line_items_from_estimate({"subtotal": 0, "categories": []}) == []
    assert _line_items_from_estimate({}) == []


# --- "% of contract" lines -------------------------------------------------
# INV-2026-10059 was created on 2026-10-06 with one line reading
# "Milestone Achieved. / 20% of contract" and a total of 0.00. The progress form
# offers a "% of contract" column and invoice_pdf renders it, but _compute_totals
# only ever summed `amount`, so the percentage contributed nothing and the
# invoice took a number out of the sequence looking real and worth zero.

def test_a_percentage_line_bills_against_the_contract():
    payload = {"lineItems": [{"description": "Milestone", "percentage": 20}]}
    _compute_totals("progress", payload, 180_414.00)
    assert payload["lineItems"][0]["amount"] == 36_082.80
    assert payload["subtotal"] == 36_082.80
    assert payload["gst"] == 1_804.14
    assert payload["total"] == 37_886.94


def test_the_basis_is_the_subtotal_not_the_gst_inclusive_total():
    """INV-1234-001 billed 10% as 18,041.90 on a 180,419 subtotal, then added
    GST. Taking the percentage off the tax-inclusive total would overbill 5%."""
    payload = {"lineItems": [{"description": "Deposit", "percentage": 10}]}
    _compute_totals("progress", payload, 180_419.00)
    assert payload["lineItems"][0]["amount"] == 18_041.90
    assert payload["total"] == 18_944.00


def test_an_explicit_amount_beats_a_percentage():
    payload = {"lineItems": [{"description": "Agreed", "percentage": 20, "amount": 5_000}]}
    _compute_totals("progress", payload, 180_414.00)
    assert payload["lineItems"][0]["amount"] == 5_000
    assert payload["subtotal"] == 5_000.00


def test_a_percentage_with_no_contract_refuses_rather_than_billing_zero():
    payload = {"lineItems": [{"description": "Milestone", "percentage": 20}]}
    with pytest.raises(PercentageWithoutBasis):
        _compute_totals("progress", payload, None)


def test_a_zero_percentage_line_is_not_a_percentage_line():
    """0% is indistinguishable from blank here, and must not raise."""
    payload = {"lineItems": [{"description": "Nil", "percentage": 0, "amount": 250}]}
    _compute_totals("progress", payload, None)
    assert payload["subtotal"] == 250.00


# --- adjustments -----------------------------------------------------------
# An adjustment corrects the ACCOUNT, not the work: GST missed on an earlier
# invoice, a credit, a holdback. It is added after GST and never taxed, and it
# must not move the subtotal, which is the value of the work billed here.

def test_an_adjustment_is_added_after_gst_and_not_taxed():
    """The case this was built for: GST of 902.10 was not collected on
    INV-1234-001 and is being picked up on the next invoice. Charging 5% on it
    would be charging tax on tax."""
    payload = {
        "lineItems": [{"description": "Milestone 2", "amount": 36_082.86}],
        "adjustments": [
            {"description": "GST not charged on INV-1234-001", "amount": 902.10}
        ],
    }
    _compute_totals("progress", payload)
    assert payload["subtotal"] == 36_082.86          # unmoved
    assert payload["gst"] == 1_804.14                # 5% of the subtotal only
    assert payload["adjustmentsTotal"] == 902.10
    assert payload["total"] == 38_789.10             # 36,082.86 + 1,804.14 + 902.10


def test_an_adjustment_can_be_negative():
    """A credit and a holdback deduction are the same mechanism, sign flipped."""
    payload = {
        "lineItems": [{"description": "Milestone", "amount": 10_000}],
        "adjustments": [{"description": "10% holdback", "amount": -1_000}],
    }
    _compute_totals("progress", payload)
    assert payload["subtotal"] == 10_000.00
    assert payload["gst"] == 500.00
    assert payload["total"] == 9_500.00


def test_adjustments_do_not_change_the_percentage_basis():
    payload = {
        "lineItems": [{"description": "Milestone 2", "percentage": 20}],
        "adjustments": [{"description": "GST catch-up", "amount": 902.10}],
    }
    _compute_totals("progress", payload, 180_414.00)
    assert payload["lineItems"][0]["amount"] == 36_082.80
    assert payload["subtotal"] == 36_082.80
    assert payload["total"] == 38_789.04


def test_no_adjustments_is_unchanged_arithmetic():
    payload = {"lineItems": [{"description": "x", "amount": 1_000}]}
    _compute_totals("progress", payload)
    assert payload["adjustmentsTotal"] == 0
    assert payload["total"] == 1_050.00


def test_an_adjustment_cannot_disguise_unpriced_line_items():
    """_refuse_zero_total checks the SUBTOTAL. Line items worth nothing plus a
    positive adjustment produce a non-zero total and must still be refused."""
    from app.routers.invoices import _refuse_zero_total
    from fastapi import HTTPException

    payload = {
        "lineItems": [{"description": "Milestone", "amount": 0}],
        "adjustments": [{"description": "GST catch-up", "amount": 902.10}],
    }
    _compute_totals("progress", payload)
    assert payload["total"] == 902.10
    with pytest.raises(HTTPException):
        _refuse_zero_total(payload)
