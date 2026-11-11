"""Render a project note to a BRIO-branded PDF.

These notes are the documents that leave the building: a cladding takeoff goes
to a supplier, a window schedule goes out for quotation. A supplier will not be
given a login, so the note has to become a file.

The stylesheet is IMPORTED from invoice_pdf rather than copied, for the same
reason change_order_pdf imports it — two copies of a BRIO stylesheet drift.

⚠️ Images are stored in the markdown as `doc:<uuid>` references, never as URLs,
because an R2 presign dies in minutes and this content is kept for years. The
PDF therefore needs the SAME resolution step the web view does, and it must
inline the bytes: WeasyPrint fetching a presigned URL at render time would work
today and fail silently the moment the window closes.
"""

from __future__ import annotations

import base64
import html
import re
from typing import Any

from app.company import LETTERHEAD_HTML
from app.services.invoice_pdf import _CSS, _fmt_date

_DOC_REF = re.compile(r"doc:([0-9a-fA-F-]{36})")

#: WeasyPrint silently ignores flex `gap`, so every rule here uses margin.
_NOTE_CSS = """
.note-title { font-size: 17pt; font-weight: 700; color: #1D3146; margin: 0 0 2px 0; }
.note-meta  { font-size: 8pt; color: #6B7280; margin: 0 0 14px 0; }
.note-body h2 { font-size: 12pt; color: #1D3146; margin: 16px 0 4px 0;
                border-bottom: 1px solid #E5E7EB; padding-bottom: 3px; }
.note-body h3 { font-size: 10pt; color: #1D3146; margin: 12px 0 3px 0; }
.note-body p  { font-size: 9pt; line-height: 1.45; margin: 0 0 7px 0; }
.note-body table { width: 100%; border-collapse: collapse; margin: 6px 0 10px 0;
                   font-size: 8.5pt; }
.note-body th { text-align: left; background: #F5F5F5; color: #1D3146;
                font-weight: 600; padding: 4px 6px; border-bottom: 1px solid #D1D5DB; }
.note-body td { padding: 4px 6px; border-bottom: 1px solid #EFEFEF;
                vertical-align: top; }
.note-body img { max-width: 100%; margin: 8px 0; }
.note-body blockquote { border-left: 3px solid #0068A0; margin: 8px 0;
                        padding: 2px 0 2px 10px; color: #374151; }
.note-body code { font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; }
"""


def resolve_images(content_md: str, images: dict[str, bytes]) -> str:
    """Swap every `doc:<uuid>` for an inlined data URI.

    A reference with no supplied bytes is left as plain text rather than
    rendered as a broken image, so a missing asset reads as a missing asset.
    """
    def _sub(m: re.Match) -> str:
        data = images.get(m.group(1).lower())
        if not data:
            return "doc:MISSING"
        return "data:image/png;base64," + base64.b64encode(data).decode()

    return _DOC_REF.sub(_sub, content_md)


def _markdown_to_html(md: str) -> str:
    """Markdown to HTML via the same renderer the rest of the app uses."""
    import markdown as _md

    return _md.markdown(md, extensions=["tables", "nl2br", "sane_lists"])


def render_narrative_pdf(note: dict[str, Any], project: dict[str, Any],
                         images: dict[str, bytes] | None = None) -> bytes:
    """`note` carries title, content_md, version_number and created_at."""
    from weasyprint import HTML

    body = _markdown_to_html(resolve_images(note.get("content_md") or "",
                                            images or {}))
    title = html.escape(note.get("title") or "Project note")
    proj = html.escape(project.get("name") or "")
    meta = (f"{proj} &middot; version {note.get('version_number')} &middot; "
            f"{_fmt_date(note.get('created_at'))}")

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}{_NOTE_CSS}</style></head><body>
{LETTERHEAD_HTML}
<div class="note-title">{title}</div>
<div class="note-meta">{meta}</div>
<div class="note-body">{body}</div>
</body></html>"""
    return HTML(string=doc).write_pdf()
