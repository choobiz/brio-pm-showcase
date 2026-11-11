"""Render an Invoice PDF — BRIO-branded, via WeasyPrint.

Ports brio-admin/templates/invoice.mjs — the 4 invoice types (progress, t&m,
change_order, retainer), the totals block with amount-paid / balance-due, and
the payment-methods box. weasyprint is imported lazily (see scope_pdf.py).
"""

from __future__ import annotations
from app.company import FOOTER_CONTACT, LETTERHEAD_HTML, WEBSITE

import html
from datetime import date, datetime
from typing import Any

from app.services.pdf_brand import logo_data_url

_CSS = """
@page { size: Letter; margin: 14mm 16mm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
  color: #1D3146; line-height: 1.5; font-size: 10pt;
}
h1, h2, h3 { font-family: 'Sora', 'Helvetica Neue', Arial, sans-serif; }
.logo-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 12px; border-bottom: 2.5px solid #0068A0; margin-bottom: 18px;
}
.logo-img { height: 38px; }
.company-info { text-align: right; font-size: 8pt; color: #6B7280; line-height: 1.5; }
h1 { font-size: 20pt; font-weight: 700; color: #0068A0; margin-bottom: 2px; }
.subtitle { font-size: 11pt; color: #6B7280; margin-bottom: 6px; }
.badge {
  display: inline-block; font-size: 8.5pt; font-weight: 600;
  padding: 2px 9px; text-transform: uppercase; letter-spacing: 0.4px;
}
.badge-paid { background: #D1FAE5; color: #065F46; }
.badge-unpaid { background: #FEE2E2; color: #991B1B; }
.badge-partial { background: #FEF3C7; color: #92400E; }
.badge-draft { background: #E5E7EB; color: #374151; }
h2 {
  font-size: 12pt; font-weight: 600; color: #0068A0;
  margin-top: 16px; margin-bottom: 7px; padding-bottom: 4px;
  border-bottom: 1px solid #D1E3F0;
}
h3 { font-size: 10.5pt; font-weight: 600; color: #1D3146; margin-top: 12px; margin-bottom: 5px; }
p { margin-bottom: 5px; }
.info-grid { display: flex; gap: 12px; margin: 10px 0; }
.info-box { flex: 1; background: #F0F7FC; border: 1px solid #D1E3F0; padding: 10px 14px; font-size: 9pt; }
.info-box .label { font-weight: 600; color: #0068A0; display: inline-block; min-width: 110px; }
.info-box .row { margin-bottom: 2px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 9.5pt; }
thead th {
  background: #F0F7FC; font-weight: 600; text-align: left; padding: 6px 9px;
  border-bottom: 2px solid #0068A0; color: #1D3146; font-size: 8pt;
  text-transform: uppercase; letter-spacing: 0.3px;
}
td { padding: 5px 9px; border-bottom: 1px solid #E5E7EB; vertical-align: top; }
tr:nth-child(even) td { background: #FAFBFC; }
.text-right { text-align: right; }
.text-center { text-align: center; }
.total-row td {
  font-weight: 700; border-top: 2px solid #0068A0; border-bottom: none;
  background: #F0F7FC; font-size: 10.5pt;
}
.totals { width: 320px; margin-left: auto; }
.paid { color: #059669; }
.highlight {
  background: #F0F7FC; border-left: 4px solid #0068A0;
  padding: 9px 14px; margin: 12px 0; font-size: 9pt;
}
/* Notes carry the scope of work, written as headed and indented lines. Without
   this the newlines collapse and it renders as one run-on block. pre-wrap rather
   than pre-line so the indentation under each heading survives; it still wraps. */
.highlight p { white-space: pre-wrap; margin: 0; }
/* The scope belongs to the billing block above it: it is the description of a
   line that otherwise reads only "Lump-sum price as issued". It used to sit
   BELOW the payment instructions in a box styled identically to them, so two
   visually identical panels carried unrelated content and the scope read as an
   afterthought.
   It now follows the billing table and its totals, and precedes the payment
   instructions. Placing it between the table and the totals was tried first and
   reverted: a 2,300-character scope pushed Subtotal, GST and Total onto page 2,
   separating the money from the line it summarises. The commercial block stays
   whole; the detail that substantiates it comes next; the call to action last. */
.scope { margin: 2px 0 14px 0; padding-left: 12px; border-left: 2px solid #E5E7EB; }
.scope h3 {
  margin: 0 0 4px 0; font-size: 9pt; font-weight: 600; color: #6B7280;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.scope p { white-space: pre-wrap; margin: 0; font-size: 8.8pt; color: #374151; }
.invoice-desc { margin: 6px 0; font-size: 9.5pt; }
.guarantee-strip {
  margin-top: 18px; padding: 9px 14px;
  background: #F2F7FA; border-left: 3px solid #0068A0;
}
.guarantee-title {
  font-size: 7.5pt; font-weight: 600; color: #1D3146;
  letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 3px;
}
.guarantee-title .motto {
  color: #0068A0; font-style: italic; text-transform: none;
  letter-spacing: 0.02em; font-size: 8.5pt;
}
.guarantee-items { font-size: 8.5pt; color: #1D3146; }
.guarantee-items strong { color: #0068A0; }
.brand-closing { margin-top: 3px; font-size: 7.5pt; color: #6B7280; font-style: italic; }
"""

_TYPE_LABEL = {
    "progress": "Progress Invoice",
    "tm": "Time & Materials Invoice",
    "change_order": "Change Order Invoice",
    "retainer": "Retainer Invoice",
}
# The badge says something to the CLIENT about the money, so it carries only
# the states a client needs to act on. Draft is an internal working state: it
# means nobody has sent this yet, which is a fact about BRIO's workflow and not
# about the client's obligation. Stamping it on a document that was then emailed
# invited the reading that the invoice was not yet payable.
#
# Void stays, deliberately and for the opposite reason: a cancelled invoice that
# looks ordinary is how a client pays something they should not.
_BADGE = {
    "paid": '<span class="badge badge-paid">Paid</span>',
    "partial": '<span class="badge badge-partial">Partial</span>',
    "sent": "",
    "overdue": '<span class="badge badge-unpaid">Overdue</span>',
    "draft": "",
    "void": '<span class="badge badge-draft">Void</span>',
}


def _esc(v: Any) -> str:
    return html.escape(str(v)) if v not in (None, "") else ""


def _money(n: Any) -> str:
    try:
        return f"${float(n or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_date(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    if isinstance(raw, (date, datetime)):
        d: date = raw.date() if isinstance(raw, datetime) else raw
        return d.strftime("%B %-d, %Y")
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10]).strftime("%B %-d, %Y")
        except ValueError:
            return raw
    return str(raw)


def _rows(items: list[tuple[str, Any]]) -> str:
    return "".join(
        f'<div class="row"><span class="label">{_esc(k)}:</span> {_esc(v)}</div>'
        for k, v in items
        if v not in (None, "")
    )


# --- type-specific bodies --------------------------------------------------


def _progress_body(payload: dict) -> str:
    items = payload.get("lineItems") or []
    if not items:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(it.get('description'))}</td>"
        f'<td class="text-center">{_esc(str(it["percentage"]) + "%") if it.get("percentage") else ""}</td>'
        f'<td class="text-right">{_money(it.get("amount"))}</td></tr>'
        for it in items
        if isinstance(it, dict)
    )
    milestone = (
        f'<p><strong>Milestone:</strong> {_esc(payload["milestone"])}</p>'
        if payload.get("milestone")
        else ""
    )
    return (
        f"<h2>Progress Billing</h2>{milestone}"
        "<table><thead><tr><th>Description</th>"
        '<th class="text-center">% of Contract</th>'
        '<th class="text-right">Amount</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _tm_body(payload: dict) -> str:
    out = ["<h2>Time &amp; Materials</h2>"]
    labor = payload.get("labor") or []
    if labor:
        rows = "".join(
            f"<tr><td>{_esc(l.get('worker') or l.get('role'))}</td>"
            f"<td>{_esc(l.get('description'))}</td>"
            f'<td class="text-center">{_esc(l.get("hours") or 0)}</td>'
            f'<td class="text-right">{_money(l.get("rate"))}/hr</td>'
            f'<td class="text-right">{_money(_num(l.get("hours")) * _num(l.get("rate")))}</td></tr>'
            for l in labor
            if isinstance(l, dict)
        )
        out.append(
            "<h3>Labor</h3><table><thead><tr><th>Worker / Role</th>"
            '<th>Description</th><th class="text-center">Hours</th>'
            '<th class="text-right">Rate</th><th class="text-right">Amount</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    materials = payload.get("materials") or []
    if materials:
        rows = "".join(
            f"<tr><td>{_esc(m.get('item'))}</td>"
            f"<td>{_esc(m.get('supplier'))}</td>"
            f'<td class="text-center">{_esc(m.get("qty") or 1)}</td>'
            f'<td class="text-right">{_money(m.get("unitCost"))}</td>'
            f'<td class="text-right">{_money(_num(m.get("qty") or 1) * _num(m.get("unitCost")))}</td></tr>'
            for m in materials
            if isinstance(m, dict)
        )
        out.append(
            "<h3>Materials</h3><table><thead><tr><th>Item</th><th>Supplier</th>"
            '<th class="text-center">Qty</th><th class="text-right">Unit Cost</th>'
            f'<th class="text-right">Amount</th></tr></thead><tbody>{rows}</tbody></table>'
        )
    return "".join(out)


def _change_order_body(payload: dict) -> str:
    items = payload.get("lineItems") or []
    if not items:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(it.get('description'))}</td>"
        f'<td class="text-right">{_money(it["labor"]) if it.get("labor") else ""}</td>'
        f'<td class="text-right">{_money(it["materials"]) if it.get("materials") else ""}</td>'
        f'<td class="text-right">{_money(it.get("amount"))}</td></tr>'
        for it in items
        if isinstance(it, dict)
    )
    head = ""
    if payload.get("changeOrderNumber"):
        head += f'<p><strong>Change Order #:</strong> {_esc(payload["changeOrderNumber"])}</p>'
    if payload.get("reason"):
        head += f'<p><strong>Reason:</strong> {_esc(payload["reason"])}</p>'
    return (
        f"<h2>Change Order</h2>{head}"
        "<table><thead><tr><th>Description</th>"
        '<th class="text-right">Labor</th><th class="text-right">Materials</th>'
        f'<th class="text-right">Amount</th></tr></thead><tbody>{rows}</tbody></table>'
    )


def _retainer_body(payload: dict) -> str:
    terms = payload.get("retainerTerms") or (
        "This deposit secures your project start date and will be applied to "
        "the final invoice."
    )
    return (
        "<h2>Retainer Deposit</h2><div class=\"highlight\">"
        f'<p><strong>Retainer Amount:</strong> {_money(payload.get("subtotal"))}</p>'
        f"<p>{_esc(terms)}</p></div>"
    )


_BODY = {
    "progress": _progress_body,
    "tm": _tm_body,
    "change_order": _change_order_body,
    "retainer": _retainer_body,
}


def build_invoice_html(
    *,
    number: str,
    inv_type: str,
    status: str,
    paid_amount: Any,
    payload: dict[str, Any],
    project: dict[str, Any],
    client: dict[str, Any],
) -> str:
    """Assemble the full HTML document for an invoice PDF."""
    subtotal = _num(payload.get("subtotal"))
    gst = _num(payload.get("gst")) or subtotal * 0.05
    # Includes adjustments in the fallback: without it a payload whose `total`
    # is missing would be rendered as subtotal + GST and quietly lose them.
    _adj = sum(_num(a.get("amount")) for a in (payload.get("adjustments") or [])
               if isinstance(a, dict))
    total = _num(payload.get("total")) or subtotal + gst + _adj
    paid = _num(paid_amount)
    balance = total - paid

    # Balanced two-column info grid — all rows split evenly (matches the
    # brio-admin invoice layout).
    all_rows = [
        ("Invoice #", number),
        ("Date", _fmt_date(payload.get("date"))),
        ("Client", client.get("name")),
        ("Email", client.get("email")),
        ("Phone", client.get("phone")),
        ("Project Address", project.get("address")),
        ("Client Address", client.get("address")),
        ("Estimate Ref", payload.get("estimateRef")),
    ]
    present = [(k, v) for k, v in all_rows if v not in (None, "")]
    mid = (len(present) + 1) // 2
    left = _rows(present[:mid])
    right = _rows(present[mid:])

    body = _BODY.get(inv_type, _progress_body)(payload)

    # Each adjustment is printed on its own line between GST and Total, with its
    # own description. A client who is being asked for GST missed on an earlier
    # invoice must be able to see exactly that, by name, rather than find the
    # Total not matching the arithmetic above it.
    adjustment_rows = ""
    for a in payload.get("adjustments") or []:
        if not isinstance(a, dict):
            continue
        amount = _num(a.get("amount"))
        if not amount:
            continue
        label = _esc(str(a.get("description") or "Adjustment"))
        adjustment_rows += (
            f'<tr><td>{label}</td>'
            f'<td class="text-right">{_money(amount)}</td></tr>'
        )

    total_rows = (
        f'<tr><td><strong>Subtotal</strong></td>'
        f'<td class="text-right">{_money(subtotal)}</td></tr>'
        f'<tr><td>GST (5%)</td><td class="text-right">{_money(gst)}</td></tr>'
        f'{adjustment_rows}'
        f'<tr class="total-row"><td><strong>Total</strong></td>'
        f'<td class="text-right"><strong>{_money(total)}</strong></td></tr>'
    )
    if paid > 0:
        total_rows += (
            f'<tr><td>Amount Paid</td>'
            f'<td class="text-right paid">{_money(paid)}</td></tr>'
            f'<tr class="total-row"><td><strong>Balance Due</strong></td>'
            f'<td class="text-right"><strong>{_money(balance)}</strong></td></tr>'
        )

    description = (
        f'<p class="invoice-desc">{_esc(payload["description"])}</p>'
        if payload.get("description")
        else ""
    )
    notes = (
        '<div class="scope"><h3>Scope of work included</h3>'
        f'<p>{_esc(payload["notes"])}</p></div>'
        if payload.get("notes")
        else ""
    )

    # The subtitle defaults to the project name, but an invoice may override it
    # via payload["subtitle"]. Needed when the job identifier and the billing
    # party disagree: INV-2026-10056 is work at 710 Cedar Rd billed to Sample Realty
    # Realty at their office, and printing "710 Cedar Rd" above two 880 Harbour
    # Drive addresses read as a contradiction.
    #
    # Checked with `is None`, not truthiness, so an explicit "" suppresses the
    # line entirely while a missing key still falls back to the project name.
    subtitle = payload.get("subtitle")
    if subtitle is None:
        subtitle = project.get("name") or "Project"
    subtitle_html = (
        f'<p class="subtitle">{_esc(subtitle)} &nbsp; {_BADGE.get(status, "")}</p>'
        if subtitle
        else f'<p class="subtitle">{_BADGE.get(status, "")}</p>'
    )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="logo-bar">
  <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
  <div class="company-info">
    {LETTERHEAD_HTML}
  </div>
</div>
<h1>{_TYPE_LABEL.get(inv_type, "Invoice")}</h1>
{subtitle_html}
<div class="info-grid">
  <div class="info-box">{left}</div>
  <div class="info-box">{right}</div>
</div>
{description}
{body}
<table class="totals"><tbody>{total_rows}</tbody></table>
{notes}
<div class="highlight">
  <p><strong>Payment Methods</strong></p>
  <p>E-Transfer: amir@builtbybrio.com<br>
  Cheque payable to: Built by BRIO<br>
  Terms: {_esc(payload.get("terms") or "Due upon receipt")}</p>
</div>
<div class="guarantee-strip">
  <div class="guarantee-title">
    <span class="motto">Built Right. Built by BRIO.</span> &nbsp;&middot;&nbsp;
    Every contract includes the 5-Star Guarantee
  </div>
  <div class="guarantee-items">
    <strong>On-Time</strong> &middot; <strong>Fixed-Price</strong> &middot;
    <strong>1-Year Workmanship Warranty</strong>
  </div>
  <div class="brand-closing">Thank you for trusting BRIO — {WEBSITE}</div>
</div>
</body></html>"""


def render_invoice_pdf(
    *,
    number: str,
    inv_type: str,
    status: str,
    paid_amount: Any,
    payload: dict[str, Any],
    project: dict[str, Any],
    client: dict[str, Any],
) -> bytes:
    """Render an invoice to PDF bytes."""
    from weasyprint import HTML  # lazy — heavy native deps

    document = build_invoice_html(
        number=number,
        inv_type=inv_type,
        status=status,
        paid_amount=paid_amount,
        payload=payload,
        project=project,
        client=client,
    )
    return HTML(string=document).write_pdf()
