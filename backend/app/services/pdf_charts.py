"""Server-side SVG charts for the BRIO presentation PDF.

WeasyPrint renders inline SVG (circles/rects/paths/text/lines), so charts are
generated as SVG strings from real workbench data — no JS, no external images.
  - donut_svg + donut_legend : budget "where the money goes" snapshot
  - hbar_svg                  : budget category comparison
  - gantt_svg                 : schedule (critical-path bars + milestone diamonds)
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Sequence

PRIMARY = "#0068A0"
ACCENT = "#1D3146"
# restrained BRIO-derived palette (primary/accent + tints) for chart segments
PALETTE = ["#0068A0", "#1D3146", "#4A90C2", "#2E5E7E", "#7FB2D6",
           "#375A70", "#A9CBE3", "#5C7A90", "#C9DDEB"]


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _parse(d) -> date | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(str(d)[:10]).date()
    except (ValueError, TypeError):
        return None


def top_segments(rows: Sequence[tuple[str, float]], keep: int = 6) -> list[tuple[str, float]]:
    """Largest `keep` categories; the rest folded into 'Other'."""
    clean = [(n, float(v or 0)) for n, v in rows if float(v or 0) > 0]
    clean.sort(key=lambda r: r[1], reverse=True)
    if len(clean) <= keep:
        return clean
    head, tail = clean[:keep], clean[keep:]
    return head + [("Other", sum(v for _, v in tail))]


def donut_svg(segments: Sequence[tuple[str, float]], size: int = 168, thickness: int = 32) -> str:
    total = sum(max(0.0, v) for _, v in segments) or 1.0
    cx = cy = size / 2
    r = (size - thickness) / 2
    circ = 2 * math.pi * r
    out = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">']
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#eef1f4" stroke-width="{thickness}"/>')
    offset = 0.0
    for i, (_, value) in enumerate(segments):
        dash = (max(0.0, value) / total) * circ
        out.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{PALETTE[i % len(PALETTE)]}" '
            f'stroke-width="{thickness}" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        offset += dash
    out.append(f'<text x="{cx}" y="{cy - 1}" text-anchor="middle" font-family="Sora,Arial" '
               f'font-size="15" font-weight="700" fill="{ACCENT}">{_money(total)}</text>')
    out.append(f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-family="Open Sans,Arial" '
               f'font-size="7.5" letter-spacing="1" fill="#8a94a0">SUBTOTAL</text>')
    out.append("</svg>")
    return "".join(out)


def donut_legend(segments: Sequence[tuple[str, float]]) -> str:
    total = sum(max(0.0, v) for _, v in segments) or 1.0
    maxv = max((max(0.0, v) for _, v in segments), default=1.0) or 1.0
    rows = "".join(
        f'<div class="lg"><span class="sw" style="background:{PALETTE[i % len(PALETTE)]}"></span>'
        f'<span class="ln">{name}</span>'
        f'<span class="lbar"><span class="lbarf" style="width:{round(100 * max(0.0, value) / maxv)}%;background:{PALETTE[i % len(PALETTE)]}"></span></span>'
        f'<span class="lv">{_money(value)} &middot; {round(100 * max(0.0, value) / total)}%</span></div>'
        for i, (name, value) in enumerate(segments)
    )
    return f'<div class="legend">{rows}</div>'


def hbar_svg(rows: Sequence[tuple[str, float]], width: int = 250, bar_h: int = 15, gap: int = 8) -> str:
    data = [(n, float(v or 0)) for n, v in rows]
    maxv = max((v for _, v in data), default=1.0) or 1.0
    h = len(data) * (bar_h + gap) + gap
    out = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" xmlns="http://www.w3.org/2000/svg">']
    y = gap
    for i, (_, value) in enumerate(data):
        w = max(2.0, (max(0.0, value) / maxv) * (width * 0.68))
        out.append(f'<rect x="0" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{PALETTE[i % len(PALETTE)]}"/>')
        out.append(f'<text x="{w + 6:.1f}" y="{y + bar_h - 3}" font-family="Open Sans,Arial" '
                   f'font-size="8.5" fill="#5b6672">{_money(value)}</text>')
        y += bar_h + gap
    out.append("</svg>")
    return "".join(out)


def gantt_svg(start_iso, finish_iso, bars: Sequence[tuple[str, str, str]],
              milestones: Sequence[tuple[str, str]], width: int = 540) -> str:
    """bars: (name, start_iso, finish_iso); milestones: (name, date_iso). Returns "" if no dates."""
    all_dates = [d for d in (
        [_parse(start_iso), _parse(finish_iso)]
        + [_parse(b[1]) for b in bars] + [_parse(b[2]) for b in bars]
        + [_parse(m[1]) for m in milestones]) if d]
    if not all_dates:
        return ""
    lo, hi = min(all_dates), max(all_dates)
    span = max((hi - lo).days, 1)
    left, pad_r = 132, 16
    plot_w = width - left - pad_r

    def x(d: date) -> float:
        return left + ((d - lo).days / span) * plot_w

    row_h, gap, top = 12, 3, 24
    bar_rows = [b for b in bars if _parse(b[1])]
    h = top + max(1, len(bar_rows)) * (row_h + gap) + (34 if milestones else 12)
    out = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Open Sans,Arial">']
    # month gridlines
    cur = date(lo.year, lo.month, 1)
    while cur <= hi:
        gx = x(max(cur, lo))
        out.append(f'<line x1="{gx:.1f}" y1="{top - 4}" x2="{gx:.1f}" y2="{h - (28 if milestones else 8)}" stroke="#eef1f4"/>')
        out.append(f'<text x="{gx + 2:.1f}" y="{top - 8}" font-size="7.5" fill="#9aa3ad">{cur.strftime("%b %Y")}</text>')
        cur = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
    # activity bars — critical path in primary, everything else in a lighter tint
    y = top
    for bar in bar_rows:
        name, s_iso, f_iso = bar[0], bar[1], bar[2]
        is_crit = bar[3] if len(bar) > 3 else True
        s = _parse(s_iso)
        f = _parse(f_iso) or s
        bx, bw = x(s), max(3.0, x(f) - x(s))
        out.append(f'<text x="0" y="{y + row_h - 4}" font-size="7.5" fill="#3f4a56">{(name or "")[:26]}</text>')
        out.append(f'<rect x="{bx:.1f}" y="{y}" width="{bw:.1f}" height="{row_h - 4}" fill="{PRIMARY if is_crit else "#A9CBE3"}"/>')
        y += row_h + gap
    # milestone diamonds
    if milestones:
        my = h - 18
        out.append(f'<line x1="{left}" y1="{my}" x2="{width - pad_r}" y2="{my}" stroke="#dfe4ea"/>')
        for name, d_iso in milestones:
            d = _parse(d_iso)
            if not d:
                continue
            mx = x(d)
            out.append(f'<path d="M{mx:.1f} {my - 5} L{mx + 5:.1f} {my} L{mx:.1f} {my + 5} L{mx - 5:.1f} {my} Z" fill="{ACCENT}"/>')
    out.append("</svg>")
    return "".join(out)
