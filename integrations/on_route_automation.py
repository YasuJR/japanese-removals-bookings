"""Phase 10 — On Route departure + ETA SMS automation."""

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import automation
import database as db
import job_status
from integrations import maps_routing, sms_automation, sms_config

ETA_SMS_TEMPLATE = "eta_on_route"


def _parse_manual_eta(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        minutes = int(float(text))
    except (TypeError, ValueError):
        return None
    if minutes <= 0 or minutes > 600:
        return None
    return minutes


def _resolve_eta_minutes(
    booking: Dict[str, Any],
    manual_eta_minutes: Optional[int],
    driver_origin: str,
) -> Tuple[Optional[int], str, str]:
    if manual_eta_minutes is not None:
        return manual_eta_minutes, "manual", "ETA entered manually."

    origin = (driver_origin or "").strip()
    destination = (booking.get("pickup_address") or "").strip()
    computed, source, detail = maps_routing.estimate_driving_minutes(
        origin, destination
    )
    if computed is not None:
        return computed, source, detail
    return None, "manual", detail


def start_on_route(
    booking: Dict[str, Any],
    *,
    driver_name: str,
    manual_eta_minutes: Optional[int] = None,
    driver_origin: str = "",
) -> Tuple[bool, str]:
    """Set status On Route, save departure time, and send ETA SMS once."""
    booking_id = int(booking["id"])
    status = job_status.display(booking)
    if status in ("Cancelled", "Paid"):
        return False, "Cannot start route for a {0} booking.".format(status)

    manual_eta = _parse_manual_eta(manual_eta_minutes)
    eta_minutes, eta_source, eta_detail = _resolve_eta_minutes(
        booking, manual_eta, driver_origin
    )
    if eta_minutes is None:
        return False, (
            "Enter ETA minutes or configure Google Maps. {0}".format(eta_detail)
        )

    driver = (driver_name or "").strip() or "Your driver"
    was_on_route = bool((booking.get("on_route_at") or "").strip())
    now = datetime.utcnow().isoformat(timespec="seconds")

    db.update_booking_on_route_fields(
        booking_id,
        {
            "on_route_at": booking.get("on_route_at") or now,
            "driver_name": driver,
            "eta_minutes": eta_minutes,
        },
    )
    db.update_booking_status(booking_id, "On Route")

    automation.log_event(
        automation.AUTOMATION_ON_ROUTE_STARTED,
        automation.STATUS_SUCCESS,
        "Driver {0} en route — ETA {1} min ({2}).".format(
            driver, eta_minutes, eta_source
        ),
        booking_id,
    )

    row = db.get_booking(booking_id)
    updated = dict(row) if row else dict(booking)
    updated["driver_name"] = driver
    updated["eta_minutes"] = eta_minutes

    if was_on_route and (updated.get("eta_sms_sent_at") or "").strip():
        automation.log_event(
            automation.AUTOMATION_ETA_SMS_SENT,
            automation.STATUS_PARTIAL,
            "ETA SMS already sent.",
            booking_id,
        )
        return True, "Already on route — ETA updated to {0} minutes.".format(
            eta_minutes
        )

    ok, sms_msg = _send_eta_sms(updated, force=False)
    if ok:
        return True, "On route — ETA {0} minutes. {1}".format(eta_minutes, sms_msg)
    return True, "On route — ETA {0} minutes. SMS: {1}".format(eta_minutes, sms_msg)


def resend_eta_sms(
    booking: Dict[str, Any],
    *,
    driver_name: str = "",
    manual_eta_minutes: Optional[int] = None,
    driver_origin: str = "",
) -> Tuple[bool, str]:
    """Send ETA SMS again (manual resend from booking page)."""
    booking_id = int(booking["id"])
    fields: Dict[str, Any] = {}
    manual_eta = _parse_manual_eta(manual_eta_minutes)
    if manual_eta is not None:
        fields["eta_minutes"] = manual_eta
    elif not (booking.get("eta_minutes") or 0):
        computed, _source, detail = _resolve_eta_minutes(
            booking, None, driver_origin
        )
        if computed is None:
            return False, "Enter ETA minutes before resending. {0}".format(detail)
        fields["eta_minutes"] = computed

    if (driver_name or "").strip():
        fields["driver_name"] = driver_name.strip()

    if fields:
        db.update_booking_on_route_fields(booking_id, fields)
        row = db.get_booking(booking_id)
        booking = dict(row) if row else booking
        booking.update(fields)

    return _send_eta_sms(booking, force=True)


def _send_eta_sms(booking: Dict[str, Any], *, force: bool) -> Tuple[bool, str]:
    booking_id = int(booking["id"])
    if not force and (booking.get("eta_sms_sent_at") or "").strip():
        msg = "ETA SMS already sent."
        automation.log_event(
            automation.AUTOMATION_ETA_SMS_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return False, msg

    if not sms_config.is_trigger_enabled(ETA_SMS_TEMPLATE):
        msg = "ETA SMS disabled in Settings → SMS."
        automation.log_event(
            automation.AUTOMATION_ETA_SMS_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return False, msg

    ok, msg = sms_automation.send_template_sms(
        booking,
        ETA_SMS_TEMPLATE,
        force=force,
        automation_log_type=automation.AUTOMATION_ETA_SMS_SENT,
    )
    return ok, msg
