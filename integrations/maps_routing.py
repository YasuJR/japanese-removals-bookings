"""Driving ETA estimates via Google Maps Distance Matrix API."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

import config


def estimate_driving_minutes(
    origin: str,
    destination: str,
) -> Tuple[Optional[int], str, str]:
    """
    Return (minutes, source, detail).

    source is ``google_maps`` when the API returns a route, otherwise ``manual``.
    Apple Maps has no server-side routing API in this app — drivers use Pickup Map
    links client-side; use manual ETA minutes when Google Maps is unavailable.
    """
    origin_text = (origin or "").strip()
    destination_text = (destination or "").strip()
    if not destination_text:
        return None, "manual", "Pickup address missing — enter ETA minutes manually."

    api_key = (config.GOOGLE_MAPS_API_KEY or "").strip()
    if not api_key:
        return None, "manual", "Google Maps API key not configured."

    if not origin_text:
        origin_text = (config.DEFAULT_DRIVER_ORIGIN or "").strip()
    if not origin_text:
        return None, "manual", "Driver origin not set — enter ETA minutes manually."

    params = urllib.parse.urlencode(
        {
            "origins": origin_text,
            "destinations": destination_text,
            "mode": "driving",
            "units": "metric",
            "departure_time": "now",
            "key": api_key,
        }
    )
    url = "https://maps.googleapis.com/maps/api/distancematrix/json?{0}".format(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, "manual", "Google Maps request failed: {0}".format(exc)

    status = (payload.get("status") or "").upper()
    if status != "OK":
        message = payload.get("error_message") or status or "Unknown error"
        return None, "manual", "Google Maps error: {0}".format(message)

    rows = payload.get("rows") or []
    if not rows:
        return None, "manual", "Google Maps returned no route data."

    elements = rows[0].get("elements") or []
    if not elements:
        return None, "manual", "Google Maps returned no route data."

    element = elements[0]
    element_status = (element.get("status") or "").upper()
    if element_status != "OK":
        return None, "manual", "Google Maps route status: {0}".format(element_status)

    duration = element.get("duration_in_traffic") or element.get("duration") or {}
    seconds = duration.get("value")
    if not seconds:
        return None, "manual", "Google Maps returned no travel time."

    minutes = max(1, int(round(float(seconds) / 60.0)))
    return minutes, "google_maps", "Estimated via Google Maps driving route."
