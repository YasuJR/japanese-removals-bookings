"""WSGI entry point for Gunicorn on Render."""

import database as db

db.init_db()

import production_bootstrap

production_bootstrap.bootstrap_production()
production_bootstrap.ensure_staff_user()

from app import app as application  # noqa: E402

app = application
