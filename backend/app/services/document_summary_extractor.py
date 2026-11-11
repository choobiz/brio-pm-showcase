"""Extract text + first-page image previews from arbitrary uploaded
documents for the project-index pipeline.

Goal of this module: given a Document row + its bytes, return a
normalized "extraction context" the AI worker can wrap into a prompt.
We never call out to AI here — that lives in the worker. This is just
the text + image preparation, fast and deterministic.

Supported file types (P1):
- PDFs: PyMuPDF text. If text is sparse (likely a scanned drawing),
  render the first page as a JPEG for the vision path instead.
- Images (jpg / png / heic via Pillow if available): downscale to a
  ~1024px JPEG, return as base64 for vision.
- Excel (.xlsx / .xls): openpyxl text dump, sheet by sheet.
- Word (.docx) + everything else: filename-only fallback (no AI call;
  the summary is built from category + filename heuristics).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

# How much text to send to the model. Most contracts / scopes / reports
# are dense enough that 12K chars (~3K tokens) captures the key terms.
_MAX_TEXT_CHARS = 12_000

# Text below this length on a "PDF" is a strong signal that we have a
# raster-scanned drawing or a redacted image — fall back to vision.
_TEXT_VS_IMAGE_THRESHOLD = 200

# Render preview at this DPI for the vision fallback. Higher = more
# legible title-block, more tokens, more $. 110 is enough to read sheet
# numbers / discipline tags / revision marks at letter size.
_PDF_PREVIEW_DPI = 110

# Vision images downscale to this max edge so the b64 payload stays
# bounded. 1024 keeps drawing detail without blowing the cost budget.
_IMAGE_MAX_EDGE = 1024


@dataclass
class ExtractionContext:
    """Normalized payload handed to the AI worker. Exactly one of `text`
    or `image_b64` is the primary signal; the other is empty / None."""

    mode: str  # "text" | "image" | "filename_only"
    text: str = ""
    image_b64: str | None = None
    image_media_type: str | None = None  # e.g. "image/jpeg"
    notes: list[str] = None  # type: ignore  # extractor diagnostics

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


# ── PDFs ─────────────────────────────────────────────────────────────


def _extract_pdf_text(data: bytes, max_pages: int = 30) -> str:
    import fitz  # PyMuPDF (lazy — heavy native dep)

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages = []
        for i in range(min(len(doc), max_pages)):
            pages.append(doc[i].get_text("text"))
        return "\n\n".join(p.strip() for p in pages if p.strip())
    finally:
        doc.close()


def _render_pdf_first_page_image(data: bytes) -> tuple[str, str]:
    """Return (base64_jpeg, mime). Used as vision fallback for sparse-text
    PDFs (scanned drawings / image-heavy sheets)."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if len(doc) == 0:
            return "", "image/jpeg"
        mat = fitz.Matrix(_PDF_PREVIEW_DPI / 72, _PDF_PREVIEW_DPI / 72)
        pix = doc[0].get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        return _normalize_image_to_b64(png_bytes, "image/png")
    finally:
        doc.close()


# ── Images ────────────────────────────────────────────────────────────


def _normalize_image_to_b64(
    data: bytes, source_mime: str
) -> tuple[str, str]:
    """Downscale to <= _IMAGE_MAX_EDGE on the longest edge, re-encode to
    JPEG, return (b64, mime). PIL handles common formats inc. HEIC if
    pillow-heif is present (it isn't in v1 — HEIC photos fall through
    the resampling and may fail; logged as a `notes` entry by the caller)."""
    from PIL import Image  # Pillow ships via weasyprint

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _IMAGE_MAX_EDGE:
        scale = _IMAGE_MAX_EDGE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


# ── Excel ─────────────────────────────────────────────────────────────


def _extract_excel_text(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts: list[str] = []
    total = 0
    for ws in wb.worksheets:
        parts.append(f"\n# Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            vals = [str(v) if v is not None else "" for v in row]
            if not any(v for v in vals):
                continue
            line = "\t".join(vals)
            parts.append(line)
            total += len(line)
            if total > _MAX_TEXT_CHARS:
                parts.append("\n[... truncated ...]")
                return "\n".join(parts)
        if total > _MAX_TEXT_CHARS:
            break
    return "\n".join(parts)


# ── Dispatcher ────────────────────────────────────────────────────────


def extract_for_summary(
    *,
    filename: str,
    mime: str | None,
    data: bytes,
) -> ExtractionContext:
    """Pick the right extractor for the file. Designed to never throw —
    on any failure we degrade to `filename_only` and let the caller fall
    back to a heuristic summary."""

    mime = (mime or "").lower()
    lower_name = filename.lower()
    ctx = ExtractionContext(mode="filename_only")

    try:
        # PDFs
        if mime == "application/pdf" or lower_name.endswith(".pdf"):
            try:
                text = _extract_pdf_text(data)
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(f"pdf text extract failed: {e}")
                text = ""
            if len(text) >= _TEXT_VS_IMAGE_THRESHOLD:
                ctx.mode = "text"
                ctx.text = text[:_MAX_TEXT_CHARS]
                if len(text) > _MAX_TEXT_CHARS:
                    ctx.notes.append(
                        f"pdf text truncated from {len(text)} to {_MAX_TEXT_CHARS} chars"
                    )
                return ctx
            # Sparse text → likely a scanned/drawing PDF; vision fallback.
            try:
                b64, mt = _render_pdf_first_page_image(data)
                if b64:
                    ctx.mode = "image"
                    ctx.image_b64 = b64
                    ctx.image_media_type = mt
                    ctx.notes.append(
                        f"pdf sparse text ({len(text)} chars) → vision on first page"
                    )
                    return ctx
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(f"pdf image render failed: {e}")
            return ctx  # filename_only

        # Images
        if mime.startswith("image/") or lower_name.endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")
        ):
            try:
                b64, mt = _normalize_image_to_b64(data, mime or "image/jpeg")
                ctx.mode = "image"
                ctx.image_b64 = b64
                ctx.image_media_type = mt
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(f"image normalize failed: {e}")
            return ctx

        # Excel
        if lower_name.endswith((".xlsx", ".xlsm")) or mime in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        ):
            try:
                text = _extract_excel_text(data)
                if text.strip():
                    ctx.mode = "text"
                    ctx.text = text[:_MAX_TEXT_CHARS]
                    return ctx
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(f"excel extract failed: {e}")
            return ctx

        # Plain text-y formats
        if mime.startswith("text/") or lower_name.endswith(
            (".txt", ".md", ".csv", ".json", ".log")
        ):
            try:
                text = data.decode("utf-8", errors="replace")
                if text.strip():
                    ctx.mode = "text"
                    ctx.text = text[:_MAX_TEXT_CHARS]
                    return ctx
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(f"text decode failed: {e}")
            return ctx

    except Exception as e:  # noqa: BLE001 — never raise from extraction
        ctx.notes.append(f"unexpected extraction error: {e}")

    return ctx
