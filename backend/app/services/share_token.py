"""Signed share tokens for the public invoice-view link.

The link itself is the credential — a client opens an invoice with no account.
The token is a stateless HMAC of the invoice id with the app secret, so no DB
column or expiry bookkeeping is needed; it stays valid as long as the invoice
exists and the secret is unchanged.
"""

from __future__ import annotations

import base64
import hmac
from hashlib import sha256

from app.config import settings


def _secret() -> bytes:
    return (settings.nextauth_secret or "brio-pm").encode()


def _sig(invoice_id: str) -> str:
    return hmac.new(_secret(), invoice_id.encode(), sha256).hexdigest()[:24]


def sign_invoice_token(invoice_id: str) -> str:
    """Deterministic, URL-safe share token for an invoice."""
    raw = f"{invoice_id}.{_sig(invoice_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_invoice_token(token: str) -> str | None:
    """Return the invoice id if the token's signature checks out, else None."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    invoice_id, _, sig = raw.partition(".")
    if not invoice_id or not sig:
        return None
    return invoice_id if hmac.compare_digest(sig, _sig(invoice_id)) else None


def sign_presentation_token(presentation_id: str) -> str:
    """Deterministic, URL-safe share token for a presentation (namespaced so it
    can't be confused with an invoice token)."""
    raw = f"{presentation_id}.{_sig('pres:' + presentation_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_presentation_token(token: str) -> str | None:
    """Return the presentation id if the token's signature checks out, else None."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    pid, _, sig = raw.partition(".")
    if not pid or not sig:
        return None
    return pid if hmac.compare_digest(sig, _sig("pres:" + pid)) else None
