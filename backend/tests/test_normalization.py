"""Confirms the four normalization helpers in app.services.clients match the
JS reference at brio-admin/lib/registry.mjs byte-for-byte for the inputs we
care about (the 6 seed contacts plus a few hand-picked edge cases).

If any of these break, the importer will create duplicate contacts.
"""

from __future__ import annotations

from app.services.clients import (
    normalize_address,
    normalize_email,
    normalize_name,
    normalize_phone,
)


# ── normalize_phone ──────────────────────────────────────────────────────


def test_phone_strips_dashes():
    assert normalize_phone("604-555-0181") == "6045550181"


def test_phone_strips_parens_and_spaces():
    assert normalize_phone("(604) 555-0193") == "6045550193"


def test_phone_handles_intl_prefix():
    # JS regex /\D/g strips everything non-digit, so "+1" becomes "1"
    assert normalize_phone("+1 604 555 0123") == "16045550123"


def test_phone_empty_inputs():
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""


# ── normalize_email ──────────────────────────────────────────────────────


def test_email_lowers_and_trims():
    assert normalize_email("Foo@Bar.COM") == "foo@bar.com"
    assert normalize_email("  Client@mail.example  ") == "client@mail.example"


def test_email_empty_inputs():
    assert normalize_email("") == ""
    assert normalize_email(None) == ""


# ── normalize_name ───────────────────────────────────────────────────────


def test_name_collapses_whitespace():
    assert normalize_name("  Sample  Client ") == "sample client"


def test_name_preserves_punctuation():
    # JS reference doesn't touch &/, ./. — keep the exact punctuation
    assert normalize_name("Mr. Doe & Mrs. Roe") == "mr. doe & mrs. roe"


def test_name_empty_inputs():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


# ── normalize_address ────────────────────────────────────────────────────


def test_address_strips_unit_word_and_punct():
    assert (
        normalize_address("Unit #1201 - 1500 Birch St, Vancouver BC")
        == "1201 - 1500 birch st vancouver bc"
    )


def test_address_handles_periods():
    assert (
        normalize_address("456 Harbour Drive, North Vancouver BC V7M 1H3")
        == "456 harbour drive north vancouver bc v7m 1h3"
    )


def test_address_empty_inputs():
    assert normalize_address("") == ""
    assert normalize_address(None) == ""


# ── seed-contact parity check ────────────────────────────────────────────


def test_seed_contacts_match_brio_admin_normalized_forms():
    """Confirms the Python normalizers produce the same normalized strings as
    the JS reference produced when it built the existing _index.json
    contacts[]."""

    pairs = [
        # (raw, expected_normalized)
        ("604-555-0181", "6045550181"),
        ("604-555-0123", "6045550123"),
        ("604-555-0187", "6045550187"),
        ("604-555-0171", "6045550171"),
        ("604-555-0193", "6045550193"),
    ]
    for raw, expected in pairs:
        assert normalize_phone(raw) == expected, raw

    email_pairs = [
        ("client@mail.example", "client@mail.example"),
        ("smith@email.com", "smith@email.com"),
        ("office@samplerealty.example", "office@samplerealty.example"),
        ("alex.example@mail.example", "alex.example@mail.example"),
    ]
    for raw, expected in email_pairs:
        assert normalize_email(raw) == expected, raw

    name_pairs = [
        ("Mr. Doe & Mrs. Roe", "mr. doe & mrs. roe"),
        ("Sample Client", "sample client"),
        ("John & Sarah Smith", "john & sarah smith"),
        ("Pat Office", "pat office"),
        ("Chris M", "chris m"),
        ("Alex Example", "alex example"),
    ]
    for raw, expected in name_pairs:
        assert normalize_name(raw) == expected, raw
