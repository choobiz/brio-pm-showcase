"""Category-aware prompts for the document_summary AI skill.

Each prompt asks Claude to produce a single JSON object with a
deterministic shape (summary + category-specific structured fields).
The worker parses this JSON and writes it into DocumentSummary —
`summary_text` becomes the prose paragraph; `structured_fields` becomes
the JSONB blob used by the MD renderer and by downstream AI tools.

Shape per category (all share `summary` at the top level):

  drawing      → discipline, sheet_number, sheet_title, revision, scale,
                 date, drawn_by, approved_by, key_features[], rooms[]
  contract     → contract_type, parties{contractor,owner}, contract_value,
                 currency, start_date, completion_date, payment_terms,
                 liquidated_damages, key_clauses[]
  scope        → trade, version, work_categories[], notable_items[]
  report       → report_type, issue_date, author, key_findings[],
                 recommendations[]
  permit       → permit_type, permit_number, ahj, issue_date, status,
                 expiry, conditions[]
  photo        → location, before_after, visible_elements[],
                 notable_conditions[]
  client_note  → topic, action_items[]
  invoice_legacy → invoice_number, vendor, date, total, line_items[]
  other        → summary only

Token budget: ~600 output tokens for text-path summaries, ~400 for vision
(images carry less precise structured detail). System prompts are short
to leave room for the document text in the user message.
"""

from __future__ import annotations

from typing import Any

from app.services.document_summary_extractor import ExtractionContext


_SYSTEM_BASE = (
    "You are a construction-document indexer for BRIO Construction Inc. "
    "Given the document content provided, emit a SINGLE valid JSON object "
    "and nothing else (no markdown fences, no commentary). Use the exact "
    "field names specified for the category. If a field can't be "
    "determined from the document, omit it — don't invent values. The "
    "`summary` field is mandatory: one paragraph, ~30-80 words, plain "
    "language, written for a project manager skimming the project file."
)


# Category-specific schemas, expressed as a brief instruction the model
# follows. Keep these terse — the model has the BRIO context from system.
_SCHEMA_INSTRUCTIONS: dict[str, str] = {
    "drawing": (
        'Schema: {"summary": str, "discipline": "Architectural"|"Structural"'
        '|"Mechanical"|"Electrical"|"Plumbing"|"Civil"|"Fire Protection"|'
        '"Landscape"|"Other", "sheet_number": str?, "sheet_title": str?, '
        '"revision": str?, "scale": str?, "date": "YYYY-MM-DD"?, '
        '"drawn_by": str?, "approved_by": str?, '
        '"key_features": [str], "rooms": [str]}.'
    ),
    "contract": (
        'Schema: {"summary": str, "contract_type": str, '
        '"parties": {"contractor": str?, "owner": str?, "other": [str]?}, '
        '"contract_value": number?, "currency": str?, '
        '"start_date": "YYYY-MM-DD"?, "completion_date": "YYYY-MM-DD"?, '
        '"payment_terms": str?, "liquidated_damages": str?, '
        '"holdback_pct": number?, "key_clauses": [str]}.'
    ),
    "scope": (
        'Schema: {"summary": str, "trade": str?, "version": str?, '
        '"reference": str?, "work_categories": [str], '
        '"notable_items": [str], "key_assumptions": [str], '
        '"key_exclusions": [str]}.'
    ),
    "report": (
        'Schema: {"summary": str, "report_type": str, '
        '"issue_date": "YYYY-MM-DD"?, "author": str?, "subject": str?, '
        '"key_findings": [str], "recommendations": [str], '
        '"risk_level": "low"|"moderate"|"high"|"unknown"}.'
    ),
    "permit": (
        'Schema: {"summary": str, "permit_type": str, '
        '"permit_number": str?, "ahj": str?, '
        '"issue_date": "YYYY-MM-DD"?, "expiry": "YYYY-MM-DD"?, '
        '"status": "Issued"|"Pending"|"Closed"|"Unknown", '
        '"conditions": [str]}.'
    ),
    "photo": (
        'Schema: {"summary": str, "location": str?, '
        '"before_after": "before"|"after"|"in_progress"|"unknown", '
        '"visible_elements": [str], "notable_conditions": [str], '
        '"discipline_relevance": [str]}.'
    ),
    "client_note": (
        'Schema: {"summary": str, "topic": str?, '
        '"author": str?, "action_items": [str], "decisions": [str]}.'
    ),
    "invoice_legacy": (
        'Schema: {"summary": str, "invoice_number": str?, '
        '"vendor": str?, "date": "YYYY-MM-DD"?, '
        '"total": number?, "currency": str?, "line_items": [str]}.'
    ),
    "other": (
        'Schema: {"summary": str, "category_guess": str?, '
        '"key_facts": [str]}.'
    ),
}


def _category_instruction(category: str) -> str:
    return _SCHEMA_INSTRUCTIONS.get(category, _SCHEMA_INSTRUCTIONS["other"])


def build_messages_for_document_summary(
    *,
    project_context: dict[str, Any],
    document: dict[str, Any],
    extraction: ExtractionContext,
) -> tuple[str, list[dict[str, Any]], int]:
    """Return (system_prompt, messages, max_tokens) for the AI worker.

    `project_context` is shape-agnostic — anything we already know about
    the project (name, address, type, sqft, client name). It's prepended
    to the user message so the model can prefer interpretations that fit
    the live project."""

    category = document.get("category") or "other"
    filename = document.get("filename") or "untitled"
    schema_instruction = _category_instruction(category)

    project_block = (
        "Project context:\n"
        f"- Name: {project_context.get('name') or '—'}\n"
        f"- Address: {project_context.get('address') or '—'}\n"
        f"- Type: {project_context.get('type') or '—'}\n"
        f"- Client: {project_context.get('client_name') or '—'}\n"
    )

    file_block = (
        "Document:\n"
        f"- Filename: {filename}\n"
        f"- Category (heuristic): {category}\n"
        f"- MIME: {document.get('mime') or '—'}\n"
    )

    system = (
        _SYSTEM_BASE
        + " "
        + schema_instruction
    )

    user_content: list[dict[str, Any]] = []
    intro = f"{project_block}\n{file_block}\n"

    if extraction.mode == "text" and extraction.text:
        user_content.append(
            {
                "type": "text",
                "text": intro
                + "Document text follows. Produce the JSON now.\n\n----- BEGIN DOCUMENT -----\n"
                + extraction.text
                + "\n----- END DOCUMENT -----",
            }
        )
    elif extraction.mode == "image" and extraction.image_b64:
        user_content.append({"type": "text", "text": intro + "See image. Produce the JSON now."})
        user_content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": extraction.image_media_type or "image/jpeg",
                    "data": extraction.image_b64,
                },
            }
        )
    else:
        # filename_only — model gets the filename + category and is asked
        # to produce a minimal summary. Cheap, but only marginally useful.
        user_content.append(
            {
                "type": "text",
                "text": intro
                + "No document content was extractable. Produce a brief JSON "
                "with only the `summary` field, inferred from filename + "
                "category context. Don't invent specific facts.",
            }
        )

    # ⚠️ One source of truth. This used to hard-code 800/600, which silently
    # shadowed KIND_MAX_TOKENS["document_summary"] — raising that value on
    # 2026-10-01 changed nothing here, and the next re-run truncated at exactly
    # 800 again. An image-only extraction has less to summarise, so it keeps the
    # same 0.75 share of the budget it always had.
    from app.services.ai import max_tokens_for_kind

    budget = max_tokens_for_kind("document_summary")
    max_tokens = budget if extraction.mode == "text" else int(budget * 0.75)
    return system, [{"role": "user", "content": user_content}], max_tokens
