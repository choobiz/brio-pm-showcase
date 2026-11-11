"""BRIO-branded PDF for a prepared document (order, rental request, takeoff,
field sheet). Matches the three 1234 forming documents Amir approved on
2026-10-13: title, company line, a details table, then sections.

The `basis` and `source` of each line are the office's working, not the
supplier's business, so they are never printed. No rounded corners (BRIO PDFs),
and margins rather than flex `gap` (WeasyPrint ignores gap).
"""
from __future__ import annotations

from html import escape
from typing import Any

from app.company import COMPANY

CSS = """
@page { size: letter; margin: 0.6in 0.65in; }
* { box-sizing: border-box; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1e293b; line-height: 1.35; }
h1 { color: #1f4e79; font-size: 20pt; margin: 0 0 4pt 0; }
.co { font-size: 8.5pt; color: #334155; margin: 0 0 14pt 0; }
.co b { color: #0f172a; }
h2 { color: #1f4e79; font-size: 12pt; margin: 16pt 0 6pt 0; padding-bottom: 3pt; border-bottom: 1px solid #cbd5e1; }
table { width: 100%; border-collapse: collapse; margin: 0 0 6pt 0; }
th, td { border: 1px solid #cbd5e1; padding: 5pt 7pt; text-align: left; vertical-align: top; }
th { background: #eef2f7; font-size: 9pt; }
table.kv td.k { width: 26%; background: #f3f5f8; font-weight: bold; }
td.num { text-align: right; white-space: nowrap; }
td.idx { width: 4%; }
.text p { margin: 0 0 6pt 0; }
.text ul { margin: 0 0 6pt 0; padding-left: 14pt; }
.note { margin-top: 12pt; padding: 8pt 10pt; background: #f4f6f9; border-left: 4px solid #1f4e79; }
"""


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return escape(str(v))


def _text(md: str) -> str:
    """Plain paragraphs and "- " bullets; **bold** kept. Nothing else."""
    import re

    out, items = [], []

    def flush() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in (md or "").splitlines():
        line = escape(raw.strip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        if not line:
            flush()
        elif line.startswith(("- ", "• ")):
            items.append(line[2:])
        else:
            flush()
            out.append(f"<p>{line}</p>")
    flush()
    return "".join(out)


def build_html(title: str, payload: dict[str, Any]) -> str:
    parts = [f"<h1>{escape(title)}</h1>",
             f'<p class="co"><b>{escape(COMPANY["legal_name"])}</b> · {escape(COMPANY["address"])} · '
             f'{escape(COMPANY["phone"])} · {escape(COMPANY["email"])}</p>']
    header = [h for h in payload.get("header") or [] if str(h.get("value") or "").strip()]
    if header:
        parts.append('<table class="kv">' + "".join(
            f'<tr><td class="k">{_cell(h.get("label"))}</td><td>{_cell(h.get("value"))}</td></tr>'
            for h in header) + "</table>")
    for s in payload.get("sections") or []:
        parts.append(f"<h2>{escape(str(s.get('heading') or ''))}</h2>")
        kind = s.get("kind") or "table"
        rows = s.get("rows") or []
        if kind == "pairs":
            parts.append("<table><tr><th>{}</th><th>Detail</th></tr>".format(
                escape(str(s.get("label_heading") or "Item"))) + "".join(
                f"<tr><td>{_cell(r.get('label'))}</td><td>{_cell(r.get('value'))}</td></tr>" for r in rows)
                + "</table>")
        elif kind == "table":
            parts.append("<table><tr><th>#</th><th>Item</th><th>Specification</th><th>Qty</th><th>Unit</th></tr>"
                         + "".join(
                             f'<tr><td class="idx">{i}</td><td>{_cell(r.get("item"))}</td><td>{_cell(r.get("spec"))}</td>'
                             f'<td class="num">{_cell(r.get("qty"))}</td><td>{_cell(r.get("unit"))}</td></tr>'
                             for i, r in enumerate(rows, 1)) + "</table>")
        if s.get("text"):
            parts.append(f'<div class="text">{_text(s["text"])}</div>')
    if payload.get("notes"):
        parts.append(f'<div class="note">{_text(payload["notes"])}</div>')
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"


def render(title: str, payload: dict[str, Any]) -> bytes:
    from weasyprint import HTML

    return HTML(string=build_html(title, payload)).write_pdf()
