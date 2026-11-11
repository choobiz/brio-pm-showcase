"""Client package — standalone branded documents + a merged bundle.

Produces: cover/index, discussion summary, schedule, cash-flow requirement, and a
CCDC-style stipulated-sum contract (adapted from the BRIO contract template). The
estimate (material-selection edition) is rendered by estimate_selection_pdf and
merged in. All share the BRIO brand; no curves. All content is project-variable —
doc reference codes, the page footer, and the discussion summary derive from the
project (no project-specific text is hard-coded).
"""
from __future__ import annotations
from app.company import LEGAL_NAME, WEBSITE

import re
from typing import Any

from app.services.estimate_pdf import _esc, _money, _num
from app.services.pdf_brand import logo_data_url
from app.services.pdf_charts import gantt_svg

PRIMARY = "#0068A0"
ACCENT = "#1D3146"

# BRIO's third-party home-warranty provider (2-5-10), disclosed in the contract
# for "new home" (dwelling) projects. BRIO-wide, not per-project.
HOME_WARRANTY_PROVIDER = "Travelers Insurance"


def _pkg_slug(project: dict) -> str:
    """A short uppercase token for reference codes, from the project slug/name."""
    raw = (project or {}).get("slug") or (project or {}).get("name") or "project"
    return re.sub(r"[^A-Z0-9]+", "-", str(raw).upper()).strip("-") or "PROJECT"


def _ref(project: dict, kind: str, n: int = 1) -> str:
    """Doc reference code, e.g. SUM-123-EXAMPLE-ST-001 — derived from the project."""
    return f"{kind}-{_pkg_slug(project)}-{n:03d}"


def _project_label(project: dict | None) -> str:
    """Project name for the running page header (empty-safe)."""
    name = str((project or {}).get("name") or "").replace('"', "").strip()
    return name or "Client Package"


# Cover sheets are full-bleed (no margin, no running header/footer). Docs that
# open with a cover use this; a standalone doc (e.g. the contract on its own)
# sets first_page_plain=False so page 1 gets the normal margins + header/footer.
_FIRST_PAGE_COVER_RULE = (
    '@page :first { margin: 0;\n'
    '  @top-left { content: ""; } @top-right { content: ""; }\n'
    '  @bottom-left { content: ""; } @bottom-center { content: ""; } @bottom-right { content: ""; } }'
)


def _render(html: str, project: dict | None = None, first_page_plain: bool = True,
            doc_label: str = "Client Package") -> bytes:
    from weasyprint import HTML
    css = _CSS.replace("__PKG_PROJECT__", _project_label(project))
    css = css.replace("__PKG_DOCLABEL__", doc_label)
    css = css.replace("__FIRST_PAGE_RULE__", _FIRST_PAGE_COVER_RULE if first_page_plain else "")
    return HTML(string=f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{html}</body></html>").write_pdf()


def _doc_cover(title: str, subtitle: str, ref: str, project: dict, extra: str = "", hero: str | None = None) -> str:
    hero_html = f'<div class="cover-hero"><img src="{hero}" alt="Project elevation"/></div>' if hero else ""
    return f"""
    <div class="cover">
      <div class="brand"><img src="{logo_data_url()}" alt="BRIO"/></div>
      {hero_html}
      <div class="cover-body">
        <div class="doc-kicker">{LEGAL_NAME} &middot; Client Package</div>
        <h1 class="doc-title">{_esc(subtitle)}</h1>
        <div class="cover-meta">
          <div class="proj-name">{_esc(title)}</div>
          <div>{_esc(project.get('address') or '')}</div>
          <div>{_esc(ref)} &middot; {WEBSITE}</div>
        </div>
        {extra}
      </div>
    </div>"""


def _fmt_d(iso) -> str:
    from datetime import date as _d
    try:
        return _d.fromisoformat(str(iso)[:10]).strftime("%b %-d, %Y")
    except Exception:
        return str(iso or "—")


# ── Package cover + one-page overview ────────────────────────────────────────
def render_cover(project: dict, client: dict, total: float, docs: list[tuple[str, str]],
                 hero: str | None = None, *, revised_total: float | None = None,
                 draws: list[dict] | None = None, sched: dict | None = None,
                 milestones: list[tuple] | None = None, scope: list | None = None) -> bytes:
    body = _doc_cover(
        project.get("name") or "Project", "Client Package", _ref(project, "PKG"), project, hero=hero,
    )

    # ── Page 2 — Project Overview (salient facts at a glance) ─────────────────
    show_total = revised_total if (revised_total and revised_total != total) else total
    sched = sched or {}
    tiles = (
        '<div class="ov-tiles">'
        f'<div><span>Contract Total</span><b>{_money(show_total)}</b><em>incl. GST</em></div>'
        f'<div><span>Start</span><b>{_fmt_d(sched.get("projectStart"))}</b></div>'
        f'<div><span>Target completion</span><b>{_fmt_d(sched.get("finishP50"))}</b></div>'
        f'<div><span>Conservative finish</span><b>{_fmt_d(sched.get("finishP80"))}</b></div>'
        '</div>'
    ) if sched.get("projectStart") else ""

    ms_rows = "".join(
        f'<tr><td>{_esc(n)}</td><td class="r dt">{_fmt_d(d)}</td></tr>' for n, d in (milestones or [])
    ) or '<tr><td class="muted">Schedule pending</td><td></td></tr>'
    # Short, single-line payment labels so the two columns line up.
    _short_pay = ["Deposit — on signing", "At building lock-up", "At interior finishes", "Completion — holdback"]
    pay_rows = "".join(
        f'<tr><td>{_esc(_short_pay[i]) if i < len(_short_pay) else _esc(dw["milestone"])}</td>'
        f'<td class="r muted">{dw.get("pct")}%</td><td class="r">{_money(dw["amount"])}</td></tr>'
        for i, dw in enumerate(draws or [])
    )
    contents_txt = " · ".join(_esc(n) for n, _ in docs)

    # Scope & pricing — category name + client-facing amount, split into two columns.
    sc = list(scope or [])
    half = (len(sc) + 1) // 2

    def _sc_rows(items):
        return "".join(f'<tr><td>{_esc(n)}</td><td class="r">{_money(a)}</td></tr>' for n, a in items)

    scope_html = (
        '<div class="ov-cols">'
        f'<div><table class="ov-t sc"><tbody>{_sc_rows(sc[:half])}</tbody></table></div>'
        f'<div><table class="ov-t sc"><tbody>{_sc_rows(sc[half:])}</tbody></table></div>'
        '</div>'
    ) if sc else '<p class="ov-scope">See the Estimate for the full scope.</p>'

    summary_txt = _esc((project.get("summary") or "").strip())
    summary_html = f'<p class="ov-summary">{summary_txt}</p>' if summary_txt else ""

    body += (
        '<section class="sec overview"><h2>Project Overview</h2>'
        f'{summary_html}'
        f'{tiles}'
        '<div class="ov-card ov-cols">'
        '<div><h3>Schedule &amp; critical dates</h3>'
        f'<table class="ov-t"><tbody>{ms_rows}</tbody></table></div>'
        '<div><h3>Payment schedule</h3>'
        f'<table class="ov-t"><tbody>{pay_rows}</tbody></table></div>'
        '</div>'
        '<div class="ov-card">'
        '<h3>Scope &amp; pricing</h3>'
        f'{scope_html}'
        '<p class="ov-note">Amounts are the client price for each category, inclusive of GST.</p>'
        '</div>'
        f'<p class="fine">Prepared for {_esc((client or {}).get("name") or "the Owner")}. '
        'All figures in CAD, inclusive of GST, based on the current permit-set drawings.</p>'
        '</section>'
    )
    return _render(body, project)


def render_summary(project: dict, client: dict, points: list[dict] | None = None,
                   sw_total: float = 0.0, site_total: float = 0.0) -> bytes:
    """Discussion summary. `points` come from the PROJECT (projects.discussion_points);
    there is no hard-coded fallback — a project with no points renders a short
    placeholder rather than another project's notes. A point with
    computed=="stormwater" still substitutes {sw}/{site}/{total} live."""
    body = _doc_cover(project.get("name") or "Project", "Discussion Summary", _ref(project, "SUM"), project)
    items = ""
    for i, pt in enumerate([p for p in (points or []) if isinstance(p, dict)], 1):
        if not (pt.get("title") or pt.get("body")):
            continue
        txt = _esc(pt.get("body") or "")
        if pt.get("computed") == "stormwater":
            txt = (txt.replace("{sw}", f"<b>{_money(sw_total)}</b>")
                      .replace("{site}", f"<b>{_money(site_total)}</b>")
                      .replace("{total}", f"<b>{_money(sw_total + site_total)}</b>"))
        items += (f'<div class="disc"><div class="disc-h"><span class="num">{i}</span>{_esc(pt.get("title") or "")}</div>'
                  f'<p>{txt}</p></div>')
    if not items:
        items = ('<p class="lead">Discussion notes for this project have not been added yet. '
                 'Add them on the project so this summary reflects what was reviewed with the client.</p>')
    body += f'<section class="sec"><h2>What We Discussed</h2>{items}</section>'
    return _render(body, project)


# ── Schedule (Gantt + task table) ────────────────────────────────────────────
def render_schedule(project: dict, sched: dict, activities: list[dict],
                    revision: str | None = None, revision_note: str | None = None) -> bytes:
    """`revision` suffixes the document reference (e.g. "002") and
    `revision_note` prints beneath the dates.

    A re-baselined schedule carries different dates under the same reference as
    the one the client already holds, which is how two versions of a programme end
    up in circulation with nothing to tell them apart. Both default to None, so
    every existing caller renders exactly as before.
    """
    acts = [a for a in activities if not a.get("milestone")]
    bars = [(a.get("name"), a.get("start"), a.get("finish"), a.get("critical")) for a in acts]
    ms = [(a.get("name"), a.get("start")) for a in activities if a.get("milestone")]
    gantt = gantt_svg(sched.get("projectStart"), sched.get("finishP50"), bars, ms, width=940)
    facts = []
    if sched.get("projectStart"): facts.append(f"Start {_esc(sched['projectStart'])}")
    if sched.get("finishP50"): facts.append(f"Target finish (P50) {_esc(sched['finishP50'])}")
    if sched.get("finishP80"): facts.append(f"Conservative (P80) {_esc(sched['finishP80'])}")
    # _ref already ends in a sequence number; a revision REPLACES it rather than
    # appending, or the reference reads "...-001-002".
    ref = _ref(project, "SCH")
    if revision:
        ref = re.sub(r"-\d+$", "", ref) + f"-{revision}"
    trows = "".join(
        f'<tr class="{"crit" if a.get("critical") else ""}"><td>{_esc(a.get("name"))}</td>'
        f'<td class="ph">{_esc(a.get("phase") or "")}</td><td class="r">{round(_num(a.get("duration")))}</td>'
        f'<td class="dt">{_esc(a.get("start") or "")}</td><td class="dt">{_esc(a.get("finish") or "")}</td></tr>'
        for a in acts)
    legend = ('<div class="gantt-legend"><span><i class="sw-bar"></i>Critical path</span>'
              '<span><i class="sw-bar2"></i>Other work</span><span><i class="sw-dia"></i>Milestone</span></div>')
    body = _doc_cover(project.get("name") or "Project", "Project Schedule", ref, project)
    body += (
        '<section class="sec"><h2>Schedule</h2>'
        "<p class='lead'>Construction sequence with the critical path highlighted. See the Estimate for the "
        "priced scope and the Cash-Flow Requirement for the draw timing.</p>"
        f'<p class="muted">{" &middot; ".join(facts)}</p>'
        + (f'<p class="fine">{_esc(revision_note)}</p>' if revision_note else "")
        + '</section>'
    )
    # Gantt on its own LANDSCAPE page (wide timeline), task list back in portrait.
    body += (f'<div class="land"><div class="land-h">Construction Timeline</div>'
             f'<div class="gantt">{gantt}</div>{legend}</div>')
    body += (
        '<section class="sec tasks-sec"><h2>Task List</h2>'
        '<table class="tasks"><thead><tr><th>Task</th><th>Phase</th><th class="r">Days</th><th>Start</th><th>Finish</th></tr></thead>'
        f'<tbody>{trows}</tbody></table>'
        '<p class="fine">Working days (Mon–Fri), most-likely durations with a P80 confidence buffer. Assumes permits '
        'are issued at start; permitting is a separate pre-construction step.</p></section>'
    )
    return _render(body, project)


# ── Cash-flow requirement (draws in $) ───────────────────────────────────────
def render_cashflow(project: dict, total: float, draws: list[dict]) -> bytes:
    rows = ""
    running = 0.0
    for i, d in enumerate(draws, 1):
        running += d["amount"]
        rows += (f'<tr><td>{i}</td>'
                 f'<td><div class="ms-name">{_esc(d["milestone"])} '
                 f'<span class="ms-pct">({d.get("pct")}%)</span></div>'
                 f'<div class="ms-det">{_esc(d.get("detail") or "")}</div></td>'
                 f'<td class="r">{_money(d["amount"])}</td>'
                 f'<td class="r muted">{_money(running)}</td></tr>')
    body = _doc_cover(project.get("name") or "Project", "Cash-Flow Requirement", _ref(project, "CF"), project)
    body += (
        '<section class="sec"><h2>Payment Draw Schedule</h2>'
        "<p class='lead'>Payment follows the milestone draw schedule below, with a final holdback released once the "
        "work is complete and accepted.</p>"
        '<table class="draws"><thead><tr><th>#</th><th>Milestone</th><th class="r">Draw</th>'
        '<th class="r">Cumulative</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'<tfoot><tr class="grand"><td></td><td>Contract Total (incl. GST)</td>'
        f'<td class="r">{_money(total)}</td><td></td></tr></tfoot></table>'
        '<p class="fine">All amounts CAD, inclusive of GST. The final 10% is released after final completion and '
        'acceptance. See the Contract for the full payment terms and the Estimate for the priced scope.</p></section>'
    )
    return _render(body, project)


# ── Contract (adapted CCDC-style stipulated sum) ─────────────────────────────
def render_contract(project: dict, client: dict, pretax: float, gst: float, total: float,
                    draws: list[dict], start: str, finish: str, refs: dict,
                    cover: bool = True, signatories: list[dict] | None = None,
                    change_orders: list[dict] | None = None, base_pretax: float | None = None) -> bytes:
    from datetime import date as _d
    pay_rows = "".join(
        f'<tr><td>{i}</td><td>{_esc(d["milestone"])}</td><td class="r">{d["pct"]}%</td>'
        f'<td class="r">{_money(d["amount"] / 1.05)}</td></tr>'
        for i, d in enumerate(draws, 1))
    owner = _esc((client or {}).get("name") or "the Owner")
    owner_addr = _esc((client or {}).get("address") or "")
    addr = _esc(project.get("address") or "")
    contract_date = _d.today().strftime("%B %-d, %Y")
    A = f'<h3 class="article">Article A-1 — The Work</h3><p>The Contractor shall perform all labour, materials, equipment, and services necessary to complete the Work at {addr} as described in the Contract Documents (the "Work"), in accordance with the Contract Documents.</p>'
    _co_list = [c for c in (change_orders or []) if _num(c.get("cost_impact")) != 0]
    _sched_g = ""
    if _co_list:
        _items = "".join(
            f'<li>CO-{c["number"]}: {_esc(c["title"])} '
            f'({"+" if _num(c["cost_impact"]) >= 0 else "−"}{_money(abs(_num(c["cost_impact"])))} pre-tax)</li>'
            for c in _co_list)
        _sched_g = ('<li><b>Schedule G</b> — Approved Change Order(s), incorporated into the '
                    f'Contract Price under Article A-3:<ul>{_items}</ul></li>')
    A += ('<h3 class="article">Article A-2 — Contract Documents</h3><p>The Contract consists of this Agreement and the following Schedules, which are incorporated by reference:</p><ul class="clauses">'
          f'<li><b>Schedule A</b> — Project Information;</li>'
          f'<li><b>Schedule B</b> — Scope of Work (BRIO Estimate {_esc(refs.get("estimate",""))});</li>'
          f'<li><b>Schedule C</b> — Payment Schedule (Article A-4) + Cash-Flow Requirement ({_esc(refs.get("cashflow",""))});</li>'
          + (f'<li><b>Schedule D</b> — Project Schedule ({_esc(refs.get("schedule",""))}, including the CPM critical path);</li>'
             if start else
             '<li><b>Schedule D</b> — Project Schedule, to be agreed in writing by both parties before the Work starts;</li>') +
          f'<li><b>Schedule E</b> — Permit-set Drawings (Architectural, Structural, Stormwater);</li>'
          f'<li><b>Schedule F</b> — Insurance Requirements (Article A-9){";" if _co_list else "."}</li>'
          f'{_sched_g}</ul>')
    if _co_list and base_pretax is not None:
        _price_rows = f'<tr><td>Base Contract Value (pre-tax)</td><td class="r">{_money(base_pretax)}</td></tr>'
        for c in _co_list:
            _ci = _num(c["cost_impact"])
            _price_rows += (f'<tr><td>Change Order CO-{c["number"]} — {_esc(c["title"])}</td>'
                            f'<td class="r">{"+" if _ci >= 0 else "−"}{_money(abs(_ci))}</td></tr>')
        _price_rows += f'<tr><td><b>Revised Contract Value (pre-tax)</b></td><td class="r"><b>{_money(pretax)}</b></td></tr>'
    else:
        _price_rows = f'<tr><td>Contract Value (pre-tax)</td><td class="r">{_money(pretax)}</td></tr>'
    A += ('<h3 class="article">Article A-3 — Contract Price</h3><p>The Contract Price for the Work is a <b>stipulated sum</b>, not subject to adjustment except by written Change Order under Article A-7:</p>'
          '<table class="price"><tbody>'
          f'{_price_rows}'
          f'<tr><td>GST (5%)</td><td class="r">{_money(gst)}</td></tr>'
          f'<tr class="grand"><td><b>Contract Total (incl. GST)</b></td><td class="r"><b>{_money(total)}</b></td></tr></tbody></table>')
    A += ('<h3 class="article">Article A-4 — Payment</h3><p>The Owner shall pay the Contractor per the following Payment Schedule, on certified completion of each milestone. The first payment shown below is payable on execution of this Agreement.</p>'
          '<table class="pay"><thead><tr><th>#</th><th>Milestone</th><th class="r">% of Contract</th><th class="r">Amount (excl. GST)</th></tr></thead>'
          f'<tbody>{pay_rows}</tbody></table>'
          '<ul class="clauses">'
          '<li><b>4.1</b> The Contractor shall invoice on achievement of each milestone; the Owner shall pay within fifteen (15) days of receipt.</li>'
          '<li><b>4.2 Holdback.</b> As required by the Builders Lien Act (BC), the Owner holds back ten percent (10%) of each payment (the "holdback"). This holdback protects the Owner against claims by subcontractors or suppliers. The Owner releases it 55 days after Substantial Performance, provided no liens remain registered against the property.</li>'
          '<li><b>4.3 Completion &amp; Acceptance.</b> Any remaining amount is paid after final completion and acceptance, once all deficiencies (punch-list items) are remedied.</li>'
          '<li><b>4.4</b> All amounts are exclusive of GST, which the Owner shall pay in addition.</li>'
          '<li><b>4.5</b> Overdue amounts bear interest at two percent (2%) per month (twenty-four percent (24%) per year).</li></ul>')
    # No schedule yet (456 W Sample, 2026-10-27): the dates are agreed later, in
    # writing, instead of printing "None" into a contract.
    _when = (f'The Contractor shall commence the Work on <b>{_esc(start)}</b> and achieve Substantial Performance '
             f'on or before <b>{_esc(finish)}</b>.' if start and finish else
             'The date the Contractor shall commence the Work and the date by which it shall achieve Substantial '
             'Performance shall be agreed in writing by both parties before the Work starts, and recorded in Schedule D.')
    A += (f'<h3 class="article">Article A-5 — Contract Time</h3><p>{_when} Working hours are Monday–Friday, 7:00am–5:00pm, BC statutory holidays observed. The Contract Time is subject to extension for Force Majeure (weather beyond seasonal norms, strikes, governmental delays, hazmat, and events outside the Contractor\'s reasonable control).</p>')
    A += ('<h3 class="article">Article A-6 — Substantial Performance and Final Completion</h3><ul class="clauses">'
          '<li><b>6.1</b> "Substantial Performance" has the meaning given in the Builders Lien Act (BC): in plain terms, the Work is ready to be used for its intended purpose and only a minor amount of work remains. The exact threshold is set by that Act.</li>'
          '<li><b>6.2</b> Upon Substantial Performance the Owner shall issue a Certificate, and the holdback period commences.</li>'
          '<li><b>6.3</b> "Final Completion" means all Work and punch-list items are complete and all occupancy permits are issued.</li></ul>')
    A += ('<h3 class="article">Article A-7 — Changes in the Work</h3><ul class="clauses">'
          '<li><b>7.1</b> Changes shall be directed by written Change Order executed by both parties before the change is performed.</li>'
          '<li><b>7.2</b> Each Change Order specifies the changed Work, the price adjustment, the time adjustment (if any), and both signatures.</li>'
          '<li><b>7.3</b> Additive changes are priced at actual cost plus a twenty percent (20%) management fee, consistent with the Base Contract markup.</li>'
          '<li><b>7.4</b> Hidden or unforeseen site conditions (hazmat, undocumented structural conditions, pre-existing code violations, or existing services not shown on the drawings) materially affecting the Work are addressed by Change Order at the Owner\'s expense.</li></ul>')
    A += ('<h3 class="article">Article A-8 — Subcontractors</h3><ul class="clauses">'
          '<li><b>8.1</b> The Contractor may engage subcontractors and remains responsible for their Work.</li>'
          '<li><b>8.2</b> The Contractor shall pay subcontractors per the Builders Lien Act (BC).</li>'
          '<li><b>8.3</b> The Owner shall not direct subcontractors except through the Contractor.</li></ul>')
    A += ('<h3 class="article">Article A-9 — Insurance and Indemnification</h3><ul class="clauses">'
          '<li><b>9.1 Contractor\'s Insurance:</b> Commercial General Liability ($5,000,000/occurrence), Auto liability ($2,000,000), WorkSafeBC coverage in good standing.</li>'
          '<li><b>9.2 Owner\'s Insurance:</b> Course of Construction insurance covering the Work, limit not less than the Contract Price.</li>'
          '<li><b>9.3</b> Each party provides proof of insurance on request and names the other as additional insured where appropriate.</li>'
          '<li><b>9.4 Mutual Indemnification.</b> Each party indemnifies the other for claims arising from its own negligence or breach.</li></ul>')
    warranty_2510 = ""
    if project.get("home_warranty_required"):
        warranty_2510 = (
            f'<li><b>10.4 Third-Party Home Warranty (2-5-10).</b> Because this is a new home under the British Columbia Homeowner Protection Act, the Work is covered by mandatory third-party home warranty insurance, provided by <b>{_esc(HOME_WARRANTY_PROVIDER)}</b>. This coverage is in addition to the Contractor\'s workmanship warranty in 10.1.</li>'
            '<li><b>10.5</b> The 2-5-10 coverage provides, from the date the home warranty begins:'
            '<ul><li>two (2) years on labour and materials (with some shorter sub-limits set by the policy);</li>'
            '<li>five (5) years on the building envelope (water penetration); and</li>'
            '<li>ten (10) years on the structure of the home.</li></ul></li>'
            '<li><b>10.6</b> BRIO Construction Inc. is a Licensed Residential Builder in British Columbia. The Contractor will enrol the home with the warranty provider and give the Owner the warranty certificate and policy documents, which set out the full terms, coverage, and exclusions.</li>'
        )
    A += ('<h3 class="article">Article A-10 — Warranty</h3><ul class="clauses">'
          '<li><b>10.1</b> The Contractor warrants the Work free from defects in workmanship for one (1) year from Substantial Performance.</li>'
          '<li><b>10.2</b> Manufacturer warranties on materials pass through to the Owner.</li>'
          '<li><b>10.3</b> The Contractor shall promptly remedy defective Work under warranty at no cost to the Owner.</li>'
          f'{warranty_2510}</ul>')
    A += ('<h3 class="article">Article A-11 — Default and Termination</h3><ul class="clauses">'
          '<li><b>11.1</b> Either party may terminate for material breach on ten (10) days\' written notice and opportunity to cure.</li>'
          '<li><b>11.2</b> The Owner may terminate for convenience, paying for Work performed to date plus the profit margin on Work not performed.</li>'
          '<li><b>11.3</b> On termination the Contractor ceases Work, protects materials, and delivers documents to the Owner.</li></ul>')
    A += ('<h3 class="article">Article A-12 — Dispute Resolution</h3><ul class="clauses">'
          '<li><b>12.1</b> The parties will first try to resolve any dispute through good-faith discussion.</li>'
          '<li><b>12.2</b> If that does not resolve it, the parties will attempt mediation in Vancouver, BC with a mutually agreed mediator, sharing the mediator\'s fee equally.</li>'
          '<li><b>12.3</b> If mediation does not resolve the dispute, either party may take the matter to the courts of British Columbia or, where it applies, the Civil Resolution Tribunal. Nothing in this Contract requires the Owner to give up the right to have a dispute decided by a court or tribunal.</li></ul>')
    A += ('<h3 class="article">Article A-13 — General Provisions</h3><ul class="clauses">'
          '<li><b>13.1 Governing Law.</b> This Contract is governed by the laws of British Columbia.</li>'
          '<li><b>13.2 Entire Agreement.</b> This Contract and its Schedules constitute the entire agreement, superseding prior negotiations.</li>'
          '<li><b>13.3 Amendments</b> must be in writing and signed by both parties.</li>'
          '<li><b>13.4 Severability.</b> If any provision is found invalid, the rest of the Contract remains in effect.</li></ul>')
    A += ('<h3 class="article">Article A-14 — Your Cancellation Rights</h3>'
          '<p>This is a contract for services to be provided in the future, and is subject to British Columbia\'s '
          'Business Practices and Consumer Protection Act. The Owner has the following rights:</p><ul class="clauses">'
          '<li><b>14.1</b> If this Contract does not include all of the information required by the Business Practices and Consumer Protection Act, the Owner may cancel it for up to one (1) year after receiving a signed copy, as provided by that Act.</li>'
          '<li><b>14.2</b> If this Contract was signed somewhere other than the Contractor\'s regular place of business (for example, at the Owner\'s home), the Owner may also cancel it, without reason, within ten (10) days of receiving a signed copy.</li>'
          '<li><b>14.3</b> To cancel, the Owner gives written notice to the Contractor at the address in this Contract, by any method that shows the date it was sent (such as email or registered mail). On cancellation, the Contractor refunds amounts paid, less the value of Work already performed and materials already ordered at the Owner\'s request.</li></ul>')
    # Signatories: configurable list of {name, title}. Falls back to the two-owner
    # + BRIO default. Rendered two per row; the GST reg. no. sits under the block.
    sig_items = [s for s in (signatories or []) if (s.get("name") or s.get("title"))]
    if not sig_items:
        sig_items = [
            {"name": owner, "title": "Owner"},
            {"name": "", "title": "Co-Owner (if the property has more than one registered owner)"},
            {"name": "BRIO Construction Inc.", "title": "Contractor · Per: Amir Omidvar, Authorized Signatory"},
        ]

    def _sig_cell(s: dict) -> str:
        nm = _esc(s.get("name") or "")
        ti = _esc(s.get("title") or "")
        # Always render name/title blocks (with a non-breaking space fallback) so
        # every cell has the same structure and the fields line up column-to-column.
        name_html = f'<div class="sig-name">{nm or "&nbsp;"}</div>'
        title_html = f'<div class="sig-title">{ti or "&nbsp;"}</div>'
        return (f'<td><div class="sig-line"></div>{name_html}{title_html}'
                f'<div class="sig-cap">Signature &middot; Date</div></td>')

    sig_rows = ""
    for i in range(0, len(sig_items), 2):
        pair = sig_items[i:i + 2]
        cells = "".join(_sig_cell(s) for s in pair)
        if len(pair) == 1:
            cells += '<td></td>'  # keep the two-column grid aligned
        sig_rows += f'<tr>{cells}</tr>'

    A += ('<h2 style="margin-top:22px;">Execution</h2>'
          '<p>This Contract is a binding legal agreement — please read it carefully and keep a signed copy. '
          '<b>All registered owners of the property must sign below.</b></p>'
          f'<table class="sig">{sig_rows}</table>'
          '<p class="sig-gst">GST Reg. No. 000000000RT0001</p>')

    # The cover page acts as a section divider inside the merged package. For a
    # standalone contract PDF it's just a redundant title sheet, so omit it.
    body = _doc_cover(project.get("name") or "Project", "Construction Contract",
                      _ref(project, "CONTRACT"), project) if cover else ""
    owner_line = f"<b>{owner}</b>" + (f" of {owner_addr}" if owner_addr else "")
    body += (f'<section class="sec contract"><h2>Construction Contract — Stipulated Sum</h2>'
             f'<p class="lead">This Agreement is made on <b>{contract_date}</b> between {owner_line} (the "Owner") '
             f'and <b>BRIO Construction Inc.</b>, a British Columbia company (the "Contractor"), for the Work at {addr}. '
             f'It is a contract for services to be provided in the future under BC\'s Business Practices and Consumer Protection Act '
             f'(see Article A-14 for the Owner\'s cancellation rights).</p>{A}</section>')
    # Standalone contract (no cover) → page 1 must be a normal page (margins +
    # header/footer) and the running header names the doc "Construction Contract"
    # (not "Client Package"). With a cover, keep the package treatment.
    return _render(body, project, first_page_plain=cover,
                   doc_label="Client Package" if cover else "Construction Contract")


# Generic 30 / 30 / 30 / 10 draw schedule — milestone names + plain-language
# descriptions that apply to any BRIO build. Project-specific narrative is NOT
# hard-coded here; it belongs in the estimate/scope.
_DRAW_WEIGHTS = [
    ("Contract signing & mobilization — deposit", 30,
     "Secures the contract and mobilizes the project — building permit, supplier and sub-trade deposits, "
     "site setup and protection — and funds the first phase of work: site preparation, demolition, "
     "excavation, and the foundation."),
    ("Foundation, framing & building weathertight (lock-up)", 30,
     "Structural framing and the roof structure, roof membrane, exterior windows and doors, and the exterior "
     "cladding — bringing the building to a weathertight lock-up, including the lumber and structural packages, "
     "roof sheathing/membrane, and window/door supply."),
    ("MEP rough-in, insulation, drywall & interior finishes", 30,
     "The interior build-out: plumbing / electrical / HVAC rough-in, insulation, drywall, paint, flooring and "
     "tile, millwork and cabinetry, and fixture installation — including the associated material and fixture "
     "purchases."),
    ("Substantial completion & occupancy — holdback", 10,
     "Final finishes and trim, mechanical commissioning, final cleaning, deficiency (punch-list) correction, "
     "and final inspections and occupancy. Released once the work is complete and accepted."),
]


def _package_hero(drawings: list[dict]) -> str | None:
    """Compressed JPEG elevation for the covers — a 3D/elevation drawing at modest res
    (embedded on several covers, so keep it small). Skips site/stormwater/plan sheets."""
    def _score(d: dict) -> int:
        fn = (d.get("filename") or "").lower()
        if any(k in fn for k in ("3d", "exterior", "elevation", "render", "perspect")):
            return 2
        if any(k in fn for k in ("site", "storm", "structural", "plan", "cover", "detail")):
            return -1
        return 0
    best = max(drawings, key=_score, default=None)
    key = best.get("r2_key") if best else None
    if not key or _score(best) <= 0:
        return None
    try:
        import base64
        import fitz  # pymupdf
        from app.services.r2 import get_bytes
        d = fitz.open(stream=get_bytes(key), filetype="pdf")
        pix = d[0].get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        jpg = pix.tobytes(output="jpg", jpg_quality=72)
        d.close()
        return "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    except Exception:
        return None


class PackageIncomplete(ValueError):
    """A requested package document cannot exist for this project yet."""


def load_schedule(db, project_id: str) -> tuple[dict, list[dict]]:
    """The latest schedule's dates and activities, or ({}, []) when the project
    has none. A project priced before it is scheduled (456 W Sample,
    2026-10-27) made the whole client package a 500 on ``.one()``."""
    from sqlalchemy import text as _T
    srow = db.execute(_T("select id,project_start,p50_finish_date,p80_finish_date from schedules where project_id=:p and client_visible order by created_at desc limit 1"),
                      {"p": project_id}).mappings().first()
    if srow is None:
        return {}, []
    arows = db.execute(_T("select activity_code,name,phase,expected_wd,start_date,finish_date,is_critical,is_milestone from activities where schedule_id=:s order by early_start_wd"),
                       {"s": srow["id"]}).mappings().all()
    acts = [{"name": a["name"], "phase": a["phase"], "duration": _num(a["expected_wd"]),
             "start": str(a["start_date"]), "finish": str(a["finish_date"]),
             "critical": a["is_critical"], "milestone": a["is_milestone"]} for a in arows]
    sched = {"projectStart": str(srow["project_start"]), "finishP50": str(srow["p50_finish_date"]), "finishP80": str(srow["p80_finish_date"])}
    return sched, acts


def package_order(has_schedule: bool) -> list[str]:
    """Documents in the merged package; the schedule only when there is one."""
    order = ["cover", "summary", "estimate", "schedule", "cashflow", "contract"]
    return order if has_schedule else [d for d in order if d != "schedule"]


def build_client_package(db, project_id: str, estimate_ref: str = "active", doc: str = "merged") -> tuple[bytes, str]:
    """Build the client package for a project. doc = merged | cover | summary |
    estimate | schedule | cashflow | contract. Returns (pdf_bytes, filename)."""
    from sqlalchemy import text as _T
    from app.services.estimate_selection_pdf import render_client_selection_pdf

    if estimate_ref in (None, "", "active"):
        _r = db.execute(_T("select reference, payload from estimate_versions where project_id=:p and is_active=true"),
                        {"p": project_id}).first()
        if _r is None:
            raise PackageIncomplete("This project has no current estimate, so there is no client package to build.")
        estimate_ref, est = _r[0], _r[1]
    else:
        _r = db.execute(_T("select payload from estimate_versions where reference=:r and project_id=:p"),
                        {"r": estimate_ref, "p": project_id}).first()
        if _r is None:
            raise PackageIncomplete(f"No estimate with reference {estimate_ref} on this project.")
        est = _r[0]
    # Stormwater vs. site-servicing split for the summary comment. Prefer SEPARATE
    # categories (the standard now — "… Stormwater …" / "… Site Work …"); fall back to
    # item-keyword classification for legacy estimates with one combined "16 …" category.
    _SW = ("ecobloc", "filter", "tank", "sump", "tie-in", "downspout", "locat", "infiltration")
    sw_total = site_total = 0.0
    _has_split = False

    def _cat_cont(_c: dict) -> float:  # category contingency (pulled out in the client view)
        return sum(_num(_it.get("materials")) for _it in (_c.get("items") or [])
                   if "contingency" in str(_it.get("name") or "").lower())
    for _c in est.get("categories", []):
        _cn = str(_c.get("name") or "").lower()
        if "stormwater" in _cn:
            sw_total += _num(_c.get("total")) - _cat_cont(_c); _has_split = True
        elif "site work" in _cn:
            site_total += _num(_c.get("total")) - _cat_cont(_c); _has_split = True
    if not _has_split:
        for _c in est.get("categories", []):
            if str(_c.get("name") or "").startswith("16 "):
                for _it in _c.get("items", []):
                    _nm = str(_it.get("name") or "").lower()
                    if "contingency" in _nm:
                        continue
                    _amt = _num(_it.get("labor")) + _num(_it.get("materials"))
                    if any(k in _nm for k in _SW):
                        sw_total += _amt
                    else:
                        site_total += _amt
    # Fold in the management fee (overhead + profit) so the summary figures match the
    # client-facing estimate-detail category totals (which are shown at margin), not the
    # internal direct cost.
    _m = (1 + _num(est.get("overhead")) / 100) * (1 + _num(est.get("profit")) / 100)
    sw_total *= _m
    site_total *= _m
    proj = dict(db.execute(_T("select * from projects where id=:p"), {"p": project_id}).mappings().one())
    client = {}
    if proj.get("client_id"):
        c = db.execute(_T("select * from clients where id=:c"), {"c": proj["client_id"]}).mappings().first()
        client = dict(c) if c else {}
    project = {"name": proj.get("name"), "address": proj.get("address"), "type": proj.get("type"),
               "sqft": proj.get("sqft"), "slug": proj.get("slug"), "summary": proj.get("summary"),
               "discussion_points": proj.get("discussion_points") or [],
               "home_warranty_required": bool(proj.get("home_warranty_required"))}
    # Elevation image for the covers — compressed JPEG, 3D/elevation drawing only.
    drawings = db.execute(_T("select id, filename, r2_key from documents where project_id=:p and category='drawing' "
                             "and archived=false and (mime='application/pdf' or lower(filename) like '%.pdf')"),
                          {"p": project_id}).mappings().all()
    hero = _package_hero([dict(d) for d in drawings])  # first (package) cover only

    sched, acts = load_schedule(db, project_id)
    has_schedule = bool(sched)

    oh, pr = _num(est.get("overhead")), _num(est.get("profit"))
    base = sum(_num(c.get("total")) for c in est.get("categories", []) if not c.get("optional"))
    pretax = round(base * (1 + oh / 100) * (1 + pr / 100))
    total = round(pretax * 1.05)
    gst = total - pretax

    # Approved change orders fold into the contract price at cost (pre-tax cost_impact,
    # +GST only — no markup), so the standalone contract + payment schedule reflect the
    # true revised value. With no approved COs, revised == base (behaviour unchanged).
    _co_rows = db.execute(_T("select number, title, cost_impact from change_orders "
                             "where project_id=:p and status='approved' order by number"),
                          {"p": project_id}).mappings().all()
    _cos = [{"number": r["number"], "title": r["title"], "cost_impact": _num(r["cost_impact"])} for r in _co_rows]
    _co = sum(c["cost_impact"] for c in _cos)
    r_pretax = pretax + round(_co)
    r_total = round(r_pretax * 1.05)
    r_gst = r_total - r_pretax
    revised_total = r_total

    # Per-project payment schedule: prefer the contract's own payload.paymentSchedule
    # ([{milestone, pct, detail?}]); otherwise the generic 30/30/30/10 default.
    _cpl = db.execute(_T("select payload from contracts where project_id=:p "
                         "order by version_number desc limit 1"), {"p": project_id}).scalar()
    _cpl = _cpl if isinstance(_cpl, dict) else {}
    _spec = _cpl.get("paymentSchedule") or []
    if _spec and all("pct" in d for d in _spec):
        _weights = [(d.get("milestone", ""), _num(d.get("pct")), d.get("detail", "")) for d in _spec]
    else:
        _weights = list(_DRAW_WEIGHTS)
    draws, acc = [], 0
    for i, (m, pct, detail) in enumerate(_weights):
        amt = r_total - acc if i == len(_weights) - 1 else round(r_total * pct / 100)
        acc += amt
        draws.append({"milestone": m, "pct": pct, "amount": amt, "detail": detail})

    idx = [("Discussion Summary", _ref(project, "SUM")), ("Estimate — Material Selections", estimate_ref),
           ("Project Schedule", _ref(project, "SCH")), ("Cash-Flow Requirement", _ref(project, "CF")),
           ("Construction Contract", _ref(project, "CONTRACT"))]
    if not has_schedule:
        idx = [d for d in idx if d[0] != "Project Schedule"]

    # Overview-page data: milestones, scope (revised_total computed above).
    milestones = [(a["name"], a["start"]) for a in acts if a.get("milestone")]
    # Category name + client-facing amount (margin folded in, incl. GST), biggest first.
    _mk = (1 + oh / 100) * (1 + pr / 100)
    scope_names = sorted(
        [(c.get("name"), round(_num(c.get("total")) * _mk * 1.05))
         for c in est.get("categories", []) if not c.get("optional") and _num(c.get("total")) > 0],
        key=lambda x: -x[1],
    )

    # Contract signatories — configurable per contract record (payload.signatories).
    # Falls back inside render_contract to the two-owner + BRIO default when absent.
    signatories = _cpl.get("signatories") if isinstance(_cpl, dict) else None

    builders = {
        "cover": lambda: render_cover(project, client, total, idx, hero=hero,
                                      revised_total=revised_total, draws=draws, sched=sched,
                                      milestones=milestones, scope=scope_names),
        "summary": lambda: render_summary(project, client, project["discussion_points"], sw_total, site_total),
        "estimate": lambda: render_client_selection_pdf({**est, "reference": estimate_ref}, project, client, "selection", hide_total=True),
        "schedule": lambda: render_schedule(project, sched, acts),
        "cashflow": lambda: render_cashflow(project, r_total, draws),
        "contract": lambda cover=True: render_contract(project, client, r_pretax, r_gst, r_total, draws,
                                            sched.get("projectStart"), sched.get("finishP50"),
                                            {"estimate": estimate_ref, "cashflow": _ref(project, "CF"),
                                             "schedule": _ref(project, "SCH")}, cover=cover,
                                            signatories=signatories, change_orders=_cos, base_pretax=pretax),
    }
    slug = _pkg_slug(project)
    if doc == "schedule" and not has_schedule:
        raise PackageIncomplete("This project has no schedule yet, so there is no schedule document to download.")
    if doc in builders:
        # Standalone contract drops the cover sheet (it's only a divider in the merge).
        if doc == "contract":
            return builders["contract"](cover=False), f"{slug}-{doc}.pdf"
        return builders[doc](), f"{slug}-{doc}.pdf"
    order = package_order(has_schedule)
    merged = merge_pdfs([builders[k]() for k in order])
    return merged, f"{slug}-Client-Package.pdf"


def merge_pdfs(pdf_bytes_list: list[bytes]) -> bytes:
    import fitz  # pymupdf
    out = fitz.open()
    for b in pdf_bytes_list:
        src = fitz.open(stream=b, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes()
    out.close()
    return data


_CSS = f"""
@page {{ size: Letter; margin: 18mm 16mm 18mm;
  @top-left {{ content: "__PKG_PROJECT__"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }}
  @top-right {{ content: "__PKG_DOCLABEL__"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }}
  @bottom-left {{ content: "{LEGAL_NAME} · {WEBSITE}"; font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }}
  @bottom-center {{ content: "North Vancouver, BC · GST 000000000RT0001"; font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }} }}
__FIRST_PAGE_RULE__
* {{ box-sizing: border-box; }}
body {{ font-family: 'Open Sans','Helvetica Neue',Arial,sans-serif; color: #26303a; font-size: 10.5pt; line-height: 1.5; margin: 0; }}
h1,h2,h3 {{ font-family: 'Sora','Helvetica Neue',Arial,sans-serif; color: {ACCENT}; }}
.cover {{ min-height: 100vh; padding: 24mm 22mm; color: {ACCENT}; background: #fff; page-break-after: always; display: flex; flex-direction: column; }}
.cover .brand img {{ height: 40px; }}
.cover .cover-hero {{ margin-top: 11mm; border: 1px solid #e4e8ec; padding: 5mm; }}
.cover .cover-hero img {{ display: block; width: 100%; height: 74mm; object-fit: contain; background: #fff; }}
.cover .cover-body {{ margin-top: auto; }}
.cover .doc-kicker {{ font-size: 9pt; letter-spacing: 3px; text-transform: uppercase; color: #8a94a0; }}
.cover h1 {{ color: {ACCENT}; font-size: 27pt; line-height: 1.08; margin: 6px 0 0; }}
.cover h1.doc-title {{ color: {PRIMARY}; font-size: 36pt; line-height: 1.02; letter-spacing: -0.5px; margin: 10px 0 2px; }}
.cover .proj-name {{ font-size: 13pt; font-weight: 600; color: {ACCENT}; margin-top: 8px; }}
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
p.muted, .muted {{ color: #5b6672; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; font-size: 8pt; text-transform: uppercase; letter-spacing: .4px; color: #8a94a0; border-bottom: 1.5px solid #cfd6dd; padding: 5px 5px; }}
td {{ padding: 4px 5px; border-bottom: 1px solid #eef1f4; font-size: 9.5pt; }}
th.r, td.r {{ text-align: right; white-space: nowrap; }}
table.idx td {{ font-size: 10.5pt; padding: 7px 5px; }}
.disc {{ margin: 10px 0; page-break-inside: avoid; }}
.disc-h {{ font-family: 'Sora',Arial,sans-serif; font-size: 11.5pt; color: {ACCENT}; font-weight: 600; }}
.disc-h .num {{ display: inline-block; width: 20px; height: 20px; line-height: 20px; text-align: center; background: {PRIMARY}; color: #fff; font-size: 9pt; margin-right: 8px; }}
.disc p {{ margin: 4px 0 0 28px; color: #3f4a56; }}
.overview h3 {{ font-size: 10.5pt; color: {PRIMARY}; margin: 0 0 8px; border: none;
  padding: 0 0 6px; border-bottom: 1px solid #e8ecf0; }}
.ov-card {{ border: 1px solid #dfe4ea; background: #fbfcfe; padding: 13px 18px; margin: 14px 0; }}
.ov-card.ov-cols {{ gap: 40px; }}
.ov-tiles {{ display: flex; border: 1px solid #dfe4ea; margin: 14px 0 6px; }}
.ov-tiles > div {{ flex: 1; padding: 11px 14px; border-left: 1px solid #eef1f4; }}
.ov-tiles > div:first-child {{ border-left: none; }}
.ov-tiles span {{ display: block; font-size: 7pt; text-transform: uppercase; letter-spacing: 1px; color: #8a94a0; }}
.ov-tiles b {{ display: block; font-family: 'Sora',Arial,sans-serif; font-size: 13pt; color: {ACCENT}; margin-top: 4px; line-height: 1.1; }}
.ov-tiles > div:first-child b {{ color: {PRIMARY}; }}
.ov-tiles em {{ font-style: normal; font-size: 7pt; color: #8a94a0; }}
.ov-cols {{ display: flex; gap: 52px; margin-top: 2px; }}
.ov-cols > div {{ flex: 1; }}
table.ov-t {{ width: 100%; }}
table.ov-t td {{ padding: 6px 4px; border-bottom: 1px solid #eef1f4; font-size: 9pt; }}
table.ov-t td.dt {{ color: #6b7580; white-space: nowrap; padding-right: 8px; }}
table.ov-t.sc td {{ font-size: 9pt; padding: 5.5px 4px; }}
table.ov-t.sc td.r {{ white-space: nowrap; color: {ACCENT}; padding-left: 14px; }}
.ov-note {{ font-size: 8pt; color: #8a94a0; margin: 6px 0 0; }}
.ov-scope {{ color: #4a5560; font-size: 9.5pt; margin-top: 2px; }}
.ov-summary {{ color: #3f4a56; font-size: 10.5pt; line-height: 1.55; margin: 12px 0 2px; }}
.gantt-block {{ page-break-inside: avoid; }}
.gantt-legend {{ display: flex; gap: 18px; font-size: 8pt; color: #6b7580; margin-top: 2px; }}
.gantt-legend span {{ display: inline-flex; align-items: center; }} .gantt-legend i {{ display: inline-block; margin-right: 5px; }}
.gantt-legend .sw-bar {{ width: 16px; height: 8px; background: {PRIMARY}; }}
.gantt-legend .sw-bar2 {{ width: 16px; height: 8px; background: #A9CBE3; }}
.gantt-legend .sw-dia {{ width: 8px; height: 8px; background: {ACCENT}; transform: rotate(45deg); }}
@page land {{ size: Letter landscape; margin: 14mm 14mm 12mm;
  @top-left {{ content: "__PKG_PROJECT__"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }}
  @top-right {{ content: "__PKG_DOCLABEL__"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }}
  @bottom-left {{ content: "{LEGAL_NAME} · {WEBSITE}"; font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }}
  @bottom-center {{ content: "North Vancouver, BC · GST 000000000RT0001"; font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-family: 'Open Sans', Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }} }}
.land {{ page: land; page-break-before: always; page-break-after: always; }}
.land .gantt {{ text-align: center; }}
.land .gantt svg {{ display: block; width: auto; height: auto; max-width: 100%; max-height: 158mm; margin: 0 auto; }}
.land .land-h {{ font-family: 'Sora','Helvetica Neue',Arial,sans-serif; font-size: 14pt; color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; padding-bottom: 5px; margin-bottom: 12px; }}
.tasks-sec {{ page-break-before: always; }}
thead {{ display: table-header-group; }} tr {{ page-break-inside: avoid; }}
table.tasks {{ margin-top: 12px; }} table.tasks tr.crit td {{ font-weight: 600; color: {ACCENT}; }}
table.tasks tr.crit td:first-child {{ border-left: 2px solid {PRIMARY}; padding-left: 6px; }}
table.tasks td.ph, table.tasks td.dt {{ color: #6b7580; white-space: nowrap; }}
table.draws td .ms-name {{ font-weight: 600; color: {ACCENT}; }}
table.draws td .ms-name .ms-pct {{ font-weight: 600; color: {PRIMARY}; }}
table.draws td .ms-det {{ font-size: 8pt; color: #6b7580; margin-top: 3px; line-height: 1.4; }}
table.draws td {{ vertical-align: top; }}
table.draws tfoot tr.grand td, table.price tr.grand td, table.pay tr.grand td {{ font-family: 'Sora',Arial,sans-serif; font-weight: 700; color: {PRIMARY}; border-top: 2px solid #cfd6dd; font-size: 11.5pt; }}
table.price {{ width: 60%; margin-left: auto; }}
.contract h3.article {{ font-size: 12pt; color: {PRIMARY}; margin: 20px 0 7px; }}
.contract p {{ font-size: 11pt; line-height: 1.75; margin: 9px 0; }}
.contract p.lead {{ font-size: 11pt; line-height: 1.75; }}
ul.clauses {{ margin: 7px 0; padding-left: 18px; }} ul.clauses li {{ margin: 8px 0; font-size: 11pt; line-height: 1.7; }}
ul.clauses li ul {{ margin: 5px 0; }} ul.clauses li ul li {{ margin: 4px 0; }}
table.sig {{ width: 100%; margin-top: 34px; border-spacing: 0 26px; border-collapse: separate; }}
table.sig td {{ border: none; width: 50%; padding: 0 26px 0 0; font-size: 10pt; vertical-align: top; }}
table.sig td + td {{ padding: 0 0 0 26px; }}
.sig-line {{ border-bottom: 1px solid {ACCENT}; height: 42px; margin-bottom: 8px; }}
.sig-name {{ font-weight: 600; color: {ACCENT}; font-size: 10.5pt; line-height: 1.3; }}
/* min-height reserves ~2 lines so a 1-line title (e.g. "Owner") and a wrapped
   2-line title keep the caption below them aligned across columns. */
.sig-title {{ color: #5b6672; font-size: 9.5pt; line-height: 1.35; min-height: 26px; }}
.sig-cap {{ color: #9aa3ad; font-size: 8pt; letter-spacing: 0.6px; text-transform: uppercase; margin-top: 4px; }}
p.sig-gst {{ font-size: 9pt; color: #6b7580; margin-top: 10px; }}
"""
