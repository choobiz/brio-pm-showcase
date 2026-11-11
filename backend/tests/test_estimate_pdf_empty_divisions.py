"""Client and internal estimate PDFs leave out divisions with nothing priced.

456 W Sample corridor v009 (2026-11-06): the "Client: detailed" PDF listed
eight $0 divisions (03, 04, 05, 07, 10, 11, 12, 22) with "Subtotal $0.00".
"""
from app.services.estimate_pdf import build_client_estimate_html, build_estimate_html

PAYLOAD = {
    "detailLevel": "detailed",
    "overhead": 8, "profit": 20,
    "categories": [
        {"name": "09 Finishes", "description": "Carpet and wallcovering", "total": 1000,
         "items": [{"name": "Carpet tile", "qty": 1, "labor": 400, "materials": 600}]},
        {"name": "03 Concrete", "description": "No scope identified for this division.", "total": 0, "items": []},
        {"name": "22 Plumbing", "description": "", "total": 0,
         "items": [{"name": "Placeholder", "labor": 0, "materials": 0}]},
    ],
}
PROJECT = {"name": "Test project", "address": "1 Test St"}
CLIENT = {"name": "Test client"}


def test_client_detailed_pdf_drops_zero_divisions():
    html = build_client_estimate_html(payload=PAYLOAD, project=PROJECT, client=CLIENT)
    assert "09 Finishes" in html
    assert "03 Concrete" not in html
    assert "22 Plumbing" not in html


def test_client_summary_pdf_drops_zero_divisions():
    html = build_client_estimate_html(payload=PAYLOAD, project=PROJECT, client=CLIENT, summary=True)
    assert "09 Finishes" in html
    assert "03 Concrete" not in html


def test_internal_estimate_pdf_drops_zero_divisions():
    html = build_estimate_html(payload=PAYLOAD, project=PROJECT, client=CLIENT)
    assert "09 Finishes" in html
    assert "03 Concrete" not in html
    assert "22 Plumbing" not in html


def test_priced_division_with_zero_total_but_money_lines_is_kept():
    p = {**PAYLOAD, "categories": [{"name": "26 Electrical", "description": "", "total": 0,
                                    "items": [{"name": "Fixtures", "labor": 100, "materials": 50}]}]}
    html = build_client_estimate_html(payload=p, project=PROJECT, client=CLIENT)
    assert "26 Electrical" in html
