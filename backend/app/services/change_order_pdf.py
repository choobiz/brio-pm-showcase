"""Render a Change Order PDF — BRIO-branded, via WeasyPrint.

A change order is not an invoice and must not look like one. An invoice says
"pay this"; a change order says "authorise this, and here is the working behind
the number". So this document leads with the price impact and the schedule
impact side by side, shows the quantity breakdown rather than a lump sum, and
ends with a signature block — the signature is the whole reason the document
exists, because a change order's value over a revised estimate is that it
records who approved the extra work and when.

The presentation layer (CSS, letterhead, money/date helpers) is IMPORTED from
invoice_pdf rather than copied. Both are BRIO money documents and they must not
drift apart visually; a second copy of a 100-line stylesheet is how they would.
"""

from __future__ import annotations

import html
from typing import Any

from app.company import LETTERHEAD_HTML, WEBSITE
from app.services.client_text import PRICING_CLAUSE, redact_internal_pricing
from app.services.invoice_pdf import _CSS, _fmt_date, _money, _num, _rows
from app.services.pdf_brand import logo_data_url

# Extra rules on top of the shared invoice stylesheet. Everything here is
# specific to a change order: the two headline impact figures and the
# signature block.
_CO_CSS = """
.impact-grid { display: flex; gap: 12px; margin: 14px 0 4px 0; }
.impact {
  flex: 1; background: #F0F7FC; border-left: 4px solid #0068A0;
  padding: 10px 14px;
}
.impact .k {
  font-size: 7.5pt; font-weight: 600; color: #6B7280;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px;
}
.impact .v { font-size: 16pt; font-weight: 700; color: #0068A0; line-height: 1.2; }
.impact .sub { font-size: 8pt; color: #6B7280; margin-top: 1px; }
.credit .v { color: #065F46; }
ul.plain { margin: 2px 0 8px 16px; font-size: 9pt; }
ul.plain li { margin-bottom: 2px; }
.sign-grid { display: flex; gap: 22px; margin-top: 26px; }
.sign { flex: 1; }
.sign .line { border-bottom: 1px solid #1D3146; height: 30px; }
.sign .who {
  font-size: 8pt; color: #6B7280; margin-top: 3px;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.decided {
  margin-top: 18px; background: #D1FAE5; border-left: 4px solid #065F46;
  padding: 9px 14px; font-size: 9pt; color: #065F46;
}
"""

_BADGE = {
    # Draft and pending are internal workflow states, but unlike an invoice a
    # change order is legitimately CIRCULATED while unapproved — that is how it
    # gets approved. The client must be able to see that they are looking at a
    # proposal, so these badges stay.
    "draft": '<span class="badge badge-draft">Draft — not yet issued</span>',
    "pending": '<span class="badge badge-partial">Awaiting approval</span>',
    "approved": '<span class="badge badge-paid">Approved</span>',
    "rejected": '<span class="badge badge-unpaid">Rejected</span>',
    "void": '<span class="badge badge-draft">Void</span>',
}


def _esc(v: Any) -> str:
    return html.escape(str(v)) if v not in (None, "") else ""


def _days(n: Any) -> str:
    d = int(_num(n))
    if d == 0:
        return "No change"
    return f"{d:+d} day" + ("" if abs(d) == 1 else "s")


def _clean_item_text(text: Any) -> str:
    """Trim an internal-pricing clause off a line item's description.

    Unlike assumptions and exclusions, a line item is NEVER dropped here even
    if it reads as a pure estimating artifact: the lines have to sum to the
    total on the page, and silently removing one would leave a document whose
    arithmetic does not work. Only the trailing clause goes.
    """
    return PRICING_CLAUSE.sub("", str(text or "")).rstrip(" ,;:-—–")


def _bullets(title: str, items: Any) -> str:
    cleaned = redact_internal_pricing([str(i) for i in (items or []) if str(i).strip()])
    if not cleaned:
        return ""
    lis = "".join(f"<li>{_esc(i)}</li>" for i in cleaned)
    return f"<h3>{_esc(title)}</h3><ul class=\"plain\">{lis}</ul>"


def _credits(items: list[dict]) -> str:
    """Credits, shown WITH their value.

    Everything else here is priced as a lump sum and the per-line money stays
    internal. A credit is the deliberate exception: you conceal your cost
    structure, you do not conceal money handed back. A credit buried in a list
    of work items with no figure beside it reads as another thing being done
    rather than a reduction, and the client never sees the concession.
    """
    rows = []
    for it in items:
        if not isinstance(it, dict) or _num(it.get("amount")) >= 0:
            continue
        desc = _clean_item_text(it.get("description"))
        if not desc:
            continue
        rows.append(
            f"<tr><td>{_esc(desc)}</td>"
            f'<td class="text-right">({_money(abs(_num(it.get("amount"))))})</td></tr>'
        )
    if not rows:
        return ""
    return (
        "<h2>Credits applied</h2>"
        "<table><thead><tr><th>Description</th>"
        '<th class="text-right" style="width:120px">Credit</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _work_included(items: list[dict]) -> str:
    """The scope the lump sum buys — descriptions only, no per-line money.

    This document is priced as a LUMP SUM. The internal breakdown behind the
    figure stays in brio-pm, where it is what the office checks before issuing;
    it does not go to the client. Publishing unit rates on a change order
    invites the price to be re-negotiated line by line, and it exposes BRIO's
    rate structure on a document whose whole purpose is to get a signature.
    That is the same rule the estimate renderer has always followed — the
    client sees scope, subtotal, GST and total.

    The descriptions still carry their quantities inline ("100 LF x 8' high"),
    so nothing the client needs in order to argue with the SCOPE is lost.
    """
    lis = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if _num(it.get("amount")) < 0:
            continue          # credits get their own block, with the figure
        desc = _clean_item_text(it.get("description"))
        if desc:
            lis.append(f"<li>{_esc(desc)}</li>")
    if not lis:
        return ""
    return f"<h2>Work included</h2><ul class=\"plain\">{''.join(lis)}</ul>"


def build_change_order_html(
    *,
    co: dict[str, Any],
    project: dict[str, Any],
    client: dict[str, Any],
) -> str:
    """Assemble the full HTML document for a change order PDF.

    `co` is the `co_read()` shape from the change-orders router.
    """
    detail = co.get("detail") or {}
    items = [i for i in (detail.get("line_items") or []) if isinstance(i, dict)]

    # The total is DERIVED from the line items when they exist, and falls back
    # to cost_impact only for a hand-written change order with no breakdown.
    # Taking cost_impact while also printing lines that sum to something else
    # would put two different numbers on the same page.
    subtotal = (
        round(sum(_num(i.get("amount")) for i in items), 2)
        if items
        else _num(co.get("cost_impact"))
    )
    gst = round(subtotal * 0.05, 2)
    total = round(subtotal + gst, 2)

    info = [
        ("Change Order", co.get("reference")),
        ("Date", _fmt_date(co.get("created_at"))),
        ("Project", project.get("name")),
        ("Project Address", project.get("address")),
        ("Client", client.get("name")),
        ("Email", client.get("email")),
        ("Phone", client.get("phone")),
    ]
    present = [(k, v) for k, v in info if v not in (None, "")]
    mid = (len(present) + 1) // 2

    credit = " credit" if subtotal < 0 else ""
    impact = (
        '<div class="impact-grid">'
        f'<div class="impact{" credit" if subtotal < 0 else ""}">'
        f'<div class="k">Cost impact{credit}</div>'
        f'<div class="v">{_money(total)}</div>'
        f'<div class="sub">{_money(subtotal)} + GST {_money(gst)}</div></div>'
        '<div class="impact"><div class="k">Schedule impact</div>'
        f'<div class="v">{_esc(_days(co.get("schedule_impact_days")))}</div>'
        '<div class="sub">to the contract completion date</div></div>'
        "</div>"
    )

    description = (
        f'<p class="invoice-desc">{_esc(co.get("description"))}</p>'
        if co.get("description")
        else ""
    )

    # One figure, presented as a lump sum. The label says so explicitly so the
    # absence of a per-line breakdown reads as the pricing basis it is, rather
    # than as something missing from the document.
    totals = (
        '<table class="totals"><tbody>'
        f'<tr><td><strong>Lump sum, pre-tax</strong></td><td class="text-right">{_money(subtotal)}</td></tr>'
        f'<tr><td>GST (5%)</td><td class="text-right">{_money(gst)}</td></tr>'
        '<tr class="total-row"><td><strong>Total change to contract</strong></td>'
        f'<td class="text-right"><strong>{_money(total)}</strong></td></tr>'
        "</tbody></table>"
    )

    basis = _bullets("Assumptions", detail.get("assumptions")) + _bullets(
        "Exclusions", detail.get("exclusions")
    )

    # An approved change order records the decision instead of asking for one.
    if co.get("status") == "approved" and co.get("decided_at"):
        note = co.get("owner_note")
        closing = (
            f'<div class="decided"><strong>Approved</strong> on '
            f"{_esc(_fmt_date(co.get('decided_at')))}."
            + (f" Owner note: {_esc(note)}" if note else "")
            + "</div>"
        )
    else:
        closing = (
            '<h2>Authorisation</h2><p style="font-size:9pt">Signing below authorises '
            "the work described above, the cost change and the schedule change, as an "
            "amendment to the existing contract. Work does not proceed until this is signed.</p>"
            '<div class="sign-grid">'
            '<div class="sign"><div class="line"></div><div class="who">Owner / Client &mdash; signature &amp; date</div></div>'
            '<div class="sign"><div class="line"></div><div class="who">Built by BRIO &mdash; signature &amp; date</div></div>'
            "</div>"
        )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}{_CO_CSS}</style></head><body>
<div class="logo-bar">
  <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
  <div class="company-info">
    {LETTERHEAD_HTML}
  </div>
</div>
<h1>Change Order</h1>
<p class="subtitle">{_esc(co.get("reference"))} &mdash; {_esc(co.get("title"))} &nbsp; {_BADGE.get(str(co.get("status")), "")}</p>
<div class="info-grid">
  <div class="info-box">{_rows(present[:mid])}</div>
  <div class="info-box">{_rows(present[mid:])}</div>
</div>
{impact}
{description}
{_work_included(items)}
{_credits(items)}
{totals}
{basis}
{closing}
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


def render_change_order_pdf(
    *, co: dict[str, Any], project: dict[str, Any], client: dict[str, Any]
) -> bytes:
    from weasyprint import HTML  # lazy — heavy native deps

    return HTML(string=build_change_order_html(co=co, project=project, client=client)).write_pdf()
