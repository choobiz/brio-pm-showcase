"""Stamping a quantity with WHERE it was read — on the server, from the record.

⚠️ THE RULE. Provenance the caller asserts is not provenance. `OverrideWrite`
accepts `source_revision_label`, and taking it from the body would let a client
record any revision it liked against a drawing — a number that looks checkable
and is not. The label is therefore read from `documents.revision_label` here and
the submitted value is discarded.

That column is only populated once someone confirms a project's drawing
register, so the honest outcome for an unconfirmed project is NULL. Null means
"no revision was recorded for this sheet", which `provenance.py` already renders
as "not recorded". It must never be filled with a guess.

⚠️ BBOX IS PDF ONLY, and production says why this matters. All 50 quantities on
record are `cad_extract` and every one of their source documents is .dwg/.dxf or
has no mime at all — not a single PDF. DXF model space has no relationship to a
printed sheet without a known viewport, scale and origin, so a bbox against one
would be a coordinate in a space the viewer cannot draw. Rejected rather than
stored, because a stored-but-meaningless box is worse than an absent one: the
interface would offer to show you a region that is not there.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Mimes a page-and-box locator can actually describe.
PDF_MIMES = ("application/pdf",)


class SourceRejected(ValueError):
    """The submitted locator cannot describe the document it points at."""


def _valid_bbox(bbox: Any) -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise SourceRejected("A region must be four numbers: [x0, y0, x1, y1].")
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise SourceRejected("A region must be four numbers: [x0, y0, x1, y1].")
    if not all(math.isfinite(v) for v in vals):
        raise SourceRejected("A region cannot contain infinite or missing values.")
    x0, y0, x1, y1 = vals
    # Zero-area is rejected too. A box with no extent points at a spot but
    # describes no region, and the viewer would draw nothing where the reader
    # was promised a highlight.
    if x1 <= x0 or y1 <= y0:
        raise SourceRejected(
            "A region must have width and height, with x0 < x1 and y0 < y1."
        )
    return vals


def stamp(db: Session, payload: dict[str, Any], project_id: UUID) -> dict[str, Any]:
    """Return `payload` with its drawing provenance resolved from the record.

    Mutates nothing the caller owns; returns a new dict. Raises SourceRejected
    with a sentence meant for a person when the locator cannot be honoured.
    """
    out = dict(payload)
    doc_id = out.get("source_document_id")
    bbox = out.pop("source_bbox", None)
    # Never trusted. Re-derived below, or left null.
    out.pop("source_revision_label", None)
    out["source_revision_label"] = None
    out["source_bbox"] = None

    if doc_id is None:
        if bbox is not None:
            raise SourceRejected(
                "A region on a drawing needs the drawing it was read from."
            )
        return out

    row = db.execute(
        _T("""SELECT project_id, mime, revision_label, filename
                FROM documents WHERE id = :d"""),
        {"d": str(doc_id)},
    ).first()
    if row is None:
        raise SourceRejected("That drawing is no longer on file.")

    # Project match is enforced rather than assumed. Verified against production
    # before adding: 0 of 50 rows point at a document in another project, so this
    # rejects nothing that exists — it stops a quantity being attributed to a
    # drawing from a different job, which would be invisible once stored.
    if str(row.project_id) != str(project_id):
        raise SourceRejected(
            "That drawing belongs to a different project."
        )

    # The label is whatever the register recorded, including nothing at all.
    out["source_revision_label"] = row.revision_label

    if bbox is not None:
        if (row.mime or "") not in PDF_MIMES:
            raise SourceRejected(
                f"A region can only be recorded against a PDF drawing. "
                f"{row.filename!r} is {row.mime or 'of unknown type'}, and a "
                f"position on it cannot be drawn back onto a sheet."
            )
        if out.get("source_page") is None:
            raise SourceRejected("A region needs the page it was read from.")
        out["source_bbox"] = _valid_bbox(bbox)

    return out
