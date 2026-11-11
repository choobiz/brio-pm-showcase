"""Render a Scope of Work PDF — BRIO-branded, via WeasyPrint.

Ports the visual language of the brio-admin scope template (primary #0068A0,
accent #1D3146, sharp / no-curve layout) into a server-side Python renderer,
so Track C produces a downloadable branded PDF without a Node/Puppeteer
sidecar. Brand fonts (Sora/Open Sans) are a follow-up — this uses a clean
system sans stack so rendering stays offline and deterministic.
"""

from __future__ import annotations
from app.company import FOOTER_CONTACT, LEGAL_NAME, LETTERHEAD_HTML

import html
from datetime import date, datetime
from typing import Any

from app.services.client_text import redact_internal_pricing
from app.services.pdf_brand import logo_data_url

# NOTE: `weasyprint` is imported lazily inside render_scope_pdf() — it pulls
# heavy native deps (Pango) that are present in the Docker image but not in a
# bare local venv, and importing this module must not require them.

PRIMARY = "#0068A0"
ACCENT = "#1D3146"

# Ported from brio-admin/templates/shared-css.mjs — no border-radius anywhere
# (BRIO PDF rule: sharp, straight). Google-Fonts @import dropped for offline,
# deterministic rendering.
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
h3 { font-size: 11pt; font-weight: 600; color: #1D3146; margin-bottom: 7px; }
p { margin-bottom: 7px; }
ul { margin-bottom: 7px; padding-left: 20px; }
li { margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 14px 0; font-size: 10pt; }
thead th {
  background: #F0F7FC; font-weight: 600; text-align: left; padding: 7px 10px;
  border-bottom: 2px solid #0068A0; color: #1D3146; font-size: 8.5pt;
  text-transform: uppercase; letter-spacing: 0.3px;
}
td { padding: 6px 10px; border-bottom: 1px solid #E5E7EB; vertical-align: top; }
tr:nth-child(even) td { background: #FAFBFC; }
.info-grid { display: flex; gap: 12px; margin: 12px 0 4px 0; }
.info-box {
  flex: 1; background: #F0F7FC; border: 1px solid #D1E3F0;
  padding: 12px 16px; font-size: 9.5pt;
}
.info-box .label {
  font-weight: 600; color: #0068A0; display: inline-block; min-width: 95px;
}
.info-box .row { margin-bottom: 3px; }
.summary {
  background: #F0F7FC; border-left: 4px solid #0068A0;
  padding: 12px 16px; margin: 14px 0; font-size: 10pt;
}
.scope-category {
  border: 1px solid #E5E7EB; padding: 12px 16px; margin: 9px 0;
  page-break-inside: avoid;
}
.scope-category h3 { color: #0068A0; margin-bottom: 5px; }
.scope-category ul { margin-bottom: 0; }
.warning {
  background: #FFFBEB; border-left: 4px solid #D97706;
  padding: 12px 16px; margin: 12px 0; font-size: 10pt;
}
.warning ul { margin-bottom: 0; }
.notes-section {
  background: #FAFBFC; border: 1px solid #E5E7EB;
  padding: 12px 16px; margin: 12px 0; font-size: 9.5pt;
}
.notes-section h3 { font-size: 10pt; color: #1D3146; }
.notes-section ul { margin-bottom: 0; }
.footer {
  margin-top: 32px; padding-top: 12px; border-top: 2px solid #0068A0;
  font-size: 8.5pt; color: #6B7280; display: flex; justify-content: space-between;
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value not in (None, "") else ""


def _fmt_date(raw: Any) -> str:
    """YYYY-MM-DD or date/datetime → 'May 16, 2026'. Falls back to raw text."""
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
    if d is None:
        return _esc(raw) or date.today().strftime("%B %-d, %Y")
    return d.strftime("%B %-d, %Y")


def _str_list(items: Any) -> list[str]:
    # Every list on the scope document passes through here: categories,
    # assumptions, exclusions, permits, risks. Redacting at this one point is
    # what stops estimating structure reaching the client in the item wording.
    return redact_internal_pricing(
        [str(x).strip() for x in (items or []) if str(x).strip()]
    )


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"


def _info_rows(rows: list[tuple[str, Any]]) -> str:
    return "".join(
        f'<div class="row"><span class="label">{_esc(label)}:</span> {_esc(value)}</div>'
        for label, value in rows
        if value not in (None, "")
    )


def build_scope_html(
    *, payload: dict[str, Any], project: dict[str, Any], client: dict[str, Any]
) -> str:
    """Assemble the full HTML document for a scope PDF."""
    reference = payload.get("reference") or "SCOPE-DRAFT"
    project_name = project.get("name") or "Project"
    project_type = project.get("type") or ""

    # ---- Client + project info boxes -------------------------------------
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
            ("Reference", reference),
            ("Date", _fmt_date(payload.get("date"))),
            ("Site Address", project.get("address")),
            ("Project Type", project_type),
        ]
    )

    # ---- Summary ----------------------------------------------------------
    summary = (payload.get("summary") or "").strip()
    summary_html = (
        f'<div class="summary">{_esc(summary)}</div>' if summary else ""
    )

    # ---- Work categories --------------------------------------------------
    categories = payload.get("categories") or []
    cat_blocks = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        items = _str_list(cat.get("items"))
        if not (cat.get("name") or items):
            continue
        cat_blocks.append(
            f'<div class="scope-category"><h3>{_esc(cat.get("name") or "—")}</h3>'
            f"{_ul(items) if items else ''}</div>"
        )
    categories_html = (
        "<h2>Work Categories</h2>" + "".join(cat_blocks) if cat_blocks else ""
    )

    # ---- Materials --------------------------------------------------------
    materials = [m for m in (payload.get("materials") or []) if isinstance(m, dict) and (m.get("item") or "").strip()]
    if materials:
        rows = "".join(
            f"<tr><td>{_esc(m.get('item'))}</td>"
            f"<td>{_esc(m.get('spec') or 'TBD')}</td>"
            f"<td>{_esc(m.get('notes') or '')}</td></tr>"
            for m in materials
        )
        materials_html = (
            "<h2>Materials &amp; Specifications</h2><table>"
            "<thead><tr><th>Item</th><th>Specification</th><th>Notes</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        materials_html = ""

    # ---- Simple list sections --------------------------------------------
    def section(title: str, items: list[str], css: str = "") -> str:
        if not items:
            return ""
        body = f'<div class="{css}">{_ul(items)}</div>' if css else _ul(items)
        return f"<h2>{title}</h2>{body}"

    def notes_block(title: str, items: list[str]) -> str:
        if not items:
            return ""
        return (
            f'<div class="notes-section"><h3>{title}</h3>{_ul(items)}</div>'
        )

    issues_html = section(
        "Potential Issues &amp; Risks", _str_list(payload.get("potentialIssues")), "warning"
    )
    access_html = section(
        "Access &amp; Site Constraints", _str_list(payload.get("accessConstraints"))
    )
    permits_html = section("Permit Requirements", _str_list(payload.get("permits")))
    assumptions_html = notes_block("Assumptions", _str_list(payload.get("assumptions")))
    exclusions_html = notes_block("Exclusions", _str_list(payload.get("exclusions")))

    subtitle = _esc(project_name)
    if project_type:
        subtitle += f" &mdash; {_esc(project_type)}"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="logo-bar">
  <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
  <div class="company-info">
    {LETTERHEAD_HTML}
  </div>
</div>
<h1>Scope of Work</h1>
<p class="subtitle">{subtitle}</p>
<div class="info-grid">
  <div class="info-box">{client_box}</div>
  <div class="info-box">{project_box}</div>
</div>
{summary_html}
{categories_html}
{materials_html}
{issues_html}
{access_html}
{permits_html}
{assumptions_html}
{exclusions_html}
<div class="footer">
  <span>Built By Brio &mdash; Scope of Work</span>
  <span>{FOOTER_CONTACT}</span>
</div>
</body></html>"""


def render_scope_pdf(
    *, payload: dict[str, Any], project: dict[str, Any], client: dict[str, Any]
) -> bytes:
    """Render a scope payload to PDF bytes."""
    from weasyprint import HTML  # lazy — see module note

    document = build_scope_html(payload=payload, project=project, client=client)
    return HTML(string=document).write_pdf()


# ── Whole-project Scope of Work ─────────────────────────────────────────────
# Industry-standard commercial-construction layout: cover page →
# numbered sections (Project Information → Project Overview → Scope of
# Work → Materials → Permits → Site Logistics → Assumptions →
# Exclusions → Risk Considerations). All per-trade scopes are CONSOLIDATED
# into a single project-level payload (categories merged in construction-
# flow order, materials/permits/risks deduped) — the document reads as
# one project, not six separately-rendered sub-scopes.

_FULL_CSS_ADDITIONS = """
@page {
  size: Letter; margin: 18mm 18mm 20mm 18mm;
  @bottom-left {
    content: "BRIO Construction · Scope of Work";
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
h2 { break-before: auto; }
table { page-break-inside: auto; break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; break-inside: avoid; }
.cover {
  page-break-after: always; break-after: page;
  display: block; padding: 22mm 0 0 0;
}
.cover .brand-bar {
  border-bottom: 3px solid #0068A0; padding-bottom: 14px;
  display: flex; justify-content: space-between; align-items: flex-end;
}
.cover .doc-type {
  text-transform: uppercase; letter-spacing: 2px; font-size: 9.5pt;
  color: #6B7280; margin: 56mm 0 8px 0;
}
.cover h1 {
  font-size: 34pt; line-height: 1.04; color: #0068A0;
  margin-bottom: 6px; letter-spacing: -0.5px; font-weight: 700;
}
.cover .project-subtitle {
  font-size: 14pt; color: #1D3146; font-weight: 600; margin-bottom: 30px;
}
.cover .meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px 40px;
  margin-top: 14px; padding-top: 14px;
  border-top: 1px solid #D1E3F0;
}
.cover .meta-grid .meta-label {
  text-transform: uppercase; letter-spacing: 1.5px; font-size: 7.5pt;
  font-weight: 700; color: #0068A0; margin-bottom: 3px;
}
.cover .meta-grid .meta-value { font-size: 11pt; color: #1D3146; }
.cover .prepared-by {
  position: absolute; bottom: 22mm; left: 18mm; right: 18mm;
  font-size: 9pt; color: #6B7280;
  padding-top: 12px; border-top: 1px solid #E5E7EB;
  display: flex; justify-content: space-between;
}
section.numbered { break-before: page; page-break-before: always; }
section.numbered.first { break-before: auto; page-break-before: auto; }
section.numbered > h2 {
  font-size: 15pt; color: #0068A0; padding-bottom: 8px;
  border-bottom: 2px solid #0068A0; margin: 0 0 16px 0;
}
section.numbered > h2 .section-num {
  font-weight: 700; margin-right: 14px;
  font-feature-settings: 'tnum';
}
.sub-h h3, h3.sub-h {
  font-size: 11pt; color: #1D3146; margin: 14px 0 6px 0;
  padding-bottom: 3px; border-bottom: 1px dashed #D1E3F0;
}
.sub-h .sub-num { color: #0068A0; font-weight: 700; margin-right: 8px; }
.lead {
  font-size: 11pt; line-height: 1.55; margin-bottom: 12px;
  color: #1D3146;
}
.stat-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 10px; margin: 4px 0 18px 0; page-break-inside: avoid;
}
.stat-tile {
  background: #F0F7FC; border: 1px solid #D1E3F0;
  padding: 14px 14px 12px 14px; text-align: left;
  page-break-inside: avoid;
}
.stat-tile .stat-num {
  display: block; font-size: 22pt; font-weight: 700; color: #0068A0;
  line-height: 1; margin-bottom: 4px;
}
.stat-tile .stat-label {
  display: block; font-size: 8.5pt; color: #1D3146;
  text-transform: uppercase; letter-spacing: 0.5px;
}
.work-section { margin-bottom: 14px; page-break-inside: avoid; }
.work-section h3 {
  font-size: 11pt; color: #0068A0; margin-bottom: 4px;
  padding-bottom: 3px;
}
.work-section ul { padding-left: 22px; }
.work-section li { margin-bottom: 3px; }
"""


def _scope_body(payload: dict[str, Any]) -> str:
    """Build the content body for a single scope (summary + categories +
    materials + issues + access + permits + assumptions + exclusions).
    Shared between the per-scope PDF (which adds its own header) and the
    full-scope PDF (which prepends a trade divider per scope). Returns
    the empty string for an empty payload."""

    summary = (payload.get("summary") or "").strip()
    summary_html = (
        f'<div class="summary">{_esc(summary)}</div>' if summary else ""
    )

    categories = payload.get("categories") or []
    cat_blocks: list[str] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        items = _str_list(cat.get("items"))
        if not (cat.get("name") or items):
            continue
        cat_blocks.append(
            f'<div class="scope-category"><h3>{_esc(cat.get("name") or "—")}</h3>'
            f"{_ul(items) if items else ''}</div>"
        )
    categories_html = (
        "<h2>Work Categories</h2>" + "".join(cat_blocks) if cat_blocks else ""
    )

    materials = [
        m
        for m in (payload.get("materials") or [])
        if isinstance(m, dict) and (m.get("item") or "").strip()
    ]
    if materials:
        rows = "".join(
            f"<tr><td>{_esc(m.get('item'))}</td>"
            f"<td>{_esc(m.get('spec') or 'TBD')}</td>"
            f"<td>{_esc(m.get('notes') or '')}</td></tr>"
            for m in materials
        )
        materials_html = (
            "<h2>Materials &amp; Specifications</h2><table>"
            "<thead><tr><th>Item</th><th>Specification</th><th>Notes</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        materials_html = ""

    def section(title: str, items: list[str], css: str = "") -> str:
        if not items:
            return ""
        body = f'<div class="{css}">{_ul(items)}</div>' if css else _ul(items)
        return f"<h2>{title}</h2>{body}"

    def notes_block(title: str, items: list[str]) -> str:
        if not items:
            return ""
        return f'<div class="notes-section"><h3>{title}</h3>{_ul(items)}</div>'

    issues_html = section(
        "Potential Issues &amp; Risks",
        _str_list(payload.get("potentialIssues")),
        "warning",
    )
    access_html = section(
        "Access &amp; Site Constraints",
        _str_list(payload.get("accessConstraints")),
    )
    permits_html = section(
        "Permit Requirements", _str_list(payload.get("permits"))
    )
    assumptions_html = notes_block(
        "Assumptions", _str_list(payload.get("assumptions"))
    )
    exclusions_html = notes_block(
        "Exclusions", _str_list(payload.get("exclusions"))
    )

    return (
        summary_html
        + categories_html
        + materials_html
        + issues_html
        + access_html
        + permits_html
        + assumptions_html
        + exclusions_html
    )


# Trade codes the AI worker bakes into estimate references like
# `PROJB-DAYCARE-PLUMB-1000-1ST`. Mapped to human labels for the cover
# TOC + per-trade dividers in the consolidated PDF.
_TRADE_CODE_LABELS: dict[str, str] = {
    "ARC": "Architectural",
    "ARCH": "Architectural",
    "STR": "Structural",
    "STRUCT": "Structural",
    "MECH": "Mechanical",
    "HVAC": "Mechanical (HVAC)",
    "ELEC": "Electrical",
    "PLUMB": "Plumbing",
    "PLBG": "Plumbing",
    "GC": "General Contractor Rollup",
    "CIVIL": "Civil",
    "FIRE": "Fire Protection",
    "LANDSCAPE": "Landscape",
    "LAND": "Landscape",
}


def _trade_from_reference(ref: str | None) -> str | None:
    """Pull a known trade code out of a reference like PROJB-DAYCARE-PLUMB-…
    Returns the human label, or None if no segment matches."""
    if not ref:
        return None
    for seg in str(ref).upper().split("-"):
        if seg in _TRADE_CODE_LABELS:
            return _TRADE_CODE_LABELS[seg]
    return None


def _scope_title(payload: dict[str, Any], fallback_idx: int) -> str:
    """Best-effort human title for a single scope row. Resolution order:
    explicit title/trade/category/name in the payload → `discipline` field
    → trade code parsed from the reference → reference itself → "Scope N".
    """
    explicit = (
        payload.get("title")
        or payload.get("trade")
        or payload.get("category")
        or payload.get("name")
    )
    if explicit:
        return str(explicit).strip() or f"Scope {fallback_idx}"

    disc = (payload.get("discipline") or "").strip()
    if disc:
        # Snake_case "gc_rollup" → ["GC", "ROLLUP"], etc.; first segment
        # matching our trade map wins so "gc_rollup" reads "General
        # Contractor Rollup" instead of "Gc_rollup".
        upper_segments = [s.upper() for s in disc.replace("_", " ").split() if s]
        for seg in upper_segments:
            if seg in _TRADE_CODE_LABELS:
                return _TRADE_CODE_LABELS[seg]
        # Fallback: humanise snake_case, keep short all-caps tokens intact.
        return " ".join(
            t if t.isupper() and len(t) <= 4 else t.capitalize()
            for t in disc.replace("_", " ").split()
        )

    from_ref = _trade_from_reference(payload.get("reference"))
    if from_ref:
        return from_ref

    ref = (payload.get("reference") or "").strip()
    if ref:
        return ref
    return f"Scope {fallback_idx}"


# Construction-flow ordering keywords. Each category is matched against
# these substrings (case-insensitive) and the lowest priority wins. Anything
# unmatched lands at the end. Tweak in one place to reorder a section.
_CATEGORY_FLOW_ORDER: list[tuple[str, int]] = [
    ("project management", 10),
    ("general conditions", 12),
    ("supervision", 14),
    ("permit", 20),
    ("inspection", 22),
    ("survey", 24),
    ("hoarding", 28),
    ("temporary", 28),
    ("demolition", 30),
    ("removal", 32),
    ("abatement", 34),
    ("excavation", 36),
    ("earthwork", 36),
    ("shoring", 38),
    ("exterior", 40),
    ("site work", 42),
    ("site servicing", 44),
    ("foundation", 50),
    ("underpinning", 52),
    ("concrete", 54),
    ("masonry", 56),
    ("structural", 58),
    ("metals", 60),
    ("framing", 62),
    ("roofing", 65),
    ("waterproofing", 66),
    ("envelope", 67),
    ("plumbing", 70),
    ("mechanical", 75),
    ("hvac", 75),
    ("ventilation", 76),
    ("electrical", 80),
    ("lighting", 81),
    ("low voltage", 82),
    ("fire", 84),
    ("fire protection", 84),
    ("life safety", 85),
    ("insulation", 88),
    ("vapour", 88),
    ("drywall", 90),
    ("finishing", 92),
    ("painting", 93),
    ("flooring", 94),
    ("ceiling", 95),
    ("doors", 96),
    ("windows", 96),
    ("hardware", 96),
    ("millwork", 97),
    ("casework", 97),
    ("specialty", 98),
    ("appliance", 98),
    ("equipment", 98),
    ("commissioning", 99),
    ("punch", 100),
    ("closeout", 101),
]


def _category_flow_priority(name: str) -> int:
    n = (name or "").lower()
    best = 999
    for kw, pri in _CATEGORY_FLOW_ORDER:
        if kw in n and pri < best:
            best = pri
    return best


def _consolidate_scope_payloads(
    scope_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-trade scope payloads into a single project-level payload.

    - `categories` with the same name (case-insensitive after stripping) get
      their items merged with item-level text dedup.
    - `materials` deduped by (item, spec) case-insensitive.
    - `permits` / `potentialIssues` / `accessConstraints` / `assumptions` /
      `exclusions` deduped by lowercase trim of the bullet text.
    - `summary` is built by concatenating each non-empty scope summary,
      prefixed with the trade label, so the executive overview reads as
      a project-wide narrative rather than one trade's voice.

    Returns a single merged payload of the same shape as one scope row.
    """
    cat_items: dict[str, list[str]] = {}
    cat_display_name: dict[str, str] = {}  # preserve original casing
    cat_seen: dict[str, set[str]] = {}

    materials: list[dict[str, Any]] = []
    seen_material_keys: set[tuple[str, str]] = set()

    list_fields = (
        "permits",
        "potentialIssues",
        "accessConstraints",
        "assumptions",
        "exclusions",
    )
    list_out: dict[str, list[str]] = {f: [] for f in list_fields}
    list_seen: dict[str, set[str]] = {f: set() for f in list_fields}

    summary_chunks: list[tuple[str, str]] = []  # (trade label, text)

    for i, payload in enumerate(scope_payloads, start=1):
        if not isinstance(payload, dict):
            continue

        # Per-trade summary chunk
        s = (payload.get("summary") or "").strip()
        if s:
            trade = _scope_title(payload, i)
            summary_chunks.append((trade, s))

        # Categories — merge items into the same category bucket
        for cat in payload.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            raw_name = (cat.get("name") or "").strip()
            if not raw_name:
                continue
            key = raw_name.lower()
            cat_items.setdefault(key, [])
            cat_seen.setdefault(key, set())
            cat_display_name.setdefault(key, raw_name)
            for it in cat.get("items") or []:
                text = str(it).strip()
                if not text:
                    continue
                norm = " ".join(text.lower().split())
                if norm in cat_seen[key]:
                    continue
                cat_seen[key].add(norm)
                cat_items[key].append(text)

        # Materials
        for m in payload.get("materials") or []:
            if not isinstance(m, dict):
                continue
            item = (m.get("item") or "").strip()
            if not item:
                continue
            spec = (m.get("spec") or "").strip()
            mkey = (item.lower(), spec.lower())
            if mkey in seen_material_keys:
                continue
            seen_material_keys.add(mkey)
            materials.append(m)

        # Bullet-list fields
        for f in list_fields:
            for it in payload.get(f) or []:
                text = str(it).strip()
                if not text:
                    continue
                norm = " ".join(text.lower().split())
                if norm in list_seen[f]:
                    continue
                list_seen[f].add(norm)
                list_out[f].append(text)

    # Order categories by construction flow, then alphabetically
    ordered_keys = sorted(
        cat_items.keys(),
        key=lambda k: (_category_flow_priority(cat_display_name[k]), k),
    )
    merged_categories = [
        {"name": cat_display_name[k], "items": cat_items[k]} for k in ordered_keys
    ]

    return {
        "summary_chunks": summary_chunks,
        "categories": merged_categories,
        "materials": materials,
        **list_out,
    }


def build_full_scope_html(
    *,
    scopes: list[dict[str, Any]],
    project: dict[str, Any],
    client: dict[str, Any],
) -> str:
    """Assemble the project-wide Scope of Work — one cohesive professional
    document. Each item in `scopes` is a dict with at least `payload`, and
    optionally `version` / `generated_at`. All trade scopes are merged into
    sections organised by industry-standard structure (project info →
    project overview → scope of work → materials → permits → site
    logistics → assumptions → exclusions → risk considerations)."""

    payloads = [s.get("payload") or {} for s in scopes if isinstance(s, dict)]
    payloads = [p for p in payloads if isinstance(p, dict)]
    merged = _consolidate_scope_payloads(payloads)

    project_name = project.get("name") or "Project"
    project_type = project.get("type") or ""
    project_addr = project.get("address") or ""
    project_sqft = project.get("sqft")

    # ── Cover page ────────────────────────────────────────────────────────
    cover_meta = [
        ("Client", client.get("name") or "—"),
        ("Date", _fmt_date(date.today())),
        ("Site Address", project_addr or "—"),
        ("Project Type", project_type or "—"),
    ]
    cover_meta_html = "".join(
        f'<div><div class="meta-label">{_esc(label)}</div>'
        f'<div class="meta-value">{_esc(value)}</div></div>'
        for label, value in cover_meta
    )

    # ── 1. Project Information ────────────────────────────────────────────
    client_box = _info_rows(
        [
            ("Client", client.get("name")),
            ("Email", client.get("email")),
            ("Phone", client.get("phone")),
            ("Address", client.get("address")),
        ]
    ) or '<div class="row">No client on file.</div>'

    project_info_rows = [
        ("Project", project_name),
        ("Site Address", project_addr),
        ("Project Type", project_type),
    ]
    if project_sqft:
        project_info_rows.append(("Approx. Area", f"{project_sqft:,} sq ft"))
    project_info_rows.append(("Document Date", _fmt_date(date.today())))
    project_box = _info_rows(project_info_rows)

    # Pull every per-trade reference into a single line for the trail.
    refs = sorted(
        {
            (p.get("reference") or "").strip()
            for p in payloads
            if (p.get("reference") or "").strip()
        }
    )
    refs_html = (
        f'<p style="margin-top:6px; font-size:9pt; color:#6B7280;">'
        f'<strong>Sub-scope references:</strong> {_esc(" · ".join(refs))}</p>'
        if refs
        else ""
    )

    section_1 = f"""
<section class="numbered first">
  <h2><span class="section-num">1.</span>Project Information</h2>
  <div class="info-grid">
    <div class="info-box">{client_box}</div>
    <div class="info-box">{project_box}</div>
  </div>
  {refs_html}
</section>"""

    # ── 2. Project Overview ───────────────────────────────────────────────
    summary_chunks: list[tuple[str, str]] = merged["summary_chunks"]
    if summary_chunks:
        overview_paragraphs = "".join(
            f'<p class="lead"><strong style="color:#0068A0;">'
            f"{_esc(trade)}:</strong> {_esc(text)}</p>"
            for trade, text in summary_chunks
        )
    else:
        overview_paragraphs = (
            '<p class="lead" style="color:#6B7280; font-style:italic;">'
            "No project narrative captured yet.</p>"
        )

    # Stats tiles — projects-at-a-glance metrics
    n_categories = len(merged["categories"])
    n_items = sum(len(c["items"]) for c in merged["categories"])
    n_materials = len(merged["materials"])
    n_permits = len(merged["permits"])
    stat_tiles = [
        (str(len(payloads)), "Sub-scopes Merged"),
        (str(n_categories), "Work Categories"),
        (str(n_items), "Discrete Work Items"),
        (str(n_materials + n_permits), "Materials & Permits"),
    ]
    stat_html = "".join(
        f'<div class="stat-tile">'
        f'<span class="stat-num">{_esc(num)}</span>'
        f'<span class="stat-label">{_esc(label)}</span></div>'
        for num, label in stat_tiles
    )

    section_2 = f"""
<section class="numbered">
  <h2><span class="section-num">2.</span>Project Overview</h2>
  <div class="stat-grid">{stat_html}</div>
  {overview_paragraphs}
</section>"""

    # ── 3. Scope of Work ──────────────────────────────────────────────────
    work_blocks: list[str] = []
    for idx, cat in enumerate(merged["categories"], start=1):
        items = cat["items"]
        items_html = _ul(items) if items else (
            '<p style="color:#6B7280; font-style:italic; margin-top:4px;">'
            "Items captured at a project level only — see Project Overview "
            "for narrative.</p>"
        )
        sub_num = f"3.{idx:02d}"
        work_blocks.append(
            f'<div class="work-section">'
            f'<h3 class="sub-h"><span class="sub-num">{sub_num}</span>'
            f'{_esc(cat["name"])}</h3>'
            f"{items_html}"
            f"</div>"
        )
    section_3 = f"""
<section class="numbered">
  <h2><span class="section-num">3.</span>Scope of Work</h2>
  {"".join(work_blocks) if work_blocks else "<p>No work categories captured.</p>"}
</section>"""

    # ── 4. Materials & Specifications ─────────────────────────────────────
    mats = merged["materials"]
    if mats:
        rows = "".join(
            f"<tr><td>{_esc(m.get('item'))}</td>"
            f"<td>{_esc(m.get('spec') or 'TBD')}</td>"
            f"<td>{_esc(m.get('notes') or '')}</td></tr>"
            for m in mats
        )
        section_4 = f"""
<section class="numbered">
  <h2><span class="section-num">4.</span>Materials &amp; Specifications</h2>
  <table>
    <thead><tr><th>Item</th><th>Specification</th><th>Notes</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""
    else:
        section_4 = ""

    # ── 5. Permits, Approvals & Inspections ───────────────────────────────
    permits = merged["permits"]
    section_5 = (
        f"""
<section class="numbered">
  <h2><span class="section-num">5.</span>Permits, Approvals &amp; Inspections</h2>
  {_ul(permits)}
</section>"""
        if permits
        else ""
    )

    # ── 6. Site Logistics & Constraints ───────────────────────────────────
    constraints = merged["accessConstraints"]
    section_6 = (
        f"""
<section class="numbered">
  <h2><span class="section-num">6.</span>Site Logistics &amp; Constraints</h2>
  {_ul(constraints)}
</section>"""
        if constraints
        else ""
    )

    # ── 7. Assumptions & Clarifications ───────────────────────────────────
    assumptions = merged["assumptions"]
    section_7 = (
        f"""
<section class="numbered">
  <h2><span class="section-num">7.</span>Assumptions &amp; Clarifications</h2>
  {_ul(assumptions)}
</section>"""
        if assumptions
        else ""
    )

    # ── 8. Exclusions ─────────────────────────────────────────────────────
    exclusions = merged["exclusions"]
    section_8 = (
        f"""
<section class="numbered">
  <h2><span class="section-num">8.</span>Exclusions</h2>
  <p style="font-size:9.5pt; color:#6B7280; margin-bottom:6px;">
    The following are explicitly not included in this scope.
  </p>
  {_ul(exclusions)}
</section>"""
        if exclusions
        else ""
    )

    # ── 9. Risk Considerations ────────────────────────────────────────────
    issues = merged["potentialIssues"]
    section_9 = (
        f"""
<section class="numbered">
  <h2><span class="section-num">9.</span>Risk Considerations</h2>
  <p style="font-size:9.5pt; color:#6B7280; margin-bottom:6px;">
    Project-level risks identified during scope development. Each is
    actively monitored and mitigated through trade coordination,
    sequencing, and progress checkpoints.
  </p>
  <div class="warning">{_ul(issues)}</div>
</section>"""
        if issues
        else ""
    )

    # ── Cover + assembled body ────────────────────────────────────────────
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_CSS}{_FULL_CSS_ADDITIONS}</style></head><body>

<section class="cover">
  <div class="brand-bar">
    <img class="logo-img" src="{logo_data_url()}" alt="Built By Brio">
    <div class="company-info">
      {LETTERHEAD_HTML}
    </div>
  </div>
  <div class="doc-type">Scope of Work</div>
  <h1>{_esc(project_name)}</h1>
  <div class="project-subtitle">{_esc(project_type) if project_type else "Commercial Construction Project"}</div>
  <div class="meta-grid">{cover_meta_html}</div>
  <div class="prepared-by">
    <span>Prepared by Amir Omidvar &middot; {LEGAL_NAME}</span>
    <span>{_fmt_date(date.today())}</span>
  </div>
</section>

{section_1}
{section_2}
{section_3}
{section_4}
{section_5}
{section_6}
{section_7}
{section_8}
{section_9}

</body></html>"""


def render_full_scope_pdf(
    *,
    scopes: list[dict[str, Any]],
    project: dict[str, Any],
    client: dict[str, Any],
) -> bytes:
    """Render the project-wide unified Scope of Work to PDF bytes."""
    from weasyprint import HTML  # lazy — see module note

    document = build_full_scope_html(
        scopes=scopes, project=project, client=client
    )
    return HTML(string=document).write_pdf()


