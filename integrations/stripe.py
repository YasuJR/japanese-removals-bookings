"""Stripe Checkout — card payments with customer surcharge."""

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import automation
import booking_profit
import config
import database as db
import invoice
from integrations import sms_automation, stripe_config, xero

COMPLIANCE_NOTE = "Card surcharge reflects payment processing costs."


def _stripe_value(obj: Any, key: str, default: Any = None) -> Any:
    if not obj:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        val = obj[key]
    except (KeyError, TypeError):
        val = getattr(obj, key, default)
    return default if val is None else val


def calculate_card_payment(
    base_total: float,
    surcharge_percent: Optional[float] = None,
) -> Dict[str, Any]:
    """Surcharge on GST-inclusive invoice total (no extra GST)."""
    pct = (
        float(surcharge_percent)
        if surcharge_percent is not None
        else stripe_config.surcharge_percent()
    )
    base = round(float(base_total), 2)
    surcharge = round(base * pct / 100.0, 2)
    card_total = round(base + surcharge, 2)
    return {
        "base_total": base,
        "surcharge_percent": pct,
        "surcharge_amount": surcharge,
        "card_total": card_total,
    }


def payment_options_for_booking(
    booking: Dict[str, Any],
    base_total: Optional[float] = None,
) -> Dict[str, Any]:
    resolved = invoice.resolve_booking_invoice(booking)
    totals = invoice.calculate_invoice_totals(resolved)
    base = base_total if base_total is not None else totals["total"]
    calc = calculate_card_payment(base)
    pct = calc["surcharge_percent"]
    pay_now_url = customer_payment_url(booking)
    stripe_ready = stripe_config.is_ready()
    unpaid = (booking.get("payment_status") or "").strip() != invoice.PAYMENT_STATUS_PAID
    return {
        "bank_total": calc["base_total"],
        "bank_total_display": invoice.format_aud(calc["base_total"]),
        "card_total": calc["card_total"],
        "card_total_display": invoice.format_aud(calc["card_total"]),
        "surcharge_amount": calc["surcharge_amount"],
        "surcharge_display": invoice.format_aud(calc["surcharge_amount"]),
        "surcharge_percent": pct,
        "surcharge_percent_display": "{0:.1f}".format(pct).rstrip("0").rstrip("."),
        "stripe_enabled": stripe_ready,
        "compliance_note": COMPLIANCE_NOTE,
        "can_checkout": stripe_ready and unpaid,
        "pay_now_url": pay_now_url,
        "can_pay_now": bool(pay_now_url) and stripe_ready and unpaid,
    }


def customer_payment_url(booking: Dict[str, Any]) -> str:
    token = (booking.get("payment_token") or "").strip()
    if not token:
        return ""
    return config.oauth_url("/pay/{0}".format(token))


def ensure_customer_payment_link(booking_id: int) -> str:
    token = db.ensure_payment_token(booking_id)
    if not token:
        return ""
    return config.oauth_url("/pay/{0}".format(token))


def is_configured() -> bool:
    return stripe_config.has_credentials()


def is_ready() -> bool:
    return stripe_config.is_ready()


def _stripe_client():
    import stripe

    stripe.api_key = stripe_config.get_secret_key()
    return stripe


def create_checkout_session(
    booking: Dict[str, Any],
    *,
    success_url: str,
    cancel_url: str,
    require_email: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    if not is_ready():
        return False, "Stripe is not enabled — configure keys in Settings → Stripe.", None

    email = (booking.get("email") or "").strip()
    if require_email and not email:
        return False, "Add customer email before card payment.", None

    if (booking.get("payment_status") or "").strip() == invoice.PAYMENT_STATUS_PAID:
        return False, "This invoice is already paid.", None

    totals = invoice.calculate_invoice_totals(
        invoice.resolve_booking_invoice(booking)
    )
    calc = calculate_card_payment(totals["total"])
    invoice_number = (booking.get("invoice_number") or "").strip() or "DRAFT"
    booking_id = int(booking["id"])
    amount_cents = int(round(calc["card_total"] * 100))

    if amount_cents < 50:
        return False, "Invoice total is too low for card payment.", None

    description = "Japanese Removals Invoice #{0}".format(invoice_number)
    pct_label = "{0:.1f}".format(calc["surcharge_percent"]).rstrip("0").rstrip(".")

    try:
        stripe = _stripe_client()
        session_kwargs = {
            "mode": "payment",
            "line_items": [
                {
                    "price_data": {
                        "currency": "aud",
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": description,
                            "description": (
                                "Invoice total incl. GST plus {0}% card processing fee"
                            ).format(pct_label),
                        },
                    },
                    "quantity": 1,
                }
            ],
            "metadata": {
                "booking_id": str(booking_id),
                "invoice_number": invoice_number,
                "base_total": str(calc["base_total"]),
                "surcharge_amount": str(calc["surcharge_amount"]),
                "card_total": str(calc["card_total"]),
            },
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if email:
            session_kwargs["customer_email"] = email
        session = stripe.checkout.Session.create(**session_kwargs)
    except Exception as exc:
        automation.log_event(
            automation.AUTOMATION_STRIPE_CHECKOUT,
            automation.STATUS_ERROR,
            str(exc),
            booking_id,
        )
        return False, "Stripe checkout failed: {0}".format(exc), None

    session_id = _stripe_value(session, "id", "") or ""
    checkout_url = _stripe_value(session, "url", "") or ""
    if not session_id or not checkout_url:
        return False, "Stripe returned an incomplete checkout session.", None

    db.update_booking_invoice_fields(
        booking_id,
        {
            "stripe_checkout_session_id": session_id,
            "stripe_payment_status": "pending",
            "stripe_surcharge_amount": calc["surcharge_amount"],
            "stripe_total_charged": calc["card_total"],
        },
    )
    automation.log_event(
        automation.AUTOMATION_STRIPE_CHECKOUT,
        automation.STATUS_SUCCESS,
        "Checkout session {0} for {1}".format(
            session_id[:16], invoice.format_aud(calc["card_total"])
        ),
        booking_id,
    )
    return True, "Redirecting to Stripe…", checkout_url


def start_customer_checkout(
    booking: Dict[str, Any],
    *,
    success_url: str,
    cancel_url: str,
) -> Tuple[bool, str, Optional[str]]:
    """Public customer checkout from invoice Pay Now link."""
    return create_checkout_session(
        booking,
        success_url=success_url,
        cancel_url=cancel_url,
        require_email=False,
    )


def handle_webhook_event(payload: bytes, signature: str) -> Tuple[bool, str]:
    if not stripe_config.get_webhook_secret():
        return False, "Webhook secret not configured."

    try:
        stripe = _stripe_client()
        event = stripe.Webhook.construct_event(
            payload, signature, stripe_config.get_webhook_secret()
        )
    except ValueError:
        return False, "Invalid webhook payload."
    except Exception as exc:
        return False, "Webhook signature verification failed: {0}".format(exc)

    event_type = _stripe_value(event, "type", "") or ""
    if event_type == "checkout.session.completed":
        data = _stripe_value(event, "data", {}) or {}
        session = _stripe_value(data, "object", {}) or {}
        return _handle_checkout_completed(session)
    return True, "Ignored event: {0}".format(event_type)


def _log_stripe_payment_received(
    booking_id: int,
    message: str,
    *,
    duplicate: bool = False,
) -> None:
    automation.log_event(
        automation.AUTOMATION_STRIPE_PAYMENT_RECEIVED,
        automation.STATUS_PARTIAL if duplicate else automation.STATUS_SUCCESS,
        message,
        booking_id,
    )


def _is_duplicate_stripe_payment(
    booking: Dict[str, Any],
    session_id: str,
    payment_intent_id: str,
) -> bool:
    if (booking.get("payment_status") or "").strip() != invoice.PAYMENT_STATUS_PAID:
        return False
    if (booking.get("stripe_payment_status") or "").strip() != "paid":
        return False
    existing_intent = (booking.get("stripe_payment_intent_id") or "").strip()
    existing_session = (booking.get("stripe_checkout_session_id") or "").strip()
    if payment_intent_id and existing_intent == payment_intent_id:
        return True
    if session_id and existing_session == session_id:
        return True
    return False


def _apply_local_stripe_payment(
    booking_id: int,
    *,
    paid_at: str,
    session_id: str,
    payment_intent_id: str,
    surcharge: Optional[float],
    card_total: Optional[float],
) -> None:
    db.update_booking_invoice_fields(
        booking_id,
        {
            "payment_status": invoice.PAYMENT_STATUS_PAID,
            "invoice_status": "PAID",
            "paid_at": paid_at,
            "stripe_checkout_session_id": session_id,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_payment_status": "paid",
            "stripe_surcharge_amount": surcharge,
            "stripe_total_charged": card_total,
        },
    )
    db.update_booking_status(booking_id, "Paid")
    booking_profit.recalculate_and_save(booking_id)


def _handle_checkout_completed(session: Any) -> Tuple[bool, str]:
    session_id = str(_stripe_value(session, "id", "") or "").strip()
    metadata = _stripe_value(session, "metadata", {}) or {}
    booking_id_raw = str(_stripe_value(metadata, "booking_id", "") or "").strip()
    if not booking_id_raw.isdigit():
        row = db.get_booking_by_stripe_session(session_id)
        if not row:
            return False, "Checkout session missing booking_id."
        booking_id = int(row["id"])
    else:
        booking_id = int(booking_id_raw)

    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking #{0} not found.".format(booking_id)

    booking = dict(row)
    payment_intent_id = _stripe_value(session, "payment_intent", "") or ""
    if isinstance(payment_intent_id, dict):
        payment_intent_id = _stripe_value(payment_intent_id, "id", "") or ""
    else:
        payment_intent_id = str(payment_intent_id).strip()

    base_total = _metadata_amount(metadata, "base_total", booking)
    paid_at = (booking.get("paid_at") or "").strip()

    if _is_duplicate_stripe_payment(booking, session_id, payment_intent_id):
        _log_stripe_payment_received(
            booking_id,
            "Duplicate Stripe payment ignored — booking already marked Paid.",
            duplicate=True,
        )
        _run_post_payment_automation(
            booking_id,
            booking,
            payment_intent_id=payment_intent_id,
            base_total=base_total,
            paid_at=paid_at,
        )
        return True, "Payment already recorded."

    paid_at = _stripe_timestamp(_stripe_value(session, "created"))
    surcharge = _metadata_amount(metadata, "surcharge_amount", booking)
    card_total = _metadata_amount(metadata, "card_total", booking)
    if card_total is None:
        amount_total = _stripe_value(session, "amount_total")
        if amount_total is not None:
            card_total = round(float(amount_total) / 100.0, 2)
    if base_total is None and card_total is not None and surcharge is not None:
        base_total = round(card_total - surcharge, 2)

    _apply_local_stripe_payment(
        booking_id,
        paid_at=paid_at,
        session_id=session_id,
        payment_intent_id=payment_intent_id,
        surcharge=surcharge,
        card_total=card_total,
    )

    invoice_number = (booking.get("invoice_number") or "").strip() or "invoice"
    _log_stripe_payment_received(
        booking_id,
        "Stripe payment received for {0} — booking marked Paid.".format(invoice_number),
    )

    row = db.get_booking(booking_id)
    booking = dict(row) if row else booking
    _run_post_payment_automation(
        booking_id,
        booking,
        payment_intent_id=payment_intent_id,
        base_total=base_total,
        paid_at=paid_at,
    )
    return True, "Payment recorded for booking #{0}.".format(booking_id)


def _run_post_payment_automation(
    booking_id: int,
    booking: Dict[str, Any],
    *,
    payment_intent_id: str,
    base_total: Optional[float],
    paid_at: str,
) -> None:
    _post_xero_payment_after_stripe(
        booking_id,
        booking,
        payment_intent_id=payment_intent_id,
        base_total=base_total,
        paid_at=paid_at,
    )
    row = db.get_booking(booking_id)
    if row:
        sms_automation.maybe_send_payment_confirmation(dict(row))


def _post_xero_payment_after_stripe(
    booking_id: int,
    booking: Dict[str, Any],
    *,
    payment_intent_id: str,
    base_total: Optional[float],
    paid_at: str,
) -> None:
    existing_xero_payment = (booking.get("xero_payment_id") or "").strip()
    if existing_xero_payment:
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_SUCCESS,
            "Xero payment already posted ({0}).".format(existing_xero_payment[:8]),
            booking_id,
        )
        try:
            row = db.get_booking(booking_id)
            if row:
                xero.sync_invoice_after_stripe_payment(dict(row))
        except Exception:
            pass
        return

    if not xero.is_ready():
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            "Xero not connected — local booking marked Paid; payment not posted to Xero.",
            booking_id,
        )
        return

    ok, ensure_msg, _inv = xero.ensure_invoice_ready_for_stripe_payment(booking)
    if not ok:
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            ensure_msg,
            booking_id,
        )
        return

    row = db.get_booking(booking_id)
    if not row:
        return
    booking = dict(row)
    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if not xero.is_real_invoice_id(invoice_id):
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            "Could not link a Xero invoice for payment posting.",
            booking_id,
        )
        return

    if not xero.payments_scope_granted():
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            "Xero payment scope missing — open Settings → Xero and click Connect Xero again "
            "to grant accounting.payments.",
            booking_id,
        )
        return

    account_code = stripe_config.xero_payment_account_code()
    if not account_code:
        account_code = xero.default_bank_account_code()
    if not account_code:
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_PARTIAL,
            "Stripe payment recorded locally. No Xero payment account code configured "
            "— add one in Settings → Stripe to post payments to Xero automatically.",
            booking_id,
        )
        return

    if base_total is None:
        totals = invoice.calculate_invoice_totals(
            invoice.resolve_booking_invoice(booking)
        )
        base_total = totals["total"]

    reference = "Stripe {0}".format((payment_intent_id or "payment")[:32])
    try:
        ok, msg, xero_payment_id = xero.post_stripe_payment_to_xero(
            booking,
            amount=base_total,
            account_code=account_code,
            reference=reference,
            payment_date=(paid_at or "")[:10],
        )
    except Exception as exc:
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            "Xero payment failed: {0}".format(exc),
            booking_id,
        )
        return

    if ok and xero_payment_id:
        db.update_booking_invoice_fields(
            booking_id, {"xero_payment_id": xero_payment_id}
        )
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_SUCCESS,
            msg,
            booking_id,
        )
        try:
            row = db.get_booking(booking_id)
            if row:
                sync_ok, sync_msg = xero.sync_invoice_after_stripe_payment(dict(row))
                automation.log_event(
                    automation.AUTOMATION_XERO_STRIPE_PAYMENT,
                    automation.STATUS_SUCCESS if sync_ok else automation.STATUS_PARTIAL,
                    sync_msg,
                    booking_id,
                )
        except Exception as exc:
            automation.log_event(
                automation.AUTOMATION_XERO_STRIPE_PAYMENT,
                automation.STATUS_PARTIAL,
                "Xero status sync after payment: {0}".format(exc),
                booking_id,
            )
    else:
        automation.log_event(
            automation.AUTOMATION_XERO_STRIPE_PAYMENT,
            automation.STATUS_ERROR,
            msg,
            booking_id,
        )


def _metadata_amount(
    metadata: Dict[str, Any],
    key: str,
    booking: Dict[str, Any],
) -> Optional[float]:
    raw = _stripe_value(metadata, key)
    if raw not in (None, ""):
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            pass
    stored = booking.get(
        "stripe_surcharge_amount" if key == "surcharge_amount" else "stripe_total_charged"
    )
    if stored not in (None, ""):
        try:
            return round(float(stored), 2)
        except (TypeError, ValueError):
            pass
    return None


def _stripe_timestamp(value: Any) -> str:
    try:
        ts = int(value)
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
