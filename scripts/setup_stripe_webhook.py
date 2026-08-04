#!/usr/bin/env python3
"""Register Stripe webhook for production and save signing secret locally."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_WEBHOOK_URL = (
    "https://japanese-removals-bookings.onrender.com/integrations/stripe/webhook"
)
EVENTS = ["checkout.session.completed"]


def main() -> int:
    from integrations import stripe_config

    sk = stripe_config.get_secret_key()
    if not stripe_config.secret_key_valid(sk):
        print("FAIL: Valid Stripe secret key required.")
        return 1

    import stripe

    stripe.api_key = sk

    existing = stripe.WebhookEndpoint.list(limit=100)
    endpoint = None
    for ep in existing.data:
        if ep.url.rstrip("/") == PRODUCTION_WEBHOOK_URL.rstrip("/"):
            endpoint = ep
            print("Found existing webhook:", ep.id, ep.status)
            break

    if endpoint is None:
        endpoint = stripe.WebhookEndpoint.create(
            url=PRODUCTION_WEBHOOK_URL,
            enabled_events=EVENTS,
        )
        print("Created webhook:", endpoint.id)

    secret = (getattr(endpoint, "secret", None) or "").strip()
    if not secret:
        print(
            "NOTE: Stripe only returns the signing secret when a webhook is first created."
        )
        print("If you lost it, delete the endpoint in Stripe Dashboard and re-run this script.")
        return 1

    settings_path = Path(stripe_config.SETTINGS_PATH)
    data = {}
    if settings_path.is_file():
        data = json.loads(settings_path.read_text())
    data["webhook_secret"] = secret
    data["stripe_enabled"] = bool(data.get("stripe_enabled", True))
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        import os

        os.chmod(settings_path, 0o600)
    except OSError:
        pass

    print("Saved webhook secret to", settings_path)
    print("Add to Render env: STRIPE_WEBHOOK_SECRET=(value in stripe_settings.json)")
    print("Webhook URL:", PRODUCTION_WEBHOOK_URL)
    print("Events:", ", ".join(EVENTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
