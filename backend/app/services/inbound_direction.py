"""Who an email filed by the project address is really from.

Mail reaches the project address two ways, and both arrive FROM a BRIO
address (#5, found 2026-10): Amir BCCs it on mail he sends, or forwards
mail he received. Recording every one as inbound from Amir made the
Conversation read as though the client wrote what he sent, and hid who
actually wrote what he forwarded.

  * from a BRIO address, with a forwarded block → the original sender, inbound
    (or outbound to the original recipients if BRIO wrote the original too);
  * from a BRIO address, no forwarded block → BRIO's own mail (a BCC copy),
    outbound, to its real recipients;
  * anyone else → inbound, as sent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BRIO_DOMAINS = ("builtbybrio.com", "sister-co.example")

# Gmail / Outlook / Apple Mail forwarded-message markers.
_FWD = re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}|^Begin forwarded message:|^-{2,}\s*Original Message\s*-{2,}",
                  re.I | re.M)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass
class Sender:
    direction: str
    from_name: str | None
    from_email: str | None
    to_emails: str | None
    subject: str | None


def is_brio(email: str | None, office_emails: set[str] | frozenset[str] = frozenset()) -> bool:
    e = (email or "").strip().lower()
    return bool(e) and (e.split("@")[-1] in BRIO_DOMAINS or e in office_emails)


def _field(block: str, name: str) -> str | None:
    m = re.search(rf"^\s*\*?{name}:\*?\s*(.+)$", block, re.I | re.M)
    return m.group(1).strip() if m else None


def _strip_fwd(subject: str | None) -> str | None:
    return re.sub(r"^\s*((fwd?|fw)\s*:\s*)+", "", subject or "", flags=re.I) or subject


def _without(addresses: str | None, drop: str | None) -> str | None:
    found = [e for e in _EMAIL.findall(addresses or "") if not drop or drop.lower() not in e.lower()]
    return ", ".join(found) or None


def resolve(*, from_email: str | None, from_name: str | None, to_raw: str | None, subject: str | None,
            body: str, project_address_token: str | None = None,
            office_emails: set[str] | frozenset[str] = frozenset()) -> Sender:
    if not is_brio(from_email, office_emails):
        return Sender("inbound", from_name, from_email, to_raw, subject)
    m = _FWD.search(body or "")
    if m:
        block = body[m.end():m.end() + 2000]
        frm = _field(block, "From")
        orig_email = (_EMAIL.search(frm or "") or [None])[0] if frm else None
        orig_name = re.sub(r"\s*<[^>]*>\s*$", "", frm or "").strip().strip('"') or None
        if orig_email and not is_brio(orig_email, office_emails):
            return Sender("inbound", orig_name, orig_email.lower(), from_email, _strip_fwd(subject))
        if orig_email:  # forwarding BRIO's own sent mail
            return Sender("outbound", orig_name, orig_email.lower(), _field(block, "To"), _strip_fwd(subject))
    # A BCC copy of mail BRIO sent: its real recipients, not the project address.
    return Sender("outbound", from_name, from_email, _without(to_raw, project_address_token), subject)
