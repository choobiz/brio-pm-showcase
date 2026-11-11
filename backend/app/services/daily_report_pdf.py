"""Branded Daily Site Report PDF (BRIO brand, no curves)."""

from __future__ import annotations
from app.company import LEGAL_NAME

from datetime import date
from typing import Any

from app.services.estimate_pdf import _esc, _num
from app.services.pdf_brand import logo_data_url

PRIMARY = "#0068A0"
ACCENT = "#1D3146"

_STATUS_LABEL = {
    "not_started": "Not started", "in_progress": "In progress",
    "completed": "Complete", "delayed": "Delayed", "not_worked": "Not worked",
}


def _fmt_date(raw: Any) -> str:
    try:
        return date.fromisoformat(str(raw)[:10]).strftime("%A, %B %-d, %Y")
    except (ValueError, TypeError):
        return str(raw or "")


def _rows(items: list[dict], cols: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    head = "".join(f"<th>{_esc(label)}</th>" for _, label in cols)
    body = ""
    for it in items:
        body += "<tr>" + "".join(f"<td>{_esc(it.get(key) or '')}</td>" for key, _ in cols) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _tasks_table(tasks: list[dict]) -> str:
    if not tasks:
        return '<p class="muted">No tasks recorded for this day.</p>'
    body = ""
    for t in tasks:
        cls = "removed" if t.get("removed") else ""
        crit = '<span class="crit-dot" title="Critical path"></span>' if t.get("is_critical") else ""
        tag = ""
        if t.get("ad_hoc"):
            tag = '<span class="tag ad">added on site</span>'
        elif t.get("removed"):
            tag = '<span class="tag rm">no longer scheduled</span>'
        st = _STATUS_LABEL.get(t.get("status"), t.get("status") or "")
        pct = _num(t.get("percent_complete"))
        body += (
            f'<tr class="{cls}"><td>{crit}{_esc(t.get("name"))}{tag}'
            f'<div class="sub">{_esc(t.get("phase") or "")}'
            + (f' · {_esc(t.get("scheduled_start"))}–{_esc(t.get("scheduled_finish"))}' if t.get("scheduled_start") else "")
            + f'</div></td>'
            f'<td class="st st-{_esc(t.get("status"))}">{_esc(st)}</td>'
            f'<td class="r">{pct:g}%</td>'
            f'<td>{_esc(t.get("crew") or "")}</td>'
            f'<td>{_esc(t.get("notes") or "")}</td></tr>'
        )
    return (
        '<table class="tasks"><thead><tr><th>Task</th><th>Status</th>'
        '<th class="r">%</th><th>Crew</th><th>Notes</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


SECTION_LABEL = {"conditions": "site conditions", "tasks": "work done", "manpower": "crew on site",
                 "deliveries": "deliveries", "visitors": "visitors", "delays": "delays",
                 "safety": "health and safety", "notes": "notes"}


def render_daily_report_pdf(*, report: dict[str, Any], project: dict[str, Any],
                            photos: list[str] | None = None) -> bytes:
    from weasyprint import HTML

    return HTML(string=daily_report_html(report=report, project=project, photos=photos)).write_pdf()


def daily_report_html(*, report: dict[str, Any], project: dict[str, Any],
                      photos: list[str] | None = None) -> str:
    """`photos` are image data URLs. The report carries what was raised for the
    office, what was not mentioned (never printed as "none") and its photos;
    all three were missing (rollout test 2026-10-14)."""
    p = report.get("payload") or {}
    cond = p.get("conditions") or {}
    safety = p.get("safety") or {}
    cond_bits = []
    if cond.get("weather"):
        cond_bits.append(f'<b>Weather:</b> {_esc(cond["weather"])}')
    if cond.get("temp_c") not in (None, ""):
        cond_bits.append(f'<b>Temp:</b> {_esc(cond["temp_c"])}&deg;C')
    if cond.get("ground"):
        cond_bits.append(f'<b>Ground:</b> {_esc(cond["ground"])}')

    def section(title: str, inner: str) -> str:
        return f'<section class="sec"><h2>{title}</h2>{inner}</section>' if inner else ""

    manpower = _rows(p.get("manpower") or [], [("trade", "Trade"), ("company", "Company"), ("count", "Crew"), ("hours", "Hours")])
    deliveries = _rows(p.get("deliveries") or [], [("item", "Item"), ("supplier", "Supplier"), ("notes", "Notes")])
    visitors = _rows(p.get("visitors") or [], [("name", "Name"), ("company", "Company"), ("purpose", "Purpose")])
    delays = _rows(p.get("delays") or [], [("reason", "Reason"), ("impact", "Impact"), ("notes", "Notes")])
    safety_html = ""
    if safety.get("incident") or safety.get("notes"):
        flag = '<b style="color:#b91c1c;">Incident reported.</b> ' if safety.get("incident") else ""
        safety_html = f'<p>{flag}{_esc(safety.get("notes") or "")}</p>'
    notes_html = f'<p>{_esc(p.get("notes"))}</p>' if p.get("notes") else ""
    raised = [r for r in (p.get("raised_for_office") or []) if r]
    raised_html = ("<ul>" + "".join(
        f"<li>{_esc(r.get('what') if isinstance(r, dict) else r)}"
        + (f" <span class=\"muted\">({_esc(r.get('why'))})</span>" if isinstance(r, dict) and r.get('why') else "")
        + "</li>" for r in raised) + "</ul>") if raised else ""
    missing = [SECTION_LABEL.get(s, s) for s in (p.get("not_recorded") or [])]
    missing_html = (f'<p class="muted">Not mentioned in this report, so left blank (not the same as none): '
                    f'{_esc(", ".join(missing))}.</p>') if missing else ""
    photos_html = ('<div class="photos">' + "".join(f'<img src="{u}"/>' for u in (photos or [])) + "</div>"
                   ) if photos else ""

    body = f"""
    <div class="head">
      <img class="logo" src="{logo_data_url()}"/>
      <div class="hd-meta">
        <div class="kick">{LEGAL_NAME} &middot; Daily Site Report</div>
        <h1>{_esc(project.get('name') or 'Project')}</h1>
        <div class="sub2">{_esc(project.get('address') or '')}</div>
      </div>
      <div class="hd-date">
        <div class="d">{_fmt_date(report.get('report_date'))}</div>
        <div class="status status-{_esc(report.get('status'))}">{_esc((report.get('status') or 'draft').title())}</div>
      </div>
    </div>
    {('<div class="cond">' + ' &nbsp;·&nbsp; '.join(cond_bits) + '</div>') if cond_bits else ''}
    <section class="sec"><h2>Work Performed</h2>{_tasks_table(p.get('tasks') or [])}</section>
    {section('Manpower', manpower)}
    {section('Deliveries', deliveries)}
    {section('Site Visitors', visitors)}
    {section('Delays &amp; Blockers', delays)}
    {section('Health &amp; Safety', safety_html)}
    {section('General Notes', notes_html)}
    {section('Raised for the office', raised_html)}
    {missing_html}
    {section('Photos', photos_html)}
    """
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>{body}</body></html>'


_CSS = f"""
@page {{ size: Letter; margin: 14mm 15mm 16mm; @bottom-center {{ content: "{LEGAL_NAME} — Daily Site Report"; font-family: 'Open Sans', Arial, sans-serif; font-size: 8pt; color: #9aa3ad; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Open Sans','Helvetica Neue',Arial,sans-serif; color: #26303a; font-size: 10pt; line-height: 1.5; margin: 0; }}
h1,h2 {{ font-family: 'Sora','Helvetica Neue',Arial,sans-serif; color: {ACCENT}; }}
.head {{ display: flex; align-items: flex-start; gap: 14px; border-bottom: 2.5px solid {PRIMARY}; padding-bottom: 12px; }}
.head .logo {{ height: 34px; }}
.hd-meta {{ flex: 1; }}
.hd-meta .kick {{ font-size: 8pt; letter-spacing: 2px; text-transform: uppercase; color: #8a94a0; }}
.hd-meta h1 {{ font-size: 18pt; color: {PRIMARY}; margin: 2px 0 0; }}
.hd-meta .sub2 {{ color: #6b7580; font-size: 9.5pt; }}
.hd-date {{ text-align: right; }}
.hd-date .d {{ font-weight: 600; color: {ACCENT}; font-size: 10pt; }}
.status {{ display: inline-block; margin-top: 4px; padding: 2px 8px; font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; background: #eef2f6; color: #5b6672; }}
.status-submitted {{ background: #e7f3ea; color: #1f7a3d; }}
.status-finalized {{ background: #e6eef5; color: {PRIMARY}; }}
.cond {{ margin: 10px 0 2px; font-size: 9.5pt; color: #3f4a56; }}
.sec {{ margin-top: 14px; }}
.sec h2 {{ font-size: 12pt; border-bottom: 1.5px solid #d7dee5; padding-bottom: 4px; margin: 0 0 8px; }}
p.muted {{ color: #8a94a0; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .3px; color: #8a94a0; border-bottom: 1.2px solid #cfd6dd; padding: 4px 6px; }}
td {{ padding: 5px 6px; border-bottom: 1px solid #eef1f4; font-size: 9pt; vertical-align: top; }}
td.r, th.r {{ text-align: right; white-space: nowrap; }}
table.tasks td .sub {{ font-size: 8pt; color: #8a94a0; margin-top: 1px; }}
.crit-dot {{ display: inline-block; width: 7px; height: 7px; background: {PRIMARY}; margin-right: 6px; }}
.tag {{ display: inline-block; margin-left: 6px; font-size: 7pt; font-weight: 700; text-transform: uppercase; padding: 1px 5px; }}
.tag.ad {{ background: #eef4fb; color: {PRIMARY}; }}
.tag.rm {{ background: #fbeaea; color: #b91c1c; }}
tr.removed td {{ color: #9aa3ad; }}
.photos img {{ width: 31%; margin: 0 1.5% 8px 0; }}
.st {{ font-weight: 600; }}
.st-completed {{ color: #1f7a3d; }}
.st-delayed, .st-not_worked {{ color: #b91c1c; }}
.st-in_progress {{ color: {PRIMARY}; }}
"""
