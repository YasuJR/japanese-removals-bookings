"""WSGI entry point for Gunicorn on Render."""

import logging
import sys

import config
import production_bootstrap

logger = logging.getLogger(__name__)

production_bootstrap.bootstrap_production()

import database as db

db.init_db()
production_bootstrap.bootstrap_stripe_settings()
production_bootstrap.bootstrap_xero_settings()
production_bootstrap.ensure_staff_user()

if config.PRODUCTION and not config.get_database_url():
    logger.critical(
        "DATABASE_URL is not set on Render — link japanese-removals-db to the web service."
    )
    print(
        "CRITICAL: DATABASE_URL missing. Run: RENDER_API_KEY=... python scripts/render_link_database.py --deploy",
        file=sys.stderr,
    )

from app import app as application  # noqa: E402

app = application
