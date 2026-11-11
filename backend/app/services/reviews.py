"""Review solicitation service — Google Places review stats + AI-drafted asks.

Two jobs:
  • review_stats(): BRIO's live rating + review count + the "write a review"
    link, from Places API (New). Cached (paid per call) so the dashboard and the
    daily reminder don't hammer it.
  • draft_message(): a humble, personal review-request the owner can send from
    their own phone/email. It SUGGESTS talking points from the job note — it never
    writes the review for the client (that reads fake and violates Google policy).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.services.ai import MODEL_SONNET, call_anthropic


# ── Places review stats (cached) ─────────────────────────────────────────────
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_TTL = 6 * 3600  # 6h — reviews change slowly; keep Places calls cheap


def write_review_url() -> str:
    return f"https://search.google.com/local/writereview?placeid={settings.brio_place_id}"


def review_stats(force: bool = False) -> dict[str, Any]:
    """{rating, count, write_url, maps_uri, ok, error?}. Cached 6h."""
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]

    base = {"write_url": write_review_url(), "rating": None, "count": None, "maps_uri": None, "ok": False}
    if not settings.google_places_api_key:
        return {**base, "error": "Places API key not configured."}

    try:
        r = httpx.get(
            f"https://places.googleapis.com/v1/places/{settings.brio_place_id}",
            headers={
                "X-Goog-Api-Key": settings.google_places_api_key,
                "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,googleMapsUri",
            },
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        data = {
            "write_url": write_review_url(),
            "rating": d.get("rating"),
            "count": d.get("userRatingCount"),
            "maps_uri": d.get("googleMapsUri"),
            "ok": True,
        }
        _CACHE["data"] = data
        _CACHE["at"] = now
        return data
    except Exception as e:  # noqa: BLE001 — surface a soft error, never 500 the dashboard
        return {**base, "error": f"Couldn't reach Google: {e}"[:200]}


# ── AI-drafted review request ────────────────────────────────────────────────
_SYSTEM = """You draft short, warm, PERSONAL messages that a small Vancouver construction \
company (BRIO Construction) sends to a client who was happy with their work, gently asking \
if they'd leave a Google review.

Hard rules:
- Write it as if from the owner (first person, warm, humble, genuine — a real person, not marketing).
- NEVER write the review for them or put words in their mouth. Instead, briefly SUGGEST 1–2 things \
they might mention if they choose to — drawn from the job note.
- Make it easy and no-pressure ("no worries at all if you're busy").
- Say a review link will follow (the sender pastes it) — do NOT invent a URL.
- Keep it short enough for a text message: 2–4 sentences. No subject line, no signature block, \
no placeholders like [Name] — use the recipient's actual first name.
- Plain language. No emojis unless it feels natural (at most one)."""


def draft_message(recipient_name: str, person_type: str, note: str | None, variation: int = 0) -> str:
    first = (recipient_name or "there").strip().split()[0]
    who = "a trade partner / subcontractor we worked with" if person_type == "trade" else "a client we did work for"
    tone = (
        "Tone: peer-to-peer, professional, appreciative of the working relationship."
        if person_type == "trade"
        else "Tone: warm and grateful, like thanking a neighbour."
    )
    vary = "" if variation == 0 else f"\n\nThis is an ALTERNATE version #{variation} — noticeably different wording/opening from a typical draft."
    user = (
        f"Recipient first name: {first}\n"
        f"They are: {who}\n"
        f"Note about the job / relationship: {note or '(none provided)'}\n\n"
        f"{tone}\n"
        f"Write the message.{vary}"
    )
    res = call_anthropic(model=MODEL_SONNET, system=_SYSTEM, messages=[{"role": "user", "content": user}], max_tokens=400)
    return res.text.strip()
