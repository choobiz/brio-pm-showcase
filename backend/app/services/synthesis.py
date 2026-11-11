"""Reading a set of documents against each other.

THE QUESTION THIS ANSWERS: "what is missing, and what contradicts what?" across
a selected set of documents. §02 rates it a strong win for one reason — it has
no home in any existing UI. A viewer shows one sheet at a time; a document list
shows filenames; nothing reads twenty PDFs against each other and says the
mechanical drawings reference a ceiling detail that is not in the set.

⚠️ WHAT THIS MUST NEVER DO, and the whole reason the prompt is this strict.

A synthesis is read by someone deciding whether to chase a consultant for a
missing drawing. A confident invented gap wastes a phone call; a confident
invented CONTRADICTION ("the structural plan says 8-inch anchors, the detail
says 6-inch") sends someone to the wrong argument with a consultant, and that is
worse than silence. So:

  · Every finding must NAME the documents it came from. A finding that cannot
    name its sources is not reported.
  · "Not in this set" is the honest phrasing for an absence, never "missing" —
    the office may hold the file somewhere this selection did not include.
  · Where the documents do not settle something, say so. Do not resolve it.
  · No quantities, no prices, no scope. Those surfaces have their own gates and
    this one is read-only prose.

This is consistent with Correction 4 elsewhere in the product: unknown is
reported as unknown and never rendered as zero or none.
"""

from __future__ import annotations

from typing import Any

#: Kept below the estimate prompts' ceiling. A synthesis is prose about a
#: document set, not a line-item breakdown; 4k gives room for ~15 findings with
#: their sources and stops a run from rambling into a document of its own.
MAX_TOKENS = 4096

SYSTEM = """You are reading a set of construction documents for a BC residential \
and commercial builder, to answer one question: what is missing from this set, \
and what in it contradicts something else in it?

You are NOT writing a scope, an estimate, or a quantity takeoff. Those are \
produced elsewhere, under review gates, and nothing you write here feeds them.

HOW TO REPORT A FINDING

Every finding names the documents it came from, by filename. A finding you \
cannot source is not a finding — drop it. Prefer five findings you can point at \
over fifteen you cannot.

Use these three kinds and label each finding with one:

  NOT IN THIS SET — something the documents themselves refer to, or that this \
kind of package normally carries, which is not among the files provided. Phrase \
it as "not in this set", NEVER as "missing": the office may hold the file and \
simply not have included it here. Say what referred to it.

  DISAGREEMENT — two documents in the set that state different things about the \
same subject. Quote or closely paraphrase BOTH sides and name both files. If you \
cannot show both sides, it is not a disagreement; it is a thing you are unsure \
about, and it belongs under UNRESOLVED.

  UNRESOLVED — something the set raises but does not settle. State what would \
settle it. Do not settle it yourself.

RULES THAT OVERRIDE EVERYTHING ABOVE

1. Never state a dimension, quantity, specification or price as fact unless it \
is written in one of these documents, and name that document when you do.
2. If the set is too small or too one-sided to answer, say exactly that. "These \
four photos do not establish what is missing from a drawing set" is a correct \
and useful answer.
3. Do not speculate about what a drawing "probably" shows. You either read it or \
you did not.
4. Where a document is illegible, scanned, or you could not read it, say which \
one and treat it as unread rather than empty.
5. Do not recommend commercial action. You report what the documents say; what \
to do about it is the reader's call.

FORMAT

Short markdown. A one-paragraph summary of what the set is and what state it is \
in, then the findings grouped under the three labels above. Omit a group \
entirely if it has nothing in it rather than writing "none found" three times. \
Close with one line naming how many files you actually read, and any you could \
not."""


def _caption_list(blocks: list[dict[str, Any]], label: str) -> list[str]:
    out = []
    for b in blocks or []:
        name = (b.get("caption") or "").strip() or "(unnamed file)"
        out.append(f"- {name} ({label})")
    return out


def build_synthesis_messages(
    input_payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """(system, messages, max_tokens) for a document_synthesis job.

    Takes the same materialised payload `_merge_scope_inputs` produces, so the
    document-loading path, its project-isolation check and its text extraction
    are shared with scope analysis rather than reimplemented.
    """
    payload = input_payload or {}
    meta = payload.get("project_metadata") or {}
    pdfs = payload.get("documents") or []
    photos = payload.get("images") or []
    all_texts = payload.get("text_documents") or []
    # ⚠️ A file that extracted to nothing must NOT be counted as provided. The
    # manifest used to list it and the content block used to skip it, so the
    # model was told about a document it was never given — which is the exact
    # silent drop the manifest exists to surface. Split, count only what is
    # attached, and name the rest as unread.
    texts = [t for t in all_texts if (t.get("text") or "").strip()]
    unreadable = [t for t in all_texts if not (t.get("text") or "").strip()]
    question = (payload.get("question") or "").strip()

    lines: list[str] = []
    if meta.get("name"):
        lines.append(f"Project: {meta['name']}")
    if meta.get("address"):
        lines.append(f"Address: {meta['address']}")
    if meta.get("project_type"):
        lines.append(f"Project type: {meta['project_type']}")
    if meta.get("building_age"):
        lines.append(f"Year built: {meta['building_age']}")
    if not lines:
        lines.append("No project metadata was recorded for this project.")

    # The manifest is listed in the prompt as well as attached, so the model can
    # say "I read 9 of 11" and the reader can check that against what they
    # selected. Without it a silently dropped file is invisible.
    manifest: list[str] = []
    manifest += _caption_list(pdfs, "PDF")
    manifest += _caption_list(photos, "photo")
    manifest += _caption_list(texts, "extracted text")
    total = len(pdfs) + len(photos) + len(texts)

    framing = [
        "\n".join(lines),
        f"\nFILES PROVIDED ({total}):\n" + ("\n".join(manifest) if manifest else "- none"),
    ]

    if unreadable:
        names = ", ".join((t.get("caption") or "(unnamed)") for t in unreadable)
        framing.append(
            f"\nSELECTED BUT UNREADABLE ({len(unreadable)}): {names}\n"
            "These were chosen but no text could be extracted from them, so you "
            "have NOT been given their contents. Treat them as unread — say so "
            "in your closing line, and never infer what they contain."
        )

    if total == 0:
        # Refusing here rather than letting the model invent a document set.
        framing.append(
            "\nNo files were provided. Say that no documents were supplied and "
            "that nothing can be concluded about the set. Do not describe a "
            "typical package."
        )
    elif question:
        framing.append(
            f"\nThe reader asked specifically:\n{question}\n\n"
            "Answer that question against these documents. If the documents do "
            "not answer it, say so plainly and say what would."
        )
    else:
        framing.append(
            "\nNo specific question was asked. Report what is not in this set, "
            "what disagrees, and what is unresolved."
        )

    content: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(framing)}]

    for d in pdfs:
        content.append({
            "type": "document",
            "source": {"type": "base64",
                       "media_type": d.get("media_type") or "application/pdf",
                       "data": d.get("data")},
        })
    for im in photos:
        content.append({
            "type": "image",
            "source": {"type": "base64",
                       "media_type": im.get("media_type") or "image/jpeg",
                       "data": im.get("data")},
        })
    for t in texts:
        body = (t.get("text") or "").strip()
        content.append({
            "type": "text",
            "text": f"--- {t.get('caption') or 'document'} (extracted text) ---\n{body}",
        })

    return SYSTEM, [{"role": "user", "content": content}], MAX_TOKENS
