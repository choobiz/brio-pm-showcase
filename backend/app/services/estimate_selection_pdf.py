"""Client-facing estimate PDFs — two modes off the same estimate payload.

  mode="detailed"  → every line item with a single client price (labor+material
                     combined), grouped by trade. "Send them all the items."
  mode="selection" → the LABOR + MATERIAL-SELECTION edition (BRIO's 5th estimate
                     type): cosmetic/fixture items tagged `selectable` show their
                     material as a client ALLOWANCE ("included — you may upgrade")
                     separated from the fixed BRIO labor, plus a "Your Selections"
                     summary. Non-selectable work stays bundled.

Client convention (matches estimate_pdf.py): overhead + profit are folded into
every figure via the margin multiplier; the client sees Subtotal → GST → Total,
never OH/profit. No curves (BRIO brand rule).
"""
from __future__ import annotations
from app.company import LEGAL_NAME

import re
from typing import Any

from app.services.estimate_pdf import _esc, _money, _num
from app.services.pdf_brand import logo_data_url

PRIMARY = "#0068A0"
ACCENT = "#1D3146"


def _margin(payload: dict) -> float:
    oh = _num(payload.get("overhead"))
    pr = _num(payload.get("profit"))
    return (1 + oh / 100) * (1 + pr / 100)


def _item_labor(it: dict) -> float:
    return _num(it.get("labor"))


def _item_material(it: dict) -> float:
    return _num(it.get("materials"))


def _item_total(it: dict) -> float:
    return _item_labor(it) + _item_material(it)


def _preselected_name(name: str) -> str:
    """Client allowances must name the PRE-SELECTED material, not the upgrade alternative.
    Cedar is a separate upgrade (covered in the package's discussion notes), so the base
    cladding allowance should read 'fibre-cement', never 'cedar or fibre-cement' — that
    ambiguity confuses the client. Resolves 'cedar or/·/ fibre-cement' → 'fibre-cement'."""
    s = name or ""
    s = re.sub(r"cedar\s*(?:or|/|,)\s*(fibre[\s-]*cement)", r"\1", s, flags=re.I)
    s = re.sub(r"(fibre[\s-]*cement)\s*(?:or|/|,)\s*cedar", r"\1", s, flags=re.I)
    return s


def _strip_rates(name: str) -> str:
    """Remove client-facing unit-rate and hours build-ups from an item name — the client
    sees folded category totals, never our per-unit rates or labour hours. Keeps dimensions
    (24" × 8", @ 24" O/C) and quantities (~250 sqft). Examples:
      '… — 250 sqft × $6.50/sqft installed' → '… — 250 sqft installed'
      '… light supervision @ $1,200/mo'      → '… light supervision'
      '… 3 swaps × $700'                     → '… 3 swaps'"""
    s = name or ""
    s = re.sub(r"\s*[@×x]\s*\$[\d,]+(?:\.\d+)?\s*/\s*[A-Za-z%]+", "", s)  # @ $1,200/mo · × $6.50/sqft
    s = re.sub(r"\s*[×x]\s*\$[\d,]+(?:\.\d+)?\b", "", s)                  # × $700
    s = re.sub(r"\s*\$[\d,]+(?:\.\d+)?\s*/\s*[A-Za-z]+", "", s)           # leftover $310/CY
    s = re.sub(r"\s*\d+(?:\.\d+)?\s*(?:hours?|hrs?)\b", "", s, flags=re.I)  # N hours
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"[—–-]\s*$", "", s).strip()
    return s


_MONEY = r"[-−–]?\$\s?\d[\d,]*(?:\.\d+)?\s*[kKmM]?(?:\s*[–-]\s*\$?\d[\d,]*(?:\.\d+)?\s*[kKmM]?)?"


def _strip_money(name: str) -> str:
    """Remove dollar figures left inside an item name.

    2026-10-27: 19 lines across the 11 current estimates still carried internal
    figures after _strip_rates, e.g. "(target raw $65,000)", "[per owner markup:
    line total $1,500]", "budget $2,000", "allowance ($3,200 ea)". Every client
    PDF that lists line names printed them. A bracketed or parenthesised aside
    that mentions money is dropped whole (it is a note to us); any other dollar
    figure is dropped with the word that labels it."""
    s = name or ""
    for _ in range(3):  # nested asides: [... ( $x ) ...]
        s2 = re.sub(r"\[[^\[\]]*\$[^\[\]]*\]", "", s)
        s2 = re.sub(r"\([^()]*\$[^()]*\)", "", s2)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\b(?:target raw|line total|budget|carried|carries|midpoint|range)?\s*:?\s*" + _MONEY
               + r"(?:\s*(?:ea|each|range|carried|ls|lump sum)\b)?", "", s, flags=re.I)
    if "[" not in s:
        s = s.replace("]", "")  # its opening half was inside a removed aside
    s = re.sub(r"\s+([,;:.)])", r"\1", s)
    s = re.sub(r"[(\[]\s*[)\]]", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"[\s,;:—–-]+$", "", s).strip()
    return s


def _client_item_name(name: str) -> str:
    """Client-safe item label: pre-selected material, no unit rates, hours or
    dollar figures."""
    return _strip_money(_strip_rates(_preselected_name(name or "")))


def _is_contingency(it: dict) -> bool:
    return "contingency" in str(it.get("name") or "").lower()


def _qty_is_zero(it: dict) -> bool:
    """True only when the item's quantity is explicitly numeric zero. Items with no
    quantity (None / blank / non-numeric) are kept — only a real 0 is dropped, so a
    client never reads a not-being-done line as included work."""
    q = it.get("qty")
    if q is None or q == "":
        return False
    try:
        return float(q) == 0
    except (TypeError, ValueError):
        return False


# Finish/fixture PRODUCTS the client selects — their material becomes an allowance
# they can upgrade or supply themselves. Matched by keyword in the ITEM name (no
# CSI code required, so it works on AI-generated estimates too). The client picks
# finish products (fixtures, tile, cabinets, doors, lighting) — NOT the commodity /
# structural build-up (lumber, drywall, concrete, wiring), which stays bundled.
_SEL_KEYWORD_RULES = [
    (("window", "glazing", "skylight"), "Windows & Skylights"),
    (("door", "door operator"), "Doors & Hardware"),
    (("floor", "flooring", "tile", "lvt", "laminate", "hardwood", "vinyl plank", "carpet"), "Flooring & Tile"),
    (("vanity", "cabinet", "countertop", "counter top", "millwork", "closet organizer"), "Cabinetry & Countertops"),
    (("toilet", "lavatory", "sink", "faucet", "shower", "bathtub", "grab bar", "mirror", "towel bar", "coat hook", "accessor"), "Plumbing Fixtures & Bath Accessories"),
    (("light fixture", "pot light", "recessed light", "pendant", "sconce", "chandelier", "vanity light", "downlight", "emergency light"), "Light Fixtures"),
    (("cladding", "siding", "shiplap"), "Exterior Cladding"),
    (("appliance", "range hood", "refrigerator", "dishwasher", "cooktop", "oven"), "Appliances"),
]

# Commodity / structural / rough-in materials the client does NOT select — checked
# FIRST so 'drywall board' or 'framing lumber' never slips into the allowance even
# if a finish keyword coincides.
_SEL_EXCLUDE = (
    "rough-in", "rough in", "dwv", "branch circuit", "circuit", "wiring", "supply rough",
    "sub-panel", "sub panel", "conduit", "lumber", "framing", "stud", "joist", "sheathing",
    "plywood", "osb", "drywall", "gypsum", "board", "mud", "tape", "insulation", "vapour",
    "vapor barrier", "concrete", "rebar", "gravel", "membrane", "waterproof", "primer",
    "paint", "caulk", "sealant", "adhesive", "fastener", "strapping", "blocking", "backing",
    "demolition", "disposal", "permit", "engineer", "supervision", "cleanup", "clean-up",
    # Labour/prep verbs — a fixture keyword inside a removal/protection task is not a
    # client selection (e.g. "Remove existing door frame", "Protect adjacent flooring").
    "remove", "removal", "protect", "haul", "sweep", "demo ",
)


def _selectable_group(cat_name: Any, it: dict) -> str | None:
    """Client-selectable finish/fixture group for an item, else None.

    The client picks finish PRODUCTS (fixtures, tile, cabinets, doors, lighting) —
    their material becomes an allowance they can upgrade or supply themselves.
    Commodity/structural materials (lumber, drywall, concrete, wiring…) are NOT
    selectable and stay bundled in the price."""
    if _is_contingency(it) or _item_material(it) <= 0:
        return None
    low = str(it.get("name") or "").lower()
    if any(x in low for x in _SEL_EXCLUDE):
        return None
    if it.get("selectable"):
        return it.get("selectionGroup") or "Selections"
    for kws, grp in _SEL_KEYWORD_RULES:
        if any(k in low for k in kws):
            return grp
    return None


def _cat_contingency(c: dict) -> float:
    return sum(_item_material(it) for it in (c.get("items") or []) if _is_contingency(it))


def client_totals(payload: dict) -> dict[str, float]:
    """The client-facing figures every client edition prints: trades and
    contingency at margin, subtotal, GST, total. One function so the email
    drafter quotes exactly what the PDF says."""
    m = _margin(payload)
    cats = [c for c in (payload.get("categories") or []) if isinstance(c, dict) and not c.get("optional")]
    trades = sum((_num(c.get("total")) - _cat_contingency(c)) * m for c in cats)
    contingency = sum(_cat_contingency(c) for c in cats) * m
    subtotal = trades + contingency
    gst = subtotal * 0.05
    return {"trades_subtotal": trades, "contingency": contingency, "subtotal": subtotal,
            "gst": gst, "total": subtotal + gst}


def selection_items(payload: dict) -> list[dict]:
    """The finishes and fixtures the client is asked to confirm, by group."""
    out = []
    for c in payload.get("categories") or []:
        if not isinstance(c, dict):
            continue
        for it in c.get("items") or []:
            if _qty_is_zero(it):
                continue
            grp = _selectable_group(c.get("name") or "", it)
            if grp:
                out.append({"group": grp, "name": _client_item_name(it.get("name") or "")})
    return out


def build_selection_html(payload: dict, project: dict, client: dict, mode: str, hide_total: bool = False) -> str:
    m = _margin(payload)
    cats = [c for c in (payload.get("categories") or []) if isinstance(c, dict)]
    proj_name = _esc(project.get("name") or "Project")
    address = _esc(project.get("address") or "")
    client_name = _esc((client or {}).get("name") or "")

    # Contingency is pulled OUT of each trade and shown as one separate line.
    def cat_contingency(c: dict) -> float:
        return _cat_contingency(c)
    def cat_client_total(c: dict) -> float:  # trade cost EX-contingency, client view
        return (_num(c.get("total")) - cat_contingency(c)) * m
    _t = client_totals(payload)
    trades_subtotal, contingency = _t["trades_subtotal"], _t["contingency"]
    subtotal, gst, total = _t["subtotal"], _t["gst"], _t["total"]

    # ---- selectable items: explicit `selectable` tag OR the cosmetic auto-rule ----
    selections: list[dict] = []
    for c in cats:
        cn = c.get("name") or ""
        for it in c.get("items") or []:
            if _qty_is_zero(it):
                continue  # de-scoped item — don't offer it as a selectable allowance
            grp = _selectable_group(cn, it)
            if grp:
                selections.append({
                    "group": grp,
                    "name": _client_item_name(it.get("name") or ""),
                    "material": _item_material(it),  # pre-tax material allowance (management fee excluded)
                    "labor": _item_labor(it) * m,
                })

    logo = logo_data_url()
    # "scopepack" (2026-10-27, Amir): the project's scope document is merged in
    # front of this (see the selection-pdf route), then the material selections
    # with NO prices for the client to confirm, then the lump-sum detail and one
    # price. Everything after the selections is the lump-sum edition.
    lump = mode in ("lumpsum", "scopepack")
    doc_type = (
        "Material Selections and Lump-Sum Price" if mode == "scopepack"
        else "Lump-Sum Estimate" if mode == "lumpsum"
        else "Estimate — Material Selections" if mode == "selection"
        else "Detailed Estimate"
    )
    parts: list[str] = []

    # ── Cover ────────────────────────────────────────────────────────────────
    if lump:
        # No full-page cover for the lump-sum edition — a slim header band, then
        # straight into the scope of work (client asked to drop the cover here).
        meta_bits = "".join([
            f'<div class="mini-proj">{proj_name}</div>',
            f'<div class="mini-sub">{address}</div>' if address else "",
            f'<div class="mini-sub">Prepared for {client_name}</div>' if client_name else "",
            f'<div class="mini-sub">{_esc(payload.get("reference") or "")}</div>',
        ])
        parts.append(f"""
    <div class="mini-head">
      <img class="mini-logo" src="{logo}" alt="BRIO"/>
      <div class="mini-meta">
        <div class="mini-kicker">{LEGAL_NAME} — {doc_type}</div>
        {meta_bits}
      </div>
    </div>""")
    else:
        parts.append(f"""
    <div class="cover">
      <div class="brand"><img src="{logo}" alt="BRIO"/></div>
      <div class="cover-body">
        <div class="doc-kicker">{LEGAL_NAME}</div>
        <h1 class="doc-title">{doc_type}</h1>
        <div class="cover-meta">
          <div class="proj-name">{proj_name}</div>
          {f'<div>{address}</div>' if address else ''}
          {f'<div>Prepared for {client_name}</div>' if client_name else ''}
          <div>{_esc(payload.get('reference') or '')}</div>
        </div>
        {'' if hide_total else f'<div class="cover-total"><span>Project Total</span><strong>{_money(total)}</strong><em>incl. GST</em></div>'}
      </div>
    </div>""")

    # ── Selection-mode intro + Your Selections summary ───────────────────────
    if mode == "selection" and selections:
        by_group: dict[str, list[dict]] = {}
        for s in selections:
            by_group.setdefault(s["group"], []).append(s)
        mat_total = sum(s["material"] for s in selections)
        intro = (
            "<p class='lead'>The items below are where <b>you choose the materials</b>. For each we've "
            "pre-selected a quality product and <b>included a material allowance</b> — keep it, upgrade, or "
            "<b>supply the material yourself</b>. Only an upgrade above the allowance changes your total; "
            "installation is already included in your price.</p>"
        )
        blocks = ""
        for grp, rows in by_group.items():
            lis = "".join(
                f'<tr><td>{_esc(r["name"])}</td>'
                f'<td class="r mat">{_money(r["material"])}<span class="up">you may upgrade</span></td></tr>'
                for r in rows
            )
            blocks += (f'<div class="sel-group"><div class="sel-grp-head">{_esc(grp)}</div>'
                       f'<table class="sel"><thead><tr><th>Item</th>'
                       f'<th class="r">Material allowance<br>(your selection)</th></tr></thead>'
                       f'<tbody>{lis}</tbody></table></div>')
        parts.append(
            f'<section class="sec"><h2>Your Material Selections</h2>{intro}{blocks}'
            f'<p class="fine">Material allowances total <b>{_money(mat_total)}</b>, shown at material cost. '
            f'Upgrading an item adjusts only the material difference above its allowance. If you prefer to '
            f'<b>supply a material yourself</b>, its allowance is credited from your total — installation stays as quoted. '
            f'All adjustments are confirmed by written change order.</p></section>'
        )

    # ── Scope pack: selections to confirm, no prices ─────────────────────────
    if mode == "scopepack":
        if selections:
            by_grp: dict[str, list[dict]] = {}
            for sel in selections:
                by_grp.setdefault(sel["group"], []).append(sel)
            blocks = ""
            for grp, rows in by_grp.items():
                lis = "".join(
                    f'<tr><td>{_esc(r["name"])}</td><td class="confirm"></td></tr>' for r in rows)
                blocks += (f'<div class="sel-group"><div class="sel-grp-head">{_esc(grp)}</div>'
                           f'<table class="sel"><thead><tr><th>Item and the product we selected</th>'
                           f'<th class="confirm-h">OK, or change to</th></tr></thead>'
                           f'<tbody>{lis}</tbody></table></div>')
            parts.append(
                '<section class="sec"><h2>Material Selections: Please Confirm</h2>'
                "<p class='lead'>These are the finishes and fixtures we have selected for your project. Please "
                "check each one: write <b>OK</b>, or the product you would prefer, and send this back to us. "
                "A different product may change the price; any change is confirmed by written change order "
                "before it is ordered.</p>"
                f'{blocks}</section>')
        else:
            parts.append(
                '<section class="sec"><h2>Material Selections</h2>'
                "<p class='lead'>This estimate has no finishes or fixtures for you to select.</p></section>")

    # ── Estimate detail — categories + line items ────────────────────────────
    body = ""
    for c in cats:
        cname = _esc(c.get("name") or "Trade")
        ctotal = cat_client_total(c)
        opt = ' <span class="opt">(optional)</span>' if c.get("optional") else ""
        rows = ""
        for it in c.get("items") or []:
            if _is_contingency(it):
                continue  # folded into the category total; not shown line-by-line
            if _qty_is_zero(it):
                continue  # de-scoped (qty 0) — never list on a client estimate (reads as promised work)
            nm = _esc(_client_item_name(it.get("name") or ""))
            # Item line prices are intentionally omitted — the client sees category totals
            # only; per-item prices stay internal.
            if mode == "selection" and _selectable_group(c.get("name"), it):
                rows += (
                    f'<tr class="sel-row"><td colspan="2">{nm}'
                    f'<span class="sel-tag">your material selection ↑</span></td></tr>'
                )
            else:
                rows += f'<tr><td colspan="2">{nm}</td></tr>'
        desc = _esc((c.get("description") or "").strip())
        desc_html = f'<tr class="desc"><td colspan="2">{desc}</td></tr>' if desc else ""
        if not rows and round(ctotal, 2) == 0:
            continue  # nothing renders and nothing is billed — drop the trade, description
            # included. Covers BOTH shapes of empty trade: every line item de-scoped (qty 0)
            # and no line items at all. A header with no live items reads to the client as
            # promised work. Keyed on cost, not on the presence of an items array: a
            # summary-level trade carrying a real stored cost is KEPT even with no line items,
            # because dropping it would hide billed scope. Money is unaffected either way —
            # the subtotal sums stored category totals independently of what's displayed.
        # Lump-sum edition hides per-category prices — the client sees the full
        # scope, then one total. Category header spans both columns (no price).
        if lump:
            cat_head = f'<tr class="cat-head"><th colspan="2">{cname}{opt}</th></tr>'
        else:
            cat_head = f'<tr class="cat-head"><th>{cname}{opt}</th><th class="r">{_money(ctotal)}</th></tr>'
        body += (f'<div class="cat"><table class="items"><thead>'
                 f'{cat_head}</thead>'
                 f'<tbody>{desc_html}{rows}</tbody></table></div>')
    detail_title = ("Estimate Detail" if mode == "scopepack" else "Scope of Work") if lump else "Estimate Detail"
    parts.append(f'<section class="sec"><h2>{detail_title}</h2>{body}</section>')

    # ── Totals ───────────────────────────────────────────────────────────────
    if lump:
        # One fixed price for the whole scope — contingency folded into the subtotal,
        # no per-trade breakdown. Subtotal → GST → Lump-Sum Price.
        parts.append(
            '<section class="sec totals"><table class="tot">'
            f'<tr class="sub-line"><td>Subtotal</td><td class="r">{_money(subtotal)}</td></tr>'
            f'<tr><td>GST (5%)</td><td class="r">{_money(gst)}</td></tr>'
            f'<tr class="grand"><td>Lump-Sum Price</td><td class="r">{_money(total)}</td></tr>'
            '</table><p class="fine">Fixed lump-sum price for the scope of work described above — in CAD and '
            'inclusive of GST. A shared contingency for unforeseen conditions is included in the price; unused '
            'contingency is not charged. Changes to the scope are handled by written change order.</p></section>'
        )
    else:
        parts.append(
            '<section class="sec totals"><table class="tot">'
            f'<tr><td>Trades subtotal</td><td class="r">{_money(trades_subtotal)}</td></tr>'
            f'<tr><td>Project contingency</td><td class="r">{_money(contingency)}</td></tr>'
            f'<tr class="sub-line"><td>Subtotal</td><td class="r">{_money(subtotal)}</td></tr>'
            f'<tr><td>GST (5%)</td><td class="r">{_money(gst)}</td></tr>'
            f'<tr class="grand"><td>Project Total</td><td class="r">{_money(total)}</td></tr>'
            '</table><p class="fine">Contingency is a shared allowance for unforeseen conditions across all trades; '
            'unused contingency is not charged. All figures in CAD and inclusive of GST. This estimate is based on the current '
            'permit-set drawings; changes in scope or client selections above the included allowances are handled by '
            'written change order.</p></section>'
        )

    # ── Exclusions ───────────────────────────────────────────────────────────
    exc = payload.get("exclusions") or []
    if exc:
        lis = "".join(f"<li>{_esc(x)}</li>" for x in exc)
        parts.append(f'<section class="sec"><h2>What\'s Not Included</h2><ul>{lis}</ul></section>')

    # The full-bleed cover needs a zero-margin first page; the lump-sum edition has
    # no cover, so its first page keeps the normal print margins.
    first_page_css = (
        "" if lump
        else '@page :first { margin: 0; @bottom-center { content: ""; } }'
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{first_page_css}{_CSS}</style></head><body>{''.join(parts)}</body></html>"


def render_client_selection_pdf(payload: dict, project: dict, client: dict, mode: str = "detailed", hide_total: bool = False) -> bytes:
    from weasyprint import HTML
    return HTML(string=build_selection_html(payload, project, client or {}, mode, hide_total)).write_pdf()


_CSS = f"""
@page {{ size: Letter; margin: 16mm 16mm 18mm; @bottom-center {{ content: "{LEGAL_NAME} — Estimate"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Open Sans','Helvetica Neue',Arial,sans-serif; color: #26303a; font-size: 10.5pt; line-height: 1.5; margin: 0; }}
h1,h2,h3 {{ font-family: 'Sora','Helvetica Neue',Arial,sans-serif; color: {ACCENT}; }}
.mini-head {{ display: flex; align-items: center; border-bottom: 2px solid {ACCENT}; padding-bottom: 10px; margin-bottom: 14px; }}
/* Logo/text separation is a margin, NOT flex `gap` — WeasyPrint 62 ignores gap on
   flex containers, so gap silently collapsed the logo against the project title. */
.mini-head .mini-logo {{ height: 44px; margin-right: 24px; }}
.mini-head .mini-kicker {{ font-size: 8.5pt; letter-spacing: 1.5px; text-transform: uppercase; color: {PRIMARY}; font-weight: 600; margin-bottom: 3px; }}
.mini-head .mini-proj {{ font-size: 12pt; font-weight: 600; color: {ACCENT}; }}
.mini-head .mini-sub {{ font-size: 9.5pt; color: #5b6672; }}
.cover {{ min-height: 100vh; padding: 24mm 22mm; color: {ACCENT}; background: #fff; page-break-after: always; display: flex; flex-direction: column; }}
.cover .brand img {{ height: 40px; }}
.cover .cover-body {{ margin-top: auto; }}
.cover .doc-kicker {{ font-size: 9pt; letter-spacing: 3px; text-transform: uppercase; color: #8a94a0; }}
.cover h1 {{ color: {ACCENT}; font-size: 27pt; line-height: 1.08; margin: 6px 0 0; }}
.cover h1.doc-title {{ color: {PRIMARY}; font-size: 32pt; line-height: 1.05; letter-spacing: -0.5px; margin: 10px 0 2px; }}
.cover .proj-name {{ font-size: 12.5pt; font-weight: 600; color: {ACCENT}; margin-top: 8px; }}
.cover .cover-meta {{ margin-top: 12px; color: #5b6672; font-size: 11pt; }}
.cover .cover-meta div {{ margin-top: 3px; }}
.cover .cover-total {{ margin-top: 20px; padding-top: 14px; border-top: 2px solid {PRIMARY}; }}
.cover .cover-total span {{ display: block; font-size: 9pt; letter-spacing: 2px; text-transform: uppercase; color: #8a94a0; }}
.cover .cover-total strong {{ font-family: 'Sora',Arial,sans-serif; font-size: 30pt; color: {PRIMARY}; }}
.cover .cover-total em {{ font-style: normal; color: #5b6672; margin-left: 8px; font-size: 10pt; }}
.sec {{ margin: 0 0 14px; }}
.sec h2 {{ font-size: 14pt; border-bottom: 2px solid {PRIMARY}; padding-bottom: 5px; margin: 16px 0 10px; }}
p.lead {{ color: #4a5560; }}
p.fine {{ font-size: 8.5pt; color: #6b7580; margin-top: 8px; }}
.opt {{ color: #9aa3ad; font-size: 9pt; }}
.cat {{ page-break-inside: avoid; margin-bottom: 10px; }}
table.items {{ width: 100%; border-collapse: collapse; }}
table.items tr.cat-head th {{ text-align: left; font-family: 'Sora',Arial,sans-serif; font-size: 11.5pt; color: {PRIMARY}; border-bottom: 1.5px solid {PRIMARY}; padding: 6px 4px 5px; }}
table.items tr.cat-head th.r {{ text-align: right; }}
table.items td {{ padding: 4px 4px; border-bottom: 1px solid #eef1f4; font-size: 10pt; }}
table.items td.r {{ text-align: right; white-space: nowrap; }}
table.items tr.desc td {{ color: #6b7580; font-size: 9pt; border-bottom: 1px solid #eef1f4; padding-bottom: 6px; }}
table.items tr.sub2 td {{ color: #8a94a0; font-size: 8.5pt; border-bottom: 1px solid #eef1f4; padding: 0 4px 5px; }}
.sel-tag {{ display: inline-block; margin-left: 8px; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .5px; color: {PRIMARY}; }}
.sel-group {{ margin: 8px 0 12px; page-break-inside: avoid; }}
.sel-grp-head {{ font-family: 'Sora',Arial,sans-serif; font-size: 11pt; color: {ACCENT}; margin-bottom: 4px; }}
table.sel {{ width: 100%; border-collapse: collapse; }}
table.sel th {{ text-align: left; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .5px; color: #8a94a0; border-bottom: 1.5px solid #cfd6dd; padding: 4px 5px; }}
table.sel th.r, table.sel td.r {{ text-align: right; }}
table.sel th {{ vertical-align: bottom; }}
table.sel td {{ padding: 5px 5px; border-bottom: 1px solid #f0f3f6; font-size: 9.5pt; }}
table.sel td.lab {{ color: #5b6672; white-space: nowrap; }}
table.sel th.confirm-h {{ width: 38%; }}
table.sel td.confirm {{ border-bottom: 1px solid #9aa3ad; }}
table.sel td.mat {{ color: {ACCENT}; font-weight: 600; white-space: nowrap; }}
table.sel .up {{ display: block; font-size: 7pt; color: {PRIMARY}; font-weight: 400; text-transform: uppercase; letter-spacing: .3px; }}
.totals {{ page-break-inside: avoid; }}
table.tot {{ width: 55%; margin-left: auto; border-collapse: collapse; }}
table.tot td {{ padding: 5px 4px; border-bottom: 1px solid #eef1f4; }}
table.tot td.r {{ text-align: right; white-space: nowrap; }}
table.tot tr.sub-line td {{ border-top: 1px solid #cfd6dd; font-weight: 600; }}
table.tot tr.grand td {{ font-family: 'Sora',Arial,sans-serif; font-weight: 700; color: {PRIMARY}; border-top: 2px solid #cfd6dd; font-size: 13pt; padding-top: 7px; border-bottom: none; }}
ul {{ margin: 5px 0; padding-left: 16px; }}
li {{ margin: 4px 0; font-size: 9.5pt; color: #4a5560; }}
"""
