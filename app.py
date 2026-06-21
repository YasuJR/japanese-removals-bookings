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

from flask import (
    Flask,
    flash,
    g,
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
    xero,
)
from integrations import executive_config, review_config, sms_config, xero_config, company_config, xero_branding, stripe_config, gmail_config, google_oauth
from integrations import stripe as stripe_service
from booking_helpers import apple_maps_url, mailto_href, sms_href, tel_href
from driver_run_sheet_data import build_driver_run_sheet
from outstanding_invoices_data import INVOICE_FILTERS, build_outstanding_dashboard
from executive_dashboard_data import build_executive_dashboard
from profit_data import PROFIT_CSV_HEADERS, build_profit_dashboard, profit_csv_rows
import booking_profit
from integrations import payment_reminder_automation
from booking_times import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_FINISH_TIME,
    DEFAULT_START_TIME,
    display_finish_time,
    display_start_time,
    format_time_12h,
    inferred_duration_hours,
)
from crew import CREW_OPTIONS, active_crew_names, crew_from_storage, display_crew
from resource_conflicts import find_resource_conflict_warnings
from trucks import active_truck_names
import double_booking
import job_status
from crew_schedule_data import RANGE_OPTIONS, build_crew_schedule
from ceo_dashboard_data import build_ceo_dashboard
from daily_checklist_data import build_daily_checklist
from quote_form import parse_quote_form
from integrations import website_quote, sms_inbound
from dashboard_data import build_dashboard, dashboard_jobs
from display_dates import format_display_date, get_weekday_class
import automation
import invoice
from validators import parse_booking_form

app = Flask(__name__)

if config.PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _row_to_dict(row):
    return dict(row) if row else {}


def _form_duration_from_row(row) -> str:
    booking = _row_to_dict(row)
    stored = str(booking.get("duration_hours") or "").strip()
    if stored:
        return stored
    inferred = inferred_duration_hours(booking)
    if inferred is None:
        return ""
    if inferred == int(inferred):
        return str(int(inferred))
    return str(inferred)


app.jinja_env.filters["booking_start_time"] = lambda row: display_start_time(
    _row_to_dict(row)
)
app.jinja_env.filters["booking_finish_time"] = lambda row: display_finish_time(
    _row_to_dict(row)
)
app.jinja_env.filters["booking_crew"] = lambda row: display_crew(_row_to_dict(row))
app.jinja_env.filters["apple_maps_url"] = apple_maps_url
app.jinja_env.filters["tel_href"] = tel_href
app.jinja_env.filters["sms_href"] = sms_href
app.jinja_env.filters["mailto_href"] = mailto_href
app.jinja_env.filters["format_hm_12h"] = format_time_12h
app.jinja_env.filters["format_display_date"] = format_display_date
app.jinja_env.filters["weekday_class"] = get_weekday_class
app.jinja_env.filters["format_aud"] = invoice.format_aud
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
    return {
        "crew_options": active_crew_names() or CREW_OPTIONS,
        "truck_options": active_truck_names(),
        "job_status_options": job_status.OPTIONS,
        "dashboard_filters": job_status.DASHBOARD_FILTERS,
        "crew_schedule_ranges": RANGE_OPTIONS,
        "invoice_filters": INVOICE_FILTERS,
        "company_settings": company_config.get_settings(),
    }


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


def _invoice_summary_for_row(row) -> dict:
    booking = services.booking_to_dict(row)
    summary = invoice.invoice_summary(booking)
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
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
        "stripe_configured": stripe_config.has_credentials(),
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
    invoice_status = xero.resolve_invoice_status(booking) if linked else ""
    return {
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
        "phone": defaults["phone"],
        "email": defaults["email"],
        "hourly_rate": defaults["hourly_rate"],
        "callout_fee": defaults["callout_fee"],
        "gst_enabled": defaults["gst_enabled"],
        "crew": defaults["crew"],
        "extra_charges": [],
        "start_time": DEFAULT_START_TIME,
        "finish_time": DEFAULT_FINISH_TIME,
        "duration_hours": str(int(DEFAULT_DURATION_HOURS)),
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
        gst_enabled=data.get("gst_enabled", defaults["gst_enabled"]),
        payment_status=defaults["payment_status"],
        invoice_status=defaults["invoice_status"],
        status=data.get("status", job_status.DEFAULT_STATUS),
    )
    services._persist_booking_extras(booking_id, data)
    services.prepare_booking_payment_link(booking_id)
    return booking_id


def _update_booking_from_form(booking_id, form):
    data, errors = parse_booking_form(form)
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
        gst_enabled=data["gst_enabled"],
        payment_status=data["payment_status"],
        invoice_status=data["invoice_status"],
        status=data["status"],
    )
    if ok:
        services._persist_booking_extras(booking_id, data)
    return ok, errors, data


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
    return {"status": "ok", "production": config.PRODUCTION}, 200


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


@app.route("/settings/crew", methods=["GET", "POST"], endpoint="crew_management")
@auth.login_required
def crew_management():
    if request.method == "POST":
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
                        1 if request.form.get("active") == "on" else 0,
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
                ok = db.update_crew_member(
                    int(crew_id_raw),
                    name,
                    request.form.get("phone", ""),
                    request.form.get("role", ""),
                    1 if request.form.get("active") == "on" else 0,
                )
                flash(
                    "Crew member updated." if ok else "Crew member not found.",
                    "success" if ok else "error",
                )
        return redirect(url_for("crew_management"))

    return render_template(
        "crew_management.html",
        crew_members=db.list_crew_members(),
    )


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
            if result.get("secret_updated"):
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
        message="No payment was taken. You can use the Pay Now link on your invoice to try again.",
        company_name=config.COMPANY_NAME,
        invoice_number=invoice_number,
        pay_url=services.prepare_booking_payment_link(int(row["id"])) if row else "",
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


@app.route("/dashboard", endpoint="dashboard")
@auth.login_required
def dashboard():
    today = date.today()
    dash = build_dashboard(today)
    active_filter = request.args.get("filter", "all").strip().lower()
    valid_filters = {key for key, _ in job_status.DASHBOARD_FILTERS}
    if active_filter not in valid_filters:
        active_filter = "all"
    jobs = dashboard_jobs(active_filter, today)
    enriched_jobs = []
    for row in jobs:
        item = dict(row)
        item["double_booking_badge"] = double_booking.badge_for_booking(item)
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
    return render_template(
        "dashboard.html",
        dash=dash,
        today=dash["today"],
        jobs=enriched_jobs,
        active_filter=active_filter,
        monthly_profit=monthly_profit,
        profit_month=profit_month,
        profit_status=profit_status,
        profit_paid_only=profit_paid_only,
        profit_status_filters=booking_profit.PROFIT_STATUS_FILTERS,
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
    return render_template(
        "crew_schedule.html",
        schedule=schedule,
        active_range=schedule["active_range"],
    )


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
        data, errors = parse_booking_form(request.form)
        crew_warnings = _crew_warnings_for_data(data)
        db_errors, db_conflicts, override_applied = _validate_double_booking(data)
        if errors or db_errors:
            for msg in errors + db_errors:
                flash(msg, "error")
            ctx = _double_booking_context(data)
            return render_template(
                "new_booking.html",
                form=data,
                crew_warnings=crew_warnings,
                invoice_summary=_form_invoice_summary(data),
                **ctx,
            )
        _flash_crew_warnings(crew_warnings)
        booking_id = _create_booking_from_data(data)
        _apply_new_booking_override(booking_id, override_applied, db_conflicts)
        _flash_integration_messages(services.after_booking_created(booking_id))
        flash("Booking saved (reference #{0}).".format(booking_id), "success")
        return redirect(url_for("ceo_dashboard"))

    form = _booking_form_defaults()
    return render_template(
        "new_booking.html",
        form=form,
        crew_warnings=[],
        invoice_summary=_form_invoice_summary(form),
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
            ok, msg = services.mark_booking_paid(booking_id, paid=True)
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

        previous_status = job_status.display(services.booking_to_dict(row))
        ok, errors, data = _update_booking_from_form(booking_id, request.form)
        crew_warnings = _crew_warnings_for_data(data, booking_id=booking_id)
        db_errors, db_conflicts, override_applied = _validate_double_booking(
            data, booking_id=booking_id
        )
        if errors or db_errors:
            for msg in errors + db_errors:
                flash(msg, "error")
            row = db.get_booking(booking_id)
            ctx = _double_booking_context(data, booking_id=booking_id)
            return render_template(
                "edit_booking.html",
                booking=row,
                form=data,
                status=_integration_status(),
                crew_warnings=crew_warnings,
                **_edit_booking_extras(row),
                **ctx,
            )
        if ok:
            _flash_crew_warnings(crew_warnings)
            _flash_integration_messages(
                services.after_booking_updated(
                    booking_id, previous_status=previous_status
                )
            )
            flash("Booking #{0} updated.".format(booking_id), "success")
            return redirect(url_for("ceo_dashboard"))
        flash("Could not update booking.", "error")
        return redirect(url_for("edit_booking", booking_id=booking_id))

    form = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "start_time": row["start_time"] or DEFAULT_START_TIME,
        "finish_time": row["finish_time"] or DEFAULT_FINISH_TIME,
        "duration_hours": _form_duration_from_row(row),
        "crew": crew_from_storage(_row_to_dict(row).get("crew")),
        "num_movers": row["num_movers"],
        "notes": row["notes"] or "",
        "hourly_rate": row["hourly_rate"]
        if row["hourly_rate"] is not None
        else invoice.default_invoice_fields()["hourly_rate"],
        "callout_fee": row["callout_fee"]
        if row["callout_fee"] is not None
        else invoice.default_invoice_fields()["callout_fee"],
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
    return render_template(
        "edit_booking.html",
        booking=row,
        form=form,
        status=_integration_status(),
        crew_warnings=_crew_warnings_for_data(form, booking_id=booking_id),
        **_edit_booking_extras(row),
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


@app.route("/bookings/<int:booking_id>/invoice/preview")
@auth.login_required
def invoice_preview(booking_id):
    row = db.get_booking(booking_id)
    if row is None:
        flash("Booking not found.", "error")
        return redirect(url_for("all_bookings"))
    services.prepare_booking_payment_link(booking_id)
    row = db.get_booking(booking_id)
    booking = services.booking_to_dict(row)
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
        headers={"Content-Disposition": "inline; filename={0}".format(filename)},
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
        "profit",
        "profit_export_csv",
        "outstanding_invoices",
        "automation_hub",
        "sms_settings",
        "review_settings",
        "gmail_settings",
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
