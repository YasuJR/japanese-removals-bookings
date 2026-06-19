"""
Google Calendar sync for move bookings.

Setup (one-time):
1. Google Cloud Console → new project → enable Google Calendar API.
2. OAuth consent screen (External, add your Gmail as test user).
3. Credentials → OAuth client ID → Desktop app → download JSON.
4. Save as credentials/google_credentials.json
5. In .env set GOOGLE_CALENDAR_ENABLED=true
6. In the app: Settings → Connect Google Calendar (saves token).
"""

import oauth_local  # noqa: F401 — must run before google_auth_oauthlib

from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import config
import database as db
from booking_helpers import build_calendar_description, calendar_event_summary
from booking_times import event_datetimes
from crew import calendar_color_id, display_crew
from integrations import google_oauth

SCOPES = google_oauth.SCOPES


def is_configured() -> bool:
    return config.GOOGLE_CALENDAR_ENABLED and Path(
        config.GOOGLE_CREDENTIALS_FILE
    ).is_file()


def is_connected() -> bool:
    return is_configured() and Path(config.GOOGLE_TOKEN_FILE).is_file()


def _get_credentials():
    return google_oauth.get_credentials()


def _ensure_oauth_local_http():
    google_oauth._ensure_oauth_local_http()


def _make_flow(redirect_uri: str):
    return google_oauth._make_flow(redirect_uri)


def clear_stored_token() -> None:
    google_oauth.clear_stored_token()


def begin_oauth(redirect_uri: str) -> Tuple[str, str]:
    return google_oauth.begin_oauth(redirect_uri)


def complete_oauth(redirect_uri: str, authorization_response: str) -> bool:
    return google_oauth.complete_oauth(redirect_uri, authorization_response)


def _calendar_service():
    from googleapiclient.discovery import build

    creds = _get_credentials()
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _event_body(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Build calendar event with Perth reminders (day before 6 PM, at start time)."""
    tz = ZoneInfo(config.TIMEZONE)
    move_day = datetime.strptime(booking["move_date"], "%Y-%m-%d").date()
    event_start, event_end = event_datetimes(booking)

    reminder_day_before = datetime.combine(
        move_day - timedelta(days=1), time(18, 0), tzinfo=tz
    )
    minutes_day_before_6pm = int(
        (event_start - reminder_day_before).total_seconds() / 60
    )
    minutes_same_day_7am = 0

    customer = booking["customer_name"]
    description = build_calendar_description(
        booking,
        display_crew=display_crew,
    )

    body = {
        "summary": calendar_event_summary(customer),
        "location": booking["pickup_address"],
        "description": description,
        "start": {
            "dateTime": event_start.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": config.TIMEZONE,
        },
        "end": {
            "dateTime": event_end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": config.TIMEZONE,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": minutes_day_before_6pm},
                {"method": "popup", "minutes": minutes_same_day_7am},
            ],
        },
    }
    color_id = calendar_color_id(booking)
    if color_id:
        body["colorId"] = color_id
    return body


def sync_booking_to_calendar(booking: Dict[str, Any]) -> Optional[str]:
    """
    Create or update a calendar event. Returns user-facing status message.
    """
    if not is_configured():
        return None

    if not is_connected():
        return "Google Calendar not connected — go to Settings to connect."

    service = _calendar_service()
    if service is None:
        return "Google Calendar login expired — reconnect in Settings."

    booking_id = int(booking["id"])
    body = _event_body(booking)
    event_id = booking.get("google_calendar_event_id")

    try:
        if event_id:
            try:
                service.events().update(
                    calendarId=config.GOOGLE_CALENDAR_ID,
                    eventId=event_id,
                    body=body,
                ).execute()
                return "Calendar event updated."
            except Exception as update_exc:
                if "404" not in str(update_exc) and "Not Found" not in str(
                    update_exc
                ):
                    raise
                event_id = None

        event = (
            service.events()
            .insert(calendarId=config.GOOGLE_CALENDAR_ID, body=body)
            .execute()
        )
        new_id = event.get("id")
        if new_id:
            db.update_booking_integration_fields(
                booking_id, {"google_calendar_event_id": new_id}
            )
        if event_id:
            return "Calendar event updated."
        return "Added to Google Calendar."
    except Exception as exc:
        return "Calendar sync failed: {0}".format(exc)


def delete_calendar_event(booking: Dict[str, Any]) -> Optional[str]:
    if not is_connected():
        return None
    event_id = booking.get("google_calendar_event_id")
    if not event_id:
        return None
    service = _calendar_service()
    if service is None:
        return None
    try:
        service.events().delete(
            calendarId=config.GOOGLE_CALENDAR_ID, eventId=event_id
        ).execute()
        return "Removed from Google Calendar."
    except Exception:
        return None


def verify_connection() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Check Google Calendar configuration, token, and API access.

    Returns (ok, message, details) for scripts and diagnostics.
    """
    details: Dict[str, Any] = {
        "enabled": config.GOOGLE_CALENDAR_ENABLED,
        "credentials_file": str(Path(config.GOOGLE_CREDENTIALS_FILE).resolve()),
        "credentials_present": Path(config.GOOGLE_CREDENTIALS_FILE).is_file(),
        "token_file": str(Path(config.GOOGLE_TOKEN_FILE).resolve()),
        "token_present": Path(config.GOOGLE_TOKEN_FILE).is_file(),
        "calendar_id": config.GOOGLE_CALENDAR_ID,
        "timezone": config.TIMEZONE,
    }

    if not is_configured():
        return (
            False,
            "Google Calendar is not configured — set GOOGLE_CALENDAR_ENABLED=true "
            "and add credentials/google_credentials.json.",
            details,
        )

    if not is_connected():
        return (
            False,
            "Google Calendar credentials found but not connected — open Settings and "
            "click Connect Google Calendar.",
            details,
        )

    service = _calendar_service()
    if service is None:
        return (
            False,
            "Google Calendar token expired or invalid — reconnect in Settings.",
            details,
        )

    try:
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()
        events = (
            service.events()
            .list(
                calendarId=config.GOOGLE_CALENDAR_ID,
                timeMin=now,
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        items = events.get("items") or []
        details["upcoming_event_count"] = len(items)
        details["upcoming_events"] = [
            {
                "id": item.get("id", ""),
                "summary": item.get("summary", ""),
                "start": (item.get("start") or {}).get("dateTime")
                or (item.get("start") or {}).get("date")
                or "",
            }
            for item in items
        ]
        return (
            True,
            "Google Calendar connected — calendar \"{0}\" ({1} upcoming events listed).".format(
                config.GOOGLE_CALENDAR_ID,
                len(items),
            ),
            details,
        )
    except Exception as exc:
        return False, "Google Calendar API error: {0}".format(exc), details


def verify_booking_sync(booking_id: int) -> Tuple[bool, str, Dict[str, Any]]:
    """Sync one booking to Google Calendar and report the result."""
    import database as db

    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking #{0} not found.".format(booking_id), {}
    booking = dict(row)
    msg = sync_booking_to_calendar(booking)
    details = {
        "booking_id": booking_id,
        "event_id": booking.get("google_calendar_event_id") or "",
    }
    row_after = db.get_booking(booking_id)
    if row_after:
        details["event_id"] = row_after.get("google_calendar_event_id") or details["event_id"]
    if msg and ("failed" in msg.lower() or "not connected" in msg.lower() or "expired" in msg.lower()):
        return False, msg or "Calendar sync failed.", details
    return True, msg or "Calendar sync completed.", details
