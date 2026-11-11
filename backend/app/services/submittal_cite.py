"""Open a finding's citation with the cited words boxed (wireframe C, approved
2026-10-17: "the page opens here with the label it was read from highlighted").

A citation reads like "SMP R3 · Plate 2R3, Sump #2 label" and the finding's value
like "211.00′ on Plate 2R3, ≈211.50′ on Plate 3R3". We look for the label words
and the numbers on the cited page with PyMuPDF's text search, box every hit, and
return a JPEG cropped around them so a 24 x 36 sheet stays a small image. If
nothing matches on the cited page we look through the whole file; a scan has no
words, so its page comes back unmarked and the reply says so.
"""
from __future__ import annotations

import base64
import re
from typing import Any

MAX_PX = 1600
PAD = 120  # points of context around the hits
MAX_PAGES_SCANNED = 80
_PAGE = re.compile(r"\b(?:p|pg|page)\.?\s*(\d{1,3})\b", re.I)
#: "Plate 3R3", "Sheet 2": engineers number plates in page order (1234 SMP: Plate
#: 2R3 is page 2). Used only when the citation gives no page.
_PLATE = re.compile(r"\b(?:plate|sheet|dwg)\.?\s*(\d{1,3})", re.I)
_NUM = re.compile(r"\d+\.\d+|\d{3,}")
_FILLER = {"label", "note", "notes", "detail", "sheet", "table", "callout", "the", "and", "on", "of"}


def _first(ref: str) -> str:
    """A finding can cite several places ("Shop p4 · wrap note; p14 · Pos. 48"). The
    page shown is the first one, so only its words are looked for: the "48" from
    p14 boxed three unrelated numbers on p4 in the live audit (2026-10-17)."""
    return (ref or "").split(";")[0]


def page_from(ref: str) -> int | None:
    first = _first(ref)
    m = _PAGE.search(first) or _PLATE.search(first)
    return int(m.group(1)) if m else None


def pick_doc(pool: list[dict], ref: str) -> dict:
    """The file a citation names: the one sharing the most words with it."""
    words = [w for w in re.split(r"[^a-z0-9]+", (ref or "").lower()) if len(w) > 2]

    def score(d: dict) -> int:
        name = d["filename"].lower()
        return sum(1 for w in words if w in name)

    return max(pool, key=score) if words else pool[0]


def needles(ref: str, value: str) -> list[str]:
    """Words worth finding: the label parts of the citation (not the file or page
    part), their distinctive tokens, and the numbers in the value."""
    out: list[str] = []
    parts = [p.strip() for p in re.split(r"[·,]", _first(ref)) if p.strip()]
    for i, p in enumerate(parts):
        if i == 0 and len(parts) > 1:
            continue  # "Shop p4", "SMP R3": the file and page, not a label
        p = _PAGE.sub("", p).strip()
        words = [w for w in p.split() if w.lower() not in _FILLER]
        phrase = " ".join(words)
        if len(phrase) >= 3:
            out.append(phrase)
        # Distinctive tokens only: "#2" or "48" alone box every match on a sheet.
        out += [w for w in words if re.search(r"\d|#", w) and len(w) >= 3]
    for n in _NUM.findall(value or ""):
        out.append(n)
    seen, uniq = set(), []
    for n in out:
        if n.lower() not in seen:
            seen.add(n.lower())
            uniq.append(n)
    return uniq[:12]


def _hits(page, terms: list[str]) -> list[tuple[str, Any]]:
    found = []
    for t in terms:
        for r in page.search_for(t)[:6]:
            found.append((t, r))
    return found


def render(data: bytes, mime: str, *, ref: str, value: str) -> dict[str, Any]:
    terms = needles(ref, value)
    if mime.startswith("image/"):
        return {"kind": "image", "page": None, "found": [], "searched": terms,
                "image": base64.b64encode(data).decode() if len(data) < 3_000_000 else None,
                "media_type": mime, "note": "An image has no words to find; shown as it is."}
    if "pdf" not in (mime or "").lower() and not data[:5] == b"%PDF-":
        return {"kind": "other", "page": None, "found": [], "searched": terms, "image": None,
                "media_type": mime, "note": "This file type can't be shown with highlights; open the whole file."}
    import fitz  # pymupdf

    doc = fitz.open(stream=data, filetype="pdf")
    want = page_from(ref)
    order = []
    if want and 1 <= want <= doc.page_count:
        order.append(want - 1)
    best, best_hits = (order[0] if order else 0), []
    if order:
        best_hits = _hits(doc[order[0]], terms)
    if not best_hits:
        for i in range(min(doc.page_count, MAX_PAGES_SCANNED)):
            if i in order:
                continue
            h = _hits(doc[i], terms)
            if len(h) > len(best_hits):
                best, best_hits = i, h
    page = doc[best]
    for _, r in best_hits:
        page.draw_rect(r + (-2, -2, 2, 2), color=(0.92, 0.35, 0.05), width=2)
    if best_hits:
        clip = fitz.Rect(best_hits[0][1])
        for _, r in best_hits[1:]:
            clip |= r
        clip = (clip + (-PAD, -PAD, PAD, PAD)) & page.rect
    else:
        clip = page.rect
    zoom = min(3.0, MAX_PX / max(clip.width, clip.height, 1))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    note = None
    if not best_hits:
        has_text = bool(page.get_text("text").strip())
        note = ("None of the cited words were found in this file; showing the cited page." if has_text
                else "This page's words are drawn as lines or scanned, so they can't be found; showing it unmarked.")
    elif want and best != want - 1:
        note = f"The citation says page {want}, but the words were found on page {best + 1}."
    return {"kind": "pdf", "page": best + 1, "pages": doc.page_count,
            "found": sorted({t for t, _ in best_hits}), "searched": terms,
            "image": base64.b64encode(pix.tobytes("jpeg", jpg_quality=80)).decode(), "media_type": "image/jpeg",
            "note": note}
