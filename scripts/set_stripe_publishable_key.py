#!/usr/bin/env python3
"""Save a Stripe publishable key locally and print Render env instructions."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from integrations import stripe_config

    if len(sys.argv) != 2:
        print("Usage: python scripts/set_stripe_publishable_key.py pk_live_…")
        return 1

    publishable_key = sys.argv[1].strip()
    if not stripe_config.publishable_key_valid(publishable_key):
        print("FAIL: Publishable key must start with pk_live_ or pk_test_ and be at least 32 chars.")
        return 1

    merged = stripe_config.settings_for_form()
    stripe_config.save_settings(
        stripe_enabled=merged.get("stripe_enabled", True),
        publishable_key=publishable_key,
        secret_key="",
        webhook_secret="",
        card_surcharge_percent=merged.get("card_surcharge_percent")
        or stripe_config.DEFAULT_SURCHARGE_PERCENT,
        xero_payment_account_code=merged.get("xero_payment_account_code") or "",
    )
    print("Saved publishable key to", stripe_config.SETTINGS_PATH)
    print()
    print("Production (Render Dashboard → Environment):")
    print("  STRIPE_PUBLISHABLE_KEY=<same pk_live_… value>")
    print("Redeploy or restart the web service so production_bootstrap.py applies it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
