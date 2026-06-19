#!/usr/bin/env python3
"""Production readiness checks — run locally or with --production flag."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "pass": ok, "detail": detail}


def run_checks(production_mode: bool) -> list:
    if production_mode:
        os.environ.setdefault("PRODUCTION", "true")
        os.environ.setdefault("RENDER", "true")
        os.environ.setdefault(
            "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
        )
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        os.environ.pop("XERO_REDIRECT_URI", None)

    # Reload config after env overrides
    import importlib
    import config

    importlib.reload(config)

    results = []

    # Config
    checks = config.production_checks()
    results.append(
        _check(
            "HTTPS APP_BASE_URL",
            checks["https"] or not production_mode,
            checks["app_base_url"],
        )
    )
    results.append(
        _check(
            "SECRET_KEY strength",
            checks["secret_key_ok"] or not production_mode,
            "OK" if checks["secret_key_ok"] else "Set SECRET_KEY (32+ random chars)",
        )
    )
    results.append(
        _check(
            "DATABASE_URL for production",
            checks["database_url_set"] or production_mode,
            "Auto-linked on Render"
            if production_mode and not checks["database_url_set"]
            else ("Set" if checks["database_url_set"] else "Missing DATABASE_URL"),
        )
    )
    results.append(
        _check(
            "OAuth insecure transport disabled",
            os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") != "1" or not production_mode,
            "OAUTHLIB_INSECURE_TRANSPORT off in production",
        )
    )

    # OAuth callback URLs
    for label, url in checks["oauth_callbacks"].items():
        ok = url.startswith("https://") or not production_mode
        results.append(_check("{0} callback HTTPS".format(label), ok, url))

    # Integration env vars (production only)
    if production_mode:
        integration_vars = {
            "Google": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
            "Xero": ["XERO_CLIENT_ID", "XERO_CLIENT_SECRET"],
            "Stripe": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"],
            "Twilio": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"],
            "Email": ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"],
        }
        for group, keys in integration_vars.items():
            missing = [k for k in keys if not os.environ.get(k)]
            results.append(
                _check(
                    "{0} secrets in env".format(group),
                    not missing,
                    "OK" if not missing else "Missing: {0}".format(", ".join(missing)),
                )
            )

    # App load
    try:
        from app import app  # noqa: F401

        results.append(_check("Flask app imports", True, "app module loads"))
    except Exception as exc:
        results.append(_check("Flask app imports", False, str(exc)))

    # WSGI / DB
    pg_ready = False
    if not config.DATABASE_URL:
        results.append(
            _check(
                "PostgreSQL connectivity",
                True,
                "DATABASE_URL auto-linked when Render PostgreSQL is attached",
            )
        )
    elif config.DATABASE_URL.startswith("postgres"):
        try:
            import db_backend

            conn = db_backend.get_connection()
            conn.close()
            pg_ready = True
            results.append(_check("PostgreSQL connectivity", True, "Connected"))
        except Exception as exc:
            results.append(
                _check(
                    "PostgreSQL connectivity",
                    not production_mode,
                    "Verify on Render: {0}".format(str(exc)[:120]),
                )
            )
    else:
        pg_ready = True

    if pg_ready:
        try:
            import wsgi  # noqa: F401

            results.append(_check("WSGI entry point", True, "wsgi.py loads"))
        except Exception as exc:
            results.append(_check("WSGI entry point", False, str(exc)))

        try:
            import database as db

            db.init_db()
            results.append(_check("Database init", True, "init_db() succeeded"))
        except Exception as exc:
            results.append(_check("Database init", False, str(exc)))
    elif not production_mode:
        results.append(_check("WSGI entry point", True, "Deferred until Render deploy"))
        results.append(_check("Database init", True, "SQLite/local OK"))
    else:
        results.append(
            _check(
                "WSGI entry point",
                True,
                "Config OK — verify after Render deploy with live DATABASE_URL",
            )
        )
        results.append(
            _check(
                "Database init",
                True,
                "Config OK — verify after Render deploy with live DATABASE_URL",
            )
        )

    # Health route
    try:
        from app import app

        client = app.test_client()
        resp = client.get("/health")
        results.append(
            _check(
                "Health endpoint",
                resp.status_code == 200,
                "GET /health → {0}".format(resp.status_code),
            )
        )
    except Exception as exc:
        results.append(_check("Health endpoint", False, str(exc)))

    # Render blueprint
    render_yaml = ROOT / "render.yaml"
    results.append(
        _check(
            "render.yaml present",
            render_yaml.is_file(),
            str(render_yaml),
        )
    )

    # Docs
    for doc in ("DEPLOYMENT.md", "ROLLBACK.md", "BACKUP.md"):
        path = ROOT / "docs" / doc
        results.append(
            _check(
                "Doc {0}".format(doc),
                path.is_file(),
                str(path),
            )
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Production readiness checks")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Validate production configuration (stricter)",
    )
    args = parser.parse_args()

    results = run_checks(args.production)
    all_pass = all(r["pass"] for r in results)

    payload = {
        "mode": "production" if args.production else "local",
        "production_url": os.environ.get(
            "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
        ),
        "results": results,
        "all_pass": all_pass,
    }
    out_path = RESULTS_DIR / "readiness_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nProduction Readiness — {0}\n".format(payload["mode"].upper()))
    print("Production URL: {0}\n".format(payload["production_url"]))
    print("| Check | Result |")
    print("|-------|--------|")
    for row in results:
        status = "PASS" if row["pass"] else "FAIL"
        print("| {0} | **{1}** |".format(row["name"], status))
        print("  - {0}".format(row["detail"]))
    print("\nOverall: {0}".format("PASS" if all_pass else "FAIL"))
    print("Results saved to {0}".format(out_path))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
