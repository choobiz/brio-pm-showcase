"""Render an Estimate PDF — BRIO-branded, via WeasyPrint.

Ports brio-admin/templates/estimate.mjs: the 4 detail levels (summary,
standard, detailed, fully_itemized) and — critically — the margin model.
The model emits BASE costs; overhead + profit are folded into each category
total via a multiplier so the client sees a single all-in number per category.
Overhead and profit are NEVER rendered as separate rows (BRIO estimate rule).
"""

from __future__ import annotations
from app.company import FOOTER_CONTACT, LEGAL_NAME, LETTERHEAD_HTML

import html
import re
from datetime import date, datetime
from typing import Any

from app.services.pdf_brand import logo_data_url

# weasyprint imported lazily inside render_estimate_pdf() — see scope_pdf.py.

PRIMARY = "#0068A0"
ACCENT = "#1D3146"

_CSS = """
@page { size: Letter; margin: 14mm 16mm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
  color: #1D3146; line-height: 1.55; font-size: 10.5pt;
}
h1, h2, h3 { font-family: 'Sora', 'Helvetica Neue', Arial, sans-serif; }
.logo-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 14px; border-bottom: 2.5px solid #0068A0; margin-bottom: 26px;
}
.logo-img { height: 38px; }
.company-info { text-align: right; font-size: 8pt; color: #6B7280; line-height: 1.5; }
h1 { font-size: 21pt; font-weight: 700; color: #0068A0; margin-bottom: 3px; letter-spacing: -0.5px; }
.subtitle { font-size: 11pt; color: #6B7280; margin-bottom: 22px; }
h2 {
  font-size: 12.5pt; font-weight: 600; color: #0068A0;
  margin-top: 24px; margin-bottom: 9px; padding-bottom: 5px;
  border-bottom: 1px solid #D1E3F0;
}
h3 { font-size: 11pt; font-weight: 600; color: #1D3146; margin-top: 16px; margin-bottom: 6px; }
p { margin-bottom: 7px; }
ul { margin-bottom: 7px; padding-left: 20px; }
li { margin-bottom: 3px; }
.info-grid { display: flex; gap: 12px; margin: 12px 0 4px 0; }
.info-box {
  flex: 1; background: #F0F7FC; border: 1px solid #D1E3F0;
  padding: 12px 16px; font-size: 9.5pt;
}
.info-box .label { font-weight: 600; color: #0068A0; display: inline-block; min-width: 95px; }
.info-box .row { margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 14px 0; font-size: 10pt; }
thead th {
  background: #F0F7FC; font-weight: 600; text-align: left; padding: 7px 10px;
  border-bottom: 2px solid #0068A0; color: #1D3146; font-size: 8.5pt;
  text-transform: uppercase; letter-spacing: 0.3px;
}
td { padding: 6px 10px; border-bottom: 1px solid #E5E7EB; vertical-align: top; }
tr:nth-child(even) td { background: #FAFBFC; }
.text-right { text-align: right; }
.text-center { text-align: center; }
.subtotal-row td {
  font-weight: 600; border-top: 1px solid #D1E3F0; background: transparent;
}
.total-row td {
  font-weight: 700; border-top: 2px solid #0068A0; border-bottom: none;
  background: #F0F7FC; font-size: 11pt;
}
.totals { width: 340px; margin-left: auto; }
.highlight {
  background: #F0F7FC; border-left: 4px solid #0068A0;
  padding: 12px 16px; margin: 14px 0; font-size: 10pt;
}
.notes-section {
  background: #FAFBFC; border: 1px solid #E5E7EB;
  padding: 12px 16px; margin: 12px 0; font-size: 9.5pt;
}
.notes-section h3 { margin-top: 0; font-size: 10pt; color: #1D3146; }
.notes-section ul { margin-bottom: 0; }
.payment-card {
  border: 1px solid #D1E3F0; padding: 11px 16px; margin: 8px 0;
  page-break-inside: avoid;
}
.payment-head { display: flex; justify-content: space-between; align-items: baseline; }
.payment-title { font-weight: 600; color: #1D3146; }
.payment-amount { font-weight: 700; color: #0068A0; font-size: 12pt; }
.payment-desc { font-size: 9pt; color: #6B7280; margin-top: 3px; }
.signature-area { margin-top: 36px; display: flex; gap: 40px; }
.signature-block { flex: 1; border-top: 1px solid #1D3146; padding-top: 8px; }
.sig-label { font-size: 9pt; color: #6B7280; }
.sig-name { font-weight: 600; margin-top: 2px; }
.sig-date { font-size: 9pt; color: #6B7280; margin-top: 2px; }
.footer {
  margin-top: 30px; padding-top: 12px; border-top: 2px solid #0068A0;
  font-size: 8.5pt; color: #6B7280; display: flex; justify-content: space-between;
}
"""

_LEVEL_LABEL = {
    "summary": "Summary",
    "standard": "Standard",
    "detailed": "Detailed",
    "fully_itemized": "Fully Itemized",
}


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value not in (None, "") else ""


def _money(n: float) -> str:
    return f"${n:,.2f}"


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_date(raw: Any) -> str:
    d: date | None = None
    if isinstance(raw, datetime):
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    elif isinstance(raw, str) and len(raw) >= 10:
        try:
            d = date.fromisoformat(raw[:10])
        except ValueError:
            d = None
    return (d or date.today()).strftime("%B %-d, %Y")


def _info_rows(rows: list[tuple[str, Any]]) -> str:
    return "".join(
        f'<div class="row"><span class="label">{_esc(label)}:</span> {_esc(value)}</div>'
        for label, value in rows
        if value not in (None, "")
    )


# --- detail-level tables ---------------------------------------------------


def _summary_table(cats: list[dict]) -> str:
    rows = "".join(
        f'<tr><td><strong>{_esc(c["name"])}</strong></td>'
        f'<td class="text-right">{_money(c["display_total"])}</td></tr>'
        for c in cats
    )
    return (
        "<table><thead><tr><th>Category</th>"
        '<th class="text-right">Amount</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _standard_table(cats: list[dict]) -> str:
    rows = "".join(
        f'<tr><td><strong>{_esc(c["name"])}</strong></td>'
        f'<td>{_esc(c.get("description") or "")}</td>'
        f'<td class="text-right">{_money(c["display_total"])}</td></tr>'
        for c in cats
    )
    return (
        "<table><thead><tr><th>Category</th><th>Description</th>"
        '<th class="text-right">Amount</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _detailed_table(cats: list[dict]) -> str:
    out = []
    for c in cats:
        out.append(f"<h3>{_esc(c['name'])}</h3>")
        if c.get("description"):
            out.append(
                f'<p style="font-size:9.5pt;color:#6B7280;">{_esc(c["description"])}</p>'
            )
        rows = "".join(
            f'<tr><td>{_esc(it["name"])}</td>'
            f'<td class="text-right">{_money(it["labor"])}</td>'
            f'<td class="text-right">{_money(it["materials"])}</td>'
            f'<td class="text-right">{_money(it["labor"] + it["materials"])}</td></tr>'
            for it in c["items"]
        )
        out.append(
            "<table><thead><tr><th>Item</th>"
            '<th class="text-right">Labor</th><th class="text-right">Materials</th>'
            '<th class="text-right">Total</th></tr></thead><tbody>'
            f"{rows}"
            f'<tr class="subtotal-row"><td><strong>Subtotal — {_esc(c["name"])}</strong></td>'
            f'<td></td><td></td><td class="text-right"><strong>'
            f'{_money(c["display_total"])}</strong></td></tr>'
            "</tbody></table>"
        )
    return "".join(out)


def _fully_itemized_table(cats: list[dict]) -> str:
    out = []
    for c in cats:
        out.append(f"<h3>{_esc(c['name'])}</h3>")
        if c.get("description"):
            out.append(
                f'<p style="font-size:9.5pt;color:#6B7280;">{_esc(c["description"])}</p>'
            )
        rows = ""
        for it in c["items"]:
            line = it["labor"] + it["materials"]
            rows += (
                f'<tr><td>{_esc(it["name"])}</td>'
                f'<td class="text-center">{_esc(it.get("qty") or "")}</td>'
                f'<td class="text-right">{_money(it["unitCost"]) if it.get("unitCost") else ""}</td>'
                f'<td class="text-center">{_esc(it.get("laborHours") or "")}</td>'
                f'<td class="text-right">{_money(it["labor"])}</td>'
                f'<td class="text-right">{_money(it["materials"])}</td>'
                f'<td class="text-right">{_money(line)}</td></tr>'
            )
        out.append(
            "<table><thead><tr><th>Task</th><th class=\"text-center\">Qty</th>"
            '<th class="text-right">Unit Cost</th><th class="text-center">Labor Hrs</th>'
            '<th class="text-right">Labor</th><th class="text-right">Materials</th>'
            '<th class="text-right">Total</th></tr></thead><tbody>'
            f"{rows}"
            f'<tr class="subtotal-row"><td colspan="6"><strong>Subtotal — '
            f'{_esc(c["name"])}</strong></td><td class="text-right"><strong>'
            f'{_money(c["display_total"])}</strong></td></tr>'
            "</tbody></table>"
        )
    return "".join(out)


def build_estimate_html(
    *, payload: dict[str, Any], project: dict[str, Any], client: dict[str, Any]
) -> str:
    """Assemble the full HTML document for an estimate PDF."""
    level = payload.get("detailLevel") or "standard"
    overhead = _num(payload.get("overhead"))
    profit = _num(payload.get("profit"))
    margin = (1 + overhead / 100) * (1 + profit / 100)

    # Fold overhead + profit into every category / item total.
    cats: list[dict] = []
    for c in payload.get("categories") or []:
        if not isinstance(c, dict) or _is_empty_division(c):
            continue
        base_total = _num(c.get("total"))
        items = []
        for it in c.get("items") or []:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "name": it.get("name") or "—",
                    "labor": _num(it.get("labor")) * margin,
                    "materials": _num(it.get("materials")) * margin,
                    "unitCost": _num(it.get("unitCost")) * margin,
                    "qty": it.get("qty"),
                    "laborHours": it.get("laborHours"),
                }
            )
        cats.append(
            {
                "name": c.get("name") or "—",
                "description": c.get("description") or "",
                "display_total": base_total * margin,
                "items": items,
            }
        )

    table_fn = {
        "summary": _summary_table,
        "standard": _standard_table,
        "detailed": _detailed_table,
        "fully_itemized": _fully_itemized_table,
    }.get(level, _standard_table)
    breakdown = table_fn(cats)

    # Totals — client view: Subtotal → Allowances → GST → Total. No OH/profit.
    subtotal = sum(c["display_total"] for c in cats)
    allowances = _num(payload.get("allowances"))
    taxable = subtotal + allowances
    gst = taxable * 0.05
    total = taxable + gst

    total_rows = (
        f'<tr><td><strong>Subtotal</strong></td>'
        f'<td class="text-right">{_money(subtotal)}</td></tr>'
    )
    if allowances:
        total_rows += (
            f'<tr><td>Allowances</td>'
            f'<td class="text-right">{_money(allowances)}</td></tr>'
        )
    total_rows += (
        f'<tr><td>GST (5%)</td><td class="text-right">{_money(gst)}</td></tr>'
        f'<tr class="total-row"><td><strong>Total</strong></td>'
        f'<td class="text-right"><strong>{_money(total)}</strong></td></tr>'
    )

    # Payment schedule.
    payments = payload.get("paymentSchedule") or []
    payment_html = ""
    if payments:
        cards = []
        for p in payments:
            if not isinstance(p, dict):
                continue
            pct = _num(p.get("percentage"))
            amount = _num(p.get("amount")) or (total * pct / 100)
            cards.append(
                '<div class="payment-card"><div class="payment-head">'
                f'<span class="payment-title">{_esc(p.get("milestone") or "Payment")}</span>'
                f'<span class="payment-amount">{_money(amount)}'
                f'{f" ({pct:g}%)" if pct else ""}</span></div>'
                + (
                    f'<div class="payment-desc">{_esc(p["description"])}</div>'
                    if p.get("description")
                    else ""
                )
                + "</div>"
            )
        if cards:
            payment_html = "<h2>Payment Schedule</h2>" + "".join(cards)

    def notes_block(title: str, items: Any) -> str:
        vals = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not vals:
            return ""
        lis = "".join(f"<li>{_esc(v)}</li>" for v in vals)
        return f'<div class="notes-section"><h3>{title}</h3><ul>{lis}</ul></div>'

    valid_days = payload.get("validDays")
    validity_html = (
        f'<div class="highlight"><p><strong>Estimate Validity:</strong> '
        f"This estimate is valid for {_esc(valid_days)} days from the date of "
        f"issue.</p></div>"
        if valid_days
        else ""
    )

    project_name = project.get("name") or "Project"
    subtitle = f"{_esc(project_name)} &mdash; {_LEVEL_LABEL.get(level, 'Standard')} Breakdown"

    client_box = _info_rows(
        [
            ("Client", client.get("name")),
            ("Email", client.get("email")),
            ("Phone", client.get("phone")),
            ("Address", client.get("address")),
        ]
    ) or '<div class="row">No client on file.</div>'
    project_box = _info_rows(
        [
            ("Project", project_name),
            ("Reference", payload.get("reference") or "EST-DRAFT"),
            ("Date", _fmt_date(payload.get("date"))),
            ("Site Address", project.get("address")),
            ("Project Type", project.get("type")),
        ]
    )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="logo-bar">
  <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
  <div class="company-info">
    {LETTERHEAD_HTML}
  </div>
</div>
<h1>Estimate</h1>
<p class="subtitle">{subtitle}</p>
<div class="info-grid">
  <div class="info-box">{client_box}</div>
  <div class="info-box">{project_box}</div>
</div>
<h2>Cost Breakdown</h2>
{breakdown}
<table class="totals"><tbody>{total_rows}</tbody></table>
{validity_html}
{payment_html}
{notes_block("Assumptions", payload.get("assumptions"))}
{notes_block("Exclusions", payload.get("exclusions"))}
<div class="signature-area">
  <div class="signature-block">
    <div class="sig-label">Contractor</div>
    <div class="sig-name">Built By Brio</div>
    <div class="sig-date">Date: _______________</div>
  </div>
  <div class="signature-block">
    <div class="sig-label">Client</div>
    <div class="sig-name">{_esc(client.get("name") or "")}</div>
    <div class="sig-date">Date: _______________</div>
  </div>
</div>
<div class="footer">
  <span>Built By Brio &mdash; Estimate</span>
  <span>{FOOTER_CONTACT}</span>
</div>
</body></html>"""


def render_estimate_pdf(
    *, payload: dict[str, Any], project: dict[str, Any], client: dict[str, Any]
) -> bytes:
    """Render an estimate payload to PDF bytes."""
    from weasyprint import HTML  # lazy — heavy native deps

    document = build_estimate_html(payload=payload, project=project, client=client)
    return HTML(string=document).write_pdf()


# ── Client-presentation edition (Investment Proposal) ────────────────────────
# Same source payload as the detailed estimate, different document. Strips
# the cost build-up the client doesn't need (labor, materials, qty, unit
# cost, labor hours, per-item line totals, "Subtotal — Category" math rows,
# internal reference codes, detail-level labels). What remains: category
# names + plain-language descriptions + category totals, plus the totals
# block, payment schedule, assumptions, exclusions, BRIO 5-Star Guarantee,
# and a signature page.

_CLIENT_CSS = """
@page {
  size: Letter; margin: 18mm 18mm 20mm 18mm;
  @bottom-left {
    content: "BRIO Construction · Project Estimate";
    font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
    font-size: 7.5pt; color: #6B7280;
  }
  @bottom-right {
    content: "Page " counter(page) " of " counter(pages);
    font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
    font-size: 7.5pt; color: #6B7280;
  }
}
@page :first {
  margin: 22mm 18mm;
  @bottom-left { content: ""; }
  @bottom-right { content: ""; }
}
body { widows: 3; orphans: 3; }
h2, h3 { break-after: avoid; page-break-after: avoid; }

.cp-cover {
  page-break-after: always; break-after: page; display: block;
  padding: 22mm 0 0 0;
}
.cp-cover .brand-bar {
  border-bottom: 3px solid #0068A0; padding-bottom: 14px;
  display: flex; justify-content: space-between; align-items: flex-end;
}
.cp-cover .doc-type {
  text-transform: uppercase; letter-spacing: 2.5px; font-size: 9.5pt;
  color: #6B7280; margin: 50mm 0 8px 0;
}
.cp-cover h1 {
  font-size: 34pt; line-height: 1.04; color: #0068A0;
  margin-bottom: 6px; letter-spacing: -0.5px; font-weight: 700;
  border-bottom: none; padding-bottom: 0;
}
.cp-cover .project-subtitle {
  font-size: 14pt; color: #1D3146; font-weight: 600; margin-bottom: 30px;
}
.cp-cover .investment {
  background: #F0F7FC; border-top: 3px solid #0068A0;
  padding: 18px 22px; margin: 24px 0 28px 0;
  display: flex; justify-content: space-between; align-items: flex-end;
}
.cp-cover .investment .investment-label {
  text-transform: uppercase; letter-spacing: 1.5px; font-size: 8.5pt;
  color: #0068A0; font-weight: 700;
}
.cp-cover .investment .investment-amount {
  font-size: 28pt; font-weight: 700; color: #0068A0; line-height: 1;
  font-feature-settings: 'tnum';
}
.cp-cover .meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 20px 40px;
  margin-top: 14px; padding-top: 14px;
  border-top: 1px solid #D1E3F0;
}
.cp-cover .meta-grid .meta-label {
  text-transform: uppercase; letter-spacing: 1.5px; font-size: 7.5pt;
  font-weight: 700; color: #0068A0; margin-bottom: 3px;
}
.cp-cover .meta-grid .meta-value { font-size: 11pt; color: #1D3146; }
.cp-cover .prepared-by {
  position: absolute; bottom: 22mm; left: 18mm; right: 18mm;
  font-size: 9pt; color: #6B7280;
  padding-top: 12px; border-top: 1px solid #E5E7EB;
  display: flex; justify-content: space-between;
}

section.cp-section { break-before: page; page-break-before: always; }
section.cp-section.first { break-before: auto; page-break-before: auto; }
section.cp-section > h2 {
  font-size: 16pt; color: #0068A0; padding-bottom: 8px;
  border-bottom: 2px solid #0068A0; margin: 0 0 16px 0;
}
section.cp-section > .h2-kicker {
  text-transform: uppercase; letter-spacing: 2px; font-size: 8.5pt;
  font-weight: 700; color: #6B7280; margin-bottom: 6px;
}
section.cp-section p { font-size: 10.5pt; line-height: 1.6; margin-bottom: 10px; }

/* Project Investment — one card per category */
.cat-card {
  border: 1px solid #D1E3F0; padding: 14px 18px;
  margin-bottom: 10px; page-break-inside: avoid; break-inside: avoid;
}
.cat-card .cc-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 4px;
}
.cat-card .cc-name {
  font-size: 12pt; font-weight: 700; color: #0068A0;
}
.cat-card .cc-amount {
  font-size: 14pt; font-weight: 700; color: #0068A0;
  font-feature-settings: 'tnum';
}
.cat-card .cc-desc {
  font-size: 10pt; color: #1D3146; line-height: 1.5; margin-top: 4px;
}
.cat-card .cc-optional-pill {
  display: inline-block; font-size: 8pt; text-transform: uppercase;
  letter-spacing: 1px; font-weight: 700; color: #B45309;
  background: #FFFBEB; border: 1px solid #FBBF24;
  padding: 1px 8px; margin-left: 10px;
}

/* Investment Summary */
.totals-card {
  background: #F0F7FC; border: 1px solid #D1E3F0;
  padding: 18px 22px; margin: 16px 0;
  page-break-inside: avoid; break-inside: avoid;
}
.totals-card table { margin: 0; }
.totals-card td { background: transparent; padding: 6px 0; border: none; }
.totals-card tr.total-row td {
  font-size: 14pt; font-weight: 700; color: #0068A0;
  border-top: 2px solid #0068A0; padding-top: 12px;
}

/* Payment cards repeat existing style with a tighter feel */
.cp-payment {
  border: 1px solid #D1E3F0; padding: 12px 18px; margin-bottom: 8px;
  page-break-inside: avoid; break-inside: avoid;
}
.cp-payment .pay-head {
  display: flex; justify-content: space-between; align-items: baseline;
}
.cp-payment .pay-title { font-weight: 700; color: #1D3146; font-size: 11pt; }
.cp-payment .pay-amount {
  font-weight: 700; color: #0068A0; font-size: 13pt;
  font-feature-settings: 'tnum';
}
.cp-payment .pay-desc { font-size: 9.5pt; color: #6B7280; margin-top: 4px; }

/* Bullet sections */
.bullet-list ul { padding-left: 22px; margin-top: 4px; }
.bullet-list li { margin-bottom: 5px; line-height: 1.5; font-size: 10.5pt; }

/* Guarantee */
.guarantee-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  margin-top: 12px;
}
.guarantee-tile {
  border: 1px solid #D1E3F0; padding: 14px 16px;
  page-break-inside: avoid; break-inside: avoid;
}
.guarantee-tile .gt-num {
  display: inline-block; color: #0068A0; font-weight: 700;
  font-size: 13pt; margin-right: 6px;
}
.guarantee-tile .gt-title { font-weight: 700; color: #1D3146; font-size: 11pt; }
.guarantee-tile .gt-body {
  font-size: 9.5pt; color: #1D3146; line-height: 1.5; margin-top: 6px;
}

/* Validity callout */
.validity {
  background: #F0F7FC; border-left: 4px solid #0068A0;
  padding: 12px 16px; margin: 14px 0; font-size: 10pt;
}

/* Signature page */
section.cp-section.cp-sign-page > h2 { margin-bottom: 22px; }
.sig-grid {
  display: flex; gap: 40px; margin-top: 24px;
}
.sig-block { flex: 1; border-top: 1px solid #1D3146; padding-top: 8px; }
.sig-block .sl { font-size: 9pt; color: #6B7280; }
.sig-block .sn { font-weight: 600; margin-top: 2px; font-size: 11pt; }
.sig-block .sd { font-size: 9pt; color: #6B7280; margin-top: 6px; }
"""


def _brio_5_star_guarantee_pairs() -> list[tuple[str, str]]:
    """The five written commitments that anchor every BRIO project. Kept
    here (rather than imported from scope_pdf) so this module is
    self-contained."""
    return [
        (
            "On-Time Guarantee",
            "Guaranteed completion date; final payment waived if we run "
            "late on our own fault (excludes owner-driven scope changes "
            "or delays).",
        ),
        (
            "Fixed-Price Guarantee",
            "The quote is the final price. Any scope change requires "
            "written approval before work proceeds.",
        ),
        (
            "1-Year Workmanship Warranty",
            "We fix any workmanship failures at no cost for one full year "
            "after substantial completion.",
        ),
        (
            "Clean Site Promise",
            "Daily cleanup, belongings protected, proof of insurance "
            "before day one on site.",
        ),
        (
            "Transparent Communication",
            "Dedicated project manager, same-day responses, weekly photo "
            "updates throughout the project.",
        ),
    ]


def _category_client_description(cat: dict[str, Any]) -> str:
    """Pick the client-facing description for a category. The estimate
    payload already carries a `description` field per category — we use it
    verbatim when present. Falls back to a generic blurb derived from the
    category name so empty descriptions don't print blank cards."""
    desc = (cat.get("description") or "").strip()
    if desc:
        return desc
    name = (cat.get("name") or "this trade").strip() or "this trade"
    return (
        f"All work associated with {name.lower()} on this project, "
        "coordinated, supervised, and warrantied by BRIO."
    )


# Internal-only language to keep out of the client edition. Substring,
# case-insensitive — applied to category names (whole category dropped),
# category descriptions (description stripped), item names (item dropped),
# and every bullet in assumptions / exclusions / allowance notes.
_CLIENT_REDACT_TERMS = ("contingency", "owner audit adjustment", "mobilization")
# Wider, for NOTES only (assumptions, exclusions, allowance notes, descriptions).
# Rollout test 2026-10-15: an assumption reading "priced as an overhead
# allocation ... the $15-40k exterior and $5-15k design ranges from the
# calibration rule were scaled down" reached the client. Not applied to category
# or item NAMES, which drop their money from the client edition when matched.
# The app's client view uses the same list (lib/clientRedact.ts; a test keeps
# the two equal).
_CLIENT_NOTE_TERMS = _CLIENT_REDACT_TERMS + (
    "overhead", "profit", "margin", "markup", "calibration", "cad_extract", "discipline-scoped")


def _client_redact(text: str | None, terms: tuple[str, ...] = _CLIENT_REDACT_TERMS) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(term in t for term in terms)


def _client_item_name(name: str) -> str | None:
    """An item name as a client may read it, or None to leave it out.

    A bracketed note with internal wording is cut, keeping the item: on ZZ v2
    "PM / site supervision (overhead allocation, one shared supervisor ...)"
    reached the client PDF. A bare "overhead" outside brackets stays, because
    "Overhead door" is a real line. Names carry no amounts here, so this never
    moves money.
    """
    kept = re.sub(r"\s*\(([^()]*)\)",
                  lambda m: "" if _client_redact(m.group(1), _CLIENT_NOTE_TERMS) else m.group(0), name).strip()
    # Dollar figures inside a name ("(target raw $65,000)", "line total $1,500")
    # are notes to us; 2026-10-27 they reached every client edition.
    from app.services.estimate_selection_pdf import _strip_money
    kept = _strip_money(kept)
    return None if not kept or _client_redact(kept) else kept


def _client_filter_bullets(items: Any) -> list[str]:
    """Strip empty + redacted strings out of a bullet list."""
    return [
        str(x).strip()
        for x in (items or [])
        if str(x).strip() and not _client_redact(str(x), _CLIENT_NOTE_TERMS)
    ]


def _is_empty_division(c: dict) -> bool:
    """A division with nothing priced in it: $0 total and no line carrying money.
    Listing it on a client estimate reads as "this trade costs nothing" or, with
    a stale "No scope identified" description, as noise. The by-division and
    selection PDFs already drop these; the estimate PDFs did not (456 W Sample
    corridor v009, 2026-11-06: eight $0 divisions on the client copy)."""
    if round(_num(c.get("total")), 2) != 0:
        return False
    return not any(
        isinstance(it, dict) and (_num(it.get("labor")) + _num(it.get("materials"))) != 0
        for it in (c.get("items") or [])
    )


def _qty_is_zero(it: dict) -> bool:
    """True only when an item's quantity is explicitly numeric zero (items with no
    quantity are kept). A qty=0 line is de-scoped work — it must not appear on a
    client-facing estimate, where the client would read it as included."""
    q = it.get("qty")
    if q is None or q == "":
        return False
    try:
        return float(q) == 0
    except (TypeError, ValueError):
        return False


def _client_items_table(cats: list[dict]) -> str:
    """Client view of the per-category breakdown. Same H3 + description
    structure as the detailed table, but item rows show ONLY the item name
    (no labor / materials / qty / unit cost / labor hours / per-item total).
    The only price point per category is the subtotal row — the category's
    all-in total (management fee already blended in)."""
    out: list[str] = []
    for c in cats:
        out.append(f"<h3>{_esc(c['name'])}</h3>")
        if c.get("description"):
            out.append(
                f'<p style="font-size:9.5pt;color:#6B7280;">{_esc(c["description"])}</p>'
            )
        item_rows = "".join(
            f'<tr><td>{_esc(it["name"])}</td><td></td></tr>'
            for it in c["items"]
            if (it.get("name") or "").strip() and not _qty_is_zero(it)
        )
        if not item_rows:
            # Category with no enumerated items — still show the subtotal.
            item_rows = (
                '<tr><td style="color:#6B7280; font-style:italic;">'
                "Scope covered as part of the trade rollup.</td><td></td></tr>"
            )
        out.append(
            '<table><thead><tr><th>What&rsquo;s included</th>'
            '<th class="text-right">Amount</th></tr></thead><tbody>'
            f"{item_rows}"
            f'<tr class="subtotal-row"><td><strong>Subtotal &mdash; {_esc(c["name"])}</strong></td>'
            f'<td class="text-right"><strong>{_money(c["display_total"])}</strong></td></tr>'
            "</tbody></table>"
        )
    return "".join(out)


def _internal_fee_table(cats: list[dict], fee_pct: float) -> str:
    """INTERNAL variant of the detailed view. Same included-items lists, but each
    category exposes its base subtotal, the management fee, and the category
    total. NOT for client distribution — it reveals the fee (which the client
    edition rolls into a single number)."""
    out: list[str] = []
    for c in cats:
        out.append(f"<h3>{_esc(c['name'])}</h3>")
        if c.get("description"):
            out.append(
                f'<p style="font-size:9.5pt;color:#6B7280;">{_esc(c["description"])}</p>'
            )
        item_rows = "".join(
            f'<tr><td>{_esc(it["name"])}</td><td></td></tr>'
            for it in c["items"]
            if (it.get("name") or "").strip()
        )
        if not item_rows:
            item_rows = (
                '<tr><td style="color:#6B7280; font-style:italic;">'
                "Scope covered as part of the trade rollup.</td><td></td></tr>"
            )
        base = _num(c.get("base_total"))
        total = _num(c.get("display_total"))
        fee = total - base  # exact — avoids rounding drift vs base*pct
        out.append(
            '<table><thead><tr><th>What&rsquo;s included</th>'
            '<th class="text-right">Amount</th></tr></thead><tbody>'
            f"{item_rows}"
            f'<tr class="subtotal-row"><td>Subtotal (before fee)</td>'
            f'<td class="text-right">{_money(base)}</td></tr>'
            f'<tr><td>Management fee ({fee_pct:g}%)</td>'
            f'<td class="text-right">{_money(fee)}</td></tr>'
            f'<tr class="subtotal-row"><td><strong>Category total</strong></td>'
            f'<td class="text-right"><strong>{_money(total)}</strong></td></tr>'
            "</tbody></table>"
        )
    return "".join(out)


def _client_summary_table(cats: list[dict]) -> str:
    """Condensed client view: one row per category — name + one-line scope
    description + the category's price. No per-item lists. Used by the
    'Summary' client PDF; the 'Detailed' one uses _client_items_table."""
    rows: list[str] = []
    for c in cats:
        desc = (
            f'<div style="font-size:9pt;color:#6B7280;margin-top:2px;">{_esc(c["description"])}</div>'
            if c.get("description")
            else ""
        )
        rows.append(
            f"<tr><td><strong>{_esc(c['name'])}</strong>{desc}</td>"
            f'<td class="text-right" style="white-space:nowrap;vertical-align:top;">'
            f"{_money(c['display_total'])}</td></tr>"
        )
    return (
        '<table class="summary-table"><thead><tr>'
        '<th>Scope</th><th class="text-right">Amount</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


# Residential presentation: re-group the commercial trade categories into the
# construction-sequence PHASES a homeowner expects (NAHB-style). Matched by
# keyword against the category name so it works regardless of numbering.
# Order here = construction order = the order phases render.
_RESIDENTIAL_PHASES: list[tuple[str, tuple[str, ...]]] = [
    ("Permits & Project Setup", ("general condition", "permit", "mobiliz", "closeout", "project management", "supervision")),
    ("Site Work & Excavation", ("excavat", "demolition", "site clear", "site work", "grading", "earthwork")),
    ("Stormwater & Drainage", ("stormwater", "drainage")),
    ("Foundation", ("foundation", "concrete", "footing", "slab")),
    ("Framing & Structure", ("framing", "carpentry", "structur", "steel")),
    ("Lock-Up — Roofing, Exterior & Openings", ("roof", "cladding", "siding", "exterior", "door", "window", "glazing", "weather")),
    ("Mechanical Rough-Ins (Plumbing · Electrical · HVAC)", ("plumb", "electric", "hvac", "mechanical", "low-voltage", "low voltage")),
    ("Insulation & Drywall", ("insulation", "drywall", "acoustic", "vapour", "vapor")),
    ("Interior Finishes", ("paint", "floor", "tile", "millwork", "casework", "cabinet", "finish", "specialt", "fire detection")),
]
_RESIDENTIAL_FALLBACK = "Other Work"


def _residential_phase_table(cats: list[dict]) -> str:
    """Client view grouped by construction phase. Each phase shows its total
    plus the trades that roll into it (name + amount). No per-item lists."""
    buckets: list[tuple[str, list[dict]]] = [(name, []) for name, _ in _RESIDENTIAL_PHASES]
    fallback: list[dict] = []
    for c in cats:
        nm = (c.get("name") or "").lower()
        for (phase, kws), bucket in zip(_RESIDENTIAL_PHASES, buckets):
            if any(k in nm for k in kws):
                bucket[1].append(c)
                break
        else:
            fallback.append(c)
    ordered = [b for b in buckets if b[1]]
    if fallback:
        ordered.append((_RESIDENTIAL_FALLBACK, fallback))

    rows: list[str] = []
    for i, (phase, members) in enumerate(ordered, 1):
        ptotal = sum(m["display_total"] for m in members)
        rows.append(
            f'<tr class="phase-row"><td><strong>{i}. {_esc(phase)}</strong></td>'
            f'<td class="text-right"><strong>{_money(ptotal)}</strong></td></tr>'
        )
        for m in members:
            rows.append(
                f'<tr><td style="padding-left:20px;color:#4B5563;">{_esc(m["name"])}</td>'
                f'<td class="text-right" style="color:#6B7280;">{_money(m["display_total"])}</td></tr>'
            )
    return (
        '<table class="summary-table"><thead><tr>'
        '<th>Construction Phase</th><th class="text-right">Amount</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def build_client_estimate_html(
    *,
    payload: dict[str, Any],
    project: dict[str, Any],
    client: dict[str, Any],
    summary: bool = False,
    residential: bool = False,
    internal_fee: bool = False,
) -> str:
    """Sanitised clone of build_estimate_html — same document, internal
    columns stripped. Keeps the existing logo bar, header, project info
    boxes, per-category H3 + description + included-items list +
    subtotal, totals block, validity, payment schedule, assumptions,
    exclusions, signature, footer. Removes labor / materials / qty /
    unit cost / labor hours / per-item dollar amounts / detail-level
    label / internal reference code. Wording softened for client read."""

    overhead = _num(payload.get("overhead"))
    profit = _num(payload.get("profit"))
    margin = (1 + overhead / 100) * (1 + profit / 100)
    fee_pct = round((margin - 1) * 100, 2)

    cats: list[dict] = []
    for c in payload.get("categories") or []:
        if not isinstance(c, dict) or _is_empty_division(c):
            continue
        cat_name = c.get("name") or "—"
        # Whole category drops out if its name itself contains internal
        # language (e.g. a category literally named "Mobilization").
        if _client_redact(cat_name):
            continue
        # Redact the description if it leaks internal language; otherwise
        # the item names stay but the description gets blanked.
        cat_desc = c.get("description") or ""
        if _client_redact(cat_desc, _CLIENT_NOTE_TERMS):
            cat_desc = ""
        items = []
        for it in c.get("items") or []:
            if not isinstance(it, dict):
                continue
            if _qty_is_zero(it) and not internal_fee:
                continue  # de-scoped (qty 0) — omit from client editions; internal fee PDF keeps all
            name = _client_item_name(it.get("name") or "—")
            if name is None:
                continue
            items.append({"name": name})
        cats.append(
            {
                "name": cat_name,
                "description": cat_desc,
                "base_total": _num(c.get("total")),
                "display_total": _num(c.get("total")) * margin,
                "items": items,
            }
        )

    if residential:
        breakdown = _residential_phase_table(cats)
    elif summary:
        breakdown = _client_summary_table(cats)
    elif internal_fee:
        breakdown = _internal_fee_table(cats, fee_pct)
    else:
        breakdown = _client_items_table(cats)

    subtotal = sum(c["display_total"] for c in cats)
    allowances_raw = payload.get("allowances")
    # The detailed estimate carries `allowances` as either a number (dollar
    # value rolled into totals) OR a list of text caveats. Handle both for
    # the client edition.
    allowances_num = (
        _num(allowances_raw) if not isinstance(allowances_raw, list) else 0.0
    )
    allowance_notes = (
        _client_filter_bullets(allowances_raw)
        if isinstance(allowances_raw, list)
        else []
    )
    taxable = subtotal + allowances_num
    gst = taxable * 0.05
    total = taxable + gst

    # $/sq ft headline — residential convention. Only when we know the area.
    sqft = _num(project.get("sqft"))
    psf_html = ""
    if residential and sqft > 0:
        psf_html = (
            '<div class="highlight"><p><strong>'
            f"{_money(total / sqft)} per sq ft</strong> "
            f"&mdash; {sqft:g} sq ft (incl. GST).</p></div>"
        )
    scope_heading = "Estimate by Construction Phase" if residential else "Scope &amp; Pricing"

    if internal_fee:
        base_subtotal = sum(c["base_total"] for c in cats)
        fee_total = subtotal - base_subtotal
        total_rows = (
            f"<tr><td><strong>Subtotal (before fee)</strong></td>"
            f'<td class="text-right">{_money(base_subtotal)}</td></tr>'
            f"<tr><td>Management fee ({fee_pct:g}%)</td>"
            f'<td class="text-right">{_money(fee_total)}</td></tr>'
        )
    else:
        total_rows = (
            f"<tr><td><strong>Subtotal</strong></td>"
            f'<td class="text-right">{_money(subtotal)}</td></tr>'
        )
    if allowances_num:
        total_rows += (
            f"<tr><td>Allowance Provisions</td>"
            f'<td class="text-right">{_money(allowances_num)}</td></tr>'
        )
    total_rows += (
        f"<tr><td>GST (5%)</td>"
        f'<td class="text-right">{_money(gst)}</td></tr>'
        f'<tr class="total-row"><td><strong>Estimated Total</strong></td>'
        f'<td class="text-right"><strong>{_money(total)}</strong></td></tr>'
    )

    # Payment schedule (same render as the internal version).
    payments = payload.get("paymentSchedule") or []
    payment_html = ""
    if payments:
        cards = []
        for p in payments:
            if not isinstance(p, dict):
                continue
            pct = _num(p.get("percentage"))
            amount = _num(p.get("amount")) or (total * pct / 100)
            cards.append(
                '<div class="payment-card"><div class="payment-head">'
                f'<span class="payment-title">{_esc(p.get("milestone") or "Payment")}</span>'
                f'<span class="payment-amount">{_money(amount)}'
                f'{f" ({pct:g}%)" if pct else ""}</span></div>'
                + (
                    f'<div class="payment-desc">{_esc(p["description"])}</div>'
                    if p.get("description")
                    else ""
                )
                + "</div>"
            )
        if cards:
            payment_html = "<h2>Payment Schedule</h2>" + "".join(cards)

    def notes_block(title: str, items: Any) -> str:
        # Client edition also filters out bullets mentioning internal
        # language (contingency / owner audit adjustment / mobilization).
        vals = _client_filter_bullets(items)
        if not vals:
            return ""
        lis = "".join(f"<li>{_esc(v)}</li>" for v in vals)
        return f'<div class="notes-section"><h3>{title}</h3><ul>{lis}</ul></div>'

    valid_days = payload.get("validDays")
    validity_html = (
        f'<div class="highlight"><p><strong>Estimate Validity:</strong> '
        f"This estimate is valid for {_esc(valid_days)} days from the date "
        f"of issue.</p></div>"
        if valid_days
        else ""
    )

    project_name = project.get("name") or "Project"
    # No detail-level label in the subtitle (internal-only signal).
    subtitle = _esc(project_name)
    if internal_fee:
        subtitle += " &mdash; INTERNAL (fee breakdown)"
    project_type = project.get("type") or ""
    if project_type:
        subtitle += f" &mdash; {_esc(project_type)}"

    client_box = _info_rows(
        [
            ("Client", client.get("name")),
            ("Email", client.get("email")),
            ("Phone", client.get("phone")),
            ("Address", client.get("address")),
        ]
    ) or '<div class="row">No client on file.</div>'
    project_box = _info_rows(
        [
            ("Project", project_name),
            # Internal reference dropped on purpose — client doesn't need
            # to see the EST-... version code.
            ("Date", _fmt_date(payload.get("date"))),
            ("Site Address", project.get("address")),
            ("Project Type", project.get("type")),
        ]
    )

    allowance_notes_html = (
        '<div class="notes-section"><h3>Allowance Provisions</h3><ul>'
        + "".join(f"<li>{_esc(v)}</li>" for v in allowance_notes)
        + "</ul></div>"
        if allowance_notes
        else ""
    )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="logo-bar">
  <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
  <div class="company-info">
    {LETTERHEAD_HTML}
  </div>
</div>
<h1>Project Estimate</h1>
<p class="subtitle">{subtitle}</p>
{'<div style="background:#7F1D1D;color:#fff;padding:8px 14px;font-weight:700;font-size:9.5pt;letter-spacing:0.3px;margin:0 0 16px 0;">INTERNAL — FEE BREAKDOWN. NOT FOR CLIENT DISTRIBUTION.</div>' if internal_fee else ''}
<div class="info-grid">
  <div class="info-box">{client_box}</div>
  <div class="info-box">{project_box}</div>
</div>
<h2>{scope_heading}</h2>
{breakdown}
<table class="totals"><tbody>{total_rows}</tbody></table>
{psf_html}
{validity_html}
{payment_html}
{allowance_notes_html}
{notes_block("Assumptions", payload.get("assumptions"))}
{notes_block("Not Included", payload.get("exclusions"))}
<div class="signature-area">
  <div class="signature-block">
    <div class="sig-label">Contractor</div>
    <div class="sig-name">{LEGAL_NAME}</div>
    <div class="sig-date">Date: _______________</div>
  </div>
  <div class="signature-block">
    <div class="sig-label">Client</div>
    <div class="sig-name">{_esc(client.get("name") or "")}</div>
    <div class="sig-date">Date: _______________</div>
  </div>
</div>
<div class="footer">
  <span>Built By Brio &mdash; Project Estimate</span>
  <span>{FOOTER_CONTACT}</span>
</div>
</body></html>"""


def render_client_estimate_pdf(
    *,
    payload: dict[str, Any],
    project: dict[str, Any],
    client: dict[str, Any],
    summary: bool = False,
    residential: bool = False,
    internal_fee: bool = False,
) -> bytes:
    """Render the client-presentation edition of the estimate to PDF bytes.

    residential=True → grouped by construction phase + $/sq ft (overrides summary).
    summary=False    → Detailed (per-category included-items lists).
    summary=True     → Summary (one row per category: scope + price).
    internal_fee=True → INTERNAL detailed view exposing the management fee per
                        category (not for client distribution)."""
    from weasyprint import HTML  # lazy

    document = build_client_estimate_html(
        payload=payload, project=project, client=client,
        summary=summary, residential=residential, internal_fee=internal_fee,
    )
    return HTML(string=document).write_pdf()
