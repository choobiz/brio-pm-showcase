"""Where each of a project's documents is used — the "Used in" column.

Read from the rows that actually point at a document, never inferred from its
category: a drawing nobody has analysed is used in nothing, and a photo
embedded in a project note is used in Notes whatever it is filed under.

Places, in the order the page lists them:
  Takeoff  — a drawing analysis, a measurement taken from it, a page scale,
             a takeoff pack, or ticked "use for quantities"
  Scope    — ticked "use for scope"
  Estimate — an estimate line linked to the drawing
  Costs    — a project cost recorded from it (a receipt, an invoice)
  Notes    — embedded in a project note as `doc:<id>`
"""
from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

PLACES = ("Takeoff", "Scope", "Estimate", "Costs", "Notes")

_DOC_REF = re.compile(r"doc:([0-9a-fA-F-]{36})")


def used_in(db: Session, project_id: UUID) -> dict[str, list[str]]:
    """{document_id: [place, ...]} for every document of the project used anywhere.

    A document used nowhere is absent rather than mapped to [], so the caller
    can tell "checked, unused" from "not in this project" without a second list.
    """
    found: dict[str, set[str]] = {}

    def add(rows, place: str) -> None:
        for (doc_id,) in rows:
            if doc_id is not None:
                found.setdefault(str(doc_id), set()).add(place)

    pid = {"pid": project_id}
    add(db.execute(text(
        "SELECT a.document_id FROM drawing_analyses a "
        "JOIN documents d ON d.id = a.document_id WHERE d.project_id = :pid"), pid), "Takeoff")
    add(db.execute(text(
        "SELECT DISTINCT source_document_id FROM quantity_overrides "
        "WHERE project_id = :pid AND source_document_id IS NOT NULL"), pid), "Takeoff")
    add(db.execute(text(
        "SELECT DISTINCT o.document_id FROM page_scale_overrides o "
        "JOIN documents d ON d.id = o.document_id WHERE d.project_id = :pid"), pid), "Takeoff")
    add(db.execute(text(
        "SELECT DISTINCT jsonb_array_elements_text(source_document_ids) "
        "FROM drawing_analysis_packs WHERE project_id = :pid "
        "AND jsonb_typeof(source_document_ids) = 'array'"), pid), "Takeoff")
    add(db.execute(text(
        "SELECT document_id FROM document_selections "
        "WHERE project_id = :pid AND use_for_quantities"), pid), "Takeoff")
    add(db.execute(text(
        "SELECT document_id FROM document_selections "
        "WHERE project_id = :pid AND use_for_scope"), pid), "Scope")
    # Not mapped to a model, so it may be absent from a schema built without
    # the migrations (the test database). Asked about before it is read.
    if db.execute(text("SELECT to_regclass('drawing_line_item_links')")).scalar():
        add(db.execute(text(
            "SELECT DISTINCT l.document_id FROM drawing_line_item_links l "
            "JOIN documents d ON d.id = l.document_id WHERE d.project_id = :pid"), pid),
            "Estimate")
    add(db.execute(text(
        "SELECT DISTINCT source_document_id FROM project_costs "
        "WHERE project_id = :pid AND source_document_id IS NOT NULL"), pid), "Costs")
    for (body,) in db.execute(text(
            "SELECT content_md FROM narratives "
            "WHERE project_id = :pid AND content_md LIKE '%doc:%'"), pid):
        add(((m.lower(),) for m in _DOC_REF.findall(body or "")), "Notes")

    order = {p: i for i, p in enumerate(PLACES)}
    return {d: sorted(places, key=order.__getitem__) for d, places in found.items()}
