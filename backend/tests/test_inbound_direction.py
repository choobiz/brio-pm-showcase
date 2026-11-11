"""Email filed by the project address: who really wrote it (#5).

Before: every email arriving at the project address was recorded as inbound
from whoever sent it to us — so Amir's BCC of his own mail read as the client
writing to us, and a forward of the client's email read as Amir writing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.inbound_direction import resolve  # noqa: E402

GMAIL_FWD = """See below, client wants a window.

---------- Forwarded message ---------
From: Ben Lee <ben@lee.example>
Date: Mon, Oct 12, 2026 at 9:14 AM
Subject: Window in the office
To: Amir Omidvar <amir@builtbybrio.com>

Can we add a window on the east wall?
"""


def test_a_forward_of_the_clients_email_is_from_the_client():
    s = resolve(from_email="amir@builtbybrio.com", from_name="Amir Omidvar", to_raw="p-abc123@in.example",
                subject="Fwd: Window in the office", body=GMAIL_FWD, project_address_token="abc123")
    assert (s.direction, s.from_name, s.from_email, s.subject) == ("inbound", "Ben Lee", "ben@lee.example",
                                                                  "Window in the office")


def test_a_bcc_of_our_own_mail_is_outbound_to_its_real_recipients():
    s = resolve(from_email="amir@builtbybrio.com", from_name="Amir", subject="[KS-M16] shop drawing",
                to_raw="raj@geo.example, p-abc123@in.example", body="Please sign the attached.",
                project_address_token="abc123")
    assert (s.direction, s.to_emails) == ("outbound", "raj@geo.example")


def test_forwarding_our_own_sent_mail_is_outbound():
    body = "FYI\n\nBegin forwarded message:\n\nFrom: Amir Omidvar <amir@sister-co.example>\nTo: ben@lee.example\n\nquote attached"
    s = resolve(from_email="amir@sister-co.example", from_name="Amir", to_raw="p-abc123@in.example",
                subject="Fwd: quote", body=body, project_address_token="abc123")
    assert (s.direction, s.to_emails) == ("outbound", "ben@lee.example")


def test_an_office_user_on_another_domain_counts_as_brio():
    s = resolve(from_email="office@mail.example", from_name="Pat", to_raw="p-abc123@in.example, x@y.example",
                subject="note", body="sent", project_address_token="abc123", office_emails={"office@mail.example"})
    assert s.direction == "outbound"


def test_mail_from_anyone_else_is_inbound_as_sent():
    s = resolve(from_email="rentals@forms.example", from_name="Forms", to_raw="p-abc123@in.example",
                subject="Delivery", body="---------- Forwarded message ---------\nFrom: Bob <bob@x.example>")
    assert (s.direction, s.from_email) == ("inbound", "rentals@forms.example")
