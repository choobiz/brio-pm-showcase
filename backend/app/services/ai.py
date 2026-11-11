"""Anthropic client + cost tracking helpers for the AI worker.

Models (moved from Opus 4.7 / Sonnet 4.6 to the 5.5 models on 2026-10-15,
Amir's approval; Ask and Prepare were already on 5.5):
- claude-opus-5-5    — vision-capable, used for scope_analysis and ai_edit
- claude-sonnet-5-5  — used for estimate_generation + email_draft
- claude-haiku-4-5   — used for ocr / cheap utility calls (not moved)

Pricing rate cards (USD per million tokens). The 5.5 rates match
`prep_spend.PRICES` (docs.claude.com pricing, fetched 2026-10-13). The 4.x
rates stay so a cost recorded under an old model can still be recomputed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

from anthropic import Anthropic

from app.config import settings


MODEL_OPUS = "claude-opus-5-5"
MODEL_SONNET = "claude-sonnet-5-5"
MODEL_HAIKU = "claude-haiku-4-5"


# Per-million-token pricing in USD. Stored as Decimal to keep cost math exact.
# Sources:
#   Sonnet 4.6 / Haiku 4.5: https://platform.claude.com/docs/en/about-claude/pricing
#   Opus 4.7 (standard Opus tier): https://docs.anthropic.com/en/docs/about-claude/models
RATE_CARD: dict[str, dict[str, Decimal]] = {
    MODEL_OPUS:   {"input": Decimal("4.00"),  "output": Decimal("20.00")},
    MODEL_SONNET: {"input": Decimal("2.00"),  "output": Decimal("10.00")},
    MODEL_HAIKU:  {"input": Decimal("1.00"),  "output": Decimal("5.00")},
    "claude-opus-4-7":   {"input": Decimal("15.00"), "output": Decimal("75.00")},
    "claude-sonnet-4-6": {"input": Decimal("3.00"),  "output": Decimal("15.00")},
}


# Map AIJob.kind → model. The worker calls model_for_kind() so a Track C/D
# agent dropping in real prompts doesn't have to touch routing.
KIND_TO_MODEL: dict[str, str] = {
    "scope_analysis": MODEL_OPUS,
    "estimate_generation": MODEL_SONNET,
    "email_draft": MODEL_SONNET,
    "ocr": MODEL_HAIKU,
    "document_summary": MODEL_SONNET,  # per-file indexing — Sonnet is the sweet spot
    "change_order_generation": MODEL_SONNET,
    # Editing is a reading-comprehension task over a document that is already
    # correct, with a hard requirement to change ONLY what was asked. Opus,
    # for the same reason scope analysis uses it.
    "ai_edit": MODEL_OPUS,
    # Reading shop drawings against engineered plans, sheet by sheet: the
    # drawing-reading model, as for scope analysis.
    "submittal_review": MODEL_OPUS,
}


# Max-tokens override per kind. Scope JSON runs long — a full multi-room scope
# with materials/issues/permits exceeds 4096 output tokens and gets truncated
# mid-JSON (observed: a 2-photo bathroom scope hit the 4096 cap and failed to
# parse). 16000 gives comfortable headroom; the model only emits what it needs.
KIND_MAX_TOKENS: dict[str, int] = {
    # A change order prices one addition, not a building. 6000 is ample and
    # keeps the model from padding the breakdown to look thorough.
    "change_order_generation": 6000,
    "scope_analysis": 16000,
    # Fully-itemized estimates run long — every task with qty/unit/hours.
    "estimate_generation": 16000,
    # Document summaries are short by design — but 800 was too short for real
    # documents. Measured 2026-10-01: three of the four job failures in the whole
    # table were document_summary truncating at exactly 800 output tokens, on
    # inputs of 1,776-3,611 input tokens. The model was not padding; it ran out of
    # room mid-string. 2000 restores the headroom the other kinds already have
    # without inviting an essay — the model still emits only what it needs.
    "document_summary": 2000,
    # An edit returns a change set, not a document, so it is bounded by how
    # much the instruction asks for rather than by the size of the record.
    "ai_edit": 8000,
}


#: Above this many output tokens a call gets LONG_OUTPUT_TIMEOUT_S instead of
#: the SDK's 600s default (see call_anthropic).
LONG_OUTPUT_TOKENS = 16000
LONG_OUTPUT_TIMEOUT_S = 1200.0


def model_for_kind(kind: str) -> str:
    return KIND_TO_MODEL.get(kind, MODEL_SONNET)


def max_tokens_for_kind(kind: str, default: int = 2048) -> int:
    return KIND_MAX_TOKENS.get(kind, default)


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """USD cost = (in_tokens/1M)*in_rate + (out_tokens/1M)*out_rate.

    Returns Decimal quantized to 4 dp (matches Numeric(10,4) DB column).
    """
    rates = RATE_CARD.get(model)
    if rates is None:
        # Unknown model — fall back to Sonnet pricing, but log via return value.
        rates = RATE_CARD[MODEL_SONNET]
    million = Decimal("1000000")
    cost = (Decimal(input_tokens) / million) * rates["input"] + (
        Decimal(output_tokens) / million
    ) * rates["output"]
    return cost.quantize(Decimal("0.0001"))


@dataclass
class AnthropicResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    model: str
    # "end_turn" | "max_tokens" | … — lets callers detect truncated output.
    stop_reason: str | None = None
    # Wall-clock latency of the Messages API call, milliseconds. Populated by
    # call_anthropic(); the eval harness aggregates p50/p95 from this.
    latency_ms: int = 0


@lru_cache(maxsize=1)
def anthropic_client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def call_anthropic(
    model: str,
    system: str | None,
    messages: list[dict[str, Any]],
    max_tokens: int = 2048,
    client: Anthropic | None = None,
) -> AnthropicResult:
    """Single Anthropic Messages API call → AnthropicResult.

    `client` is injectable for tests. Caller is responsible for retry policy
    (the worker wraps this in a retry loop).
    """
    c = client or anthropic_client()
    # The SDK's default read timeout is 600s. An answer near 32k output tokens
    # can take most of that, and a timeout here is retried, so the job would pay
    # for the same long answer up to three times. Give long asks real headroom.
    if max_tokens > LONG_OUTPUT_TOKENS and hasattr(c, "with_options"):
        c = c.with_options(timeout=LONG_OUTPUT_TIMEOUT_S)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    _t0 = time.perf_counter()
    response = c.messages.create(**kwargs)
    latency_ms = int((time.perf_counter() - _t0) * 1000)

    # Concatenate any text blocks; ignore tool-use blocks (not supported in F).
    text_chunks: list[str] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_chunks.append(getattr(block, "text", ""))
    text = "".join(text_chunks)

    usage = response.usage
    in_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    out_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    return AnthropicResult(
        text=text,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=compute_cost_usd(model, in_tokens, out_tokens),
        model=model,
        stop_reason=getattr(response, "stop_reason", None),
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Scope analysis prompt (Track C)
# ---------------------------------------------------------------------------

# Embedded reference: standard construction cost categories (excerpted from
# `~/.claude/skills/BRIO-scope-analyzer/references/cost-categories.md`). The
# model uses these to bucket work items when emitting the JSON `categories`
# array. Keep this synced with the source-of-truth file when the skill is
# updated.
_COST_CATEGORIES = """\
Standard cost categories (use these names when bucketing work into `categories`):

1. Demolition & Removal — selective demolition, hazmat abatement, debris haul, adjacent protection.
2. Structural — framing, foundation, load-bearing walls, subfloor, seismic.
3. Plumbing — rough-in, fixtures, water heater, DWV, shut-offs, backflow.
4. Electrical — rough-in, lighting, GFCI/AFCI, dedicated circuits, low-voltage, smoke/CO.
5. HVAC — ductwork, bath exhaust, range hood, heating, radiant, mini-split.
6. Insulation & Vapour Barrier — batt/spray/rigid, ceiling, vapour, acoustic.
7. Drywall & Finishing — hang/tape, texture, patch, bulkheads/soffits.
8. Flooring — tile, hardwood, LVP/LVT, laminate, carpet, leveling, heated floor, transitions.
9. Cabinetry & Millwork — kitchen cabs, vanities, closets, mouldings, custom shelving.
10. Countertops & Surfaces — quartz, stone, solid surface, laminate, butcher block, backsplash.
11. Painting & Wall Finishes — interior paint, primer/prep, wallpaper, accent walls, cabinet refinishing.
12. Fixtures & Hardware — plumbing trim, cabinet hardware, door hardware, accessories, mirrors.
13. Doors & Windows — interior/exterior doors, windows, sliding/barn, trim/casing.
14. Appliances — kitchen, laundry hookup, range hood, dishwasher, disposal.
15. Exterior & Site Work — deck/patio, fencing, siding, gutters, landscaping restoration, general earthwork/grading.
16. Stormwater & Drainage — perimeter/foundation drains, catch basins, sumps, infiltration/detention systems, downspout tie-ins, storm piping, re-grading for drainage. Keep SEPARATE from general Site Work.
17. Permits & Inspections — building/plumbing/electrical/mechanical permits, engineering, inspections.
18. Project Management & General Conditions — PM, site prep, temporary facilities, cleanup, insurance.
19. Design & Engineering — architectural, structural, ID consult, 3D render, as-builts.
"""

# Embedded reference: master scope checklist condensed (excerpted from
# `~/.claude/skills/BRIO-scope-analyzer/references/scope-checklist.md`).
_SCOPE_CHECKLIST = """\
When analyzing inputs, walk this checklist mentally — only include items that
apply to the actual scope:

General site assessment:
- Building age + construction type; asbestos risk (<1990), lead paint (<1978),
  knob-and-tube (<1950), mould, strata/HOA restrictions, heritage designation.
- Site access (stairs/elevators), material delivery constraints, adjacent
  protection, working-hour restrictions.

By room (only address rooms in scope):
- Kitchen — cabinet condition, counters, backsplash, appliance/plumbing/electrical
  relocations, range-hood venting, gas line, water damage under sink.
- Bathroom — tub/shower replace vs convert, shower base, vanity, toilet rough-in,
  exhaust fan CFM + exterior venting, heated floors, tile, waterproofing, glass,
  subfloor water damage.
- Basement — moisture, ceiling height (BC 6'5" min), egress windows for bedrooms,
  HVAC extension, plumbing rough-in, panel capacity, sump pump, drainage.
- Bedroom / living / dining / exterior as applicable.

By trade:
- Plumbing — supply (copper/PEX/galv), DWV (ABS/CI/lead), water heater age,
  shut-offs, pressure, sewer condition for older homes.
- Electrical — panel size/age, wiring type, GFCI/AFCI, smoke/CO, dedicated
  circuits, outdoor/low-voltage.
- HVAC — heating/cooling type, ductwork condition + extensions, bath/kitchen
  exhaust adequacy, smart thermostat, HRV/ERV.
- Structural — load-bearing identification, engineered beams (LVL/glulam/steel),
  foundation cracks, subfloor/joist condition, header sizing.

Permit & code (BC / North Vancouver):
- Building / plumbing / electrical / mechanical permits as triggered by scope.
- Development or heritage permits for exterior or designated properties.
- BC Building Code 2024 + Energy Step Code compliance.
- Strata approval for common-property impact.
- WorkSafeBC compliance (fall protection, confined spaces, hazmat).
"""

_SCOPE_JSON_SCHEMA = """\
Respond with valid JSON ONLY — no prose, no markdown fences, no commentary.
Match this exact shape:

{
  "reference": "<short reference label, e.g. SCOPE-DRAFT or address-derived>",
  "date": "<YYYY-MM-DD>",
  "summary": "<3-5 plain-language sentences a team member OR a client could read to understand the job at a glance: what is being done, the headline transformation, and the one or two most significant risks or constraints. No bullet lists, no jargon dumps — flowing prose.>",
  "categories": [
    {
      "name": "<one of the 18 standard categories above>",
      "items": ["<specific work item>", "..."]
    }
  ],
  "materials": [
    {
      "item": "<material name>",
      "spec": "<specification or 'TBD — client to select'>",
      "notes": "<allowance / lead time / alternatives>"
    }
  ],
  "potentialIssues": ["<risk or unknown condition, with rationale>"],
  "accessConstraints": ["<site/strata/delivery/working-hour constraint>"],
  "permits": ["<permit type required + reason>"],
  "assumptions": ["<assumption baked into the scope>"],
  "exclusions": ["<item explicitly NOT included>"]
}

Rules:
- Every key above MUST be present, including `summary`. Use [] for empty
  arrays and "" only if a string genuinely has no content — never omit a key.
- `summary` is 3-5 flowing sentences of plain prose — not a bullet list.
- Strings inside arrays should be concise — one line each.
- Never include preamble like "Here is the scope" or trailing notes — JSON only.
- If a building is pre-1990, raise asbestos in `potentialIssues`. Pre-1978 →
  lead paint. Pre-1950 → knob-and-tube wiring. Mention this even if not visible.
"""


_SCOPE_SYSTEM_PROMPT = f"""\
You are a senior construction scope analyst for Built By Brio, a residential
renovation contractor in North Vancouver, BC. Given photos, drawings, and
project notes, produce a comprehensive scope of work as structured JSON.

You work in one pass — there is no tool use, no follow-up questions. Use your
construction knowledge to infer typical scope items for the project type when
inputs are incomplete, and flag anything uncertain in `potentialIssues` or
`assumptions` rather than guessing silently.

{_COST_CATEGORIES}

{_SCOPE_CHECKLIST}

{_SCOPE_JSON_SCHEMA}
"""


# Per-discipline extraction templates. Injected into the user message when the
# scope_analysis input payload carries `discipline`. The scope produced for a
# discipline goes to that subtrade as the RFQ basis — quantities and explicit
# exclusions are the negotiation lines and must be unambiguous.
_DISCIPLINE_TEMPLATES: dict[str, str] = {
    "structural": """\
STRUCTURAL discipline — load-bearing elements and lateral system:
- Existing-condition modifications: walls / slab / beams / columns to remove,
  cut, infill, or reinforce (sqft / LF / count) by location.
- New foundations: footings (count, dimensions), piers, slab patches (sqft, thickness).
- Structural steel: beams (LF by size, e.g. W8x18), columns (count + height),
  connection types (welded / bolted), embeds, brackets.
- Wood framing: dimensional lumber (LF by size), engineered (LVL / glulam / I-joists)
  by spec, sheathing (sqft).
- Concrete: cubic yards by element (footing / slab / wall), strength (psi),
  finish.
- Reinforcement: rebar tonnage (or LF by size), welded wire mesh (sqft).
- Lateral / shear: shear walls (sqft + sheathing schedule), bracing, hold-downs.
- Inspections: structural review, sign-offs, special inspections required.
Exclude work that is clearly architectural finishes, mechanical, electrical, or plumbing.""",
    "architectural": """\
ARCHITECTURAL discipline — building shell, partitions, finishes:
- Demolition: walls / floors / ceilings removed (sqft, LF) by location.
- Partitions: linear feet by type (standard / 1-hr / sound), wall height where non-standard.
- Doors: count by size, fire-rating, hardware set.
- Windows: count by size, glazing type.
- Ceilings: type (drop / drywall / exposed) and sqft per area.
- Finishes schedule: floor, base, wall, ceiling by area — type and sqft.
- Millwork / casework / built-ins: by location.
- Insulation, vapour barrier: type, R-value, sqft.
Exclude items that are clearly mechanical, electrical, plumbing, or structural.""",
    "mechanical": """\
MECHANICAL discipline — HVAC and ventilation:
- Equipment list: rooftop units / heat pumps / FCUs / HRVs / furnaces — count, capacity (BTU, CFM, tons).
- Ductwork: trunk LF and branch LF with sizes (e.g. 12" round, 16x8 rect).
- Diffusers, registers, grilles: count by type and size.
- Exhaust fans: count and CFM (kitchen / bath / specialty).
- Ventilation requirements: CFM per zone or room.
- Refrigerant lines: LF and size.
- Controls: thermostats, zone controllers, dampers — count.
- Hangers, supports, balancing, commissioning.
Exclude work that is clearly architectural, electrical, or plumbing.""",
    "electrical": """\
ELECTRICAL discipline — power, lighting, low-voltage:
- Service entrance: main panel amperage, voltage, phase, location.
- Sub-panels: count, amperage, location.
- Branch circuits: count by amperage (15A / 20A / 30A+ / dedicated equipment circuits).
- Lighting fixtures: count by type (recessed / pendant / surface / exterior / emergency / decorative).
- Receptacles: count (general / GFCI / WP / USB / dedicated).
- Switches: count by type (single-pole / 3-way / dimmer / occupancy sensor).
- Low voltage: data / comm / HDMI / security / AV — drop counts by type.
- Smoke / CO detectors: count and locations.
- Feeders, conduit, grounding: LF and size where called out.
Exclude work that is clearly architectural, mechanical, or plumbing.""",
    "plumbing": """\
PLUMBING discipline — domestic water, drainage, gas:
- Fixtures: count by type (WC / lavatory / kitchen sink / shower / tub / hose bib / floor drain / mop sink / bidet).
- Supply piping: LF by size and material (1/2" / 3/4" / 1" copper or PEX).
- DWV (drainage): LF by size (1.5" / 2" / 3" / 4").
- Water heater(s): type (tank / tankless), capacity (gal or BTU), fuel source.
- Backflow preventers, mixing valves, recirc pumps, hammer arrestors: count.
- Gas piping: LF and size where applicable.
- Floor drains, trench drains, sumps: count and size.
- Insulation and trace heating for piping where required.
Exclude work that is clearly architectural, mechanical, or electrical.""",
    "gc_rollup": """\
GC ROLLUP — the General Contractor view across all disciplines:
- Synthesize the discipline scopes provided in notes into a project-level scope
  organised by PHASE: pre-construction / mobilization / demolition / rough-in
  (sequenced across trades) / inspections / drywall close-up / finishes / commissioning / closeout.
- General conditions: project management, supervision, mobilization, dumpsters,
  temporary power / water / heat / fencing, daily cleanup, OH&S, insurance, bonding, permits coordination.
- Site work: protection of adjacent work, access, parking, hoisting, deliveries, dust control.
- Coordination items: trade interfaces that drive sequencing (e.g. plumbing rough-in before drywall close-up, electrical inspection before insulation).
- Cross-trade exclusions and overall delivery assumptions.
- Do NOT re-list every discipline line — call out interfaces, sequencing, contingencies, and items that fall between trades.""",
}


def _normalize_discipline(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.lower().strip().replace(" ", "_").replace("-", "_")
    aliases = {
        "hvac": "mechanical",
        "mech": "mechanical",
        "elec": "electrical",
        "plumb": "plumbing",
        "arch": "architectural",
        "struct": "structural",
        "structure": "structural",
        "gc": "gc_rollup",
        "rollup": "gc_rollup",
        "general_contractor": "gc_rollup",
    }
    key = aliases.get(key, key)
    return key if key in _DISCIPLINE_TEMPLATES else None


def _normalize_image_media_type(raw: str | None) -> str:
    """Snap to one of Anthropic's supported image media types."""
    if not raw:
        return "image/jpeg"
    raw = raw.lower().strip()
    if raw in ("image/jpeg", "image/jpg", "jpeg", "jpg"):
        return "image/jpeg"
    if raw in ("image/png", "png"):
        return "image/png"
    if raw in ("image/gif", "gif"):
        return "image/gif"
    if raw in ("image/webp", "webp"):
        return "image/webp"
    return "image/jpeg"


def build_scope_analysis_messages(
    input_payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """Build the (system, messages, max_tokens) tuple for a scope_analysis job.

    Expected `input_payload` shape (the worker's `_merge_scope_inputs`
    materialises `document_ids` -> populated `images`/`documents`):
      {
        "images":    [{"media_type": "image/jpeg",      "data": "<base64>", "caption": "..."}],
        "documents": [{"media_type": "application/pdf", "data": "<base64>", "caption": "..."}],
        "notes": "<free-text notes / synthesis inputs>",
        "discipline": "architectural" | "mechanical" | "electrical" | "plumbing" | "gc_rollup",
        "project_metadata": {"address": "...", "project_type": "...", ...}
      }
    """
    payload = input_payload or {}
    meta = payload.get("project_metadata") or {}
    notes = (payload.get("notes") or "").strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []
    # Word, Excel and plain-text documents, already extracted to text by the
    # worker. Carried separately from `documents` because they are not PDFs and
    # the model reads them as text rather than as a rendered document.
    text_documents = payload.get("text_documents") or []
    discipline = _normalize_discipline(payload.get("discipline"))

    # ---- Text framing block ------------------------------------------------
    meta_lines: list[str] = ["Project context:"]
    if meta.get("name"):
        meta_lines.append(f"- Project name: {meta['name']}")
    if meta.get("address"):
        meta_lines.append(f"- Address: {meta['address']}")
    if meta.get("project_type"):
        meta_lines.append(f"- Project type: {meta['project_type']}")
    if meta.get("building_type"):
        meta_lines.append(f"- Building type: {meta['building_type']}")
    if meta.get("building_age"):
        meta_lines.append(f"- Building age (year built): {meta['building_age']}")
    if meta.get("sqft"):
        meta_lines.append(f"- Approx. work-area sqft: {meta['sqft']}")
    if len(meta_lines) == 1:
        meta_lines.append("- (no structured project metadata provided)")

    text_chunks: list[str] = ["\n".join(meta_lines)]

    if discipline:
        text_chunks.append(
            f"DISCIPLINE-FOCUSED SCOPE — {discipline.upper()}\n"
            f"{_DISCIPLINE_TEMPLATES[discipline]}\n\n"
            f"This run produces the {discipline.upper()} scope ONLY — other "
            "disciplines are scoped separately. The output goes to the subtrade "
            "as the RFQ basis. **Quantities and explicit exclusions are the "
            "negotiation lines** — be specific (counts, LF, sqft, sizes) and put "
            "anything not in this discipline's scope into `exclusions`. Items in "
            "`categories[].items` should read as line-level work statements with "
            "their quantities."
        )

    if notes:
        text_chunks.append(f"Project notes / client requests / transcriptions:\n{notes}")
    else:
        text_chunks.append("Project notes: (none provided)")

    readable_texts = [t for t in text_documents if isinstance(t, dict) and (t.get("text") or "").strip()]
    unreadable = [t for t in text_documents if isinstance(t, dict) and t.get("error")]

    artifact_count = len(images) + len(documents) + len(readable_texts)
    if artifact_count:
        bits = []
        if documents:
            bits.append(f"{len(documents)} PDF document(s)")
        if images:
            bits.append(f"{len(images)} image(s)")
        if readable_texts:
            bits.append(f"{len(readable_texts)} text document(s)")
        guidance = [f"{', '.join(bits)} follow. Read each carefully."]
        if documents or images:
            guidance.append(
                "Drawings carry schedules and notes that may not be visually "
                "obvious — extract fixture counts, equipment lists, linear "
                "footages, and any schedules referenced on the sheets."
            )
        if readable_texts:
            # Specs and schedules state the work in words. They are the most
            # literal source in the set and should be preferred over a reading
            # of a drawing when the two disagree.
            guidance.append(
                "Text documents are specifications, schedules, quotes or scope "
                "sheets. They state the work in words and in quantities — take "
                "them literally, and where one contradicts a drawing, say so in "
                "`assumptions` rather than silently choosing."
            )
        text_chunks.append(" ".join(guidance))
    else:
        text_chunks.append(
            "No drawings or images were provided. Build the scope from the project "
            "metadata + notes alone. Flag the lack of source documents in `assumptions`."
        )

    # Say what was selected and could NOT be read. The user ticked it and is
    # entitled to have the scope account for its absence rather than write
    # around a gap it does not know exists.
    if unreadable:
        lines = "\n".join(
            f"- {t.get('caption') or 'file'}: {t.get('error')}" for t in unreadable
        )
        text_chunks.append(
            "The following selected documents COULD NOT be read and are not "
            "included below. Do not assume their contents; where they would "
            "have mattered, say so in `assumptions`.\n" + lines
        )

    text_chunks.append(
        "Reply with ONLY the JSON object described in the system prompt. No "
        "markdown, no preamble, no trailing commentary."
    )

    content: list[dict[str, Any]] = [
        {"type": "text", "text": "\n\n".join(text_chunks)},
    ]

    # ---- Image blocks ------------------------------------------------------
    for img in images:
        if not isinstance(img, dict):
            continue
        data = img.get("data")
        if not data:
            continue
        caption = (img.get("caption") or "").strip()
        if caption:
            content.append({"type": "text", "text": f"Image: {caption}"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _normalize_image_media_type(img.get("media_type")),
                    "data": data,
                },
            }
        )

    # ---- PDF document blocks ----------------------------------------------
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        data = doc.get("data")
        if not data:
            continue
        caption = (doc.get("caption") or "").strip()
        if caption:
            content.append({"type": "text", "text": f"Drawing: {caption}"})
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": doc.get("media_type") or "application/pdf",
                    "data": data,
                },
            }
        )

    # ---- Extracted text documents -----------------------------------------
    # Specifications, room and door schedules, subtrade quotes, scope
    # spreadsheets. These carry line-level work statements and quantities that
    # no drawing shows, and until now they were fetched and then discarded.
    for t in readable_texts:
        caption = (t.get("caption") or "document").strip()
        suffix = " (truncated)" if t.get("truncated") else ""
        content.append({
            "type": "text",
            "text": f"Document: {caption}{suffix}\n-----\n{t['text']}\n-----",
        })

    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    return _SCOPE_SYSTEM_PROMPT, messages, max_tokens_for_kind("scope_analysis")


# ---------------------------------------------------------------------------
# Estimate generation prompt (Track D)
#
# Ported from the BRIO-estimate-generator skill — its rate ranges, the 4
# detail-level shapes, and its estimating methodology. Keep in sync with
# ~/.claude/skills/BRIO-estimate-generator/ when that skill changes.
# ---------------------------------------------------------------------------

# Condensed from references/rate-ranges.md (Greater Vancouver, 2025-2026).
_ESTIMATE_RATE_REFERENCE = """\
Greater Vancouver / North Shore renovation rate ranges — use as the pricing
baseline. Default to MID; use LOW for simple/repetitive work with easy access,
HIGH for complex geometry, premium materials, difficult access, or rush work.

Labor ($/hr) — ⚠️ these are BILLABLE rates that already include overhead, and
the system applies margin to categories afterwards, so treat them as an upper
bound and price toward the LOW end of each band to avoid counting margin twice.
This static block is only reached when the rate library is unavailable; the live
library supplies a proper cost basis instead.
General Laborer 35/45/55 ·
Carpenter-framing 55/70/90 · Carpenter-finish 65/80/100 · Plumber-journeyman
95/120/150 · Electrician-journeyman 95/115/140 · HVAC Tech 95/120/150 ·
Tile Installer 65/85/110 · Drywaller 55/65/80 · Painter 45/55/70 ·
Flooring Installer 55/70/90 · GC/PM 75/95/120.

Materials (supply only, before markup): LVP $3-8/sf · engineered hardwood
$6-14/sf · porcelain tile $5-15/sf · ceramic/subway tile $2-8/sf · quartz
counter $65-120/sf installed · stock cabinets $150-280/lf · semi-custom
$300-600/lf · toilet $250-800 · bath faucet $100-500 · shower valve+trim
$200-800 · acrylic shower base $300-900 · vanity 30-36" $400-1800 ·
vanity 48-60" $800-3000 · interior paint $40-80/gal.

Rules of thumb: bathroom full gut $25-50k (std), $50-80k+ (high-end);
kitchen $40-80k (std); tile labor $8-15/sf floor, $15-25/sf shower;
drywall supply+install+finish $4-6/sf; painting $3.50-5.50/sf surface.

Waste factors: tile 10-15%, flooring 5-10%, drywall 5%, paint 10%, trim 10%.
Permit fees: building $500-2000, plumbing/electrical $200-500 each.
Disposal: mini bin $350-500, full bin $500-800 per load.

Sub-trade quotes: if a real quote is supplied in the notes, use that exact
number — do not re-estimate it."""

#: The parts of the baseline that NO table backs, so they survive verbatim when
#: the rest is generated from the rate library: whole-job benchmarks, permit and
#: disposal costs (deliberately not seeded as materials — they are project costs,
#: not supply), and the sub-trade-quote rule, which is a rule and not a rate.
_RATE_REFERENCE_STATIC_TAIL = """\
Cross-checks — these are MARKET / INSTALLED prices that already contain labour,
overhead and profit. Use them ONLY to sanity-check whether a finished total looks
sane. Never price a line item from them and never mix them with the base costs
above, or margin gets counted twice: bathroom full gut $25-50k (std), $50-80k+
(high-end); kitchen $40-80k (std); tile install $8-15/sf floor, $15-25/sf shower;
drywall supply+install+finish $4-6/sf; painting $3.50-5.50/sf surface.

Permit fees: building $500-2000, plumbing/electrical $200-500 each.
Disposal: mini bin $350-500, full bin $500-800 per load.
Permits and disposal are real project costs — price them as base costs.

Sub-trade quotes: if a real quote is supplied in the notes, use that exact
number — do not re-estimate it."""

# The 19 standard cost categories (from references/cost-categories.md).
_ESTIMATE_CATEGORIES = (
    "Demolition & Removal, Structural, Plumbing, Electrical, HVAC, "
    "Insulation & Vapour Barrier, Drywall & Finishing, Flooring, "
    "Cabinetry & Millwork, Countertops & Surfaces, Painting & Wall Finishes, "
    "Fixtures & Hardware, Doors & Windows, Appliances, Exterior & Site Work, "
    "Stormwater & Drainage, "
    "Permits & Inspections, Project Management & General Conditions, "
    "Design & Engineering"
)


#: The generic line, used ONLY when a project has no type and therefore no
#: division set. The count is derived rather than written by hand: the prose used
#: to say "18" while the list held 19 names, so the model was told one thing and
#: shown another.
_CATEGORY_LINE_GENERIC = (
    f"The {_ESTIMATE_CATEGORIES.count(',') + 1} standard cost categories "
    f"(use these names): {_ESTIMATE_CATEGORIES}."
)


def estimate_system_prompt(divisions: dict[str, Any] | None = None, *,
                           require_every_division: bool = True) -> str:
    """The estimate system prompt, with the category line bound to this project.

    BRIO's cost breakdown depends on the project type — residential uses BRIO's
    own 18 numbered divisions, commercial uses CSI MasterFormat — so a single
    baked-in list is wrong for one of them. ``divisions`` is the block attached by
    the worker (see services.divisions.division_context).

    Passing None returns the base template unchanged, which is both the fallback
    for an unclassified project and the stable, versionable core that
    ``get_skill_prompt_template`` reports.
    """
    if not divisions or not divisions.get("labels"):
        return _ESTIMATE_SYSTEM_PROMPT

    labels = divisions["labels"]
    cont = divisions.get("contingency_pct") or {}
    # Numbered divisions are a SEQUENCE, so they are listed one per line in order
    # rather than comma-joined: the number is part of the name and the order is
    # construction order, both of which a flat comma list loses.
    listed = "\n".join(f"  {lab}" for lab in labels)
    zero = [lab for lab in labels if float(cont.get(lab, 5)) == 0]
    note = (
        f"\nApply no trade contingency to: {', '.join(zero)}."
        if zero else ""
    )
    # ⚠️ "KEEP EVERY DIVISION AT 0" IS AN ASSEMBLY RULE, NOT A PER-TRADE ONE.
    #
    # It is right for the finished estimate: absent scope should stay visible on
    # the client breakdown. Given to each discipline separately it multiplies —
    # six disciplines x 21 divisions produced 126 categories, with seven or
    # eight divisions carrying cost from more than one trade. Measured on 970
    # Marine, same scope, one variable: 126 categories and 51% gc_rollup overlap
    # with the rule per-discipline; 32 categories and 16% without it.
    #
    # The NAMES still bind per-discipline — that is what lets six partial
    # breakdowns merge cleanly. Completeness is imposed once, at assembly, by
    # _ensure_every_division().
    keep_empty = (
        " Keep a division you have no scope for, at a total of 0, rather than "
        "omitting it, so absent scope stays visible."
        if require_every_division else
        " Omit a division you have no scope for rather than emitting it at 0 — "
        "this is one trade's part of a larger breakdown, and the full division "
        "list is assembled from all of them afterwards."
    )
    line = (
        f"Use EXACTLY these {len(labels)} cost divisions, with these exact names, "
        f"in this order ({divisions.get('set_name') or divisions.get('set_code')}):\n"
        f"{listed}\n"
        f"The leading number is part of the name — emit it.{keep_empty} "
        "Do not invent, merge, rename or reorder divisions."
        f"{note}"
    )
    assert _CATEGORY_LINE_GENERIC in _ESTIMATE_SYSTEM_PROMPT, (
        "the generic category line is no longer present in the estimate prompt; "
        "per-project division substitution would silently do nothing"
    )
    return _ESTIMATE_SYSTEM_PROMPT.replace(_CATEGORY_LINE_GENERIC, line)


def _with_live_rates(prompt: str, rates: dict[str, Any] | None) -> str:
    """Swap the static rate baseline for one read from the rate library.

    The hardcoded baseline gave the model BILLABLE labour rates while the same
    prompt demanded base costs and the PDF applied margin afterwards, so overhead
    was counted twice. The library holds a cost basis, which removes that.

    Falls back silently to the static block ONLY when the library is too thin to
    price from — the worker records that case in the payload so it is visible.
    """
    from app.services.rate_reference import render_rate_reference

    block = render_rate_reference(rates, static_tail=_RATE_REFERENCE_STATIC_TAIL)
    if not block:
        return prompt
    assert _ESTIMATE_RATE_REFERENCE in prompt, (
        "the static rate reference is no longer present in the estimate prompt; "
        "live-rate substitution would silently do nothing"
    )
    return prompt.replace(_ESTIMATE_RATE_REFERENCE, block)


# When the estimate is discipline-scoped (one per trade for RFQ negotiation),
# each estimate must stay in its lane to avoid double-counting cross-trade
# work that another discipline's estimate is also pricing.
_DISCIPLINE_ESTIMATE_CATEGORIES: dict[str, list[str]] = {
    "structural":    ["Structural"],
    "architectural": [
        "Demolition & Removal", "Drywall & Finishing", "Flooring",
        "Cabinetry & Millwork", "Doors & Windows", "Painting & Wall Finishes",
        "Insulation & Vapour Barrier", "Countertops & Surfaces",
        "Fixtures & Hardware", "Appliances",
    ],
    "electrical":    ["Electrical"],
    "mechanical":    ["HVAC"],
    "plumbing":      ["Plumbing"],
    # The GC rollup ESTIMATE prices only project-overhead categories — the
    # trade work is already in the discipline estimates and must not be
    # re-priced here.
    "gc_rollup": [
        "Permits & Inspections", "Project Management & General Conditions",
        "Design & Engineering", "Exterior & Site Work", "Stormwater & Drainage",
    ],
}

_ESTIMATE_JSON_SCHEMA = """\
Respond with valid JSON ONLY — no prose, no markdown fences, no commentary.
Match this exact shape:

{
  "reference": "<short reference label, e.g. EST-DRAFT>",
  "date": "<YYYY-MM-DD>",
  "detailLevel": "<summary | standard | detailed | fully_itemized>",
  "categories": [
    {
      "name": "<one of the cost divisions named above, verbatim>",
      "description": "<brief 1-line description — standard/detailed/fully_itemized only>",
      "total": <number — BASE cost for the category, pre-margin>,
      "items": [
        {
          "name": "<work item>",
          "labor": <number — base labor cost>,
          "materials": <number — base material cost>,
          "qty": <number — fully_itemized only>,
          "unit": "<unit of qty: sf, lf, ea, hr, ls ... — fully_itemized only>",
          "unitCost": <number — fully_itemized only>,
          "laborHours": <number — fully_itemized only>,
          "rate_ref": "<library assembly or material code, or null — fully_itemized only>",
          "labour_ref": "<library labour code, or null — fully_itemized only>",
          "qty_source": "<takeoff:<quantity_type>, or omit>",
          "basis": "<library | quote | allowance | no_rate — fully_itemized only>"
        }
      ]
    }
  ],
  "assumptions": ["<assumption>"],
  "exclusions": ["<exclusion>"],
  "paymentSchedule": [
    {"milestone": "<name>", "percentage": <number>, "description": "<when due>"}
  ],
  "validDays": 30,
  "notes": "<optional one-line pricing note, or ''>"
}

Rules:
- All costs are BASE costs — labor + materials only. DO NOT apply overhead or
  profit; the system folds margin into category totals afterward.
- Category-shape by detailLevel:
  - summary         → {name, total}                       (omit description/items)
  - standard        → {name, description, total}          (omit items)
  - detailed        → {name, description, total, items:[{name, labor, materials}]}
  - fully_itemized  → {name, description, total, items:[{name, qty, unit, unitCost, laborHours, labor, materials, rate_ref, labour_ref, basis}]}
- For detailed/fully_itemized, each category `total` MUST equal the sum of its
  items' (labor + materials).
- Round line items to the nearest $10 (detailed/fully_itemized), category
  totals to the nearest $50 (standard) or $100 (summary).
- Be accurate, not optimistic — slightly over beats under.
- `paymentSchedule` percentages MUST sum to 100. Default 3-payment:
  25% deposit on signing, 50% at substantial midpoint, 25% on completion.
- Every array MUST be present — use [] when empty, never omit a key.
"""

_ESTIMATE_SYSTEM_PROMPT = f"""\
You are a senior construction estimator for Built By Brio, a residential
renovation contractor in North Vancouver, BC. Given a scope of work, produce a
professional cost estimate as structured JSON.

You work in one pass — no tool use, no follow-up questions. Price every work
category in the scope using your construction knowledge and the rate reference
below. Where the scope leaves a material "TBD", price a sensible mid-range
allowance and note it in `assumptions`.

{_CATEGORY_LINE_GENERIC}

{_ESTIMATE_RATE_REFERENCE}

Generate standard assumptions (normal working hours; client selects materials
within 2 weeks; structure serviceable unless scope notes otherwise; pre-1990
buildings may need hazmat testing; permits obtained by Brio; pricing valid for
orders within 60 days) and standard exclusions (architectural/engineering
design unless scoped; furniture/decor/appliances unless specified;
hazmat abatement; work outside the project area) — include only the applicable
ones.

{_ESTIMATE_JSON_SCHEMA}
"""


def _format_scope_for_estimate(scope: dict[str, Any]) -> str:
    """Render the scope payload into a compact text block for the estimator."""
    lines: list[str] = []
    if scope.get("summary"):
        lines.append(f"Scope summary: {scope['summary']}")
    for cat in scope.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        items = [str(i) for i in (cat.get("items") or []) if str(i).strip()]
        lines.append(f"\n[{cat.get('name', '—')}]")
        lines.extend(f"  - {it}" for it in items)
    materials = scope.get("materials") or []
    if materials:
        lines.append("\nMaterials noted in scope:")
        for m in materials:
            if isinstance(m, dict):
                lines.append(
                    f"  - {m.get('item', '?')}: {m.get('spec', 'TBD')}"
                    + (f" ({m.get('notes')})" if m.get("notes") else "")
                )
    for key, label in (
        ("potentialIssues", "Potential issues"),
        ("accessConstraints", "Access constraints"),
        ("permits", "Permits"),
    ):
        vals = [str(v) for v in (scope.get(key) or []) if str(v).strip()]
        if vals:
            lines.append(f"\n{label}: " + "; ".join(vals))
    return "\n".join(lines) if lines else "(no scope detail provided)"


def _format_brio_benchmarks(benchmarks: list[dict[str, Any]]) -> str:
    """Render BRIO's historical per-category cost actuals into a prompt block."""
    lines = [
        "BRIO HISTORICAL COST DATA — calibrate to these.",
        "Real subtrade/supplier base costs (labor + materials) BRIO incurred on",
        "past projects, grouped by standard category. Where a category below",
        "has BRIO data, anchor your base-cost figure to it rather than to the",
        "generic rate reference — the more records, the more weight it carries.",
        "Adjust for this project's scope and scale; do not copy blindly. Use the",
        "rate reference only for categories with no BRIO data.",
        "",
        "| Category | BRIO base-cost total | Records | Avg per record |",
        "|---|---|---|---|",
    ]
    for b in benchmarks:
        lines.append(
            f"| {b['category']} | ${b['total']:,.0f} | {b['count']} "
            f"| ${b['avg']:,.0f} |"
        )
    return "\n".join(lines)


def build_estimate_messages(
    input_payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """Build the (system, messages, max_tokens) tuple for an estimate job.

    Expected `input_payload` (the worker injects `scope` + `project_metadata`
    + `brio_cost_benchmarks`):
      {
        "scope": {<scope payload>},
        "project_metadata": {"name", "address", "project_type", ...},
        "brio_cost_benchmarks": [{"category", "total", "count", "avg"}, ...],
        "detail_level": "standard",
        "allowances": 0,
        "notes": "<sub-trade quotes / client budget / extra context>"
      }
    """
    payload = input_payload or {}
    scope = payload.get("scope") or {}
    meta = payload.get("project_metadata") or {}
    detail_level = payload.get("detail_level") or "standard"
    allowances = payload.get("allowances") or 0
    notes = (payload.get("notes") or "").strip()
    discipline = _normalize_discipline(payload.get("discipline"))
    allowed_cats = _DISCIPLINE_ESTIMATE_CATEGORIES.get(discipline) if discipline else None
    mode = (payload.get("mode") or "").lower().strip()
    bias = (payload.get("bias") or "").lower().strip()
    cost_basis = (payload.get("cost_basis") or "").lower().strip()
    if cost_basis not in ("low", "mid", "high"):
        cost_basis = ""  # treat anything else as "no override" → model defaults to MID

    meta_lines = ["Project context:"]
    for key, label in (
        ("name", "Project name"),
        ("address", "Address"),
        ("project_type", "Project type"),
        ("building_type", "Building type"),
        ("building_age", "Building age (year built)"),
        ("sqft", "Approx. work-area sqft"),
    ):
        if meta.get(key):
            meta_lines.append(f"- {label}: {meta[key]}")

    chunks = [
        "\n".join(meta_lines),
        f"Requested detail level: {detail_level}.",
        "Scope of work to price:\n" + _format_scope_for_estimate(scope),
    ]
    if allowed_cats:
        chunks.append(
            f"DISCIPLINE-SCOPED ESTIMATE — {discipline.upper()}\n"
            f"This estimate prices ONLY the {discipline} trade portion of the work. "
            f"Produce line items ONLY in these cost categories: "
            f"{', '.join(allowed_cats)}. "
            "Do NOT add line items in other categories — those are estimated separately "
            "in the other discipline runs. Cross-trade scope must not be duplicated."
        )

    from app.services.finish_spec import prompt_block as _finish_block

    finish = _finish_block(payload.get("finish_spec"))
    if finish:
        chunks.append(finish)

    verified = payload.get("verified_quantities") or []
    if verified:
        v_lines = [
            "USER-VERIFIED QUANTITIES — authoritative, use these exactly.",
            "These come from a human review of the actual drawings; if any "
            "line item touches one of these quantity types, use the verified "
            "value as your basis rather than inferring it from the scope text.",
            "",
        ]
        for v in verified:
            val = v.get("value")
            page_note = f" (drawing p{v['page']})" if v.get("page") else ""
            v_lines.append(
                f"- {v['quantity_type']}: {val} {v['unit']}{page_note} "
                f"[source: {v['source']}]"
            )
        chunks.append("\n".join(v_lines))
    if discipline == "gc_rollup":
        chunks.append(
            "GC ROLLUP — PM calibration rule (the prior run badly overstated this):\n"
            "- 'Project Management & General Conditions' is OVERHEAD ALLOCATION "
            "over the project duration, NOT a sum of PM time per individual "
            "activity. ONE supervisor/PM is shared across all activities — do not "
            "allocate separate PM hours per phase or per trade interface.\n"
            "- Cap PM & General Conditions at 10-15% of the trade subtotal "
            "(structural + architectural + mechanical + electrical + plumbing). "
            "For a 4,000-5,000 sqft commercial TI of 3-6 month duration, total "
            "PM/supervision labour is typically 200-500 hours — NOT 1,500+.\n"
            "- 'Exterior & Site Work' must reflect actual site conditions. If "
            "exterior work is absent from the project scope (interior-only TI), "
            "limit this to allowances for site protection, deliveries, and "
            "hoisting — typically $15-40k, not $80-100k.\n"
            "- 'Stormwater & Drainage' is a SEPARATE category from 'Exterior & Site "
            "Work' — never combine them. Storm/drainage systems (perimeter drains, "
            "catch basins, sumps, infiltration/detention, downspout tie-ins, storm "
            "piping, drainage re-grading) go under Stormwater & Drainage.\n"
            "- 'Design & Engineering' is owner-supplied unless the contract "
            "explicitly includes it. Default to a small allowance ($5-15k) "
            "covering only contractor-side design coordination, not full A&E "
            "fees."
        )
    if mode == "commercial":
        chunks.append(
            "COMMERCIAL PROJECT — adjustments:\n"
            "- Keep stormwater/drainage management as its OWN category ('Stormwater & "
            "Drainage'), separate from general site work — even when using CSI-style "
            "division names.\n"
            "- This is commercial construction, NOT residential. Apply commercial "
            "productivity: labor hours per unit area are typically 15-25% lower than "
            "the equivalent residential retrofit (bulk work, repetitive details, "
            "fewer mob/demob events, single-site mobilization).\n"
            "- DROP the residential rules of thumb in the rate reference "
            "(bathroom $25-50k, kitchen $40-80k, etc.) — they are individual "
            "residential retrofits and do not apply to commercial TI.\n"
            "- Subtrade pricing is commercial-bulk. Assume competitive commercial "
            "sub rates — not retail residential rates."
        )
    if bias == "negotiation":
        chunks.append(
            "NEGOTIATION-BIAS — internal RFQ backstop, not a client quote:\n"
            "- Use calibrated MID-RANGE pricing, NOT conservative-high. Padding "
            "loses competitive bids; accuracy keeps you in the running.\n"
            "- Override the system-prompt rule 'slightly over beats under' — for "
            "this run, use the realistic midpoint and put genuine uncertainty "
            "into `assumptions` rather than into the price."
        )
    if cost_basis == "low":
        chunks.append(
            "COST BASIS: LOW.\n"
            "- Use the LOWER bound of every labor rate range in the system-prompt "
            "rate reference (e.g. Electrician 95/hr not 115/hr; Carpenter-framing "
            "55/hr not 70/hr; GC/PM 75/hr not 95/hr).\n"
            "- Assume tighter productivity than mid-range: simple/repetitive work, "
            "easy access, single mobilisation, open framing/ceilings — labor hours "
            "per unit at the LOW end of typical ranges (e.g. 0.5-0.8 hr per LED can "
            "retrofit instead of 1.5; 1.0-1.5 hr per branch-circuit receptacle "
            "instead of 2.5).\n"
            "- Material unit costs at competitive commercial-bulk pricing, NOT "
            "retail."
        )
    elif cost_basis == "high":
        chunks.append(
            "COST BASIS: HIGH.\n"
            "- Use the UPPER bound of labor rate ranges. Complex geometry, "
            "difficult access, rush schedule, or premium materials. Assume looser "
            "productivity than mid-range."
        )
    elif cost_basis == "mid":
        chunks.append(
            "COST BASIS: MID — the rate reference default. No override."
        )
    if allowances:
        chunks.append(
            f"A separate allowance line of ${allowances} will be added by the "
            "system — do NOT include it in category totals."
        )
    if notes:
        chunks.append(f"Additional context / sub-trade quotes:\n{notes}")
    benchmarks = payload.get("brio_cost_benchmarks") or []
    if benchmarks:
        chunks.append(_format_brio_benchmarks(benchmarks))
    chunks.append(
        "Reply with ONLY the JSON object described in the system prompt. "
        f"Set detailLevel to \"{detail_level}\". No markdown, no preamble."
    )

    messages = [{"role": "user", "content": "\n\n".join(chunks)}]
    # Bound to this project's division set; falls back to the generic list
    # when the project has no type (the worker records why in the payload).
    # A discipline-scoped run produces ONE TRADE's part of the breakdown, so it
    # must not pad out every division; assembly does that once.
    system = estimate_system_prompt(
        payload.get("divisions"),
        require_every_division=not payload.get("discipline"),
    )
    # Rates come from the library, so editing a rate in the admin UI changes what
    # the model prices against. Unchanged when the library is too thin.
    system = _with_live_rates(system, payload.get("rates"))
    return system, messages, max_tokens_for_kind("estimate_generation")


# ── Skill prompt registry (git-anchored versioning, Step 1) ───────────
# The code is the single source of truth for every skill's prompt. Each
# registered skill_id maps to (a) the *stable* system-prompt template that
# defines its behaviour and (b) a version string for that template. The
# `skill_versions` DB row mirrors this (see skill_engine.sync_active_prompt);
# the worker stamps every run against the active row, so a run is provably
# tied to the prompt that produced it. Bump the version here when a prompt
# changes — promotion into the active DB row is gated on approval (Step 3),
# never automatic.
SKILL_PROMPT_VERSIONS: dict[str, str] = {
    "scope_analyzer": "1.0",
    # 1.1 — the cost-division list is now resolved from the project type
    #       (BRIO residential 18 / CSI MasterFormat) instead of one baked-in
    #       generic list. Promotion into the active DB row stays manual.
    # 1.2 — the rate baseline is read from the rate library instead of a
    #       hardcoded copy, and labour is now a COST basis rather than a
    #       billable one, which removes a double count of overhead.
    "estimate_generator": "1.2",
    "document_summary": "1.0",
}


def get_skill_prompt_template(skill_id: str) -> str | None:
    """Return the stable, behaviour-defining system-prompt template for a
    registered skill, byte-identical to what the worker sends as the system
    prompt (for document_summary, the per-category schema instruction is
    appended at run time — the base here is the versionable core). Returns
    None for skill_ids we don't version."""
    if skill_id == "scope_analyzer":
        return _SCOPE_SYSTEM_PROMPT
    if skill_id == "estimate_generator":
        return _ESTIMATE_SYSTEM_PROMPT
    if skill_id == "document_summary":
        from app.services.document_summary_prompts import _SYSTEM_BASE

        return _SYSTEM_BASE
    return None


CHANGE_ORDER_SYSTEM = """You price a single change order for a Canadian residential \
construction company working in British Columbia.

A change order bills work BEYOND an already-agreed contract scope. That framing governs \
everything you do:

- Price ONLY the additional work the instruction describes. If a document shows work that is \
plainly part of the original contract, leave it out. Over-reaching here is how a contractor \
loses a dispute.
- If the instruction is too vague to price, say so in `assumptions` and give your best figure. \
Do not invent a scope to justify a number.
- Quantities and rates come from the documents and the instruction. Where you assume, record \
the assumption.
- Schedule impact is the ADDITIONAL days this work adds, which is often zero for work that runs \
alongside what is already happening.

Return ONLY a JSON object, no prose, no code fence:

{
  "title": "short line naming the work, under 80 characters",
  "description": "what the work is and why it is additional, 2-4 sentences, addressed to the client",
  "cost_impact": 0,
  "schedule_impact_days": 0,
  "line_items": [
    {"description": "...", "qty": 1, "unit": "ea", "unit_cost": 0, "amount": 0}
  ],
  "assumptions": ["..."],
  "exclusions": ["..."]
}

`cost_impact` is the pre-tax total and must equal the sum of `line_items[].amount`. GST is added \
downstream, never here. Do not include contingency, overhead or profit as line items; fold them \
into the rates. Never mention a contingency percentage, an overhead allocation or a margin: this \
text reaches the client."""


def build_change_order_messages(
    input_payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """Build the (system, messages, max_tokens) tuple for a change_order_generation job.

    Shares `_merge_scope_inputs` with scope_analysis, so `documents` and `images`
    arrive already materialised from R2. The one field this kind adds is
    `instruction`: the human description of what the extra work actually is.
    Without it the model has documents and no way to tell an addition from the
    original contract, which is the whole risk this prompt is written around.
    """
    payload = input_payload or {}
    meta = payload.get("project_metadata") or {}
    instruction = (payload.get("instruction") or "").strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []

    chunks: list[str] = []
    ctx = ["Project context:"]
    for label, key in (("Project name", "name"), ("Address", "address"),
                       ("Project type", "project_type")):
        if meta.get(key):
            ctx.append(f"- {label}: {meta[key]}")
    chunks.append("\n".join(ctx) if len(ctx) > 1 else "Project context: (none recorded)")

    if payload.get("contract_summary"):
        chunks.append(
            "Already under contract, for exclusion. Do NOT price anything that appears here:\n"
            + str(payload["contract_summary"])[:6000]
        )

    chunks.append(
        "The additional work to price:\n" + (instruction or "(none given — say so in assumptions)")
    )
    if documents or images:
        bits = []
        if documents:
            bits.append(f"{len(documents)} PDF document(s)")
        if images:
            bits.append(f"{len(images)} image(s)")
        chunks.append("Attached for reference: " + " and ".join(bits) + ".")

    content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(chunks)}]
    for img in images:
        if isinstance(img, dict) and img.get("data"):
            if (img.get("caption") or "").strip():
                content.append({"type": "text", "text": f"Image: {img['caption'].strip()}"})
            content.append({"type": "image", "source": {
                "type": "base64",
                "media_type": _normalize_image_media_type(img.get("media_type")),
                "data": img["data"]}})
    for doc in documents:
        if isinstance(doc, dict) and doc.get("data"):
            if (doc.get("caption") or "").strip():
                content.append({"type": "text", "text": f"Document: {doc['caption'].strip()}"})
            content.append({"type": "document", "source": {
                "type": "base64",
                "media_type": doc.get("media_type") or "application/pdf",
                "data": doc["data"]}})

    return (
        CHANGE_ORDER_SYSTEM,
        [{"role": "user", "content": content}],
        max_tokens_for_kind("change_order_generation", 6000),
    )


def build_messages_for_job(
    kind: str, input_payload: dict[str, Any]
) -> tuple[str | None, list[dict[str, Any]], int]:
    """Generic prompt builder used by the AI worker.

    Returns `(system, messages, max_tokens)`. Per-kind branches plug in
    specialized prompts; default branch echoes the supplied `prompt` for
    quick end-to-end testing of the worker plumbing.
    """
    if kind == "scope_analysis":
        return build_scope_analysis_messages(input_payload or {})
    if kind == "estimate_generation":
        return build_estimate_messages(input_payload or {})
    if kind == "change_order_generation":
        return build_change_order_messages(input_payload or {})
    if kind == "document_synthesis":
        from app.services.synthesis import build_synthesis_messages

        return build_synthesis_messages(input_payload or {})
    if kind == "daily_report_capture":
        from app.services.daily_report_capture import build_daily_capture_messages

        return build_daily_capture_messages(input_payload or {})
    if kind == "submittal_review":
        from app.services.submittal_review import build_submittal_review_messages

        return build_submittal_review_messages(input_payload or {})
    if kind == "email_draft":
        if (input_payload or {}).get("purpose") == "estimate_share":
            from app.services.estimate_email import build_messages as build_estimate_email

            return build_estimate_email(input_payload or {})
        from app.services.milestone_email import build_email_draft_messages

        return build_email_draft_messages(input_payload or {})
    if kind == "ai_edit":
        from app.services.edit_prompt import build_ai_edit_messages

        return build_ai_edit_messages(input_payload or {})
    if kind == "document_summary":
        # Lazy import — keeps the ai.py module load cheap for the (much
        # more common) scope / estimate jobs.
        from app.services.document_summary_extractor import ExtractionContext
        from app.services.document_summary_prompts import (
            build_messages_for_document_summary,
        )

        payload = input_payload or {}
        raw_ext = payload.get("extraction") or {}
        ctx = ExtractionContext(
            mode=raw_ext.get("mode") or "filename_only",
            text=raw_ext.get("text") or "",
            image_b64=raw_ext.get("image_b64"),
            image_media_type=raw_ext.get("image_media_type"),
            notes=list(raw_ext.get("notes") or []),
        )
        return build_messages_for_document_summary(
            project_context=payload.get("project_context") or {},
            document=payload.get("document") or {},
            extraction=ctx,
        )

    prompt = (input_payload or {}).get("prompt") or ""
    if not prompt:
        prompt = f"Job kind: {kind}. No prompt supplied — respond with 'ok'."

    system = (
        "You are a helpful assistant for the Brio construction PM app. "
        "Respond concisely. The orchestrating worker will replace this prompt "
        "with kind-specific logic in later tracks."
    )
    messages = [{"role": "user", "content": prompt}]
    return system, messages, max_tokens_for_kind(kind)
