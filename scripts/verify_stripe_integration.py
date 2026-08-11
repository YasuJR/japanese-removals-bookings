#!/usr/bin/env python3
"""Verify Stripe integration before a live payment test."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "stripe_verify"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "pass": ok, "detail": detail}


def main() -> int:
    import database as db
    import services
    from app import app
    from integrations import stripe as stripe_service
    from integrations import stripe_config, xero
    from integrations.invoice_pdf import build_invoice_document, generate_invoice_pdf

    db.init_db()
    results = []

    pk = stripe_config.get_publishable_key()
    sk = stripe_config.get_secret_key()
    wh = stripe_config.get_webhook_secret()
    form = stripe_config.settings_for_form()
    pk_source = stripe_config.publishable_key_source()

    pk_ok = stripe_config.publishable_key_valid(pk)
    sk_ok = stripe_config.secret_key_valid(sk)
    wh_ok = stripe_config.webhook_secret_valid(wh)

    results.append(
        _check(
            "1. Stripe publishable key",
            pk_ok,
            "Valid pk_* key configured (source: {0})".format(pk_source)
            if pk_ok
            else "Invalid or missing — must start with pk_live_ or pk_test_ (effective prefix {0}…, source {1}, stored_invalid={2})".format(
                pk[:8] if pk else "(empty)",
                pk_source,
                form.get("stored_publishable_invalid"),
            ),
        )
    )
    results.append(
        _check(
            "2. Stripe secret key",
            sk_ok,
            "Valid sk_* key configured"
            if sk_ok
            else "Invalid or missing secret key",
        )
    )
    results.append(
        _check(
            "3. Webhook secret",
            wh_ok,
            "Valid whsec_* secret configured"
            if wh_ok
            else "Invalid or placeholder — must be whsec_* from Stripe webhook endpoint (got len {0})".format(
                len(wh)
            ),
        )
    )

    webhook_url = form["webhook_url"]
    prod_webhook_url = "https://japanese-removals-bookings.onrender.com/integrations/stripe/webhook"
    webhook_registered = False
    webhook_detail = "Could not query Stripe (secret key missing or API error)"
    if sk_ok:
        try:
            import stripe

            stripe.api_key = sk
            endpoints = stripe.WebhookEndpoint.list(limit=20)
            matches = [
                ep
                for ep in endpoints.data
                if ep.url.rstrip("/")
                in {webhook_url.rstrip("/"), prod_webhook_url.rstrip("/")}
            ]
            webhook_registered = bool(matches)
            if matches:
                webhook_detail = "Registered: {0} ({1})".format(
                    matches[0].url, matches[0].status
                )
            else:
                webhook_detail = (
                    "No webhook for {0} — run scripts/setup_stripe_webhook.py".format(
                        prod_webhook_url
                    )
                )
        except Exception as exc:
            webhook_detail = str(exc)[:160]

    results.append(
        _check(
            "4. Webhook endpoint registered",
            webhook_registered,
            webhook_detail,
        )
    )

    xero_code = stripe_config.xero_payment_account_code()
    xero_ok = stripe_config.xero_payment_account_configured() or bool(
        xero.default_bank_account_code()
    )
    results.append(
        _check(
            "5. Xero payment account code",
            xero_ok,
            xero_code
            if stripe_config.xero_payment_account_configured()
            else "Will auto-detect first Xero bank account",
        )
    )

    pay_now_ok = False
    pay_detail = "Could not test Pay Now"
    customer_pay_ok = False
    customer_pay_detail = ""
    try:
        from datetime import date, timedelta

        bid = db.create_booking(
            "Stripe Verify",
            "0412000000",
            "verify-stripe@example.com",
            "1 Test St",
            "2 Test Ave",
            (date.today() + timedelta(days=14)).isoformat(),
            1,
            "Stripe verify",
            hourly_rate=1.0,
            callout_fee=0.0,
            duration_hours="1",
            gst_enabled=1,
            payment_status="Unpaid",
            invoice_status="AUTHORISED",
            status="Confirmed",
        )
        db.update_booking_invoice_fields(bid, {"invoice_number": "VERIFY-STRIPE"})
        services.prepare_booking_payment_link(bid)
        row = dict(db.get_booking(bid))
        doc = build_invoice_document(row)
        pdf_bytes = generate_invoice_pdf(row)
        opts = doc.get("payment_options") or {}
        pay_now_ok = bool(opts.get("can_pay_now")) and len(pdf_bytes) > 1000
        pay_detail = "can_pay_now={0}, pdf_bytes={1}, url_host={2}".format(
            opts.get("can_pay_now"),
            len(pdf_bytes),
            (opts.get("pay_now_url") or "").split("/")[2]
            if opts.get("pay_now_url")
            else "(none)",
        )

        token = row.get("payment_token")
        if token:
            client = app.test_client()
            resp = client.get("/pay/{0}".format(token), follow_redirects=False)
            customer_pay_ok = resp.status_code in (302, 303) and "checkout.stripe.com" in (
                resp.headers.get("Location") or ""
            )
            customer_pay_detail = "GET /pay/* → {0}".format(resp.status_code)
    except Exception as exc:
        pay_detail = str(exc)[:160]
        customer_pay_detail = str(exc)[:160]

    results.append(
        _check(
            "6. Pay Now on invoice PDF",
            pay_now_ok,
            pay_detail,
        )
    )
    results.append(
        _check(
            "7. Customer payment page",
            customer_pay_ok,
            customer_pay_detail or "Redirect to Stripe Checkout",
        )
    )

    webhook_code_ok = False
    webhook_code_detail = "Phase 8 handler sets booking Paid + Xero PAID (verified in test_phase8_gmail_stripe_e2e.py)"
    phase8 = ROOT / "test_results" / "phase8" / "phase8_results.json"
    if phase8.is_file():
        try:
            phase8_data = json.loads(phase8.read_text())
            webhook_code_ok = bool(phase8_data.get("passed"))
            webhook_code_detail = "Phase 8 E2E: {0}".format(
                "PASS" if webhook_code_ok else "FAIL"
            )
        except json.JSONDecodeError:
            pass
    results.append(
        _check(
            "8. Webhook updates booking + Xero",
            webhook_code_ok,
            webhook_code_detail,
        )
    )

    ready = stripe_config.is_ready()
    results.append(
        _check(
            "Stripe ready flag",
            ready,
            "stripe_enabled + credentials" if ready else "Not ready",
        )
    )

    all_pass = all(r["pass"] for r in results)
    payload = {"results": results, "all_pass": all_pass}
    out = RESULTS_DIR / "verify_results.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    print("RESULTS_FILE", out)
    print("PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
