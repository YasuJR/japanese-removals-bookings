"""Transactional email via SMTP."""

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

import config


def is_configured() -> bool:
    return bool(
        config.EMAIL_ENABLED
        and config.SMTP_HOST
        and config.EMAIL_FROM
    )


def send_email(
    to_address: str,
    subject: str,
    body_text: str,
) -> Tuple[bool, str]:
    if not is_configured():
        return False, "Email not configured — add SMTP settings to .env"

    to_address = (to_address or "").strip()
    if not to_address:
        return False, "No email address provided."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to_address
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_USE_TLS:
                server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, [to_address], msg.as_string())
        return True, "Email sent to {0}.".format(to_address)
    except Exception as exc:
        return False, "Email failed: {0}".format(exc)


def send_email_with_attachment(
    to_address: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes,
    attachment_filename: str,
) -> Tuple[bool, str]:
    if not is_configured():
        return False, "Email not configured — add SMTP settings to .env"

    to_address = (to_address or "").strip()
    if not to_address:
        return False, "No email address provided."

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to_address

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(alt)

    part = MIMEApplication(attachment_bytes, _subtype="pdf")
    part.add_header(
        "Content-Disposition",
        "attachment",
        filename=attachment_filename,
    )
    msg.attach(part)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_USE_TLS:
                server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, [to_address], msg.as_string())
        return True, "Email sent to {0}.".format(to_address)
    except Exception as exc:
        return False, "Email failed: {0}".format(exc)
