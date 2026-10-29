"""Japanese Removals — booking management system."""

# 1) OAuth local http — MUST be first (before config, Flask, or Google libraries).
import oauth_local  # noqa: F401, E402

# 2) Load .env and reinforce OAUTHLIB_INSECURE_TRANSPORT.
import config  # noqa: F401, E402

import csv
import io
import os
import secrets
from datetime import date, datetime, timedelta
from calendar import monthrange

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

import auth
import database as db
import services
from integrations import (
    driver_run_sheet_pdf,
    email_send,
    google_calendar,
    invoice_pdf as invoice_pdf_service,
    job_sheet,
    review_automation,
    sms,
    weekly_schedule_pdf,
    xero,
)
from integrations import executive_config, review_config, sms_config, xero_config, company_config, xero_branding, stripe_config, gmail_config, google_oauth
from integrations import stripe as stripe_service
from booking_helpers import apple_maps_url, mailto_href, notes_display, sms_href, tel_href
from driver_run_sheet_data import build_driver_run_sheet
from staff_portal import STAFF_VIEW_ALL, build_staff_portal, build_staff_weekly_pdf_schedule, portal_nav_params
import staff_auth
import staff_job_times
import star_points
import staff_portal_owner
from outstanding_invoices_data import (
    INVOICE_FILTERS,
    build_outstanding_dashboard,
    dashboard_invoice_number_for_row,
)
from executive_dashboard_data import build_executive_dashboard
from profit_data import PROFIT_CSV_HEADERS, build_profit_dashboard, profit_csv_rows
import booking_profit
import callout_pricing
from integrations import payment_reminder_automation, xero_payment_sync
from booking_times import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_FINISH_TIME,
    DEFAULT_START_TIME,
    display_finish_time,
    display_start_time,
    effective_finish_hm,
    effective_duration_hours,
    format_time_12h,
    normalize_time_input,
)
from crew import CREW_OPTIONS, active_crew_names, all_crew_names, crew_from_storage, display_crew
from resource_conflicts import find_resource_conflict_warnings
from trucks import active_truck_names
import double_booking
import furniture_catalog
import furniture_enquiry
import furniture_lists
import furniture_pdf
import furniture_volume_refs
import furniture_volumes
import job_status
import receipts
import work_periods
from crew_schedule_data import RANGE_OPTIONS, build_crew_schedule
from ceo_dashboard_data import build_ceo_dashboard
from daily_checklist_data import build_daily_checklist
from quote_form import parse_quote_form
from integrations import website_quote, sms_inbound
from dashboard_data import (
    build_dashboard,
    dashboard_jobs,
    dashboard_section_layout,
    dashboard_unpaid_summaries,
    paginate_dashboard_jobs,
    parse_jobs_limit,
    perth_today,
)
from daily_jobs_data import build_daily_jobs
from weekly_schedule_data import build_weekly_schedule
import calendar_data
from display_dates import format_display_date, get_weekday_class
import automation
import invoice
import invoice_numbering
import bank_transfer_match
from validators import parse_booking_form

app = Flask(__name__)

if config.PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _row_to_dict(row):
    return dict(row) if row else {}


def _form_duration_from_row(row) -> str:
    booking = _row_to_dict(row)
    hours = effective_duration_hours(booking, default=0.0)
    if hours <= 0:
        return ""
    if hours == int(hours):
        return str(int(hours))
    return str(hours)


app.jinja_env.filters["booking_start_time"] = lambda row: display_start_time(
    _row_to_dict(row)
)
app.jinja_env.filters["booking_finish_time"] = lambda row: display_finish_time(
    _row_to_dict(row)
)
app.jinja_env.filters["booking_crew"] = lambda row: display_crew(_row_to_dict(row))
app.jinja_env.filters["apple_maps_url"] = apple_maps_url
app.jinja_env.filters["tel_href"] = tel_href
app.jinja_env.filters["notes_display"] = notes_display
app.jinja_env.filters["sms_href"] = sms_href
app.jinja_env.filters["mailto_href"] = mailto_href
app.jinja_env.filters["format_hm_12h"] = format_time_12h
app.jinja_env.filters["format_display_date"] = format_display_date
app.jinja_env.filters["weekday_class"] = get_weekday_class
app.jinja_env.filters["format_aud"] = invoice.format_aud
app.jinja_env.globals["job_cost_form_values"] = booking_profit.job_cost_form_values
app.jinja_env.globals["STAFF_COST_RATE_PER_HOUR"] = (
    booking_profit.STAFF_COST_RATE_PER_HOUR
)
app.jinja_env.globals["STAFF_COST_RATE_THREE_MOVERS"] = (
    booking_profit.STAFF_COST_RATE_THREE_MOVERS
)
app.jinja_env.globals["STAFF_COST_THIRD_MOVER_EXTRA"] = (
    booking_profit.STAFF_COST_THIRD_MOVER_EXTRA
)
app.jinja_env.globals["DEFAULT_FUEL_COST"] = booking_profit.DEFAULT_FUEL_COST
app.jinja_env.filters["format_invoice_number"] = invoice_numbering.format_invoice_number
app.jinja_env.filters["display_invoice_number"] = invoice_numbering.display_invoice_number
app.jinja_env.filters["dashboard_invoice_number"] = (
    dashboard_invoice_number_for_row
)
app.jinja_env.filters["payment_status_css"] = (
    lambda value: invoice.normalize_payment_status(value)
    .lower()
    .replace(" ", "-")
)
app.jinja_env.filters["job_status_css"] = job_status.css_class
app.jinja_env.filters["booking_job_status"] = lambda row: job_status.display(
    _row_to_dict(row)
)
app.jinja_env.filters["format_margin"] = lambda pct: (
    "—"
    if pct is None
    else (
        "{0:.1f}%".format(float(pct))
        if _margin_value_ok(pct)
        else "—"
    )
)


def _margin_value_ok(pct) -> bool:
    try:
        float(pct)
        return True
    except (TypeError, ValueError):
        return False


def _margin_badge_filter(pct) -> str:
    if pct is None or not _margin_value_ok(pct):
        return ""
    return booking_profit.margin_badge_class(float(pct))


app.jinja_env.filters["margin_badge"] = _margin_badge_filter
from urllib.parse import quote

app.jinja_env.filters["urlencode"] = lambda value: quote(str(value or ""), safe="")


@app.context_processor
def inject_template_globals():
    cached = getattr(g, "_template_globals", None)
    if cached is not None:
        return cached
    user = g.get("user")
    cached = {
        "crew_options": active_crew_names() or CREW_OPTIONS,
        "truck_options": active_truck_names(),
        "job_status_options": job_status.OPTIONS,
        "dashboard_filters": job_status.DASHBOARD_FILTERS,
        "dashboard_status_options": job_status.DASHBOARD_INLINE_STATUS_OPTIONS,
        "dashboard_payment_options": invoice.DASHBOARD_INLINE_PAYMENT_OPTIONS,
        "crew_schedule_ranges": RANGE_OPTIONS,
        "invoice_filters": INVOICE_FILTERS,
        "company_settings": company_config.get_settings(),
        "is_admin": auth.is_admin_user(user),
        "asset_version": (os.environ.get("RENDER_GIT_COMMIT") or "").strip()[:12]
        or "dev",
    }
    g._template_globals = cached
    return cached


app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(days=14)

HOST = "0.0.0.0" if config.PRODUCTION else "127.0.0.1"
PORT = int(os.environ.get("PORT", "5001"))

CSV_HEADERS = [
    "id",
    "customer_name",
    "phone",
    "email",
    "pickup_address",
    "delivery_address",
    "move_date",
    "start_time",
    "finish_time",
    "duration_hours",
    "crew",
    "num_movers",
    "notes",
    "status",
    "hourly_rate",
    "callout_fee",
    "gst_enabled",
    "payment_status",
    "invoice_status",
    "invoice_number",
    "invoice_issue_date",
    "invoice_due_date",
    "paid_at",
    "google_calendar_event_id",
    "xero_invoice_id",
    "sms_last_sent_at",
    "created_at",
]


def _invoice_summary_for_row(row, *, live_xero: bool = False) -> dict:
    booking = services.booking_to_dict(row)
    summary = invoice.invoice_summary(booking)
    if live_xero and xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        live_status = xero.resolve_invoice_status(booking)
        if live_status:
            summary["invoice_status"] = live_status
    return summary


@app.before_request
def before_request() -> None:
    if request.path == "/health":
        return
    db.init_db()
    auth.load_logged_in_user()


@app.teardown_request
def teardown_db_connection(exc):
    import db_backend

    db_backend.close_request_connection(exc)


def _integration_status() -> dict:
    from pathlib import Path

    cred_path = Path(config.GOOGLE_CREDENTIALS_FILE)
    return {
        "google_enabled": config.GOOGLE_CALENDAR_ENABLED,
        "google_credentials_exists": cred_path.is_file(),
        "google_credentials_path": str(cred_path),
        "google_configured": google_calendar.is_configured(),
        "google_connected": google_calendar.is_connected(),
        "google_redirect_uri": google_oauth.resolve_redirect_uri(),
        "google_client_id": google_oauth.get_client_id(),
        "gmail_inbox_enabled": config.GMAIL_INBOX_ENABLED,
        "gmail_automation_enabled": gmail_config.is_automation_enabled(),
        "gmail_scope_granted": google_oauth.gmail_scope_granted(),
        "sms_configured": sms.is_configured(),
        "sms_automation_enabled": sms_config.is_automation_enabled(),
        "email_configured": email_send.is_configured(),
        "review_automation_enabled": review_config.is_automation_enabled(),
        "xero_enabled": config.XERO_ENABLED,
        "xero_has_credentials": xero_config.has_credentials(),
        "xero_configured": xero_config.has_credentials(),
        "xero_connected": xero.is_connected(),
        "xero_ready": xero.is_ready(),
        "stripe_configured": stripe_config.secret_key_valid(),
        "stripe_ready": stripe_config.is_ready(),
    }


def _edit_booking_extras(row) -> dict:
    booking = services.booking_to_dict(row)
    booking_id = int(booking["id"])
    customer_pay_url = services.prepare_booking_payment_link(booking_id)
    refreshed = db.get_booking(booking_id)
    if refreshed:
        booking = services.booking_to_dict(refreshed)
    linked = xero.is_real_invoice_id(booking.get("xero_invoice_id"))
    job = job_status.display(booking)
    review_row = db.get_review_request_for_booking(int(booking["id"]))
    invoice_status = (booking.get("invoice_status") or "").strip() if linked else ""
    return {
        "receipt": receipts.state(booking),
        "invoice_summary": _invoice_summary_for_row(row),
        "xero_invoice_url": _xero_invoice_url_for_row(row),
        "xero_invoice_linked": linked,
        "xero_invoice_status": invoice_status,
        "xero_invoice_draft": linked and xero.is_draft_invoice(booking),
        "xero_invoice_locked": linked and xero.is_locked_invoice(booking),
        "show_invoice_automation": job == "Completed" and not linked,
        "review_request": dict(review_row) if review_row else None,
        "payment_options": stripe_service.payment_options_for_booking(booking),
        "customer_pay_url": customer_pay_url,
        "xero_automation_error": (booking.get("xero_invoice_automation_error") or "").strip(),
        "default_driver_name": _default_driver_name(),
        "can_start_on_route": job in ("Confirmed", "On Route"),
        "can_send_review_now": job == "Completed",
        "can_cancel_review_request": (
            review_row is not None
            and dict(review_row).get("status") == automation.STATUS_SCHEDULED
        ),
        "profit_summary": booking_profit.profit_summary_for_booking(booking),
        "can_send_payment_reminder": payment_reminder_automation.can_send_manual(
            booking
        ),
        "can_cancel_payment_reminders": payment_reminder_automation.can_cancel(
            booking
        ),
        "payment_reminder_badges": payment_reminder_automation.badges_for_booking(
            booking
        ),
        **services.invoice_send_context(booking),
        **_double_booking_context(booking),
    }


def _default_driver_name() -> str:
    user = getattr(g, "user", None)
    if not user:
        return "Yasu"
    display = (user["display_name"] or "").strip()
    if display:
        return display
    return (user["username"] or "").strip() or "Yasu"


def _xero_invoice_url_for_row(row) -> str:
    booking = services.booking_to_dict(row)
    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if xero.is_real_invoice_id(invoice_id):
        return xero.invoice_url(invoice_id)
    return ""


def _booking_form_defaults() -> dict:
    defaults = company_config.booking_form_defaults()
    return {
        "phone": "",
        "email": "",
        "hourly_rate": defaults["hourly_rate"],
        "callout_fee": defaults["callout_fee"],
        "callout_minutes": defaults.get("callout_minutes", 30),
        "gst_enabled": defaults["gst_enabled"],
        "crew": defaults["crew"],
        "extra_charges": [],
        "start_time": DEFAULT_START_TIME,
        "finish_time": "",
        "duration_hours": "",
        "num_movers": "2",
        "status": "Confirmed",
    }


def _save_booking_from_form(form):
    data, errors = parse_booking_form(form)
    if errors:
        return None, errors, data
    defaults = invoice.default_invoice_fields()
    settings = company_config.get_settings()
    booking_id = db.create_booking(
        customer_name=data["customer_name"],
        phone=data["phone"] or settings["default_phone"],
        email=data["email"] or settings["default_email"],
        pickup_address=data["pickup_address"],
        delivery_address=data["delivery_address"],
        move_date=data["move_date"],
        num_movers=data["num_movers"],
        notes=data["notes"],
        start_time=data["start_time"],
        finish_time=data["finish_time"],
        duration_hours=data["duration_hours"],
        crew=data["crew_csv"],
        hourly_rate=data.get("hourly_rate", defaults["hourly_rate"]),
        callout_fee=data.get("callout_fee", defaults["callout_fee"]),
        callout_minutes=data.get("callout_minutes"),
        gst_enabled=data.get("gst_enabled", defaults["gst_enabled"]),
        payment_status=defaults["payment_status"],
        invoice_status=defaults["invoice_status"],
        status=data.get("status", job_status.DEFAULT_STATUS),
    )
    services._persist_booking_extras(booking_id, data)
    return booking_id, [], data


def _create_booking_from_data(data):
    defaults = invoice.default_invoice_fields()
    settings = company_config.get_settings()
    booking_id = db.create_booking(
        customer_name=data["customer_name"],
        phone=data["phone"] or settings["default_phone"],
        email=data["email"] or settings["default_email"],
        pickup_address=data["pickup_address"],
        delivery_address=data["delivery_address"],
        move_date=data["move_date"],
        num_movers=data["num_movers"],
        notes=data["notes"],
        start_time=data["start_time"],
        finish_time=data["finish_time"],
        duration_hours=data["duration_hours"],
        crew=data["crew_csv"],
        hourly_rate=data.get("hourly_rate", defaults["hourly_rate"]),
        callout_fee=data.get("callout_fee", defaults["callout_fee"]),
        callout_minutes=data.get("callout_minutes"),
        gst_enabled=data.get("gst_enabled", defaults["gst_enabled"]),
        payment_status=defaults["payment_status"],
        invoice_status=defaults["invoice_status"],
        status=data.get("status", job_status.DEFAULT_STATUS),
    )
    services._persist_booking_extras(booking_id, data)
    services.prepare_booking_payment_link(booking_id)
    return booking_id


def _legacy_actual_label(booking):
    """Booking-level actual times saved before they left Booking Edit (Staff Paid Hours only)."""
    start = staff_job_times.parse_actual_clock(booking.get("actual_start_time"))
    finish = staff_job_times.parse_actual_clock(booking.get("actual_finish_time"))
    if start and finish:
        return "{0} – {1}".format(format_time_12h(start), format_time_12h(finish))
    minutes = booking.get("actual_duration")
    if minutes not in (None, "", 0):
        return staff_job_times.format_worked_duration(minutes)
    return ""


def _work_period_2_form_values(booking):
    second = work_periods.period_two(booking)
    if not second:
        return {"work_period_2_enabled": "0"}
    return {
        "work_period_2_enabled": "1",
        "work_period_2_start": second["start"],
        "work_period_2_finish": second["finish"],
        "work_period_2_movers": second["movers"],
        "work_period_2_rate": "{0:.2f}".format(second["rate"]),
    }


def _update_booking_from_form(booking_id, form):
    existing_row = db.get_booking(booking_id)
    existing_crew = ""
    if existing_row:
        existing_crew = existing_row["crew"] or ""
    data, errors = parse_booking_form(form, existing_crew_csv=existing_crew)
    # Actual start/finish are no longer edited here (Booking Edit uses work periods).
    # Saved booking-level actual times are left untouched for Staff Paid Hours;
    # the owner changes them in the Staff Portal.
    if errors:
        return False, errors, data
    ok = db.update_booking(
        booking_id=booking_id,
        customer_name=data["customer_name"],
        phone=data["phone"],
        email=data["email"],
        pickup_address=data["pickup_address"],
        delivery_address=data["delivery_address"],
        move_date=data["move_date"],
        num_movers=data["num_movers"],
        notes=data["notes"],
        start_time=data["start_time"],
        finish_time=data["finish_time"],
        duration_hours=data["duration_hours"],
        crew=data["crew_csv"],
        hourly_rate=data["hourly_rate"],
        callout_fee=data["callout_fee"],
        callout_minutes=data.get("callout_minutes"),
        gst_enabled=data["gst_enabled"],
        payment_status=data["payment_status"],
        invoice_status=data["invoice_status"],
        status=data["status"],
    )
    if ok:
        services._persist_booking_extras(booking_id, data)
        _save_job_costs_from_form(booking_id, form, previous=existing_row)
        if data.get("work_period_2_present"):
            db.save_booking_work_period_2(booking_id, data)
    return ok, errors, data


def _copy_job_cost_form_values(target, form):
    for key in booking_profit.JOB_COST_FIELDS:
        if key in form:
            target[key] = form.get(key)
    if "staff_cost_manual" in form:
        target["staff_cost_manual"] = form.get("staff_cost_manual")
    return target


def _save_job_costs_from_form(booking_id, form, *, is_new=False, previous=None):
    if not booking_profit.job_cost_fields_in_form(form):
        return []
    return booking_profit.save_profit_cost_fields(
        booking_id, form, is_new=is_new, previous=previous
    )


def _overlap_payload(data, booking_id=None):
    crew = data.get("crew")
    if not crew:
        crew = crew_from_storage(data.get("crew_csv", ""))
    return {
        "id": booking_id if booking_id is not None else -1,
        "move_date": data.get("move_date", ""),
        "start_time": data.get("start_time", ""),
        "finish_time": data.get("finish_time", ""),
        "duration_hours": data.get("duration_hours", ""),
        "status": data.get("status", "Pending"),
        "crew": crew,
        "truck_assigned": data.get("truck_assigned", ""),
        "customer_name": data.get("customer_name", ""),
    }


def _crew_warnings_for_data(data, booking_id=None):
    return find_resource_conflict_warnings(
        _overlap_payload(data, booking_id), exclude_booking_id=booking_id
    )


def _flash_crew_warnings(warnings) -> None:
    for msg in warnings:
        flash(msg, "warning")


def _double_booking_context(booking_or_data, booking_id=None):
    if booking_id is not None:
        row = db.get_booking(booking_id)
        booking = services.booking_to_dict(row) if row else {}
    else:
        booking = dict(booking_or_data or {})
    form_data = booking_or_data if isinstance(booking_or_data, dict) else None
    return double_booking.ui_context(booking, form_data)


def _validate_double_booking(data, booking_id=None, form=None):
    form = form or request.form
    override = form.get(double_booking.OVERRIDE_FORM_FIELD) == "on"
    return double_booking.validate_save(
        data,
        booking_id=booking_id,
        override_confirmed=override,
    )


def _apply_new_booking_override(booking_id, override_applied, conflicts):
    if override_applied and conflicts and booking_id:
        db.update_booking_integration_fields(
            booking_id,
            {
                "double_booking_override_at": datetime.utcnow()
                .replace(microsecond=0)
                .isoformat(sep="T")
            },
        )
        automation.log_event(
            automation.AUTOMATION_DOUBLE_BOOKING_OVERRIDE,
            automation.STATUS_SUCCESS,
            "User confirmed double booking override.",
            booking_id=booking_id,
        )


def _flash_integration_messages(messages) -> None:
    for msg in messages:
        if msg:
            if "fail" in msg.lower() or "not connected" in msg.lower() or "auto-create failed" in msg.lower():
                flash(msg, "error")
            else:
                flash(msg, "success")


# --- Auth ---


@app.route("/health")
def health_check():
    import db_backend

    git_full = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    payload = {
        "status": "ok",
        "production": config.PRODUCTION,
        "git_commit": git_full[:12],
        "git_commit_full": git_full,
        "database": "postgres" if db_backend.is_postgres() else "sqlite",
        "database_url_set": bool(config.get_database_url()),
        "stripe_storage": "database"
        if config.PRODUCTION and db_backend.is_postgres()
        else "file",
    }
    if db_backend.is_postgres() and config.get_database_url():
        try:
            conn = db_backend.get_connection()
            try:
                row = conn.execute(
                    "SELECT to_regclass('public.booking_crew_hours') IS NOT NULL"
                ).fetchone()
                payload["booking_crew_hours_table"] = bool(row[0] if row else False)
                if payload["booking_crew_hours_table"]:
                    count_row = conn.execute(
                        "SELECT COUNT(*) FROM booking_crew_hours"
                    ).fetchone()
                    payload["booking_crew_hours_rows"] = int(
                        count_row[0] if count_row else 0
                    )
            finally:
                conn.close()
        except Exception as exc:
            payload["database_schema_error"] = str(exc)[:200]
    try:
        payload["stripe"] = stripe_config.public_status()
    except Exception:
        payload["stripe"] = {"checkout_ready": False}
    return payload, 200


@app.route("/quote", methods=["GET", "POST"], endpoint="website_quote")
def website_quote_route():
    if request.method == "POST":
        ip_address = request.remote_addr or ""
        data, errors, spam_blocked = parse_quote_form(request.form, ip_address)
        if spam_blocked and not errors:
            return render_template(
                "quote_form.html",
                submitted=True,
                success_message="Thank you — we will contact you shortly.",
            )
        if errors:
            return render_template(
                "quote_form.html",
                submitted=False,
                errors=errors,
                form=request.form.to_dict(flat=True),
            )
        ok, message, booking_id, _msgs = website_quote.submit_website_quote(
            data, ip_address
        )
        if ok:
            return render_template(
                "quote_form.html",
                submitted=True,
                success_message=message,
                booking_id=booking_id,
            )
        return render_template(
            "quote_form.html",
            submitted=False,
            errors=[message],
            form=request.form.to_dict(flat=True),
        )

    return render_template("quote_form.html", submitted=False, form={}, errors=[])


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("ceo_dashboard"))

    if db.staff_user_count() == 0:
        flash(
            "No staff accounts yet. Run: python create_staff.py admin yourpassword",
            "error",
        )

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        row = db.get_staff_by_username(username)
        if row and auth.verify_password(row["password_hash"], password):
            auth.login_user(row["id"], row["username"])
            flash("Welcome, {0}.".format(row["display_name"]), "success")
            dest = request.args.get("next") or url_for("ceo_dashboard")
            return redirect(dest)
        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    auth.logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/settings")
@auth.login_required
def settings():
    return render_template(
        "settings.html",
        status=_integration_status(),
    )


@app.route("/settings/review", methods=["GET", "POST"])
@auth.login_required
def review_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            review_config.save_settings(
                automation_enabled=request.form.get("automation_enabled") == "on",
                wait_hours=int(request.form.get("wait_hours", "24") or "24"),
                channel=request.form.get("channel", review_config.CHANNEL_SMS_OR_EMAIL),
                google_review_url=request.form.get("google_review_url", ""),
                sms_template=request.form.get("sms_template", ""),
                email_subject=request.form.get("email_subject", ""),
                email_body=request.form.get("email_body", ""),
                google_review_count=request.form.get("google_review_count", ""),
                google_average_rating=request.form.get("google_average_rating", ""),
            )
            flash("Google review settings saved.", "success")
        elif action == "test_send":
            channel = request.form.get("test_channel", "sms")
            ok, msg = services.send_review_test(
                channel,
                request.form.get("test_phone", ""),
                request.form.get("test_email", ""),
            )
            flash(msg, "success" if ok else "error")
        elif action == "run_scheduled":
            messages = services.run_scheduled_review_automations()
            if messages:
                flash(
                    "Review automation: {0} request(s) processed.".format(
                        len(messages)
                    ),
                    "success",
                )
            else:
                flash("Review automation: no due requests.", "success")
        return redirect(url_for("review_settings"))

    return render_template(
        "review_settings.html",
        status=_integration_status(),
        review_form=review_config.settings_for_form(),
        review_requests=db.list_review_requests(40),
        automation_logs=automation.recent_logs(
            30,
            automation_types=automation.REVIEW_AUTOMATION_TYPES,
        ),
    )


@app.route("/settings/company", methods=["GET", "POST"])
@auth.login_required
def company_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "sync_xero_branding":
            try:
                ok, msg = xero.sync_invoice_branding()
            except Exception as exc:
                ok, msg = False, str(exc)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("company_settings"))

        crew_raw = request.form.get("default_crew_csv", "")
        crew = [part.strip() for part in crew_raw.split(",") if part.strip()]
        company_config.save_settings(
            {
                "default_phone": request.form.get("default_phone", "").strip(),
                "default_email": request.form.get("default_email", "").strip(),
                "default_hourly_rate": float(
                    request.form.get("default_hourly_rate", "180") or "180"
                ),
                "default_callout_fee": float(
                    request.form.get("default_callout_fee", "90") or "90"
                ),
                "default_gst_enabled": request.form.get("default_gst_enabled") == "on",
                "gst_pricing_mode": request.form.get(
                    "gst_pricing_mode", company_config.GST_MODE_INCLUSIVE
                ),
                "default_crew": crew,
                "company_name": request.form.get("company_name", "").strip(),
                "company_legal_name": request.form.get("company_legal_name", "").strip(),
                "company_location": request.form.get("company_location", "").strip(),
                "company_phone": request.form.get("company_phone", "").strip(),
                "company_email": request.form.get("company_email", "").strip(),
                "company_website": request.form.get("company_website", "").strip(),
                "bank_account_name": request.form.get("bank_account_name", "").strip(),
                "bank_bsb": request.form.get("bank_bsb", "").strip(),
                "bank_account_number": request.form.get(
                    "bank_account_number", ""
                ).strip(),
                "xero_branding_theme_id": request.form.get(
                    "xero_branding_theme_id", ""
                ).strip(),
                "xero_sync_org_header": request.form.get("xero_sync_org_header") == "on",
            }
        )
        flash("Company defaults saved.", "success")
        return redirect(url_for("company_settings"))

    branding_status = {}
    if xero.is_connected():
        try:
            branding_status = xero_branding.branding_status(xero._api_request)
        except Exception:
            branding_status = {}

    return render_template(
        "company_settings.html",
        status=_integration_status(),
        company_form=company_config.settings_for_form(),
        branding_status=branding_status,
    )


def _crew_members_for_management():
    members = db.list_crew_members()
    for member in members:
        member["booking_count"] = db.count_bookings_with_crew_name(member["name"])
    return members


def _crew_management_redirect():
    return_to = (request.form.get("return_to") or request.args.get("return_to") or "").strip()
    if return_to == "crew_schedule":
        return redirect(url_for("crew_schedule") + "#crew-management")
    return redirect(url_for("crew_schedule") + "#crew-management")


def _process_crew_management_post():
    action = request.form.get("action", "save")
    if action == "create":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Crew name is required.", "error")
        else:
            try:
                db.create_crew_member(
                    name,
                    request.form.get("phone", ""),
                    request.form.get("role", ""),
                    1,
                )
                flash("Crew member added.", "success")
            except Exception as exc:
                flash("Could not add crew member: {0}".format(exc), "error")
    elif action == "update":
        crew_id_raw = (request.form.get("crew_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        if not crew_id_raw.isdigit() or not name:
            flash("Invalid crew update.", "error")
        else:
            member = db.get_crew_member(int(crew_id_raw))
            if not member:
                flash("Crew member not found.", "error")
            else:
                ok = db.update_crew_member(
                    int(crew_id_raw),
                    name,
                    request.form.get("phone", ""),
                    request.form.get("role", ""),
                    int(member["active"]),
                )
                flash(
                    "Crew member updated." if ok else "Crew member not found.",
                    "success" if ok else "error",
                )
    elif action == "deactivate":
        crew_id_raw = (request.form.get("crew_id") or "").strip()
        if not crew_id_raw.isdigit():
            flash("Invalid crew member.", "error")
        else:
            ok = db.set_crew_member_active(int(crew_id_raw), 0)
            flash(
                "Crew member deactivated." if ok else "Crew member not found.",
                "success" if ok else "error",
            )
    elif action == "reactivate":
        crew_id_raw = (request.form.get("crew_id") or "").strip()
        if not crew_id_raw.isdigit():
            flash("Invalid crew member.", "error")
        else:
            ok = db.set_crew_member_active(int(crew_id_raw), 1)
            flash(
                "Crew member reactivated." if ok else "Crew member not found.",
                "success" if ok else "error",
            )
    elif action == "delete":
        crew_id_raw = (request.form.get("crew_id") or "").strip()
        if not crew_id_raw.isdigit():
            flash("Invalid crew member.", "error")
        else:
            member = db.get_crew_member(int(crew_id_raw))
            if not member:
                flash("Crew member not found.", "error")
            else:
                history_count = db.count_bookings_with_crew_name(member["name"])
                if history_count:
                    flash(
                        "Cannot delete {0}: used on {1} booking(s). Deactivate instead.".format(
                            member["name"], history_count
                        ),
                        "error",
                    )
                elif db.delete_crew_member(int(crew_id_raw)):
                    flash("Crew member deleted.", "success")
                else:
                    flash("Crew member not found.", "error")


@app.route("/settings/crew", methods=["GET", "POST"], endpoint="crew_management")
@auth.admin_required
def crew_management():
    if request.method == "POST":
        _process_crew_management_post()
        return _crew_management_redirect()

    return redirect(url_for("crew_schedule") + "#crew-management")


@app.route("/settings/trucks", methods=["GET", "POST"], endpoint="truck_management")
@auth.login_required
def truck_management():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "create":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Truck name is required.", "error")
            else:
                try:
                    db.create_truck(
                        name,
                        request.form.get("registration", ""),
                        request.form.get("truck_type", ""),
                        request.form.get("capacity", ""),
                        1 if request.form.get("active") == "on" else 0,
                    )
                    flash("Truck added.", "success")
                except Exception as exc:
                    flash("Could not add truck: {0}".format(exc), "error")
        elif action == "update":
            truck_id_raw = (request.form.get("truck_id") or "").strip()
            name = (request.form.get("name") or "").strip()
            if not truck_id_raw.isdigit() or not name:
                flash("Invalid truck update.", "error")
            else:
                ok = db.update_truck(
                    int(truck_id_raw),
                    name,
                    request.form.get("registration", ""),
                    request.form.get("truck_type", ""),
                    request.form.get("capacity", ""),
                    1 if request.form.get("active") == "on" else 0,
                )
                flash(
                    "Truck updated." if ok else "Truck not found.",
                    "success" if ok else "error",
                )
        return redirect(url_for("truck_management"))

    return render_template(
        "truck_management.html",
        trucks=db.list_trucks(),
    )


@app.route("/settings/sms", methods=["GET", "POST"])
@auth.login_required
def sms_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            automation_enabled = request.form.get("automation_enabled") == "on"
            triggers = {
                key: request.form.get("trigger_{0}".format(key)) == "on"
                for key in sms_config.TEMPLATE_KEYS
            }
            templates = {
                key: request.form.get("template_{0}".format(key), "")
                for key in sms_config.TEMPLATE_KEYS
            }
            sms_config.save_settings(automation_enabled, triggers, templates)
            flash("SMS automation settings saved.", "success")
        elif action == "test_sms":
            template_key = request.form.get("test_template", "booking_reminder")
            test_phone = request.form.get("test_phone", "").strip()
            if template_key not in sms_config.TEMPLATE_KEYS:
                flash("Invalid template selected.", "error")
            else:
                ok, msg = services.send_sms_test(template_key, test_phone)
                flash(msg, "success" if ok else "error")
        elif action == "run_scheduled":
            results = services.run_scheduled_sms_automations()
            total = sum(len(v) for v in results.values())
            if total:
                flash(
                    "Scheduled SMS run: {0} message(s) processed.".format(total),
                    "success",
                )
            else:
                flash("Scheduled SMS run: no messages to send.", "success")
        return redirect(url_for("sms_settings"))

    return render_template(
        "sms_settings.html",
        status=_integration_status(),
        sms_form=sms_config.settings_for_form(),
        template_choices=sms_config.template_choices(),
        twilio_from_number=config.TWILIO_FROM_NUMBER,
        delivery_logs=db.list_sms_delivery_logs(30),
        automation_logs=automation.recent_logs(
            30, automation_types=automation.SMS_AUTOMATION_TYPES
        ),
    )


@app.route("/settings/bank-transfers", methods=["GET", "POST"])
@auth.login_required
def bank_transfers():
    if request.method == "POST":
        action = (request.form.get("action") or "import").strip()
        if action == "rematch_unmatched":
            summary = bank_transfer_match.rematch_unmatched_transactions()
            flash(
                "Re-matched {rematched} unmatched transaction(s): {paid} paid, "
                "{mismatches} mismatch, {unmatched} still unmatched.".format(
                    rematched=summary["rematched"],
                    paid=summary["paid"],
                    mismatches=summary["mismatches"],
                    unmatched=summary["unmatched"],
                ),
                "warning" if summary["mismatches"] else "success",
            )
            return redirect(url_for("bank_transfers"))
        upload = request.files.get("csv_file")
        if upload is None or not (upload.filename or "").strip():
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for("bank_transfers"))
        raw = upload.read() or b""
        if len(raw) > 5 * 1024 * 1024:
            flash("CSV file is too large (max 5 MB).", "error")
            return redirect(url_for("bank_transfers"))
        parsed = bank_transfer_match.parse_bank_csv_bytes(raw)
        if not parsed:
            flash("No bank transactions found in that CSV.", "error")
            return redirect(url_for("bank_transfers"))
        summary = bank_transfer_match.import_bank_transactions(parsed)
        flash(
            "Imported {imported} transaction(s): {paid} paid, "
            "{mismatches} mismatch, {unmatched} unmatched, "
            "{rematched} re-matched, {skipped} skipped (already processed).".format(
                imported=summary["imported"],
                paid=summary["paid"],
                mismatches=summary["mismatches"],
                unmatched=summary["unmatched"],
                rematched=summary.get("rematched") or 0,
                skipped=summary["skipped"],
            ),
            "warning" if summary["mismatches"] else "success",
        )
        return redirect(url_for("bank_transfers"))

    unmatched_count = db.count_bank_transactions(match_status="unmatched")
    return render_template(
        "bank_transfers.html",
        transactions=db.list_bank_transactions(limit=100),
        import_summary=None,
        unmatched_count=unmatched_count,
    )


@app.route("/settings/xero", methods=["GET", "POST"])
@auth.login_required
def xero_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            client_id = request.form.get("client_id", "").strip()
            client_secret = request.form.get("client_secret", "").strip()
            tenant_id = request.form.get("tenant_id", "").strip()
            if not client_id:
                flash("Client ID is required.", "error")
            elif not client_secret and not xero_config.has_stored_secret():
                flash("Client Secret is required on first save.", "error")
            elif client_secret.lower().startswith("http://") or client_secret.lower().startswith("https://"):
                flash(
                    "That looks like a redirect URL, not a Client Secret. "
                    "Copy the secret from developer.xero.com → your app → Client secret.",
                    "error",
                )
            else:
                result = xero_config.save_settings(
                    client_id,
                    client_secret,
                    tenant_id,
                    auto_create_draft_on_confirmed=(
                        request.form.get("auto_create_draft_on_confirmed") == "on"
                    ),
                    auto_create_on_booking_create=(
                        request.form.get("auto_create_on_booking_create") == "on"
                    ),
                )
                if result.get("secret_updated"):
                    flash(
                        "Xero settings saved. Client secret stored securely.",
                        "success",
                    )
                elif result.get("secret_preserved"):
                    flash(
                        "Xero settings saved. Client secret unchanged (still stored).",
                        "success",
                    )
                else:
                    flash("Xero settings saved.", "success")
        return redirect(url_for("xero_settings"))

    tenants = xero.list_tenant_options() if xero.is_connected() else []
    xero_form = xero_config.settings_for_form()
    xero_form["credentials_debug"] = xero_config.credentials_debug()
    xero_form["credentials_ok"] = xero_config.has_credentials()
    return render_template(
        "xero_settings.html",
        status=_integration_status(),
        xero_form=xero_form,
        tenants=tenants,
        config_redirect_uri=xero.resolve_redirect_uri(config.XERO_REDIRECT_URI),
    )


@app.route("/settings/stripe", methods=["GET", "POST"])
@auth.login_required
def stripe_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            try:
                pct = float(request.form.get("card_surcharge_percent", "2.0") or "2.0")
            except ValueError:
                pct = 2.0
            result = stripe_config.save_settings(
                stripe_enabled=request.form.get("stripe_enabled") == "on",
                publishable_key=request.form.get("publishable_key", "").strip(),
                secret_key=request.form.get("secret_key", "").strip(),
                webhook_secret=request.form.get("webhook_secret", "").strip(),
                card_surcharge_percent=pct,
                xero_payment_account_code=request.form.get(
                    "xero_payment_account_code", ""
                ).strip(),
            )
            if result.get("publishable_key_rejected"):
                flash(
                    "Publishable key not saved — must start with pk_live_ or pk_test_ "
                    "(copy from Stripe Dashboard → Developers → API keys).",
                    "error",
                )
            elif result.get("stripe_enabled_rejected"):
                flash(
                    "Stripe not enabled — enter a valid secret key (sk_live_… or sk_test_…) and save again.",
                    "error",
                )
            elif result.get("secret_updated"):
                flash("Stripe settings saved. Secret key stored securely.", "success")
            else:
                flash("Stripe settings saved.", "success")
        return redirect(url_for("stripe_settings"))

    return render_template(
        "stripe_settings.html",
        status=_integration_status(),
        stripe_form=stripe_config.settings_for_form(),
    )


# --- Integration OAuth ---


def _clear_google_oauth_session() -> None:
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_redirect_uri", None)


@app.route("/integrations/google/connect")
@auth.login_required
def google_connect():
    if not google_calendar.is_configured():
        flash("Add Google credentials file first (see SETUP.md).", "error")
        return redirect(url_for("settings"))
    redirect_uri = google_oauth.resolve_redirect_uri()
    auth_url, state = google_calendar.begin_oauth(redirect_uri)
    session["google_oauth_state"] = state
    session["google_oauth_redirect_uri"] = redirect_uri
    return redirect(auth_url)


@app.route("/integrations/google/callback")
@auth.login_required
def google_callback():
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    redirect_uri = google_oauth.resolve_redirect_uri(
        session.get("google_oauth_redirect_uri") or ""
    )
    try:
        google_calendar.complete_oauth(redirect_uri, request.url)
        _clear_google_oauth_session()
        flash("Google connected (Calendar + Gmail inbox scopes).", "success")
    except Exception as exc:
        flash("Google connection failed: {0}".format(exc), "error")
    return redirect(url_for("settings"))


@app.route("/review/go/<token>")
def review_go(token):
    """Track click and redirect to Google review page."""
    request_row = review_automation.record_click(token)
    google_url = review_config.get_google_review_url()
    if request_row and google_url:
        return redirect(google_url)
    return render_template("review_error.html"), 404


@app.route("/review/done/<token>", methods=["GET", "POST"])
def review_done(token):
    """Customer confirms they left a Google review."""
    row = db.get_review_request_by_token(token)
    if not row:
        return render_template("review_error.html"), 404

    if request.method == "POST":
        ok, msg = review_automation.mark_reviewed(token, by_staff=False)
        flash(msg, "success" if ok else "error")
        return render_template(
            "review_done.html",
            confirmed=True,
            company_name=config.COMPANY_NAME,
        )

    return render_template(
        "review_done.html",
        confirmed=False,
        token=token,
        company_name=config.COMPANY_NAME,
    )


@app.route("/pay/<token>")
def customer_pay(token):
    """Public Pay Now link from invoice PDF — redirects to Stripe Checkout."""
    row = db.get_booking_by_payment_token(token)
    if not row:
        return render_template(
            "pay_result.html",
            success=False,
            title="Not found",
            message="Payment link not found.",
            company_name=config.COMPANY_NAME,
        ), 404

    booking = services.booking_to_dict(row)
    if (booking.get("payment_status") or "").strip() == invoice.PAYMENT_STATUS_PAID:
        return render_template(
            "pay_result.html",
            success=True,
            title="Already paid",
            message="This invoice has already been paid. Thank you.",
            company_name=config.COMPANY_NAME,
            booking=booking,
        )

    if not stripe_config.invoice_card_payments_enabled():
        from integrations.invoice_pdf import build_invoice_document

        doc = build_invoice_document(invoice.resolve_booking_invoice(booking))
        totals = doc["totals"]
        return render_template(
            "pay_bank_transfer.html",
            company_name=config.COMPANY_NAME,
            bank=doc["bank"],
            invoice_number=doc["invoice_number"],
            total_display=invoice.format_aud(totals["total"]),
        )

    success_url = (
        url_for("customer_pay_success", token=token, _external=True)
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = url_for("customer_pay_cancel", token=token, _external=True)
    ok, msg, checkout_url = services.start_public_stripe_checkout(
        token,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if ok and checkout_url:
        return redirect(checkout_url)
    return render_template(
        "pay_result.html",
        success=False,
        title="Payment unavailable",
        message=msg,
        company_name=config.COMPANY_NAME,
    ), 400


@app.route("/pay/<token>/success")
def customer_pay_success(token):
    row = db.get_booking_by_payment_token(token)
    if not row:
        return render_template("pay_result.html", success=False, title="Not found", message="Payment link not found.", company_name=config.COMPANY_NAME), 404
    booking = services.booking_to_dict(row)
    paid = (booking.get("payment_status") or "").strip() == invoice.PAYMENT_STATUS_PAID
    return render_template(
        "pay_result.html",
        success=True,
        title="Payment received" if paid else "Payment submitted",
        message=(
            "Thank you — your payment has been received."
            if paid
            else "Thank you — your card payment was submitted. This page will update automatically once Stripe confirms payment."
        ),
        company_name=config.COMPANY_NAME,
        booking=booking,
    )


@app.route("/pay/<token>/cancel")
def customer_pay_cancel(token):
    row = db.get_booking_by_payment_token(token)
    invoice_number = ""
    if row:
        invoice_number = (row["invoice_number"] or "").strip()
    return render_template(
        "pay_result.html",
        success=False,
        title="Payment cancelled",
        message="No payment was taken. Please pay by bank transfer using the details on your invoice.",
        company_name=config.COMPANY_NAME,
        invoice_number=invoice_number,
        pay_url=services.prepare_booking_payment_link(int(row["id"]))
        if row and stripe_config.invoice_card_payments_enabled()
        else "",
    )


@app.route("/integrations/twilio/status", methods=["POST"])
def twilio_status_callback():
    """Twilio delivery status webhook (no login)."""
    sid = request.form.get("MessageSid", "")
    raw_status = request.form.get("MessageStatus", "")
    error = request.form.get("ErrorMessage", "") or request.form.get("ErrorCode", "")
    if sid:
        sms.update_delivery_status(sid, raw_status, str(error))
    return "", 204


@app.route("/integrations/twilio/inbound", methods=["POST"])
def twilio_inbound_sms():
    """Twilio inbound SMS webhook — create Pending booking or draft lead."""
    from_number = request.form.get("From", "")
    body = request.form.get("Body", "")
    if from_number and body:
        sms_inbound.process_inbound_sms(from_number, body)
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(twiml, mimetype="text/xml")


@app.route("/leads", methods=["GET", "POST"], endpoint="leads")
@auth.login_required
def leads_page():
    if request.method == "POST":
        action = request.form.get("action", "")
        lead_id_raw = (request.form.get("lead_id") or "").strip()
        if action == "convert" and lead_id_raw.isdigit():
            ok, msg, booking_id = sms_inbound.convert_lead_to_booking(int(lead_id_raw))
            flash(msg, "success" if ok else "error")
            if ok and booking_id:
                return redirect(url_for("edit_booking", booking_id=booking_id))
        else:
            flash("Invalid action.", "error")
        return redirect(url_for("leads"))

    leads = db.list_draft_leads(status="draft")
    for lead in leads:
        lead["reply_template"] = sms_inbound.reply_template_for_lead(lead)
    return render_template("leads.html", leads=leads)


@app.route("/integrations/stripe/webhook", methods=["GET", "POST"])
def stripe_webhook():
    """Stripe payment webhook (no login). GET = health check for dashboard URL tests."""
    if request.method == "GET":
        return {"status": "ok", "endpoint": "stripe_webhook"}, 200

    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")
    ok, msg = services.handle_stripe_webhook(payload, signature)
    if not ok:
        return msg, 400
    return "", 200


@app.route("/integrations/xero/connect")
@auth.login_required
def xero_connect():
    if not xero.has_credentials():
        flash(
            "Save Client ID and Client Secret on this page first, then click Connect Xero.",
            "error",
        )
        return redirect(url_for("xero_settings"))

    state = secrets.token_urlsafe(32)
    redirect_uri = xero.resolve_redirect_uri(
        url_for("xero_callback", _external=True)
    )
    session["xero_oauth_state"] = state
    session["xero_oauth_redirect_uri"] = redirect_uri
    oauth = xero.oauth_connect_details(state, redirect_uri)
    return render_template(
        "xero_connect.html",
        oauth=oauth,
        status=_integration_status(),
    )


@app.route("/integrations/xero/callback")
def xero_callback():
    oauth_error = request.args.get("error", "").strip()
    if oauth_error:
        detail = request.args.get("error_description", oauth_error).strip()
        session.pop("xero_oauth_state", None)
        session.pop("xero_oauth_redirect_uri", None)
        flash("Xero authorization failed: {0}".format(detail), "error")
        return redirect(url_for("xero_settings"))

    state = request.args.get("state", "")
    expected = session.pop("xero_oauth_state", None)
    redirect_uri = session.pop("xero_oauth_redirect_uri", None)
    if not expected or state != expected:
        flash(
            "OAuth session expired or state mismatch. Click Connect Xero again.",
            "error",
        )
        return redirect(url_for("xero_settings"))

    code = request.args.get("code", "")
    if not code:
        flash("Xero did not return an authorization code.", "error")
        return redirect(url_for("xero_settings"))
    ok, msg = xero.exchange_code_for_token(
        code, redirect_uri or xero.resolve_redirect_uri()
    )
    flash(msg, "success" if ok else "error")
    return redirect(url_for("xero_settings"))


# --- Bookings ---


@app.route("/", methods=["GET", "POST"], endpoint="ceo_dashboard")
@auth.login_required
def ceo_dashboard():
    if request.method == "POST":
        action = request.form.get("action", "")
        booking_id_raw = (request.form.get("booking_id") or "").strip()
        if action == "send_reminder" and booking_id_raw.isdigit():
            ok, msg = services.send_payment_reminder_now(int(booking_id_raw))
            flash(msg, "success" if ok else "error")
        else:
            flash("Invalid action.", "error")
        return redirect(url_for("ceo_dashboard"))

    data = build_ceo_dashboard(date.today(), _integration_status())
    return render_template(
        "ceo_dashboard.html",
        ceo=data,
        status=_integration_status(),
    )


@app.route("/dashboard", endpoint="dashboard", methods=["GET", "POST"])
@auth.login_required
def dashboard():
    today = perth_today()
    active_filter = request.args.get("filter", "all").strip().lower()
    valid_filters = {key for key, _ in job_status.DASHBOARD_FILTERS}
    if active_filter not in valid_filters:
        active_filter = "all"

    if request.method == "POST":
        if request.form.get("action") == "sync_xero_payments":
            result = services.sync_xero_payments()
            if not result.get("ok"):
                flash(result.get("message") or "Xero payment sync failed.", "error")
            elif result.get("errors"):
                flash(
                    "{0} {1} error(s) — no bookings were changed for failed rows.".format(
                        result.get("message") or "Sync finished.",
                        len(result.get("errors") or []),
                    ),
                    "warning",
                )
            else:
                flash(result.get("message") or "Xero payment sync finished.", "success")
        return redirect(
            url_for(
                "dashboard",
                filter=active_filter,
                jobs_limit=request.args.get("jobs_limit"),
                profit_month=request.args.get("profit_month"),
                profit_status=request.args.get("profit_status"),
                profit_paid_only=request.args.get("profit_paid_only"),
            )
        )

    dash = build_dashboard(today)
    jobs = dashboard_jobs(active_filter, today)
    jobs_limit = parse_jobs_limit(request.args.get("jobs_limit"), len(jobs))
    visible_jobs, jobs_total, has_more_jobs, next_jobs_limit = paginate_dashboard_jobs(
        jobs, jobs_limit
    )
    job_dicts = [dict(row) for row in visible_jobs]
    conflict_badges = double_booking.badges_for_bookings(job_dicts)
    enriched_jobs = []
    for row in job_dicts:
        item = dict(row)
        item["double_booking_badge"] = conflict_badges.get(int(item["id"]))
        item["dashboard_invoice_number"] = dashboard_invoice_number_for_row(item)
        enriched_jobs.append(item)
    profit_month = request.args.get(
        "profit_month", today.strftime("%Y-%m")
    ).strip()
    if len(profit_month) != 7 or profit_month[4] != "-":
        profit_month = today.strftime("%Y-%m")
    profit_status = request.args.get("profit_status", "all").strip()
    valid_profit_status = {key for key, _ in booking_profit.PROFIT_STATUS_FILTERS}
    if profit_status not in valid_profit_status:
        profit_status = "all"
    profit_paid_only = request.args.get("profit_paid_only") == "1"
    monthly_profit = booking_profit.build_monthly_profit_summary(
        profit_month,
        status_filter=profit_status,
        paid_only=profit_paid_only,
    )
    load_more_url = None
    if has_more_jobs:
        load_more_url = url_for(
            "dashboard",
            filter=active_filter,
            jobs_limit=next_jobs_limit,
            profit_month=profit_month,
            profit_status=profit_status,
            profit_paid_only=1 if profit_paid_only else None,
        )
    section_layout = dashboard_section_layout(enriched_jobs, dash["today"])
    unpaid_summaries = dashboard_unpaid_summaries(jobs, dash["today"])
    return render_template(
        "dashboard.html",
        dash=dash,
        today=dash["today"],
        jobs=enriched_jobs,
        upcoming_divider_index=section_layout["upcoming"],
        payment_unpaid_index=section_layout["unpaid"],
        payment_wg_index=section_layout["wg"],
        payment_wg_last_index=section_layout["wg_last"],
        payment_paid_index=section_layout["paid"],
        unpaid_summaries=unpaid_summaries,
        jobs_total=jobs_total,
        jobs_limit=jobs_limit,
        has_more_jobs=has_more_jobs,
        load_more_url=load_more_url,
        active_filter=active_filter,
        monthly_profit=monthly_profit,
        profit_month=profit_month,
        profit_status=profit_status,
        profit_paid_only=profit_paid_only,
        profit_status_filters=booking_profit.PROFIT_STATUS_FILTERS,
        status=_integration_status(),
        xero_payment_last_sync=xero_payment_sync.dashboard_last_sync_display(),
    )


@app.route("/bookings/<int:booking_id>/payment", methods=["POST"])
@auth.login_required
def update_booking_payment_inline(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        return jsonify({"error": "Booking not found."}), 404

    payload = request.get_json(silent=True) or {}
    new_status = invoice.validate_dashboard_inline_payment(
        payload.get("payment_status")
        or payload.get("status")
        or request.form.get("payment_status")
    )
    if not new_status:
        return jsonify({"error": "Invalid payment status."}), 400

    booking = services.booking_to_dict(row)
    current = invoice.normalize_payment_status(booking.get("payment_status"))
    if new_status != current:
        invoice.apply_payment_status(booking_id, new_status)

    updated = services.booking_to_dict(db.get_booking(booking_id) or {})
    job = job_status.display(updated)
    return jsonify(
        {
            "ok": True,
            "payment_status": new_status,
            "css_class": invoice.payment_status_css(new_status),
            "job_status": job,
            "job_status_css": job_status.css_class(job),
        }
    )


@app.route("/bookings/<int:booking_id>/status", methods=["POST"])
@auth.login_required
def update_booking_status_inline(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        return jsonify({"error": "Booking not found."}), 404

    payload = request.get_json(silent=True) or {}
    new_status = job_status.validate_dashboard_inline(
        payload.get("status") or request.form.get("status")
    )
    if not new_status:
        return jsonify({"error": "Invalid status."}), 400

    booking = services.booking_to_dict(row)
    previous_status = job_status.display(booking)
    if new_status != previous_status:
        db.update_booking_status(booking_id, new_status)
        services.after_booking_updated(booking_id, previous_status=previous_status)

    return jsonify(
        {
            "ok": True,
            "status": new_status,
            "css_class": job_status.css_class(new_status),
        }
    )


@app.route("/calendar", endpoint="booking_calendar")
@auth.login_required
def booking_calendar():
    today = date.today()
    view = request.args.get("view", "month").strip().lower()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        day = int(request.args.get("day", today.day))
    except (TypeError, ValueError):
        year, month, day = today.year, today.month, today.day
    month = max(1, min(12, month))
    day = max(1, min(monthrange(year, month)[1], day))

    cal = calendar_data.build_calendar_context(
        view=view,
        year=year,
        month=month,
        day=day,
        status_filter=request.args.get("status", "all").strip(),
        crew_filter=request.args.get("crew", "all").strip(),
        truck_filter=request.args.get("truck", "all").strip(),
        payment_filter=request.args.get("payment", "all").strip(),
        today=today,
    )
    return render_template("calendar.html", cal=cal)


def _calendar_back_url(**kwargs) -> str:
    params = {key: value for key, value in kwargs.items() if value not in (None, "")}
    return url_for("booking_calendar", **params)


@app.route("/calendar/daily/<date_iso>", endpoint="calendar_daily_jobs")
@auth.login_required
def calendar_daily_jobs(date_iso):
    try:
        datetime.strptime(date_iso[:10], "%Y-%m-%d")
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("booking_calendar"))

    daily = build_daily_jobs(date_iso[:10])
    back_to_calendar_url = _calendar_back_url(
        view=request.args.get("view", "month"),
        year=request.args.get("year"),
        month=request.args.get("month"),
        day=request.args.get("day"),
        status=request.args.get("status"),
        crew=request.args.get("crew"),
        truck=request.args.get("truck"),
        payment=request.args.get("payment"),
    )
    return render_template(
        "daily_jobs.html",
        daily=daily,
        back_to_calendar_url=back_to_calendar_url,
    )


def _weekly_calendar_params() -> dict:
    return {
        "view": request.args.get("view", "month"),
        "year": request.args.get("year"),
        "month": request.args.get("month"),
        "day": request.args.get("day"),
        "status": request.args.get("status"),
        "crew": request.args.get("crew"),
        "truck": request.args.get("truck"),
        "payment": request.args.get("payment"),
    }


@app.route("/calendar/weekly", endpoint="calendar_weekly_schedule")
@app.route("/calendar/weekly/<date_iso>", endpoint="calendar_weekly_schedule")
@auth.login_required
def calendar_weekly_schedule(date_iso=None):
    raw = (date_iso or perth_today().isoformat())[:10]
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("booking_calendar"))

    weekly = build_weekly_schedule(raw)
    calendar_params = _weekly_calendar_params()
    back_to_calendar_url = _calendar_back_url(**calendar_params)
    return render_template(
        "weekly_schedule.html",
        weekly=weekly,
        calendar_params=calendar_params,
        back_to_calendar_url=back_to_calendar_url,
    )


@app.route(
    "/calendar/weekly/<date_iso>/schedule.pdf",
    endpoint="calendar_weekly_schedule_pdf",
)
@auth.login_required
def calendar_weekly_schedule_pdf(date_iso):
    raw = (date_iso or "")[:10]
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("booking_calendar"))

    weekly = build_weekly_schedule(raw)
    pdf_bytes = weekly_schedule_pdf.render_weekly_schedule_pdf(weekly)
    filename = "weekly-schedule-{0}.pdf".format(weekly["week_start"])
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename={0}".format(filename)
        },
    )


@app.route("/settings/gmail", methods=["GET", "POST"])
@auth.login_required
def gmail_settings():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            gmail_config.save_settings(
                automation_enabled=request.form.get("automation_enabled") == "on",
                inbox_query=request.form.get("inbox_query", "is:unread"),
                admin_notify_email=request.form.get("admin_notify_email", "").strip(),
            )
            flash("Gmail inbox settings saved.", "success")
        elif action == "run_inbox_check":
            results = services.run_gmail_inbox_monitor()
            if results:
                flash("; ".join(results[:3]), "success")
            else:
                flash("Inbox check completed.", "success")
        return redirect(url_for("gmail_settings"))

    return render_template(
        "gmail_settings.html",
        status=_integration_status(),
        gmail_form=gmail_config.settings_for_form(),
        processed_messages=db.list_processed_gmail_messages(30),
        automation_logs=automation.recent_logs(
            30, automation_types=automation.GMAIL_AUTOMATION_TYPES
        ),
    )


@app.route("/automation", endpoint="automation_hub")
@auth.login_required
def automation_hub():
    return render_template(
        "automation_hub.html",
        logs=automation.recent_logs(50),
        delivery_logs=db.list_sms_delivery_logs(20),
        review_requests=db.list_review_requests(15),
        status=_integration_status(),
    )


@app.route("/executive", methods=["GET", "POST"], endpoint="executive_dashboard")
@auth.login_required
def executive_dashboard():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "save_target":
            raw = request.form.get("monthly_revenue_target", "").strip()
            try:
                executive_config.save_monthly_revenue_target(float(raw))
                flash("Monthly revenue target updated.", "success")
            except ValueError:
                flash("Enter a valid revenue target amount.", "error")
        return redirect(url_for("executive_dashboard"))

    data = build_executive_dashboard(date.today())
    return render_template("executive_dashboard.html", exec=data)


@app.route("/invoices", endpoint="outstanding_invoices")
@auth.login_required
def outstanding_invoices():
    active_filter = request.args.get("filter", "unpaid").strip().lower()
    data = build_outstanding_dashboard(active_filter, date.today())
    return render_template(
        "outstanding_invoices.html",
        inv=data,
        active_filter=data["active_filter"],
    )


@app.route("/profit", endpoint="profit")
@auth.login_required
def profit_dashboard():
    data = build_profit_dashboard(date.today())
    return render_template("profit_dashboard.html", profit=data)


@app.route("/profit/export.csv", endpoint="profit_export_csv")
@auth.login_required
def profit_export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(PROFIT_CSV_HEADERS)
    for row in profit_csv_rows():
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=japanese-removals-profit.csv"
        },
    )


def _staff_login_names():
    return list(all_crew_names() or active_crew_names() or CREW_OPTIONS)


def _crew_id_for_name(staff_name: str):
    wanted = str(staff_name or "").strip()
    if not wanted:
        return None
    for member in db.list_crew_members(active_only=False):
        if str(member.get("name") or "").strip() == wanted:
            return member.get("id")
    return None


@app.route("/staff/login", methods=["GET", "POST"], endpoint="staff_login")
def staff_login():
    if staff_auth.is_staff_logged_in():
        return redirect(staff_auth.safe_staff_next(request.args.get("next")))

    crew_names = _staff_login_names()
    error = ""
    selected_staff = (request.form.get("staff") or "").strip()
    if request.method == "POST":
        password = request.form.get("password", "")
        if selected_staff not in crew_names:
            error = "Select your name."
        elif staff_auth.verify_staff_password(password):
            dest = staff_auth.safe_staff_next(
                request.args.get("next") or request.form.get("next")
            )
            response = redirect(dest)
            staff_auth.attach_staff_session(
                response,
                selected_staff,
                staff_id=_crew_id_for_name(selected_staff),
            )
            return response
        else:
            error = "Invalid password."
    return render_template(
        "staff_login.html",
        error=error,
        next_url=staff_auth.safe_staff_next(request.args.get("next")),
        staff_options=crew_names,
        selected_staff=selected_staff,
    )


@app.route("/staff/logout", endpoint="staff_logout")
def staff_logout():
    response = redirect(url_for("staff_login"))
    staff_auth.clear_staff_session(response)
    return response


@app.route("/staff", endpoint="staff_portal")
@staff_auth.staff_login_required
def staff_portal():
    session_staff = staff_auth.logged_in_staff_name() if staff_auth.is_staff_logged_in() else ""
    session_staff_id = staff_auth.logged_in_staff_id() if staff_auth.is_staff_logged_in() else None
    if request.args.get("staff_id") is not None:
        view_staff_id = request.args.get("staff_id", "").strip() or STAFF_VIEW_ALL
    elif session_staff:
        view_staff_id = session_staff_id or _crew_id_for_name(session_staff) or session_staff
    else:
        view_staff_id = STAFF_VIEW_ALL
    range_key = request.args.get("range", "today").strip()
    week_offset = request.args.get("week", "0").strip()
    cal_year = request.args.get("year", "").strip() or None
    cal_month = request.args.get("month", "").strip() or None
    cal_day = request.args.get("day", "").strip() or None
    cal_view = request.args.get("cal_view", "").strip() or None
    portal = build_staff_portal(
        session_staff,
        range_key,
        perth_today(),
        week_offset=week_offset,
        staff_id=session_staff_id,
        calendar_year=cal_year,
        calendar_month=cal_month,
        calendar_day=cal_day,
        view_staff_id=view_staff_id,
        cal_view=cal_view,
    )
    return render_template(
        "staff_portal.html",
        portal=portal,
        staff_portal_open=staff_auth.staff_portal_open_access(),
        staff_logged_in=staff_auth.is_staff_logged_in(),
        can_manage_staff_portal=staff_portal_owner.can_manage_staff_portal(g.user),
        crew_options=active_crew_names(),
        staff_portal_status_options=staff_portal_owner.STAFF_PORTAL_STATUS_OPTIONS,
    )


@app.route("/staff/weekly/schedule.pdf", endpoint="staff_portal_weekly_pdf")
@staff_auth.staff_login_required
def staff_portal_weekly_pdf():
    if request.args.get("staff_id") is not None:
        view_staff_id = request.args.get("staff_id", "").strip() or STAFF_VIEW_ALL
    elif staff_auth.is_staff_logged_in():
        view_staff_id = (
            staff_auth.logged_in_staff_id()
            or _crew_id_for_name(staff_auth.logged_in_staff_name())
            or STAFF_VIEW_ALL
        )
    else:
        view_staff_id = STAFF_VIEW_ALL
    week_offset = request.args.get("week", "0").strip()
    schedule = build_staff_weekly_pdf_schedule(
        view_staff_id, week_offset, perth_today()
    )
    pdf_bytes = weekly_schedule_pdf.render_staff_weekly_schedule_pdf(schedule)
    if schedule.get("mode") == "all":
        filename = "weekly-schedule-{0}.pdf".format(schedule["week_start"])
    else:
        staff_key = schedule.get("staff_id_key") or STAFF_VIEW_ALL
        filename = "staff-weekly-{0}-{1}.pdf".format(
            staff_key, schedule.get("week_start")
        )
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename={0}".format(filename)
        },
    )


@app.route(
    "/staff/crew/<int:crew_id>/rename",
    methods=["POST"],
    endpoint="staff_portal_rename_crew",
)
@staff_portal_owner.owner_required
def staff_portal_rename_crew(crew_id: int):
    new_name = (request.form.get("name") or "").strip()
    range_key = (request.form.get("range") or "week").strip()
    week_offset = (request.form.get("week") or "0").strip()
    if not new_name:
        flash("Enter a staff name.", "error")
    elif db.rename_crew_member(crew_id, new_name):
        flash("Staff name updated.", "success")
    else:
        flash("Could not update staff name.", "error")
    params = portal_nav_params(
        staff_id=crew_id,
        range_key=range_key,
        week_offset=week_offset,
    )
    return redirect(url_for("staff_portal", **params))


def _staff_portal_form_nav_params() -> dict:
    range_key = (request.form.get("range") or request.args.get("range") or "week").strip()
    week_offset = (request.form.get("week") or request.args.get("week") or "0").strip()
    staff_id = (
        request.form.get("staff_id")
        or request.args.get("staff_id")
        or STAFF_VIEW_ALL
    )
    params = portal_nav_params(
        staff_id=staff_id,
        range_key=range_key,
        week_offset=week_offset,
    )
    cal_year = (request.form.get("year") or request.args.get("year") or "").strip()
    cal_month = (request.form.get("month") or request.args.get("month") or "").strip()
    cal_day = (request.form.get("day") or request.args.get("day") or "").strip()
    if cal_year:
        params["year"] = cal_year
    if cal_month:
        params["month"] = cal_month
    if cal_day:
        params["day"] = cal_day
    return params


@app.route(
    "/staff/crew/<int:crew_id>/star-points/adjust",
    methods=["POST"],
    endpoint="staff_portal_adjust_star_points",
)
@staff_portal_owner.owner_required
def staff_portal_adjust_star_points(crew_id: int):
    action = (request.form.get("action") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip()
    delta = 1 if action == "increment" else -1 if action == "decrement" else 0
    user_id = auth.get_current_user_id()
    if delta == 0:
        flash("Invalid Star Points action.", "error")
    else:
        new_value = db.adjust_crew_star_points(
            crew_id, delta, reason=reason, user_id=user_id
        )
        if new_value is None:
            flash("Staff member not found.", "error")
        else:
            flash("Star Points updated to {0} / 10.".format(new_value), "success")
    return redirect(url_for("staff_portal", **_staff_portal_form_nav_params()))


@app.route(
    "/staff/crew/<int:crew_id>/star-points/reset",
    methods=["POST"],
    endpoint="staff_portal_reset_star_points",
)
@staff_portal_owner.owner_required
def staff_portal_reset_star_points(crew_id: int):
    if (request.form.get("confirm") or "").strip() != "1":
        flash("Star Points reset cancelled.", "error")
        return redirect(url_for("staff_portal", **_staff_portal_form_nav_params()))
    member = db.get_crew_member(crew_id)
    if not member:
        flash("Staff member not found.", "error")
    elif db.reset_crew_star_points(
        crew_id,
        reason=(request.form.get("reason") or "").strip(),
        user_id=auth.get_current_user_id(),
    ):
        flash("Star Points reset to 0 / 10.", "success")
    else:
        flash("Could not reset Star Points.", "error")
    return redirect(url_for("staff_portal", **_staff_portal_form_nav_params()))


def _staff_portal_redirect():
    range_key = (request.form.get("range") or request.args.get("range") or "today").strip()
    week_offset = (request.form.get("week") or request.args.get("week") or "0").strip()
    staff_id = (
        request.form.get("staff_id")
        or request.args.get("staff_id")
        or STAFF_VIEW_ALL
    )
    params = portal_nav_params(
        staff_id=staff_id,
        range_key=range_key,
        week_offset=week_offset,
    )
    cal_year = (request.form.get("year") or request.args.get("year") or "").strip()
    cal_month = (request.form.get("month") or request.args.get("month") or "").strip()
    cal_day = (request.form.get("day") or request.args.get("day") or "").strip()
    cal_view = (request.form.get("cal_view") or request.args.get("cal_view") or "").strip()
    if cal_year:
        params["year"] = cal_year
    if cal_month:
        params["month"] = cal_month
    if cal_day:
        params["day"] = cal_day
    if cal_view:
        params["cal_view"] = cal_view
    return redirect(url_for("staff_portal", **params))


@app.route(
    "/staff/bookings/<int:booking_id>/edit",
    methods=["POST"],
    endpoint="staff_portal_edit_booking",
)
@staff_portal_owner.owner_required
def staff_portal_edit_booking(booking_id: int):
    row = db.get_booking(booking_id)
    if row is None:
        abort(404)
    dest = _staff_portal_redirect()
    action = str(request.form.get("action") or "").strip().lower()
    staff_id_raw = str(request.form.get("staff_id") or "").strip()
    individual_crew_id = None
    if staff_id_raw and staff_id_raw.lower() != "all":
        try:
            individual_crew_id = int(staff_id_raw)
        except (TypeError, ValueError):
            individual_crew_id = None

    if individual_crew_id is not None:
        from crew import crew_from_storage

        crew_names = crew_from_storage(dict(row).get("crew"))
        roster = {int(m["id"]): m["name"] for m in db.list_crew_members(active_only=False)}
        crew_name = roster.get(individual_crew_id, "")
        if crew_name not in crew_names:
            flash("That staff member is not assigned to this job.", "error")
            return dest
        if action == "reset_staff_times":
            staff_portal_owner.reset_staff_crew_times_to_job(
                booking_id, individual_crew_id
            )
            flash("Personal times reset to job time.", "success")
            return dest
        if action == "clear_actual":
            staff_portal_owner.clear_staff_crew_actual_times(
                booking_id, individual_crew_id
            )
            flash("Personal actual time cleared.", "success")
            return dest
        if action == "add_extra_pay":
            staff_portal_owner.set_staff_extra_pay(
                booking_id, individual_crew_id, True
            )
            flash("Extra Pay +0.5hr added.", "success")
            return dest
        if action == "clear_extra_pay":
            staff_portal_owner.set_staff_extra_pay(
                booking_id, individual_crew_id, False
            )
            flash("Extra Pay removed.", "success")
            return dest
        ok, errors = staff_portal_owner.save_staff_crew_job_edit(
            booking_id, individual_crew_id, request.form, row
        )
    elif action == "clear_actual":
        db.save_booking_actual_times(booking_id, "", "", None)
        flash("Actual time cleared.", "success")
        return dest
    else:
        ok, errors = staff_portal_owner.save_owner_job_edit(booking_id, request.form)
    if errors:
        for message in errors:
            flash(message, "error")
        return dest
    if ok:
        flash("Job updated.", "success")
    else:
        flash("Could not save job.", "error")
    return dest


@app.route(
    "/bookings/<int:booking_id>/actual-times",
    methods=["POST"],
    endpoint="save_booking_actual_times",
)
@staff_portal_owner.owner_required
def save_booking_actual_times_view(booking_id: int):
    """Owner/Admin overwrite or clear Actual Start / Finish only."""
    row = db.get_booking(booking_id)
    if row is None:
        abort(404)
    dest = _staff_portal_redirect()
    action = str(request.form.get("action") or "save").strip().lower()
    if action == "clear":
        db.save_booking_actual_times(booking_id, "", "", None)
        flash("Actual time cleared.", "success")
        return dest
    start, finish, minutes, errors = staff_job_times.parse_actual_times_from_form(
        request.form
    )
    if errors:
        for message in errors:
            flash(message, "error")
        return dest
    db.save_booking_actual_times(booking_id, start, finish, minutes)
    flash("Actual time saved.", "success")
    return dest


@app.route("/driver", endpoint="driver")
@auth.login_required
def driver():
    crew_names = active_crew_names() or CREW_OPTIONS
    crew = request.args.get("crew", crew_names[0]).strip()
    if crew not in crew_names:
        crew = crew_names[0]
    truck = request.args.get("truck", "").strip()
    truck_names = active_truck_names()
    if truck and truck not in truck_names:
        truck = ""
    move_date = request.args.get("date", date.today().isoformat()).strip()
    sheet = build_driver_run_sheet(crew, move_date, date.today(), truck_name=truck)
    return render_template(
        "driver_run_sheet.html",
        sheet=sheet,
        active_crew=sheet["crew"],
        active_truck=truck,
        move_date=sheet["move_date"],
    )


@app.route("/driver/run-sheet.pdf", endpoint="driver_run_sheet_pdf")
@auth.login_required
def driver_run_sheet_pdf_route():
    crew_names = active_crew_names() or CREW_OPTIONS
    crew = request.args.get("crew", crew_names[0]).strip()
    if crew not in crew_names:
        crew = crew_names[0]
    truck = request.args.get("truck", "").strip()
    truck_names = active_truck_names()
    if truck and truck not in truck_names:
        truck = ""
    move_date = request.args.get("date", date.today().isoformat()).strip()
    sheet = build_driver_run_sheet(crew, move_date, date.today(), truck_name=truck)
    pdf_bytes = driver_run_sheet_pdf.generate_driver_run_sheet_pdf(
        sheet["crew"],
        sheet["move_date"],
        sheet["jobs"],
        sheet["jobs_label"],
        sheet["hours_label"],
    )
    filename = "run-sheet-{0}-{1}.pdf".format(
        sheet["crew"].lower(), sheet["move_date"]
    )
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename={0}".format(filename)},
    )


@app.route("/crew-schedule", endpoint="crew_schedule")
@auth.login_required
def crew_schedule():
    active_range = request.args.get("range", "this_week").strip().lower()
    custom_date = request.args.get("date", "").strip()
    schedule = build_crew_schedule(
        active_range, date.today(), custom_date=custom_date or None
    )
    context = {
        "schedule": schedule,
        "active_range": schedule["active_range"],
    }
    if auth.is_admin_user(g.user):
        context["crew_members"] = _crew_members_for_management()
    return render_template("crew_schedule.html", **context)


@app.route("/daily-checklist", methods=["GET", "POST"], endpoint="daily_checklist")
@auth.login_required
def daily_checklist():
    if request.method == "POST":
        action = request.form.get("action", "")
        booking_id_raw = (request.form.get("booking_id") or "").strip()
        active_filter = request.form.get("filter", "today").strip()
        if action == "mark_completed" and booking_id_raw.isdigit():
            ok, msg = services.mark_booking_completed(int(booking_id_raw))
            flash(msg, "success" if ok else "error")
        else:
            flash("Invalid action.", "error")
        return redirect(url_for("daily_checklist", filter=active_filter))

    active_filter = request.args.get("filter", "today").strip().lower()
    checklist = build_daily_checklist(active_filter, date.today())
    return render_template(
        "daily_checklist.html",
        checklist=checklist,
        active_filter=checklist["filter"],
    )


def _form_invoice_summary(form_data: dict) -> dict:
    payload = dict(form_data)
    payload.setdefault("extra_charges", [])
    return invoice.invoice_summary(payload)


@app.route("/bookings/new", methods=["GET", "POST"])
@auth.login_required
def new_booking():
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "analyse_paste":
            from integrations import enquiry_parser

            paste_text = request.form.get("paste_text", "")
            parsed = enquiry_parser.parse_pasted_text(paste_text)
            form = _booking_form_defaults()
            move_date = request.form.get("move_date", "").strip()
            if move_date:
                form["move_date"] = move_date
            form = enquiry_parser.apply_parsed_fields(form, parsed)
            if not paste_text.strip():
                flash("Paste customer information first, then click Analyse.", "error")
            else:
                flash("Customer details analysed — review the fields below.", "success")
            return render_template(
                "new_booking.html",
                form=form,
                paste_text=paste_text,
                parsed_summary=enquiry_parser.summary_rows(parsed),
                parsed_warnings=parsed.get("warnings") or [],
                crew_warnings=[],
                **_double_booking_context(form),
            )

        data, errors = parse_booking_form(request.form)
        _copy_job_cost_form_values(data, request.form)
        crew_warnings = _crew_warnings_for_data(data)
        db_errors, db_conflicts, override_applied = _validate_double_booking(data)
        cost_errors = booking_profit.job_cost_form_errors(request.form)
        if errors or db_errors or cost_errors:
            for msg in errors + db_errors + cost_errors:
                flash(msg, "error")
            ctx = _double_booking_context(data)
            paste_text = request.form.get("paste_text", "")
            parsed_summary = None
            parsed_warnings = []
            if paste_text.strip():
                from integrations import enquiry_parser

                reparsed = enquiry_parser.parse_pasted_text(paste_text)
                parsed_summary = enquiry_parser.summary_rows(reparsed)
                parsed_warnings = reparsed.get("warnings") or []
            return render_template(
                "new_booking.html",
                form=data,
                paste_text=paste_text,
                parsed_summary=parsed_summary,
                parsed_warnings=parsed_warnings,
                crew_warnings=crew_warnings,
                **ctx,
            )
        _flash_crew_warnings(crew_warnings)
        booking_id = _create_booking_from_data(data)
        _save_job_costs_from_form(booking_id, request.form, is_new=True)
        _apply_new_booking_override(booking_id, override_applied, db_conflicts)
        _flash_integration_messages(services.after_booking_created(booking_id))
        flash("Booking saved (reference #{0}).".format(booking_id), "success")
        return redirect(url_for("ceo_dashboard"))

    form = _booking_form_defaults()
    move_date = request.args.get("move_date", "").strip()
    if move_date:
        form["move_date"] = move_date
    return render_template(
        "new_booking.html",
        form=form,
        paste_text="",
        crew_warnings=[],
        **_double_booking_context(form),
    )


# Fixed paths must be registered before /bookings/<int:booking_id>/...
@app.route("/bookings/search", endpoint="search_bookings")
@auth.login_required
def booking_search():
    query = request.args.get("q", "").strip()
    bookings = db.search_bookings(query) if query else []
    return render_template(
        "search.html",
        bookings=bookings,
        query=query,
        today=date.today().isoformat(),
    )


@app.route("/bookings/export.csv", endpoint="export_csv")
@auth.login_required
def export_csv():
    query = request.args.get("q", "").strip()
    if query:
        bookings = db.search_bookings(query)
        filename = "japanese-removals-bookings-search.csv"
    else:
        bookings = db.list_all()
        filename = "japanese-removals-bookings-all.csv"

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for row in bookings:
        writer.writerow([row[h] if h in row.keys() else "" for h in CSV_HEADERS])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename={0}".format(filename)
        },
    )


@app.route("/bookings/upcoming", endpoint="upcoming")
@auth.login_required
def upcoming():
    bookings = db.list_upcoming()
    return render_template(
        "upcoming.html",
        bookings=bookings,
        today=date.today().isoformat(),
    )


@app.route("/bookings/all", endpoint="all_bookings")
@auth.login_required
def all_bookings():
    bookings = db.list_all()
    return render_template(
        "all_bookings.html",
        bookings=bookings,
        today=date.today().isoformat(),
    )


def _view_booking_extras(row) -> dict:
    from crew_schedule_data import booking_duration_hours, format_hours_label

    booking = services.booking_to_dict(row)
    actual_times = staff_job_times.job_time_state(booking)
    return {
        "duration_label": format_hours_label(booking_duration_hours(booking)),
        "receipt": receipts.state(booking),
        "invoice_summary": _invoice_summary_for_row(row),
        "profit_summary": booking_profit.profit_summary_for_booking(booking),
        "actual_times": actual_times,
        **_double_booking_context(booking),
    }


@app.route("/bookings/<int:booking_id>", endpoint="view_booking")
@auth.login_required
def view_booking(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    try:
        furniture_lists.auto_link_from_booking_text(row)
        furniture = furniture_lists.get_for_booking(booking_id)
    except Exception:
        app.logger.exception("Furniture list lookup failed for booking %s", booking_id)
        furniture = None
    return render_template(
        "view_booking.html",
        booking=row,
        furniture=furniture,
        **_view_booking_extras(row),
    )


# ---------------------------------------------------------------- Furniture lists
# Customer pages are public but scoped to one list by its secret token.


def _furniture_private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _furniture_not_found():
    return _furniture_private(
        app.make_response((render_template("furniture_missing.html"), 404))
    )


def _furniture_date_text(value) -> str:
    parts = format_display_date(value)
    if parts["weekday"] == "—":
        return "" if parts["day_month"] == "—" else parts["day_month"]
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def _furniture_json_body() -> dict:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _furniture_editor_page(row, mode: str, **extra):
    endpoints = {}
    if mode == "office":
        endpoints = {
            "save": url_for("office_furniture_save", list_id=row["id"]),
            "upload": url_for("office_furniture_photo_upload", list_id=row["id"]),
            "photo": url_for("office_furniture_photo", list_id=row["id"], photo_id=0),
            "delete_photo": url_for("office_furniture_photo_delete", list_id=row["id"], photo_id=0),
        }
    else:
        token = row["access_token"]
        endpoints = {
            "save": url_for("furniture_save", token=token),
            "upload": url_for("furniture_photo_upload", token=token),
            "photo": url_for("furniture_photo", token=token, photo_id=0),
            "delete_photo": url_for("furniture_photo_delete", token=token, photo_id=0),
        }
    booking = db.get_booking(row["booking_id"]) if row.get("booking_id") else None
    greeting = ""
    if booking is not None and mode != "office":
        first = str(dict(booking).get("customer_name") or "").split()
        greeting = first[0] if first else ""
    response = app.make_response(
        render_template(
            "furniture_editor.html",
            mode=mode,
            row=row,
            booking=booking,
            greeting=greeting,
            move_date=_furniture_date_text(dict(booking).get("move_date")) if booking is not None else "",
            state=furniture_lists.editor_state(row),
            catalog=furniture_catalog.catalog_payload(furniture_volumes.effective_volumes()),
            endpoints=endpoints,
            return_link=furniture_lists.return_link(row) if mode != "office" else "",
            quote_flow=bool(row.get("quote_flow")) and mode != "office",
            chip_label=furniture_lists.customer_status_label(row) if mode != "office" else row["status_label"],
            customer_url=url_for("furniture_edit", token=row["access_token"], _external=True),
            max_photos=furniture_lists.MAX_PHOTOS,
            **extra,
        )
    )
    return _furniture_private(response)


def _furniture_save_response(row, editor: str):
    payload = _furniture_json_body()
    updated = furniture_lists.save_content(
        row, payload, editor=editor, submit=bool(payload.get("submit"))
    )
    return jsonify(
        {
            "ok": True,
            "status": updated["status"],
            "status_label": (
                furniture_lists.customer_status_label(updated) if editor == "customer" else updated["status_label"]
            ),
            "updated_display": updated["updated_display"],
            "estimated_m3": updated["estimated_m3"],
            "item_count": updated["item_count"],
            "units": furniture_catalog.room_units(updated["content"]),
            "return_link": furniture_lists.return_link(updated),
        }
    )


def _furniture_photo_upload_response(row, uploaded_by: str):
    if (request.content_length or 0) > furniture_lists.MAX_UPLOAD_BYTES + 1024 * 1024:
        return jsonify({"ok": False, "error": "Photos must be 15 MB or smaller."}), 413
    upload = request.files.get("photo")
    if upload is None:
        return jsonify({"ok": False, "error": "No photo received."}), 400
    raw = upload.stream.read(furniture_lists.MAX_UPLOAD_BYTES + 1)
    try:
        photo = furniture_lists.add_photo(row, raw, uploaded_by=uploaded_by)
    except furniture_lists.FurnitureError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "photo": photo})


def _furniture_photo_response(list_id: int, photo_id: int):
    photo = furniture_lists.get_photo(list_id, photo_id)
    if not photo:
        abort(404)
    response = app.make_response(photo["data"])
    response.headers["Content-Type"] = photo["content_type"] or "image/jpeg"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/furniture", methods=["GET"], endpoint="furniture_start")
def furniture_start():
    response = app.make_response(
        render_template(
            "furniture_start.html",
            return_url=furniture_lists.safe_return_url(request.args.get("return")),
            code=furniture_lists.normalize_public_id(request.args.get("code")),
            error=request.args.get("error") == "limit",
        )
    )
    return _furniture_private(response)


@app.route("/furniture/start", methods=["POST"], endpoint="furniture_create")
def furniture_create():
    ip_address = request.remote_addr or ""
    return_url = furniture_lists.safe_return_url(request.form.get("return_url"))
    if furniture_lists.website_lists_created_recently(ip_address) >= furniture_lists.WEBSITE_LISTS_PER_IP_PER_HOUR:
        return redirect(url_for("furniture_start", error="limit", **({"return": return_url} if return_url else {})))
    row = furniture_lists.create_list(
        furniture_lists.SOURCE_WEBSITE, return_url=return_url, created_ip=ip_address
    )
    return redirect(url_for("furniture_edit", token=row["access_token"]))


@app.route("/furniture/<token>", methods=["GET"], endpoint="furniture_edit")
def furniture_edit(token):
    row = furniture_lists.get_by_token(token)
    if not row:
        return _furniture_not_found()
    return _furniture_editor_page(row, "customer")


@app.route("/furniture/<token>/save", methods=["POST"], endpoint="furniture_save")
def furniture_save(token):
    row = furniture_lists.get_by_token(token)
    if not row:
        return jsonify({"ok": False, "error": "This link is not valid."}), 404
    return _furniture_save_response(row, editor="customer")


@app.route("/furniture/<token>/photos", methods=["POST"], endpoint="furniture_photo_upload")
def furniture_photo_upload(token):
    row = furniture_lists.get_by_token(token)
    if not row:
        return jsonify({"ok": False, "error": "This link is not valid."}), 404
    return _furniture_photo_upload_response(row, uploaded_by="customer")


@app.route("/furniture/<token>/photos/<int:photo_id>", methods=["GET"], endpoint="furniture_photo")
def furniture_photo(token, photo_id):
    row = furniture_lists.get_by_token(token)
    if not row:
        abort(404)
    return _furniture_photo_response(row["id"], photo_id)


@app.route(
    "/furniture/<token>/photos/<int:photo_id>/delete",
    methods=["POST"],
    endpoint="furniture_photo_delete",
)
def furniture_photo_delete(token, photo_id):
    row = furniture_lists.get_by_token(token)
    if not row:
        return jsonify({"ok": False, "error": "This link is not valid."}), 404
    return jsonify({"ok": furniture_lists.delete_photo(row["id"], photo_id)})


@app.route("/api/enquiry-list/<fl_id>.pdf", methods=["GET"], endpoint="furniture_enquiry_pdf")
def furniture_enquiry_pdf(fl_id):
    """Website (server to server) fetches the list PDF when it sends the quote enquiry.

    Needs header X-JR-Enquiry-Key = FURNITURE_ENQUIRY_KEY. See furniture_enquiry.py.
    """
    ip_address = request.remote_addr or ""
    status, outcome, row = furniture_enquiry.check_request(
        ip_address, fl_id, request.headers.get("X-JR-Enquiry-Key")
    )
    public_id = row["public_id"] if row else furniture_lists.normalize_public_id(fl_id)
    if status != 200:
        furniture_enquiry.log_request(ip_address, public_id, status, outcome)
        messages = {401: "Unauthorized", 404: "Not found", 409: "Already used", 429: "Too many requests", 503: "Not configured"}
        response = app.make_response((messages.get(status, "Error"), status))
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        pdf = furniture_pdf.render_list_pdf(row, photo_count=int(row.get("photo_count") or 0))
    except Exception:
        app.logger.exception("Furniture list PDF failed for %s", public_id)
        furniture_enquiry.log_request(ip_address, public_id, 500, "pdf error")
        return app.make_response(("PDF error", 500))
    furniture_enquiry.mark_received(row)
    furniture_enquiry.log_request(ip_address, public_id, 200, outcome)
    response = app.make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = 'inline; filename="furniture-list-{0}.pdf"'.format(public_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route("/furniture-lists", methods=["GET", "POST"], endpoint="furniture_lists_index")
@auth.login_required
def furniture_lists_index():
    if request.method == "POST":
        code = request.form.get("code", "")
        row = furniture_lists.get_by_public_id(code)
        if row:
            return redirect(url_for("office_furniture_list", list_id=row["id"]))
        flash("No furniture list found for {0}.".format(code.strip() or "that code"), "error")
        return redirect(url_for("furniture_lists_index"))
    return render_template(
        "furniture_lists_index.html", lists=furniture_lists.list_recent()
    )


@app.route("/furniture-lists/volumes", methods=["GET", "POST"], endpoint="furniture_volumes_admin")
@auth.login_required
def furniture_volumes_admin():
    can_edit = staff_portal_owner.can_manage_staff_portal(g.user)
    if request.method == "POST":
        if not can_edit:
            abort(403)
        key = request.form.get("item_key", "")
        label = furniture_catalog.ITEMS.get(key, (key,))[0]
        user = (g.user["username"] if g.user else "")
        try:
            if request.form.get("action") == "reset":
                value = furniture_volumes.reset_volume(key, user)
            else:
                value = furniture_volumes.set_volume(
                    key, request.form.get("m3"), user, note=request.form.get("note", "")
                )
            flash(
                "{0}: standard volume is now {1:g} m³. It applies to items added from now on; "
                "existing lists keep their numbers.".format(label, value),
                "success",
            )
        except furniture_volumes.VolumeError as exc:
            flash("{0}: {1}".format(label, exc), "error")
        return redirect(url_for("furniture_volumes_admin") + "#item-" + key)
    return render_template(
        "furniture_volumes.html",
        groups=furniture_volumes.admin_rows(),
        changes=furniture_volumes.recent_changes(),
        can_edit=can_edit,
        checked_on=furniture_volume_refs.CHECKED_ON,
        min_m3=furniture_volumes.MIN_M3,
        max_m3=furniture_volumes.MAX_M3,
    )


@app.route("/furniture-lists/<int:list_id>", methods=["GET", "POST"], endpoint="office_furniture_list")
@auth.login_required
def office_furniture_list(list_id):
    row = furniture_lists.get_by_id(list_id)
    if not row:
        flash("Furniture list not found.", "error")
        return redirect(url_for("furniture_lists_index"))
    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "link":
                booking_id = int(str(request.form.get("booking_id") or "").strip().lstrip("#") or 0)
                furniture_lists.link_to_booking(row["id"], booking_id)
                flash("Linked {0} to booking #{1}.".format(row["public_id"], booking_id), "success")
            elif action == "unlink":
                furniture_lists.unlink(row["id"])
                flash("Unlinked {0}.".format(row["public_id"]), "success")
            elif action == "status":
                furniture_lists.set_status(row, request.form.get("status", ""), editor="office")
                flash("Status updated.", "success")
            elif action == "delete":
                furniture_lists.delete_list(row["id"])
                flash("Deleted furniture list {0}.".format(row["public_id"]), "success")
                if row.get("booking_id"):
                    return redirect(url_for("view_booking", booking_id=row["booking_id"]))
                return redirect(url_for("furniture_lists_index"))
        except (ValueError, furniture_lists.FurnitureError) as exc:
            flash(str(exc) or "Could not update the furniture list.", "error")
        return redirect(url_for("office_furniture_list", list_id=list_id))
    return _furniture_editor_page(
        row,
        "office",
        status_labels=furniture_lists.STATUS_LABELS,
        summary=furniture_catalog.annotate_changes(
            furniture_catalog.summarize(row["content"]), row["changes"]
        ),
    )


def _furniture_pdf_download(row):
    pdf = furniture_pdf.render_list_pdf(row, photo_count=int(row.get("photo_count") or 0))
    response = app.make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = 'inline; filename="furniture-list-{0}.pdf"'.format(row["public_id"])
    return _furniture_private(response)


@app.route("/furniture-lists/<int:list_id>/list.pdf", endpoint="office_furniture_pdf")
@auth.login_required
def office_furniture_pdf(list_id):
    row = furniture_lists.get_by_id(list_id)
    if not row:
        abort(404)
    return _furniture_pdf_download(row)


@app.route("/furniture-lists/<int:list_id>/save", methods=["POST"], endpoint="office_furniture_save")
@auth.login_required
def office_furniture_save(list_id):
    row = furniture_lists.get_by_id(list_id)
    if not row:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return _furniture_save_response(row, editor="office")


@app.route("/furniture-lists/<int:list_id>/photos", methods=["POST"], endpoint="office_furniture_photo_upload")
@auth.login_required
def office_furniture_photo_upload(list_id):
    row = furniture_lists.get_by_id(list_id)
    if not row:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return _furniture_photo_upload_response(row, uploaded_by="office")


@app.route("/furniture-lists/<int:list_id>/photos/<int:photo_id>", endpoint="office_furniture_photo")
@auth.login_required
def office_furniture_photo(list_id, photo_id):
    return _furniture_photo_response(list_id, photo_id)


@app.route(
    "/furniture-lists/<int:list_id>/photos/<int:photo_id>/delete",
    methods=["POST"],
    endpoint="office_furniture_photo_delete",
)
@auth.login_required
def office_furniture_photo_delete(list_id, photo_id):
    return jsonify({"ok": furniture_lists.delete_photo(list_id, photo_id)})


@app.route("/bookings/<int:booking_id>/furniture", methods=["GET"], endpoint="booking_furniture")
@auth.login_required
def booking_furniture(booking_id):
    if db.get_booking(booking_id) is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    row = furniture_lists.ensure_for_booking(booking_id)
    return redirect(url_for("office_furniture_list", list_id=row["id"]))


@app.route("/bookings/<int:booking_id>/furniture/link", methods=["POST"], endpoint="booking_furniture_link")
@auth.login_required
def booking_furniture_link(booking_id):
    if db.get_booking(booking_id) is None:
        return jsonify({"ok": False, "error": "Booking not found."}), 404
    row = furniture_lists.ensure_for_booking(booking_id)
    return jsonify(
        {
            "ok": True,
            "public_id": row["public_id"],
            "url": url_for("furniture_edit", token=row["access_token"], _external=True),
        }
    )


@app.route("/bookings/<int:booking_id>/furniture/attach", methods=["POST"], endpoint="booking_furniture_attach")
@auth.login_required
def booking_furniture_attach(booking_id):
    code = request.form.get("code", "")
    row = furniture_lists.get_by_public_id(code)
    if not row:
        flash("No furniture list found for {0}.".format(code.strip() or "that code"), "error")
    else:
        try:
            furniture_lists.link_to_booking(row["id"], booking_id)
            flash("Linked furniture list {0}.".format(row["public_id"]), "success")
        except furniture_lists.FurnitureError as exc:
            flash(str(exc), "error")
    return redirect(url_for("view_booking", booking_id=booking_id))


def _staff_furniture_row(token):
    booking_id = furniture_lists.booking_id_from_staff_token(token)
    if not booking_id:
        return None, None
    booking = db.get_booking(booking_id)
    row = furniture_lists.get_for_booking(booking_id)
    if booking is None or not row:
        return None, None
    return booking, row


@app.route("/staff/furniture/<token>", endpoint="staff_furniture_list")
@staff_auth.staff_login_required
def staff_furniture_list(token):
    booking, row = _staff_furniture_row(token)
    if not row:
        return _furniture_not_found()
    booking = dict(booking)
    response = app.make_response(
        render_template(
            "furniture_staff.html",
            token=token,
            row=row,
            booking=booking,
            move_date=_furniture_date_text(booking.get("move_date")),
            summary=furniture_catalog.annotate_changes(
                furniture_catalog.summarize(row["content"]), row["changes"]
            ),
            photos=furniture_lists.list_photos(row["id"]),
        )
    )
    return _furniture_private(response)


@app.route("/staff/furniture/<token>/list.pdf", endpoint="staff_furniture_pdf")
@staff_auth.staff_login_required
def staff_furniture_pdf(token):
    _booking, row = _staff_furniture_row(token)
    if not row:
        return _furniture_not_found()
    return _furniture_pdf_download(row)


@app.route("/staff/furniture/<token>/photos/<int:photo_id>", endpoint="staff_furniture_photo")
@staff_auth.staff_login_required
def staff_furniture_photo(token, photo_id):
    _booking, row = _staff_furniture_row(token)
    if not row:
        abort(404)
    return _furniture_photo_response(row["id"], photo_id)


@app.route("/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
@auth.login_required
def edit_booking(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))

    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "send_sms":
            ok, msg = services.send_sms_manual(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_sms_confirmation":
            ok, msg = services.send_booking_template_sms(
                booking_id, "booking_confirmation"
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_sms_payment_reminder":
            ok, msg = services.send_booking_template_sms(
                booking_id, "payment_reminder"
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_sms_thank_you":
            ok, msg = services.send_booking_template_sms(booking_id, "thank_you")
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "check_xero_payment":
            ok, msg = services.check_xero_payment_status(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "stripe_checkout":
            success_url = url_for(
                "stripe_checkout_success",
                booking_id=booking_id,
                _external=True,
            ) + "?session_id={CHECKOUT_SESSION_ID}"
            cancel_url = url_for(
                "edit_booking", booking_id=booking_id, _external=True
            )
            ok, msg, checkout_url = services.create_stripe_checkout(
                booking_id,
                success_url=success_url,
                cancel_url=cancel_url,
            )
            if ok and checkout_url:
                return redirect(checkout_url)
            flash(msg, "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "xero_draft_invoice":
            confirm_new = request.form.get("confirm_new") == "1"
            ok, msg = services.create_xero_draft(
                booking_id, confirm_new=confirm_new
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "create_email_invoice":
            ok, msg, xero_url = services.run_xero_invoice_automation(booking_id)
            if ok:
                flash(msg, "success")
                if xero_url:
                    flash(
                        "Open invoice in Xero: {0}".format(xero_url),
                        "success",
                    )
            else:
                flash(msg, "error")
                if xero_url:
                    flash(
                        "Partial progress — open in Xero: {0}".format(xero_url),
                        "error",
                    )
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "mark_paid":
            ok, msg = services.mark_booking_paid(
                booking_id, paid=True, payment_method=request.form.get("payment_method", "")
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "mark_unpaid":
            ok, msg = services.mark_booking_paid(booking_id, paid=False)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "mark_review_received":
            ok, msg = services.mark_review_received(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "sync_calendar":
            booking = services.booking_to_dict(db.get_booking(booking_id))
            if job_status.display(booking) == "Pending":
                flash("Pending bookings are not synced to Google Calendar.", "error")
                return redirect(url_for("edit_booking", booking_id=booking_id))
            msg = google_calendar.sync_booking_to_calendar(booking)
            if not msg:
                flash("Calendar not configured.", "error")
            elif any(
                token in msg.lower()
                for token in ("failed", "not connected", "expired")
            ):
                flash(msg, "error")
            else:
                flash(msg, "success")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "start_on_route":
            manual_raw = request.form.get("manual_eta_minutes", "").strip()
            manual_eta = None
            if manual_raw:
                try:
                    manual_eta = int(manual_raw)
                except ValueError:
                    flash("Enter a whole number of minutes for ETA.", "error")
                    return redirect(url_for("edit_booking", booking_id=booking_id))
            driver_name = request.form.get("driver_name", "").strip()
            if not driver_name:
                driver_name = _default_driver_name()
            ok, msg = services.start_driver_on_route(
                booking_id,
                driver_name=driver_name,
                manual_eta_minutes=manual_eta,
                driver_origin=request.form.get("driver_origin", "").strip(),
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "resend_eta_sms":
            manual_raw = request.form.get("manual_eta_minutes", "").strip()
            manual_eta = None
            if manual_raw:
                try:
                    manual_eta = int(manual_raw)
                except ValueError:
                    flash("Enter a whole number of minutes for ETA.", "error")
                    return redirect(url_for("edit_booking", booking_id=booking_id))
            ok, msg = services.resend_eta_sms(
                booking_id,
                driver_name=request.form.get("driver_name", "").strip(),
                manual_eta_minutes=manual_eta,
                driver_origin=request.form.get("driver_origin", "").strip(),
            )
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_review_now":
            ok, msg = services.send_review_request_now(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "cancel_review_request":
            ok, msg = services.cancel_review_request(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "save_profit_costs":
            ok, msg = services.save_profit_costs(booking_id, request.form)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "recalculate_profit":
            ok, msg = services.recalculate_booking_profit(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_payment_reminder_now":
            ok, msg = services.send_payment_reminder_now(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "cancel_payment_reminders":
            ok, msg = services.cancel_payment_reminders(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))
        if action == "send_invoice":
            db.update_booking_contact_fields(
                booking_id,
                email=request.form.get("email", ""),
                phone=request.form.get("phone", ""),
            )
            ok, msg = services.send_customer_invoice(booking_id)
            flash(msg, "success" if ok else "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))

        previous_status = job_status.display(services.booking_to_dict(row))
        cost_errors = booking_profit.job_cost_form_errors(request.form)
        if cost_errors:
            data, _ = parse_booking_form(request.form)
            _copy_job_cost_form_values(data, request.form)
            for msg in cost_errors:
                flash(msg, "error")
            extras = _edit_booking_extras(row)
            extras.update(_double_booking_context(data, booking_id=booking_id))
            return render_template(
                "edit_booking.html",
                booking=row,
                form=data,
                status=_integration_status(),
                crew_warnings=_crew_warnings_for_data(data, booking_id=booking_id),
                pricing_panel_mode=True,
                **extras,
            )
        ok, errors, data = _update_booking_from_form(booking_id, request.form)
        crew_warnings = _crew_warnings_for_data(data, booking_id=booking_id)
        db_errors, db_conflicts, override_applied = _validate_double_booking(
            data, booking_id=booking_id
        )
        if errors or db_errors:
            for msg in errors + db_errors:
                flash(msg, "error")
            row = db.get_booking(booking_id)
            extras = _edit_booking_extras(row)
            extras.update(_double_booking_context(data, booking_id=booking_id))
            _copy_job_cost_form_values(data, request.form)
            return render_template(
                "edit_booking.html",
                booking=row,
                form=data,
                status=_integration_status(),
                crew_warnings=crew_warnings,
                pricing_panel_mode=True,
                **extras,
            )
        if ok:
            invoice_msg = services.refresh_invoice_after_booking_save(booking_id)
            _flash_crew_warnings(crew_warnings)
            _flash_integration_messages(
                services.after_booking_updated(
                    booking_id, previous_status=previous_status
                )
            )
            flash(
                "Changes saved successfully. {0}".format(invoice_msg).strip(),
                "saved",
            )
            return redirect(url_for("edit_booking", booking_id=booking_id))
        flash("Could not update booking.", "error")
        return redirect(url_for("edit_booking", booking_id=booking_id))

    booking_dict = services.booking_to_dict(row)
    form_callout_minutes = callout_pricing.minutes_for_booking(booking_dict)
    if form_callout_minutes is None:
        form_callout_minutes = (
            callout_pricing.DEFAULT_CALLOUT_MINUTES
            if float(row["callout_fee"] or 0) > 0
            else 0
        )

    form = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "start_time": normalize_time_input(row["start_time"]) or DEFAULT_START_TIME,
        "finish_time": normalize_time_input(row["finish_time"])
        or effective_finish_hm(_row_to_dict(row)),
        "duration_hours": _form_duration_from_row(row),
        "legacy_actual_label": _legacy_actual_label(_row_to_dict(row)),
        "crew": crew_from_storage(_row_to_dict(row).get("crew")),
        "num_movers": row["num_movers"],
        "notes": row["notes"] or "",
        "hourly_rate": row["hourly_rate"]
        if row["hourly_rate"] is not None
        else invoice.default_invoice_fields()["hourly_rate"],
        "callout_fee": row["callout_fee"]
        if row["callout_fee"] is not None
        else invoice.default_invoice_fields()["callout_fee"],
        "callout_minutes": form_callout_minutes,
        "gst_enabled": row["gst_enabled"]
        if row["gst_enabled"] is not None
        else invoice.default_invoice_fields()["gst_enabled"],
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "invoice_custom_text": row["invoice_custom_text"] or "",
        "invoice_bank_account_name": row["invoice_bank_account_name"] or "",
        "invoice_bank_bsb": row["invoice_bank_bsb"] or "",
        "invoice_bank_account": row["invoice_bank_account"] or "",
        "extra_charges": db.list_extra_charges(booking_id),
        "truck_assigned": row["truck_assigned"] or "",
        "status": job_status.display(_row_to_dict(row)),
    }
    form.update(
        invoice.invoice_description_form_values(
            invoice.resolve_booking_invoice(services.booking_to_dict(row))
        )
    )
    form.update(_work_period_2_form_values(_row_to_dict(row)))
    return render_template(
        "edit_booking.html",
        booking=row,
        form=form,
        status=_integration_status(),
        crew_warnings=_crew_warnings_for_data(form, booking_id=booking_id),
        pricing_panel_mode=True,
        **_edit_booking_extras(row),
    )


def _labour_lines_json(data, totals):
    # The textarea holds the old automatic text until the browser refreshes it, so
    # only a description the user actually edited counts as custom here.
    booking = dict(data, invoice_description=invoice.stored_description_for_save(data))
    return [
        {
            "period": line["period"],
            "description": line["description"],
            "hours": line["quantity"],
            "rate": line["unit_amount"],
            "amount": line["amount"],
            "amount_display": invoice.format_aud(line["amount"]),
        }
        for line in invoice.invoice_labour_lines(booking, totals)
    ]


@app.route("/bookings/invoice/preview-calculate", methods=["POST"])
@auth.login_required
def invoice_preview_calculate():
    data, errors = parse_booking_form(request.form)
    if errors:
        return jsonify({"error": errors[0]}), 400

    totals = invoice.calculate_from_form_data(data)
    return jsonify(
        {
            "hourly_rate": totals["hourly_rate"],
            "hours": totals["hours"],
            "callout_fee": totals["callout_fee"],
            "labour_gross": totals["labour_gross"],
            "labour_total": totals["labour_amount"],
            "labour_lines": _labour_lines_json(data, totals),
            "extras_total": totals["extras_total"],
            "net_sales": totals["net_sales"],
            "gst_amount": totals["gst_amount"],
            "total": totals["total"],
            "gst_enabled": totals["gst_enabled"],
        }
    )


@app.route("/bookings/<int:booking_id>/invoice/calculate", methods=["POST"])
@auth.login_required
def invoice_calculate(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        return jsonify({"error": "Booking not found."}), 404

    data, errors = parse_booking_form(request.form)
    if errors:
        return jsonify({"error": errors[0]}), 400

    totals = invoice.calculate_from_form_data(data)
    payment = stripe_service.payment_options_for_booking(
        services.booking_to_dict(row),
        base_total=totals["total"],
    )
    return jsonify(
        {
            "labour_total": totals["labour_amount"],
            "labour_lines": _labour_lines_json(data, totals),
            "labour_gross": totals["labour_gross"],
            "extras_total": totals["extras_total"],
            "net_sales": totals["net_sales"],
            "gst_amount": totals["gst_amount"],
            "total": totals["total"],
            "gst_enabled": totals["gst_enabled"],
            "hours": totals["hours"],
            "bank_total": payment["bank_total"],
            "bank_total_display": payment["bank_total_display"],
            "card_total": payment["card_total"],
            "card_total_display": payment["card_total_display"],
            "surcharge_percent_display": payment["surcharge_percent_display"],
            "surcharge_display": payment["surcharge_display"],
            "labour_description": invoice.format_moving_labour_description(
                data, totals
            ),
        }
    )


@app.route("/bookings/<int:booking_id>/job-sheet.pdf")
@auth.login_required
def job_sheet_pdf(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    pdf_bytes = job_sheet.generate_job_sheet_pdf(services.booking_to_dict(row))
    filename = "job-sheet-{0}.pdf".format(booking_id)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename={0}".format(filename)},
    )


@app.route("/bookings/<int:booking_id>/stripe/checkout", methods=["POST"])
@auth.login_required
def stripe_checkout(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    success_url = url_for(
        "stripe_checkout_success",
        booking_id=booking_id,
        _external=True,
    ) + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = url_for("edit_booking", booking_id=booking_id, _external=True)
    ok, msg, checkout_url = services.create_stripe_checkout(
        booking_id,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if ok and checkout_url:
        return redirect(checkout_url)
    flash(msg, "error")
    return redirect(url_for("edit_booking", booking_id=booking_id))


@app.route("/bookings/<int:booking_id>/stripe/success")
@auth.login_required
def stripe_checkout_success(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    flash(
        "Thank you — card payment submitted. Status updates automatically when Stripe confirms payment.",
        "success",
    )
    return redirect(url_for("edit_booking", booking_id=booking_id))


def _booking_overlay_for_invoice_preview(booking: dict, data: dict) -> dict:
    """Apply parsed form fields onto a booking dict without writing to the DB."""
    overlay = dict(booking)
    for key in (
        "customer_name",
        "phone",
        "email",
        "pickup_address",
        "delivery_address",
        "move_date",
        "num_movers",
        "notes",
        "start_time",
        "finish_time",
        "duration_hours",
        "hourly_rate",
        "callout_fee",
        "callout_minutes",
        "gst_enabled",
        "payment_status",
        "invoice_status",
        "invoice_custom_text",
        "invoice_bank_account_name",
        "invoice_bank_bsb",
        "invoice_bank_account",
        "truck_assigned",
        "status",
    ):
        if key in data:
            overlay[key] = data[key]
    if data.get("work_period_2_present"):
        for key in work_periods.FIELDS:
            overlay[key] = data.get(key)
    overlay["extra_charges"] = list(data.get("extra_charges") or [])
    if data.get("crew_csv") is not None:
        overlay["crew"] = data["crew_csv"]
    if data.get("invoice_description_present"):
        overlay["invoice_description"] = invoice.stored_description_for_save(data)
    return overlay


@app.route("/bookings/<int:booking_id>/invoice/preview", methods=["GET", "POST"])
@auth.login_required
def invoice_preview(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    if request.method != "POST":
        services.prepare_booking_payment_link(booking_id)
        row = db.get_booking(booking_id)
    booking = services.booking_to_dict(row)
    if request.method == "POST":
        data, errors = parse_booking_form(
            request.form, existing_crew_csv=booking.get("crew") or ""
        )
        if not errors:
            booking = _booking_overlay_for_invoice_preview(booking, data)
    inv = invoice_pdf_service.build_invoice_document(booking)
    return render_template(
        "invoice_preview.html",
        booking_id=booking_id,
        inv=inv,
    )


@app.route("/bookings/<int:booking_id>/invoice.pdf")
@auth.login_required
def invoice_pdf(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    services.prepare_booking_payment_link(booking_id)
    row = db.get_booking(booking_id)
    booking = services.booking_to_dict(row)
    inv = invoice_pdf_service.build_invoice_document(booking)
    pdf_bytes = invoice_pdf_service.generate_invoice_pdf(booking)
    number = (inv.get("invoice_number") or "draft").replace("/", "-")
    filename = "invoice-{0}.pdf".format(number)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename={0}".format(filename)},
    )


def _receipt_return(booking_id):
    if request.values.get("return_to") == "view":
        return redirect(url_for("view_booking", booking_id=booking_id) + "#receipt")
    return redirect(url_for("edit_booking", booking_id=booking_id) + "#receipt")


def _paid_booking_with_receipt(booking_id):
    """Booking dict when a receipt can be shown, else (None, redirect)."""
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return None, redirect(url_for("all_bookings"))
    booking = services.booking_to_dict(row)
    if not receipts.is_paid(booking):
        flash("Receipt available after payment.", "error")
        return None, _receipt_return(booking_id)
    if not str(booking.get("receipt_number") or "").strip():
        flash("Generate the receipt first (choose payment date and method).", "error")
        return None, _receipt_return(booking_id)
    return booking, None


@app.route("/bookings/<int:booking_id>/receipt", methods=["POST"], endpoint="generate_receipt")
@auth.login_required
def generate_receipt(booking_id):
    ok, msg = receipts.issue(
        booking_id,
        method=request.form.get("payment_method"),
        paid_on=request.form.get("payment_date"),
    )
    flash(msg, "success" if ok else "error")
    return _receipt_return(booking_id)


@app.route("/bookings/<int:booking_id>/receipt/preview", endpoint="receipt_preview")
@auth.login_required
def receipt_preview(booking_id):
    booking, response = _paid_booking_with_receipt(booking_id)
    if response is not None:
        return response
    return render_template(
        "receipt_preview.html",
        booking_id=booking_id,
        rct=invoice_pdf_service.build_receipt_document(booking),
    )


@app.route("/bookings/<int:booking_id>/receipt.pdf", endpoint="receipt_pdf")
@auth.login_required
def receipt_pdf(booking_id):
    booking, response = _paid_booking_with_receipt(booking_id)
    if response is not None:
        return response
    pdf_bytes = invoice_pdf_service.generate_receipt_pdf(booking)
    number = str(booking.get("receipt_number") or "receipt").replace("/", "-")
    # Served as an attachment like the invoice PDF, so iPhone Safari opens it in the
    # PDF viewer / share sheet instead of a blank tab.
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=receipt-{0}.pdf".format(number)},
    )


@app.route("/bookings/<int:booking_id>/delete", methods=["GET", "POST"])
@auth.login_required
def delete_booking(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))

    if request.method == "POST":
        if request.form.get("confirm") == "yes":
            booking = services.booking_to_dict(row)
            _flash_integration_messages(services.after_booking_deleted(booking))
            db.delete_booking(booking_id)
            flash("Booking #{0} deleted.".format(booking_id), "success")
            return redirect(url_for("all_bookings"))
        return redirect(url_for("edit_booking", booking_id=booking_id))

    return render_template("delete_booking.html", booking=row)


if __name__ == "__main__":
    config.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    for name in (
        "ceo_dashboard",
        "dashboard",
        "executive_dashboard",
        "search_bookings",
        "upcoming",
        "all_bookings",
        "export_csv",
        "leads",
        "website_quote",
        "truck_management",
        "daily_checklist",
        "driver",
        "driver_run_sheet_pdf",
        "calendar_weekly_schedule",
        "calendar_weekly_schedule_pdf",
        "profit",
        "profit_export_csv",
        "outstanding_invoices",
        "automation_hub",
        "sms_settings",
        "review_settings",
        "gmail_settings",
        "bank_transfers",
    ):
        if name not in app.view_functions:
            raise RuntimeError("Missing route endpoint: {0}".format(name))
    print("OAUTHLIB_INSECURE_TRANSPORT =", os.environ.get("OAUTHLIB_INSECURE_TRANSPORT"))
    url = "http://{0}:{1}".format(HOST, PORT)
    print("Open {0} in your browser".format(url))
    print("Press Control+C in this window to stop the server.")
    app.run(
        host=HOST,
        port=PORT,
        debug=True,
        use_reloader=False,
        threaded=True,
    )
