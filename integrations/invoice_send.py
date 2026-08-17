"""Send customer invoices by email (PDF + pay link) or SMS."""

import re
from datetime import datetime
from typing import Any, Dict, Tuple

import automation
import config
import database as db
import invoice
import invoice_numbering
from integrations import email_send, invoice_pdf, sms, stripe as stripe_service

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COMPANY_EMAILS = frozenset({"info@japaneseremovals.com.au"})


def normalize_phone_digits(phone: str) -> str:
    """Return digits only, normalising leading +61 to 0."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("61") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def is_company_placeholder_email(email: str) -> bool:
    """True when email is the company default, not a real customer address."""
    text = (email or "").strip().lower()
    if not text:
        return False
    if text in {e.lower() for e in COMPANY_EMAILS}:
        return True
    settings = _settings()
    for key in ("default_email", "company_email"):
        default = (settings.get(key) or "").strip().lower()
        if default and text == default:
            return True
    return False


def is_valid_customer_email(email: str) -> bool:
    text = (email or "").strip()
    if not is_valid_email_format(text):
        return False
    return not is_company_placeholder_email(text)


def is_company_placeholder_phone(phone: str) -> bool:
    digits = normalize_phone_digits(phone)
    if not digits:
        return True
    company_numbers = set()
    settings = _settings()
    for key in ("default_phone", "company_phone"):
        value = (settings.get(key) or "").strip()
        if value:
            company_numbers.add(normalize_phone_digits(value))
    company_phone = (config.COMPANY_PHONE or "").strip()
    if company_phone:
        company_numbers.add(normalize_phone_digits(company_phone))
    return digits in company_numbers


def is_valid_customer_phone(phone: str) -> bool:
    text = (phone or "").strip()
    if not text:
        return False
    if is_company_placeholder_phone(text):
        return False
    digits = normalize_phone_digits(text)
    return len(digits) >= 9


def format_phone_display(phone: str) -> str:
    """Australian mobile display e.g. 0412 345 678."""
    digits = normalize_phone_digits(phone)
    if len(digits) == 10 and digits.startswith("0"):
        return "{0} {1} {2}".format(digits[:4], digits[4:7], digits[7:])
    return (phone or "").strip()


def _settings() -> Dict[str, Any]:
    from integrations import company_config

    return company_config.get_settings()


def resolve_send_destination(booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    Choose invoice delivery method from booking contact fields.
    Priority: valid customer email, else valid customer phone, else blocked.
    """
    email = (booking.get("email") or "").strip()
    phone = (booking.get("phone") or "").strip()

    if is_valid_customer_email(email):
        return {
            "can_send": True,
            "method": "email",
            "destination": email,
            "destination_display": email,
            "blocked_reason": "",
        }

    if is_valid_customer_phone(phone):
        display = format_phone_display(phone)
        return {
            "can_send": True,
            "method": "sms",
            "destination": phone,
            "destination_display": display,
            "blocked_reason": "",
        }

    if not email and not phone:
        blocked_reason = "Customer email or phone number required."
    elif email and phone:
        blocked_reason = (
            "Enter a valid customer email or mobile number (not company defaults)."
        )
    elif email:
        blocked_reason = "Enter a valid customer email, or add a customer mobile number."
    elif phone:
        blocked_reason = "Enter a valid customer mobile number, or add a customer email."
    else:
        blocked_reason = "Customer email or phone number required."

    return {
        "can_send": False,
        "method": "",
        "destination": "",
        "destination_display": "",
        "blocked_reason": blocked_reason,
    }


def _email_body(
    booking: Dict[str, Any],
    totals: Dict[str, Any],
    pay_url: str,
) -> Tuple[str, str]:
    customer = (booking.get("customer_name") or "Customer").strip()
    invoice_number = invoice_numbering.display_invoice_number(booking)
    if invoice_number == "—":
        invoice_number = "DRAFT"
    booking_id = int(booking["id"])
    total_display = invoice.format_aud(totals["total"])

    subject = "Invoice #{0} — Japanese Removals".format(invoice_number)
    lines = [
        "Dear {0},".format(customer),
        "",
        "Please find your invoice attached for booking #{0}.".format(booking_id),
        "",
        "Invoice total: {0}".format(total_display),
    ]
    if pay_url and stripe_service.payment_options_for_booking(booking)["card_payments_visible"]:
        lines.extend(
            [
                "",
                "Pay by credit card (includes card processing fee):",
                pay_url,
            ]
        )
    lines.extend(
        [
            "",
            "Bank transfer details are on the attached PDF.",
            "",
            "Thank you,",
            config.COMPANY_NAME,
        ]
    )
    if config.COMPANY_PHONE:
        lines.append(config.COMPANY_PHONE)
    return subject, "\n".join(lines)


def _sms_body(
    booking: Dict[str, Any],
    totals: Dict[str, Any],
    pay_url: str,
) -> str:
    invoice_number = invoice_numbering.display_invoice_number(booking)
    if invoice_number == "—":
        invoice_number = "DRAFT"
    total_display = invoice.format_aud(totals["total"])
    parts = [
        "{0}: Your invoice #{1} is ready.".format(config.COMPANY_NAME, invoice_number),
        "Total {0}.".format(total_display),
    ]
    if pay_url and stripe_service.payment_options_for_booking(booking)["card_payments_visible"]:
        parts.append("Pay now: {0}".format(pay_url))
    return " ".join(parts)


def _log_send(
    booking_id: int,
    destination: str,
    method: str,
    invoice_number: str,
    ok: bool,
    detail: str,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = (
        "method={0} destination={1} invoice={2} at={3} — {4}"
    ).format(method, destination, invoice_number or "DRAFT", timestamp, detail)
    automation.log_event(
        automation.AUTOMATION_INVOICE_SEND,
        automation.STATUS_SUCCESS if ok else automation.STATUS_FAILED,
        message,
        booking_id=booking_id,
    )
    if ok:
        db.update_booking_integration_fields(
            booking_id,
            {
                "invoice_sent_at": timestamp,
                "invoice_sent_to": destination,
                "invoice_sent_method": method,
            },
        )


def send_customer_invoice(booking_id: int) -> Tuple[bool, str, str]:
    """Send invoice PDF by email or pay link by SMS. Returns (ok, message, method)."""
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found.", ""

    from services import booking_to_dict

    booking = booking_to_dict(row)
    dest = resolve_send_destination(booking)
    if not dest["can_send"]:
        return False, dest["blocked_reason"], ""

    resolved = invoice.resolve_booking_invoice(booking)
    totals = invoice.calculate_invoice_totals(resolved)
    pay_url = stripe_service.ensure_customer_payment_link(booking_id) or ""
    invoice_number = (booking.get("invoice_number") or "").strip()
    method = dest["method"]
    destination = dest["destination"]

    if method == "email":
        if not email_send.is_configured():
            return False, "Email not configured — add SMTP settings to .env.", method
        try:
            pdf_bytes = invoice_pdf.generate_invoice_pdf(resolved)
        except Exception as exc:
            _log_send(booking_id, destination, method, invoice_number, False, str(exc))
            return False, "Could not generate invoice PDF: {0}".format(exc), method

        filename = "Invoice-{0}.pdf".format(
            invoice_number.replace("/", "-") if invoice_number else booking_id
        )
        subject, body = _email_body(booking, totals, pay_url)
        ok, msg = email_send.send_email_with_attachment(
            destination,
            subject,
            body,
            pdf_bytes,
            filename,
        )
        _log_send(booking_id, destination, method, invoice_number, ok, msg)
        if ok:
            return True, "✓ Invoice sent successfully.", method
        return False, msg, method

    if method == "sms":
        if not sms.is_configured():
            return False, "SMS not configured — add Twilio settings to .env.", method
        body = _sms_body(booking, totals, pay_url)
        ok, msg, _sid = sms.send_message(
            booking,
            body,
            automation_type=automation.AUTOMATION_INVOICE_SEND,
            template_key="invoice_send",
        )
        _log_send(booking_id, destination, method, invoice_number, ok, msg)
        if ok:
            return True, "✓ SMS sent successfully.", method
        return False, msg, method

    return False, dest["blocked_reason"], ""
