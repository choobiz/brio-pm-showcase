"""Inbound-email attachments → project Documents.

Postmark returns each inbound attachment as base64 inside the webhook payload.
Nothing consumed them, so a forwarded email's drawings, quotes and PDFs were
silently dropped — the Conversation appeared but its documents never arrived.

Each attachment becomes a real Document row backed by an R2 object, using the
same key layout, filename sanitising and category heuristics as a browser
upload, so it is indistinguishable from a hand-uploaded file downstream.
"""

from __future__ import annotations

import base64
import logging
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.document import Document
from app.services.r2 import put_bytes

log = logging.getLogger(__name__)

# Postmark's whole inbound payload is capped around 35 MB; keep a per-file
# ceiling so one oversized attachment can't bloat the bucket or stall the hook.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Below this, an inline image is almost certainly email furniture (logo, pixel).
# Above it, treat it as a real photo someone meant to send.
INLINE_IMAGE_MAX_BYTES = 100 * 1024


def _decode(content: Any) -> bytes | None:
    if not isinstance(content, str) or not content:
        return None
    try:
        return base64.b64decode(content, validate=False)
    except (ValueError, TypeError):
        return None


def save_inbound_attachments(
    db: Session,
    *,
    project_id: UUID,
    attachments: list[dict] | None,
    phase: str = "preconstruction",
) -> dict:
    """Persist each attachment as a Document. Returns a per-file report.

    Never raises: an attachment that fails to decode or upload is reported as
    skipped and the rest continue. The communication itself is already committed
    by the time we run, and must not be lost to an R2 hiccup.
    """
    # Imported here (not at module scope) because the helpers live in the
    # documents router — reusing them keeps categorisation identical to an
    # interactive upload instead of forking a second set of rules.
    from app.routers.documents import _mime_from_filename, _safe_filename, _suggest_category

    saved: list[dict] = []
    skipped: list[dict] = []

    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        raw_name = str(att.get("Name") or att.get("name") or "").strip()

        data = _decode(att.get("Content") or att.get("content"))
        if data is None:
            skipped.append({"name": raw_name, "reason": "undecodable or empty content"})
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            skipped.append({"name": raw_name, "reason": f"exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB cap"})
            continue

        filename = _safe_filename(raw_name or "attachment")
        # Trust the extension over the sender-declared type — same reasoning as
        # document finalize (fixes e.g. a DWG mislabelled application/pdf).
        mime = _mime_from_filename(filename) or str(att.get("ContentType") or att.get("content_type") or "") or None

        # Email furniture — signature logos, tracking pixels, spacer gifs — is
        # inline (non-empty ContentID), an image, and small. Filtering on
        # ContentID ALONE was wrong: forwarding a message frequently marks its
        # real attachments as inline too, which silently dropped genuine drawing
        # sets. Require all three signals, so a PDF or DWG is never dropped for
        # being inline and a real inline site photo survives on size.
        content_id = str(att.get("ContentID") or att.get("content_id") or "").strip()
        if content_id and (mime or "").startswith("image/") and len(data) < INLINE_IMAGE_MAX_BYTES:
            skipped.append({"name": raw_name, "reason": "inline image under 100KB (signature logo / tracking pixel)"})
            continue

        doc_id = uuid.uuid4()
        r2_key = f"projects/{project_id}/documents/{doc_id}/{filename}"
        try:
            put_bytes(r2_key, data, mime or "application/octet-stream")
        except Exception as exc:  # noqa: BLE001 — one bad upload must not drop the rest
            skipped.append({"name": raw_name, "reason": f"upload failed: {exc.__class__.__name__}"})
            continue

        db.add(
            Document(
                id=doc_id,
                project_id=project_id,
                # Categorise the RAW name, not the sanitised one. `_safe_filename`
                # rewrites spaces to underscores, and `_` is a word character, so
                # the `\b`-based heuristics never fire on "..._Elec.pdf" — passing
                # the sanitised name here made every consultant PDF land as
                # `other`. The row stores the raw name too, so this also keeps the
                # category consistent with the filename the user actually sees.
                category=_suggest_category(raw_name or filename, mime),
                phase=phase,
                filename=raw_name or filename,
                r2_key=r2_key,
                mime=mime,
                size_bytes=len(data),
                uploaded_by_id=None,  # machine ingest — no interactive actor
                # Forwarded mail can carry anything (internal pricing, trade
                # quotes). Default to office-only; a human opts it in.
                client_visible=False,
            )
        )
        saved.append({"name": raw_name or filename, "document_id": str(doc_id), "size_bytes": len(data)})

    if saved:
        db.commit()
    log.info(
        "attachments: saved %d [%s] | skipped %d [%s]",
        len(saved), ", ".join(s["name"] for s in saved),
        len(skipped), "; ".join(f"{s['name']}: {s['reason']}" for s in skipped),
    )
    return {"saved": saved, "skipped": skipped}
