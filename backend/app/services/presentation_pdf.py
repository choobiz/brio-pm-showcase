"""Render a client presentation as a BRIO-branded renovation-proposal PDF.

Consumes the same `presentations.payload` the web viewer uses. Styled as a
professional proposal: cover page, project overview, budget, the "why the
estimate changed" explanation, technical/logistic notes, scope, owner
decisions, our assumptions, exclusions, and schedule. WeasyPrint (HTML+CSS).
"""
from __future__ import annotations
from app.company import LEGAL_NAME, WEBSITE

import re
from typing import Any

from app.services.estimate_pdf import _esc, _money
from app.services.pdf_brand import logo_data_url
from app.services.pdf_charts import donut_legend, donut_svg, gantt_svg, hbar_svg, top_segments

PRIMARY = "#0068A0"
ACCENT = "#1D3146"
AMBER = "#b26a00"


def _fmt(n: Any) -> str:
    try:
        return _money(float(n))
    except Exception:
        return "—"


def _hero_image_data_url(payload: dict) -> str | None:
    """Render a 3D view / elevation drawing to a PNG data URL for the cover hero.

    Prefers an exterior 3D/rendering/elevation sheet; never uses a site,
    stormwater, structural, or plan sheet. Returns None if no good image exists."""
    drawings = payload.get("drawings") or []

    def _score(d: dict) -> int:
        fn = (d.get("filename") or "").lower()
        if any(k in fn for k in ("3d", "exterior", "elevation", "render", "perspect")):
            return 2
        if any(k in fn for k in ("site", "storm", "structural", "plan", "cover", "detail")):
            return -1
        return 0

    best = max(drawings, key=_score, default=None)
    key = best.get("r2_key") if best else None
    if not key or _score(best) <= 0:  # only use a genuinely good elevation/3D image
        return None
    try:
        import base64
        import fitz  # pymupdf
        from app.services.r2 import get_bytes
        doc = fitz.open(stream=get_bytes(key), filetype="pdf")
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8))
        png = pix.tobytes("png")
        doc.close()
        return "data:image/png;base64," + base64.b64encode(png).decode()
    except Exception:
        return None


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def _bullets(text: Any) -> str:
    """Split a prose blob into <li> bullets on sentence boundaries."""
    if not text:
        return ""
    parts = [s.strip() for s in _SENTENCE.split(str(text)) if s.strip()]
    return "".join(f"<li>{_esc(s)}</li>" for s in parts)


def _section(title: str, body: str) -> str:
    if not body.strip():
        return ""
    return f'<section class="sec"><h2>{_esc(title)}</h2>{body}</section>'


def build_presentation_html(payload: dict, project: dict, client: dict | None) -> str:
    p = payload or {}
    proj_name = _esc(p.get("project", {}).get("name") or project.get("name") or "Project")
    address = _esc(p.get("project", {}).get("address") or project.get("address") or "")
    client_name = _esc((client or {}).get("name") or (p.get("client") or {}).get("name") or "")
    b = p.get("budget") or {}
    total = b.get("total")

    parts: list[str] = []

    # ── Cover ───────────────────────────────────────────────────────────────
    logo = logo_data_url()
    hero = _hero_image_data_url(p)
    hero_html = f'<div class="cover-hero"><img src="{hero}" alt="Project rendering"/></div>' if hero else ""
    # Short title — just the project descriptor (e.g. "New Garage & Office"), not a sentence.
    ptype = _esc(str(p.get("project", {}).get("type") or "").strip())
    title = proj_name.split("—")[-1].strip() if "—" in proj_name else (ptype or proj_name)
    parts.append(f"""
    <div class="cover{' cover--hero' if hero else ''}">
      <div class="brand"><img src="{logo}" alt="BRIO"/></div>
      {hero_html}
      <div class="cover-body">
        <div class="doc-type">Project Proposal</div>
        <h1>{title}</h1>
        <div class="cover-meta">
          <div>{proj_name}{f' &middot; {address}' if address else ''}</div>
          {f'<div>Prepared for {client_name}</div>' if client_name else ''}
          <div>{LEGAL_NAME} &middot; {WEBSITE}</div>
        </div>
        {f'<div class="cover-total"><span>Project Total</span><strong>{_fmt(total)}</strong><em>incl. GST</em></div>' if total is not None else ''}
      </div>
    </div>""")

    # ── Overview ────────────────────────────────────────────────────────────
    if p.get("overview"):
        paras = "".join(f"<p>{_esc(x)}</p>" for x in str(p["overview"]).split("\n") if x.strip())
        parts.append(_section("Overview", paras))

    # ── Budget (donut "where the money goes" + bar comparison + table) ───────
    if b.get("categories"):
        base_cats = [(c.get("name") or "", c.get("displayTotal") or 0)
                     for c in b["categories"] if not c.get("optional")]
        segs = top_segments(base_cats, keep=8)
        viz = (
            '<div class="budget-viz">'
            f'<div class="donut-wrap">{donut_svg(segs)}</div>'
            f'<div class="legend-wrap"><div class="viz-label">Where the budget goes — by trade</div>{donut_legend(segs)}</div>'
            '</div>'
        )
        def _incl(c: dict) -> str:
            items = [x for x in (c.get("includes") or []) if "contingency" not in str(x).lower()]
            if items:
                lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
                return f'<div class="incl-label">Includes</div><ul class="incl-ul">{lis}</ul>'
            desc = (c.get("description") or "").strip()
            return f'<div class="incl-label">Includes</div><ul class="incl-ul"><li>{_esc(desc[:200])}</li></ul>' if desc else ""
        rows = "".join(
            (f'<tr class="cat"><td>{_esc(c.get("name"))}{" <span class=opt>(optional)</span>" if c.get("optional") else ""}</td>'
             f'<td class="r">{_fmt(c.get("displayTotal"))}</td></tr>'
             + (f'<tr class="incl"><td colspan="2">{_incl(c)}</td></tr>' if _incl(c) else ""))
            for c in b["categories"]
        )
        totals = f'<tr class="sub"><td>Subtotal</td><td class="r">{_fmt(b.get("subtotal"))}</td></tr>'
        if b.get("allowances"):
            totals += f'<tr class="sub"><td>Allowances</td><td class="r">{_fmt(b.get("allowances"))}</td></tr>'
        totals += f'<tr class="sub"><td>GST (5%)</td><td class="r">{_fmt(b.get("gst"))}</td></tr>'
        totals += f'<tr class="grand"><td>Project Total</td><td class="r">{_fmt(b.get("total"))}</td></tr>'
        note = "Derived from a detailed, calculated estimate. Figures are inclusive of GST."
        parts.append(_section("Project Budget", f'{viz}<table class="budget">{rows}{totals}</table><p class="fine">{note}</p>'))

    # ── Why the estimate changed ────────────────────────────────────────────
    ec = p.get("estimateChange")
    if ec:
        body = f"""
        <div class="callout">
          <div class="ec-row">
            <div><span>Earlier estimate{f" ({_esc(ec.get('priorDate'))})" if ec.get('priorDate') else ''}</span><b class="strike">{_fmt(ec.get('priorTotal'))}</b></div>
            <div><span>Current estimate</span><b>{_fmt(ec.get('currentTotal'))}</b></div>
            <div><span>Difference</span><b class="amber">+{_fmt(ec.get('delta'))}</b></div>
          </div>
          {f'<p>{_esc(ec.get("explanation"))}</p>' if ec.get('explanation') else ''}
        </div>"""
        parts.append(_section("Why This Differs From Our Earlier Estimate", body))

    # ── Major work areas ────────────────────────────────────────────────────
    if p.get("highCostItems"):
        def _work_item(h: dict) -> str:
            note = f'<ul class="note-ul">{_bullets(h.get("note"))}</ul>' if h.get("note") else ""
            return (f'<div class="item"><div class="item-head"><b>{_esc(h.get("categoryName"))}</b>'
                    f'<span>{_fmt(h.get("displayTotal"))}</span></div>{note}</div>')
        items = "".join(_work_item(h) for h in p["highCostItems"])
        parts.append(_section("Major Work Areas — Technical & Logistic Notes", items))

    # ── Scope ───────────────────────────────────────────────────────────────
    if p.get("scopeSections"):
        secs = "".join(
            f'<div class="scope"><h3>{_esc(s.get("title"))}</h3><ul class="scope-ul">{_bullets(s.get("clientDescription"))}</ul></div>'
            for s in p["scopeSections"]
        )
        parts.append(_section("Scope of Work", secs))

    # ── Owner decisions ─────────────────────────────────────────────────────
    if p.get("ownerDecisions"):
        od = sorted(p["ownerDecisions"], key=lambda a: a.get("decideByDate") or "9999")
        rows = ""
        for a in od:
            by = f'<span class="badge">decide by {_esc(a.get("decideByDate"))}</span>' if a.get("decideByDate") else ""
            lead = f' &middot; ~{a["leadTimeWeeks"]}-week lead' if isinstance(a.get("leadTimeWeeks"), int) else ""
            rows += (f'<div class="item blue"><div class="item-head"><b>{_esc(a.get("title"))}</b>{by}</div>'
                     f'<p><b>Decide:</b> {_esc(a.get("whatToDecide"))}</p>'
                     f'{f"<p class=muted><b>Affects:</b> {_esc(a.get('affectsSchedule'))}{lead}</p>" if a.get("affectsSchedule") else ""}</div>')
        intro = "<p class='lead'>These selections are needed from you — they gate specific work and carry lead times. To hold the schedule, please confirm each by the date shown.</p>"
        parts.append(_section("Decisions From the Owner", intro + rows))

    # ── Our assumptions ─────────────────────────────────────────────────────
    if p.get("ourAssumptions"):
        rows = "".join(
            f'<div class="item amber-l"><b>{_esc(a.get("title"))}</b>'
            f'<p><b>We assume:</b> {_esc(a.get("assumption"))}</p>'
            f'{f"<p class=muted><b>If not:</b> {_esc(a.get('ifWrong'))}</p>" if a.get("ifWrong") else ""}</div>'
            for a in p["ourAssumptions"]
        )
        intro = ("<p class='lead'>To price the work we've assumed the following, and we verify each during site "
                 "visits, excavation, and demolition. If a condition turns out different, it would be handled as a "
                 "change order — flagged now so there are no surprises.</p>")
        parts.append(_section("Our Assumptions", intro + rows))

    # ── Exclusions ──────────────────────────────────────────────────────────
    if p.get("exclusions"):
        lis = "".join(f"<li>{_esc(x)}</li>" for x in p["exclusions"])
        intro = ("<p class='lead'>The price above does <b>not</b> include the following. If any becomes required it "
                 "would be a separate change order or extra cost — listed here so it is clear up front.</p>")
        parts.append(_section("What's Not Included (Exclusions)", f"{intro}<ul>{lis}</ul>"))

    # ── Schedule (Gantt + task table + P80 + permit note) ────────────────────
    sch = p.get("schedule") or {}
    if sch.get("present"):
        acts = [a for a in (sch.get("activities") or []) if not a.get("milestone")]
        bars = [(a.get("name"), a.get("start"), a.get("finish"), a.get("critical")) for a in acts]
        if not bars:  # older payloads without the full activity list
            bars = [(c.get("name"), c.get("start"), c.get("finish"), True) for c in (sch.get("critical") or [])]
        ms = [(m.get("name"), m.get("date")) for m in (sch.get("milestones") or [])]
        gantt = gantt_svg(sch.get("projectStart"), sch.get("finishP50"), bars, ms)
        facts = []
        if sch.get("projectStart"): facts.append(f"Start {_esc(sch['projectStart'])}")
        if sch.get("finishP50"): facts.append(f"Target finish (P50) {_esc(sch['finishP50'])}")
        if sch.get("finishP80") and sch.get("finishP80") != sch.get("finishP50"):
            facts.append(f"Conservative (P80) {_esc(sch['finishP80'])}")
        legend = ('<div class="gantt-legend"><span><i class="sw-bar"></i>Critical path</span>'
                  '<span><i class="sw-bar2"></i>Other work</span>'
                  '<span><i class="sw-dia"></i>Milestone</span></div>') if gantt else ""
        body = (f'<p>{_esc(sch.get("narrative"))}</p>' if sch.get("narrative") else "")
        if facts:
            body += f'<p class="muted">{" &middot; ".join(facts)}</p>'
        if gantt:
            body += f'<div class="gantt-block"><div class="gantt">{gantt}</div>{legend}</div>'
        if acts:
            trows = "".join(
                f'<tr class="{"crit" if a.get("critical") else ""}">'
                f'<td>{_esc(a.get("name"))}</td><td class="ph">{_esc(a.get("phase") or "")}</td>'
                f'<td class="r">{round(float(a.get("duration") or 0))}</td>'
                f'<td class="dt">{_esc(a.get("start") or "")}</td><td class="dt">{_esc(a.get("finish") or "")}</td></tr>'
                for a in acts)
            body += ('<table class="tasks"><thead><tr><th>Task</th><th>Phase</th>'
                     '<th class="r">Days</th><th>Start</th><th>Finish</th></tr></thead>'
                     f'<tbody>{trows}</tbody></table>')
        body += ('<p class="fine">Durations in working days (Mon–Fri), a most-likely estimate with a P80 confidence '
                 'buffer. <b>The schedule assumes permits are issued at the project start</b> — permitting is a '
                 'separate pre-construction step, not part of the build duration above.</p>')
        parts.append('<div class="page-break"></div>' + _section("Schedule", body))

    if p.get("closingNote"):
        parts.append(f'<p class="closing">{_esc(p["closingNote"])}</p>')

    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{''.join(parts)}</body></html>"


def render_presentation_pdf(payload: dict, project: dict, client: dict | None) -> bytes:
    from weasyprint import HTML
    return HTML(string=build_presentation_html(payload, project, client)).write_pdf()


_CSS = f"""
@page {{ size: Letter; margin: 16mm 16mm 18mm; @bottom-center {{ content: "{LEGAL_NAME} — Proposal"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }} }}
@page :first {{ margin: 0; @bottom-center {{ content: ""; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Open Sans','Helvetica Neue',Arial,sans-serif; color: #26303a; font-size: 10.5pt; line-height: 1.5; margin: 0; }}
h1,h2,h3 {{ font-family: 'Sora','Helvetica Neue',Arial,sans-serif; color: {ACCENT}; }}
.cover {{ min-height: 100vh; padding: 24mm 22mm; color: {ACCENT}; background: #fff; page-break-after: always; display: flex; flex-direction: column; }}
.cover .brand img {{ height: 40px; }}
.cover .cover-hero {{ margin-top: 12mm; border: 1px solid #e4e8ec; padding: 6mm; }}
.cover .cover-hero img {{ display: block; width: 100%; height: 96mm; object-fit: contain; background: #fff; }}
.cover .cover-body {{ margin-top: auto; }}
.cover .doc-type {{ font-size: 10.5pt; letter-spacing: 4px; text-transform: uppercase; color: {PRIMARY}; }}
.cover h1 {{ color: {ACCENT}; font-size: 27pt; line-height: 1.08; margin: 6px 0 0; max-width: 150mm; }}
.cover .cover-meta {{ margin-top: 14px; color: #5b6672; font-size: 11pt; }}
.cover .cover-meta div {{ margin-top: 3px; }}
.cover .cover-total {{ margin-top: 20px; padding-top: 14px; border-top: 2px solid {PRIMARY}; }}
.cover .cover-total span {{ display: block; font-size: 9pt; letter-spacing: 2px; text-transform: uppercase; color: #8a94a0; }}
.cover .cover-total strong {{ font-family: 'Sora',Arial,sans-serif; font-size: 30pt; color: {PRIMARY}; }}
.cover .cover-total em {{ font-style: normal; color: #5b6672; margin-left: 8px; font-size: 10pt; }}
.sec {{ margin: 0 0 16px; }}
.sec h2 {{ font-size: 14pt; border-bottom: 2px solid {PRIMARY}; padding-bottom: 5px; margin: 18px 0 10px; }}
p {{ margin: 6px 0; }}
p.lead {{ color: #4a5560; }}
p.fine {{ font-size: 8.5pt; color: #9aa3ad; }}
p.muted {{ color: #5b6672; }}
p.closing {{ margin-top: 18px; padding-top: 12px; border-top: 1px solid #dfe4ea; font-style: italic; color: #4a5560; }}
table.budget {{ width: 100%; border-collapse: collapse; }}
table.budget td {{ padding: 5px 4px; border-bottom: 1px solid #eef1f4; }}
table.budget td.r {{ text-align: right; white-space: nowrap; }}
table.budget tr.sub td {{ color: #5b6672; border-bottom: none; padding: 2px 4px; }}
table.budget tr.grand td {{ font-family: 'Sora',Arial,sans-serif; font-weight: 700; color: {PRIMARY}; border-top: 2px solid #cfd6dd; font-size: 12pt; padding-top: 7px; }}
.opt {{ color: #9aa3ad; font-size: 9pt; }}
table.budget tr.cat td {{ padding-top: 8px; border-bottom: none; }}
table.budget tr.incl td {{ padding: 2px 4px 9px; border-bottom: 1px solid #eef1f4; }}
.incl-label {{ font-size: 7.5pt; text-transform: uppercase; letter-spacing: .5px; color: #8a94a0; margin-bottom: 3px; }}
ul.incl-ul {{ margin: 0; padding-left: 15px; }}
ul.incl-ul li {{ font-size: 8.5pt; color: #6b7580; margin: 2px 0; line-height: 1.35; }}
table.tasks {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 8.5pt; }}
table.tasks th {{ text-align: left; color: #8a94a0; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .5px; border-bottom: 1.5px solid #cfd6dd; padding: 4px 5px; }}
table.tasks th.r, table.tasks td.r {{ text-align: right; }}
table.tasks td {{ padding: 3px 5px; border-bottom: 1px solid #f2f5f7; }}
table.tasks td.ph, table.tasks td.dt {{ color: #6b7580; white-space: nowrap; }}
table.tasks tr.crit td {{ font-weight: 600; color: {ACCENT}; }}
table.tasks tr.crit td:first-child {{ border-left: 2px solid {PRIMARY}; padding-left: 6px; }}
table.tasks thead {{ display: table-header-group; }}
.gantt-legend .sw-bar2 {{ width: 16px; height: 8px; background: #A9CBE3; }}
.callout {{ border: 1px solid #f0d9a8; background: #fbf4e6; border-radius: 0; padding: 12px 14px; }}
.ec-row {{ display: flex; gap: 24px; flex-wrap: wrap; }}
.ec-row div span {{ display: block; font-size: 8.5pt; color: #8a94a0; text-transform: uppercase; letter-spacing: 1px; }}
.ec-row div b {{ font-family: 'Sora',Arial,sans-serif; font-size: 15pt; }}
.ec-row .strike {{ text-decoration: line-through; color: #8a94a0; }}
.ec-row .amber {{ color: {AMBER}; }}
.item {{ border: 1px solid #e4e8ec; padding: 9px 11px; margin: 7px 0; page-break-inside: avoid; }}
.item.blue {{ border-left: 3px solid {PRIMARY}; }}
.item.amber-l {{ border-left: 3px solid #c98a1a; }}
.item-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }}
.item-head b {{ font-size: 10.5pt; color: {ACCENT}; }}
.item-head span {{ font-weight: 700; color: {ACCENT}; white-space: nowrap; }}
.badge {{ background: #eef1f4; color: #3f4a56; font-size: 8.5pt; padding: 2px 8px; border-radius: 0; font-weight: 600; }}
.scope {{ margin: 11px 0; page-break-inside: avoid; }}
.scope h3 {{ font-size: 11pt; margin: 0 0 4px; color: {ACCENT}; }}
ul {{ margin: 5px 0; padding-left: 16px; }}
li {{ margin: 4px 0; line-height: 1.45; }}
ul.scope-ul, ul.note-ul {{ margin: 3px 0 0; }}
ul.note-ul li {{ color: #4a5560; }}
.page-break {{ page-break-before: always; }}
/* budget donut + legend-with-bars (single tight block, no dead space) */
.budget-viz {{ display: flex; gap: 26px; align-items: center; margin: 8px 0 14px; page-break-inside: avoid; }}
.donut-wrap {{ flex: 0 0 auto; }}
.legend-wrap {{ flex: 1; }}
.viz-label {{ font-size: 8pt; letter-spacing: 1.5px; text-transform: uppercase; color: #8a94a0; margin-bottom: 8px; }}
.legend .lg {{ display: flex; align-items: center; gap: 8px; font-size: 8.5pt; color: #4a5560; margin: 3px 0; }}
.legend .sw {{ width: 9px; height: 9px; display: inline-block; flex: 0 0 auto; }}
.legend .ln {{ flex: 0 0 40mm; }}
.legend .lbar {{ flex: 1; height: 8px; background: #f0f3f6; display: block; }}
.legend .lbarf {{ display: block; height: 8px; }}
.legend .lv {{ flex: 0 0 28mm; color: #6b7580; white-space: nowrap; text-align: right; }}
/* schedule gantt */
.gantt-block {{ page-break-inside: avoid; }}
.gantt {{ margin: 8px 0 4px; }}
.gantt-legend {{ display: flex; gap: 20px; font-size: 8pt; color: #6b7580; margin-top: 2px; }}
.gantt-legend span {{ display: inline-flex; align-items: center; }}
.gantt-legend i {{ display: inline-block; margin-right: 5px; }}
.gantt-legend .sw-bar {{ width: 16px; height: 8px; background: {PRIMARY}; }}
.gantt-legend .sw-dia {{ width: 8px; height: 8px; background: {ACCENT}; transform: rotate(45deg); }}
"""
