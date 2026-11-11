"""SES SMTP wrapper. Used by backend for outbound notifications.

NextAuth handles the magic-link send itself via nodemailer; this module
covers backend-originated emails (e.g. invoice issued, with the PDF attached).
"""

import base64
import hashlib
import hmac
import smtplib
from email.message import EmailMessage

from app.config import settings


def _ses_smtp_password(secret_key: str, region: str) -> str:
    """Derive the SES SMTP password from an IAM secret access key.

    SES SMTP does NOT accept the raw 40-char IAM secret as the password — it
    requires a password derived via the AWS Signature v4 key-derivation chain
    (per AWS docs). A raw secret passed to smtp.login() fails with 535. If the
    env already holds a proper (non-40-char) SMTP password, use it verbatim.
    """
    if len(secret_key) != 40:  # already an SES SMTP password (or unknown) — use as-is
        return secret_key
    date, service, terminal, message, version = "11111111", "ses", "aws4_request", "SendRawEmail", 0x04

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    sig = _sign(("AWS4" + secret_key).encode("utf-8"), date)
    sig = _sign(sig, region)
    sig = _sign(sig, service)
    sig = _sign(sig, terminal)
    sig = _sign(sig, message)
    return base64.b64encode(bytes([version]) + sig).decode("utf-8")


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    attachment: tuple[str, bytes, str] | None = None,
) -> None:
    """Send an email via SES SMTP.

    `attachment`, when given, is `(filename, data, mime_type)` — e.g.
    `("Invoice.pdf", pdf_bytes, "application/pdf")`.
    """
    msg = EmailMessage()
    msg["From"] = settings.aws_ses_from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if attachment is not None:
        filename, data, mime_type = attachment
        maintype, _, subtype = mime_type.partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )

    deliver(msg)


def deliver(msg: EmailMessage, recipients: list[str] | None = None) -> None:
    """Hand a built message to SES. `recipients` overrides the header list, which
    is how a BCC is sent: it is a recipient, never a header."""
    host = f"email-smtp.{settings.aws_ses_region}.amazonaws.com"
    with smtplib.SMTP(host, 587) as smtp:
        smtp.starttls()
        smtp.login(
            settings.aws_ses_access_key_id,
            _ses_smtp_password(settings.aws_ses_secret_access_key, settings.aws_ses_region),
        )
        smtp.send_message(msg, to_addrs=recipients)
