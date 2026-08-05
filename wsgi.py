"""WSGI entry point for Gunicorn on Render."""

import production_bootstrap

production_bootstrap.bootstrap_production()

import database as db

db.init_db()
production_bootstrap.bootstrap_stripe_settings()
production_bootstrap.ensure_staff_user()

from app import app as application  # noqa: E402

app = application
