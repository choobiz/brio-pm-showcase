"""Client estimate priced by CSI division, with Div 09 broken out by scope.

456 W Sample, 2026-10-27: the client's project manager asked to "breakdown
the costs per division and for Div-09 Finishes, breakout the costs per scope
(Framing/Drywall, T-Bar ceiling, Flooring, etc)". Amir's choices:
  * markup and each trade's contingency are folded into its division, as in
    "Client: summary", so the divisions add up to the lump sum already sent;
  * Div 09 is broken out now; BREAKOUTS takes other divisions later;
  * each scope lists its lines without line prices;
  * firestopping stays under "Other finishes".

A line's scope is the one saved on it (item["subScope"], set on the review
panel) or else the first keyword rule that matches. Rule order matters and is
pinned by tests: painting before flooring ("paint ... MDF base"), flooring
before drywall ("substrate prep ... patch"), and no bare "transition" (it put
"Drywall bulkheads ... ceiling transitions" under Flooring).
"""
from __future__ import annotations

from typing import Any

from app.services.estimate_selection_pdf import (
    _client_item_name,
    _esc,
    _item_labor,
    _item_material,
    _is_contingency,
    _margin,
    _money,
    _num,
    _qty_is_zero,
)

OTHER = "Other finishes"

#: Division code -> ordered (label, keywords). First match wins.
BREAKOUTS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "09": [
        ("Wall coverings (09 72)", ("wallcovering", "wall covering")),
        ("Tiling (09 30)", ("ceramic tile", "porcelain tile", "backsplash", "tile floor", "tile wall")),
        ("Acoustical / T-bar ceilings (09 51)", ("t-bar", "tegular", "ceiling tile", "ceiling grid", "grid at",
                                                  "suspended ceiling", "acoustic")),
        ("Painting and coatings (09 91)", ("paint", "primer", "prime ", "electrostatic", "coating", "touch-up",
                                           " stain ", "stained")),  # not "stainless"
        ("Flooring (09 60s)", ("carpet", "lvt", "vinyl plank", "resilient", "slab", "substrate", "flooring",
                               "transition strip", "schluter", "wall base", "rubber base", "mdf base", "floor ")),
        ("Framing and drywall (09 21/09 22)", ("stud", "framing", "gypsum", "drywall", "partition", "bulkhead",
                                               "level 5", "level 4", "skim", "tape and fill", "make good",
                                               "patch", "demising")),
    ],
}


def division_code(name: Any) -> str | None:
    """'09 Finishes' -> '09'."""
    head = str(name or "").strip().split(" ", 1)[0]
    return head.zfill(2) if head.isdigit() else None


def scope_labels(code: str) -> list[str]:
    return [label for label, _ in BREAKOUTS.get(code, [])] + [OTHER]


def suggest(code: str, name: str) -> str:
    low = f" {name.lower()} "
    for label, kws in BREAKOUTS.get(code, []):
        if any(k in low for k in kws):
            return label
    return OTHER


def scope_of(code: str, item: dict) -> str:
    saved = item.get("subScope")
    return saved if saved in scope_labels(code) else suggest(code, item.get("name") or "")


def _cents_split(total: float, weights: list[float]) -> list[float]:
    """``total`` split by ``weights``, in cents, summing to ``total`` exactly."""
    t = round(total * 100)
    w = sum(weights)
    if w <= 0 or not weights:
        return [0.0] * len(weights)
    raw = [t * x / w for x in weights]
    cents = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - cents[i], reverse=True)
    for i in order[: t - sum(cents)]:
        cents[i] += 1
    return [c / 100 for c in cents]


def _tidy(name: str | None) -> str | None:
    """What the client-name cleaner leaves behind when it strips hours: "( PM)",
    "(, one third of the PM shared with Suite 100)" (456 corridor, 2026-11-06)."""
    if not name:
        return name
    import re
    n = re.sub(r"\(\s*(?:PM)?\s*\)", "", name)
    n = re.sub(r"\(\s*,\s*one third of the PM shared with [^)]*\)", "", n)
    n = re.sub(r"\(\s*,\s*", "(", n)
    n = re.sub(r"\s{2,}", " ", n).strip()
    return re.sub(r"\s+([,.;:])", r"\1", n)


def breakdown(payload: dict) -> dict[str, Any]:
    """Divisions (and Div 09 scopes) priced for the client. Every figure is in
    the client's terms: markup and the trade's contingency folded in."""
    from app.services.estimate_selection_pdf import client_totals

    m = _margin(payload)
    cats = [c for c in (payload.get("categories") or []) if isinstance(c, dict) and not c.get("optional")
            and round(_num(c.get("total")) * m, 2) != 0]
    # Split the proposal's own subtotal across divisions in cents, so they add
    # up to it exactly. Rounding each division on its own left the corridor
    # 2 cents off the lump sum already sent (2026-10-27).
    t = client_totals(payload)
    amounts = _cents_split(round(t["subtotal"], 2), [_num(c.get("total")) * m for c in cats])
    divisions: list[dict[str, Any]] = []
    for c, amount in zip(cats, amounts):
        code = division_code(c.get("name"))
        lines = [it for it in (c.get("items") or []) if not _is_contingency(it) and not _qty_is_zero(it)]
        names = [n for n in (_tidy(_client_item_name(it.get("name") or "")) for it in lines) if n]
        div = {"code": code, "name": c.get("name") or "", "amount": amount,
               "description": (c.get("description") or "").strip(), "scopes": None, "lines": names}
        if code in BREAKOUTS and lines:
            groups: dict[str, list[dict]] = {}
            for it in lines:
                groups.setdefault(scope_of(code, it), []).append(it)
            labels = [lb for lb in scope_labels(code) if lb in groups]
            weights = [sum(_item_labor(it) + _item_material(it) for it in groups[lb]) for lb in labels]
            amounts = _cents_split(amount, weights)
            div["scopes"] = [{"label": lb, "amount": a,
                              "lines": [_tidy(_client_item_name(it.get("name") or "")) for it in groups[lb]]}
                             for lb, a in zip(labels, amounts)]
        divisions.append(div)
    # Subtotal, GST and total exactly as every other client edition prints them.
    return {"divisions": divisions, "subtotal": round(t["subtotal"], 2), "gst": t["gst"], "total": t["total"]}


def review_lines(payload: dict, code: str = "09") -> list[dict[str, Any]]:
    """The lines of one division with their suggested and saved scope, for the
    review panel. Positions are (category index, item index) in the payload."""
    out = []
    for ci, c in enumerate(payload.get("categories") or []):
        if not isinstance(c, dict) or division_code(c.get("name")) != code:
            continue
        for ii, it in enumerate(c.get("items") or []):
            if _is_contingency(it) or _qty_is_zero(it):
                continue
            name = it.get("name") or ""
            out.append({"cat_index": ci, "item_index": ii, "name": _client_item_name(name),
                        "suggested": suggest(code, name), "saved": it.get("subScope"),
                        "scope": scope_of(code, it)})
    return out


def apply_choices(payload: dict, code: str, choices: list[dict]) -> int:
    """Store scope choices on the lines (item["subScope"]). A choice equal to
    the suggestion clears the saved value, so a later rule fix still applies.
    Returns how many lines changed."""
    allowed = set(scope_labels(code))
    cats = payload.get("categories") or []
    changed = 0
    for ch in choices:
        ci, ii, label = ch["cat_index"], ch["item_index"], ch["scope"]
        if label not in allowed:
            raise ValueError(f"unknown scope {label!r}")
        c = cats[ci]
        if division_code(c.get("name")) != code:
            raise ValueError(f"line {ci}/{ii} is not in division {code}")
        it = c["items"][ii]
        new = None if label == suggest(code, it.get("name") or "") else label
        if it.get("subScope") != new:
            if new is None:
                it.pop("subScope", None)
            else:
                it["subScope"] = new
            changed += 1
    return changed


def build_html(payload: dict, project: dict, client: dict, with_lines: bool = False) -> str:
    """with_lines: the same document with each division's work listed under it
    (names only, no prices) and the assumptions and exclusions after the totals.
    The "Client: by division, with line items" edition (Amir, 2026-11-06): the
    detailed breakdown in this layout, with no signature block."""
    from app.company import LEGAL_NAME
    from app.services.estimate_pdf import _client_filter_bullets
    from app.services.estimate_selection_pdf import _CSS, logo_data_url

    b = breakdown(payload)
    proj = _esc(project.get("name") or "Project")
    rows = []
    for d in b["divisions"]:
        open_ = with_lines and not d["scopes"] and d["lines"]
        cls = "div open" if open_ else "div"
        rows.append(f'<tr class="{cls}"><td>{_esc(d["name"])}</td><td class="r">{_money(d["amount"])}</td></tr>')
        if open_:
            lis = "".join(f"<li>{_esc(n)}</li>" for n in d["lines"])
            rows.append(f'<tr class="lines"><td colspan="2"><ul>{lis}</ul></td></tr>')
        for s in d["scopes"] or []:
            lis = "".join(f"<li>{_esc(n)}</li>" for n in s["lines"])
            rows.append(f'<tr class="sub"><td>{_esc(s["label"])}<ul>{lis}</ul></td>'
                        f'<td class="r">{_money(s["amount"])}</td></tr>')
    meta = "".join([
        f'<div class="mini-proj">{proj}</div>',
        f'<div class="mini-sub">{_esc(project.get("address") or "")}</div>' if project.get("address") else "",
        f'<div class="mini-sub">Prepared for {_esc(client.get("name"))}</div>' if (client or {}).get("name") else "",
        f'<div class="mini-sub">{_esc(payload.get("reference") or "")}</div>',
    ])
    css = """
table.divs { width: 100%; border-collapse: collapse; margin-top: 6px; }
table.divs td { padding: 6px 6px; border-bottom: 1px solid #e6eaee; vertical-align: top; font-size: 10pt; }
table.divs td.r { text-align: right; white-space: nowrap; }
table.divs tr.div td { font-weight: 600; }
table.divs tr.sub td { padding-left: 22px; font-size: 9.5pt; color: #3a4550; }
table.divs tr.sub td.r { font-weight: 400; }
table.divs tr.sub ul { margin: 3px 0 0 0; padding-left: 16px; font-size: 8.5pt; color: #6b7682; }
table.divs tr.lines td { padding: 0 6px 8px 22px; border-bottom: 1px solid #e6eaee; }
table.divs tr.div.open td { border-bottom: none; }  /* WeasyPrint has no :has() */
table.divs tr.lines ul { margin: 0; padding-left: 16px; font-size: 8.5pt; color: #6b7682; }
.notes h2 { margin-top: 18px; }
.notes ul { margin: 4px 0 0 0; padding-left: 16px; font-size: 9pt; color: #3a4550; }
.notes li { margin-bottom: 3px; }
"""
    notes = ""
    if with_lines:
        for title, key in (("Assumptions", "assumptions"), ("Not Included", "exclusions")):
            vals = _client_filter_bullets(payload.get(key))
            if vals:
                lis = "".join(f"<li>{_esc(v)}</li>" for v in vals)
                notes += f'<section class="sec notes"><h2>{title}</h2><ul>{lis}</ul></section>'
    lead = ("Prices by CSI division, with the work in each division listed below it. Div 09 Finishes is broken out "
            "by scope. Each price is complete for that work, including its share of the allowance for unforeseen "
            "conditions." if with_lines else
            "Prices by CSI division. Div 09 Finishes is broken out by scope, with the work in each listed below it.\n"
            "Each price is complete for that work, including its share of the allowance for unforeseen conditions.")
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}{css}</style></head><body>
<div class="mini-head"><img class="mini-logo" src="{logo_data_url()}" alt="BRIO"/>
<div class="mini-meta"><div class="mini-kicker">{LEGAL_NAME}: Estimate by Division</div>{meta}</div></div>
<section class="sec"><h2>Cost by Division</h2>
<p class="lead">{lead}</p>
<table class="divs"><tbody>{''.join(rows)}</tbody></table></section>
<section class="sec totals"><table class="tot">
<tr class="sub-line"><td>Subtotal</td><td class="r">{_money(b['subtotal'])}</td></tr>
<tr><td>GST (5%)</td><td class="r">{_money(b['gst'])}</td></tr>
<tr class="grand"><td>Total</td><td class="r">{_money(b['total'])}</td></tr></table>
<p class="fine">In CAD. The total is the same lump-sum price as our proposal; this document only shows how it divides
by division. Changes to the scope are handled by written change order.</p></section>
{notes}
</body></html>"""


def render_pdf(payload: dict, project: dict, client: dict, with_lines: bool = False) -> bytes:
    from weasyprint import HTML
    return HTML(string=build_html(payload, project, client or {}, with_lines=with_lines)).write_pdf()
