"""
Xero draft invoices — OAuth + Accounting API.
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
import database as db
import invoice
from crew import display_crew
from integrations import company_config, xero_branding, xero_config

# Granular scopes required for apps created on/after 2026-03-02.
XERO_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "accounting.invoices",
    "accounting.contacts",
    "accounting.settings",
    "accounting.payments",
]

XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"

INVOICE_ACCOUNT_CODE = "200"
TAX_TYPE_GST = "OUTPUT"


def has_credentials() -> bool:
    """Client ID and secret available (settings file or .env)."""
    return xero_config.has_credentials()


def is_configured() -> bool:
    """Ready to start OAuth — credentials saved; not blocked by XERO_ENABLED."""
    return has_credentials()


def is_connected() -> bool:
    return has_credentials() and Path(config.XERO_TOKEN_FILE).is_file()


def is_ready() -> bool:
    return is_connected() and bool(xero_config.get_tenant_id())


def payments_scope_granted() -> bool:
    """True when the stored OAuth token includes accounting.payments."""
    path = Path(config.XERO_TOKEN_FILE)
    if not path.is_file():
        return False
    try:
        token = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    scope = (token.get("scope") or "").split()
    return "accounting.payments" in scope or "accounting.transactions" in scope


def invoice_url(invoice_id: str) -> str:
    return (
        "https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={0}".format(
            invoice_id
        )
    )


def _parse_xero_date(value: Any) -> str:
    """Return YYYY-MM-DD from a Xero date field."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    match = re.search(r"/Date\((-?\d+)", text)
    if match:
        raw = int(match.group(1))
        if abs(raw) > 10_000_000_000:
            raw = int(raw / 1000)
        try:
            return datetime.utcfromtimestamp(raw).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return ""
    return ""


def is_real_invoice_id(invoice_id: Any) -> bool:
    text = str(invoice_id or "").strip()
    return bool(text) and not text.startswith("LOCAL-")


def _load_token() -> Optional[Dict[str, Any]]:
    path = Path(config.XERO_TOKEN_FILE)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _save_token(data: Dict[str, Any]) -> None:
    path = Path(config.XERO_TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def resolve_redirect_uri(redirect_uri: str = "") -> str:
    """
    Return the redirect URI sent to Xero.

    Xero's authorize endpoint returns 403 Forbidden when redirect_uri uses
    127.0.0.1 — use localhost instead (must match the Xero Web App config).
    """
    from urllib.parse import urlparse, urlunparse

    raw = (redirect_uri or config.XERO_REDIRECT_URI).strip()
    parts = urlparse(raw)
    host = (parts.hostname or "").lower()
    if host == "127.0.0.1":
        port = parts.port
        netloc = "localhost:{0}".format(port) if port else "localhost"
        parts = parts._replace(netloc=netloc)
        raw = urlunparse(parts)
    return raw


def oauth_connect_details(state: str, redirect_uri: str) -> Dict[str, str]:
    """Build authorize request fields for display and redirect."""
    redirect_uri = resolve_redirect_uri(redirect_uri)
    client_id = xero_config.get_client_id().strip()
    scope = " ".join(XERO_SCOPES)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if not payments_scope_granted():
        params["prompt"] = "consent"
    return {
        **params,
        "authorize_url": build_authorize_url(params),
        "scope_list": " ".join(XERO_SCOPES),
    }


def build_authorize_url(params: Dict[str, str]) -> str:
    from urllib.parse import quote, urlencode

    # Scope must use %20 between values (not +) per Xero examples.
    return XERO_AUTHORIZE_URL + "?" + urlencode(params, quote_via=quote)


def get_authorize_url(state: str, redirect_uri: str = "") -> str:
    details = oauth_connect_details(state, redirect_uri)
    return details["authorize_url"]


def authorize_url_preview(state: str = "preview-not-used-for-connect") -> str:
    """Build the authorize URL for settings diagnostics (not used for OAuth)."""
    return get_authorize_url(state, config.XERO_REDIRECT_URI)


def _token_request(body: Dict[str, str]) -> Tuple[bool, Dict[str, Any], str]:
    import base64
    import urllib.error
    import urllib.parse
    import urllib.request

    creds = "{0}:{1}".format(
        xero_config.get_client_id(), xero_config.get_client_secret()
    ).encode("utf-8")
    auth_header = base64.b64encode(creds).decode("utf-8")
    encoded = urllib.parse.urlencode(body).encode("utf-8")

    req = urllib.request.Request(
        "https://identity.xero.com/connect/token",
        data=encoded,
        headers={
            "Authorization": "Basic " + auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, {}, "Xero token error: {0}".format(detail[:200])


def exchange_code_for_token(code: str, redirect_uri: str = "") -> Tuple[bool, str]:
    redirect_uri = resolve_redirect_uri(redirect_uri)
    ok, token, err = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
    )
    if not ok:
        return False, err

    _save_token(token)

    tenants = _fetch_connections(token.get("access_token", ""))
    if not tenants:
        return True, "Xero connected. No organisations found — check your Xero account."

    chosen = tenants[0]
    if len(tenants) > 1 and xero_config.get_tenant_id():
        for item in tenants:
            if item.get("tenantId") == xero_config.get_tenant_id():
                chosen = item
                break

    tenant_id = chosen.get("tenantId", "")
    tenant_name = chosen.get("tenantName", "Organisation")
    xero_config.save_settings(
        client_id=xero_config.get_client_id(),
        tenant_id=tenant_id,
    )

    if len(tenants) == 1:
        return True, (
            "Xero connected successfully. Organisation: {0}. "
            "You can create draft invoices from bookings."
        ).format(tenant_name)

    return True, (
        "Xero connected successfully. Organisation: {0}. "
        "({1} organisations available — change Tenant ID on this page if needed.)"
    ).format(tenant_name, len(tenants))


def _refresh_access_token() -> bool:
    token = _load_token()
    if not token or not token.get("refresh_token"):
        return False

    ok, refreshed, _ = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        }
    )
    if not ok:
        return False

    merged = dict(token)
    merged.update(refreshed)
    if "refresh_token" not in refreshed and token.get("refresh_token"):
        merged["refresh_token"] = token["refresh_token"]
    _save_token(merged)
    return True


def _fetch_connections(access_token: str) -> List[Dict[str, Any]]:
    import urllib.error
    import urllib.request

    if not access_token:
        return []

    req = urllib.request.Request(
        "https://api.xero.com/connections",
        headers={
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return []


def list_tenant_options() -> List[Dict[str, str]]:
    token = _load_token()
    if not token:
        return []
    connections = _fetch_connections(token.get("access_token", ""))
    return [
        {
            "tenant_id": c.get("tenantId", ""),
            "tenant_name": c.get("tenantName", "Organisation"),
        }
        for c in connections
        if c.get("tenantId")
    ]


def list_tenants() -> Tuple[bool, str]:
    options = list_tenant_options()
    if not options:
        return False, "No organisations found. Connect Xero first."
    lines = [
        "{0}: tenantId={1}".format(o["tenant_name"], o["tenant_id"])
        for o in options
    ]
    return True, "\n".join(lines)


def _api_request(
    method: str, path: str, payload: Optional[Dict] = None, retry: bool = True
) -> Dict:
    import urllib.error
    import urllib.request

    token = _load_token()
    if not token:
        raise RuntimeError("Xero not connected")

    access = token.get("access_token")
    if not access:
        raise RuntimeError("Missing Xero access token")

    tenant_id = xero_config.get_tenant_id()
    if not tenant_id:
        raise RuntimeError("Set Tenant ID on the Xero settings page.")

    url = "https://api.xero.com/api.xro/2.0/" + path.lstrip("/")
    data = None
    headers = {
        "Authorization": "Bearer " + access,
        "Accept": "application/json",
        "xero-tenant-id": tenant_id,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and retry and _refresh_access_token():
            return _api_request(method, path, payload, retry=False)
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            message = "Xero API error ({0}): {1}".format(exc.code, detail[:2000])
        else:
            message = "Xero API error ({0}): {1}".format(
                exc.code,
                exc.reason or "request failed",
            )
        raise RuntimeError(message) from exc


def _build_contact(
    booking: Dict[str, Any],
    existing_invoice: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bill-to contact — customer only; company identity uses branding theme + org header."""
    customer = (booking.get("customer_name") or "").strip()
    contact: Dict[str, Any] = {"Name": customer or "Customer"}
    email = (booking.get("email") or "").strip()
    phone = (booking.get("phone") or "").strip()
    if email:
        contact["EmailAddress"] = email
    if phone:
        contact["Phones"] = [{"PhoneType": "MOBILE", "PhoneNumber": phone}]
    if existing_invoice:
        existing_contact = existing_invoice.get("Contact") or {}
        contact_id = (existing_contact.get("ContactID") or "").strip()
        if contact_id:
            contact["ContactID"] = contact_id
    return contact


def _build_labour_description(
    booking: Dict[str, Any], totals: Dict[str, Any]
) -> str:
    """Service details only — no company or bank information."""
    hours = totals["hours"]
    hourly_rate = totals["hourly_rate"]
    crew_label = display_crew(booking)
    return "\n".join(
        [
            "Moving Labour",
            "{0:.1f} hrs".format(hours),
            "{0}/hr".format(invoice.format_aud(hourly_rate)),
            "Crew:",
            crew_label,
        ]
    )


def _line_items_for_invoice(
    booking: Dict[str, Any],
    totals: Dict[str, Any],
    existing_invoice: Optional[Dict[str, Any]] = None,
) -> List[Dict]:
    """
    Build line items for create/update.

    On update, omit LineItemID so Xero deletes old lines and recreates them.
    Reusing LineItemID does not reliably overwrite Description on draft updates.
    """
    del existing_invoice  # fetched for logging only; IDs are intentionally not reused
    return _build_line_items(booking, totals)


def _build_line_items(booking: Dict[str, Any], totals: Dict[str, Any]) -> List[Dict]:
    hours = totals["hours"]
    hourly_rate = totals["hourly_rate"]
    callout_fee = totals["callout_fee"]
    gst_enabled = totals["gst_enabled"]
    tax_type = TAX_TYPE_GST if gst_enabled else "NONE"

    items = [
        {
            "Description": _build_labour_description(booking, totals),
            "Quantity": hours,
            "UnitAmount": hourly_rate,
            "AccountCode": INVOICE_ACCOUNT_CODE,
            "TaxType": tax_type,
        }
    ]

    if callout_fee > 0:
        items.append(
            {
                "Description": "Callout fee",
                "Quantity": 1,
                "UnitAmount": callout_fee,
                "AccountCode": INVOICE_ACCOUNT_CODE,
                "TaxType": tax_type,
            }
        )

    for charge in totals.get("extra_charges") or []:
        description = (charge.get("description") or "").strip()
        if not description:
            continue
        items.append(
            {
                "Description": description,
                "Quantity": float(charge.get("quantity") or 1),
                "UnitAmount": float(charge.get("unit_price") or 0),
                "AccountCode": INVOICE_ACCOUNT_CODE,
                "TaxType": tax_type,
            }
        )

    return items


def _draft_invoice_payload(
    booking: Dict[str, Any],
    existing_invoice: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict, Dict, int, str, str]:
    resolved = invoice.resolve_booking_invoice(booking)
    totals = invoice.calculate_invoice_totals(resolved)
    booking_id = int(booking["id"])
    issue_date = resolved["move_date"]
    try:
        due_date = (
            datetime.strptime(issue_date, "%Y-%m-%d").date()
            + timedelta(days=config.INVOICE_DUE_DAYS)
        ).isoformat()
    except ValueError:
        due_date = issue_date

    payload = {
        "Type": "ACCREC",
        "Contact": _build_contact(resolved, existing_invoice),
        "Date": issue_date,
        "DueDate": due_date,
        "LineAmountTypes": (
            "Inclusive"
            if totals["gst_enabled"] and company_config.gst_pricing_inclusive()
            else ("Exclusive" if totals["gst_enabled"] else "NoTax")
        ),
        "Status": "DRAFT",
        "Reference": str(booking_id),
        "LineItems": _line_items_for_invoice(resolved, totals, existing_invoice),
    }
    theme_id = xero_branding.branding_theme_id_for_invoice()
    if not theme_id and is_connected():
        try:
            theme_id = xero_branding.resolve_branding_theme_id(_api_request)
        except Exception:
            theme_id = ""
    if theme_id:
        payload["BrandingThemeID"] = theme_id
    return payload, totals, booking_id, issue_date, due_date


def persist_invoice_from_xero(
    booking_id: int,
    inv: Dict[str, Any],
    fallback_issue: str = "",
    fallback_due: str = "",
) -> None:
    invoice_id = inv.get("InvoiceID") or ""
    saved_issue = _parse_xero_date(inv.get("Date")) or fallback_issue
    saved_due = _parse_xero_date(inv.get("DueDate")) or fallback_due
    db.update_booking_invoice_fields(
        booking_id,
        {
            "xero_invoice_id": invoice_id,
            "invoice_number": inv.get("InvoiceNumber") or "",
            "invoice_status": inv.get("Status") or "",
            "invoice_issue_date": saved_issue,
            "invoice_due_date": saved_due,
        },
    )


def fetch_invoice(invoice_id: str) -> Optional[Dict[str, Any]]:
    """Load a single invoice from Xero."""
    if not is_real_invoice_id(invoice_id):
        return None
    try:
        result = _api_request("GET", "Invoices/{0}".format(invoice_id))
        invoices = result.get("Invoices") or []
        return invoices[0] if invoices else None
    except Exception:
        return None


def _parse_xero_datetime(value: Any) -> str:
    """Return local YYYY-MM-DD HH:MM:SS from Xero date fields."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"/Date\((\d+)", text)
    if match:
        try:
            ts = int(match.group(1)) / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return ""
    if "T" in text and len(text) >= 19:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            pass
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return text[:10]
    return ""


def derive_payment_status_from_invoice(
    inv: Dict[str, Any],
    booking: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Map Xero AmountDue / AmountPaid to local payment status.
    Returns (status, paid_at).
    """
    from datetime import date

    from outstanding_invoices_data import due_date_for

    amount_due = float(inv.get("AmountDue") or 0)
    amount_paid = float(inv.get("AmountPaid") or 0)

    if amount_due <= 0.01 and amount_paid > 0:
        paid_at = _parse_xero_datetime(inv.get("FullyPaidOnDate"))
        return invoice.PAYMENT_STATUS_PAID, paid_at

    if amount_paid > 0.01 and amount_due > 0.01:
        return invoice.PAYMENT_STATUS_PART_PAID, ""

    due_text = due_date_for(booking)
    if due_text:
        try:
            due = datetime.strptime(due_text[:10], "%Y-%m-%d").date()
            if due < date.today():
                return invoice.PAYMENT_STATUS_OVERDUE, ""
        except ValueError:
            pass

    return invoice.PAYMENT_STATUS_UNPAID, ""


def sync_payment_status_from_xero(
    booking: Dict[str, Any],
) -> Tuple[bool, str]:
    """Pull payment status from linked Xero invoice and update the booking."""
    return sync_invoice_after_stripe_payment(booking)


def sync_invoice_after_stripe_payment(
    booking: Dict[str, Any],
) -> Tuple[bool, str]:
    """Refresh invoice number, Xero status, and payment date from Xero."""
    if not is_ready():
        return False, "Connect Xero and set a tenant ID first."

    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if not is_real_invoice_id(invoice_id):
        return False, "No Xero invoice linked to this booking."

    inv = fetch_invoice(invoice_id)
    if not inv:
        return False, "Could not load invoice from Xero."

    booking_id = int(booking["id"])
    persist_invoice_from_xero(booking_id, inv)
    payment_status, paid_at = derive_payment_status_from_invoice(inv, booking)
    xero_status = (inv.get("Status") or "").strip().upper()
    fields: Dict[str, Any] = {
        "invoice_status": xero_status or (booking.get("invoice_status") or ""),
        "invoice_number": (inv.get("InvoiceNumber") or "").strip()
        or (booking.get("invoice_number") or ""),
    }
    if payment_status == invoice.PAYMENT_STATUS_PAID:
        fields["payment_status"] = payment_status
        if paid_at:
            fields["paid_at"] = paid_at
    db.update_booking_invoice_fields(booking_id, fields)
    if payment_status == invoice.PAYMENT_STATUS_PAID:
        db.update_booking_status(booking_id, "Paid")

    number = fields.get("invoice_number") or invoice_id[:8]
    msg = "Xero invoice {0} synced — status {1}.".format(
        number,
        fields.get("invoice_status") or payment_status,
    )
    if paid_at and payment_status == invoice.PAYMENT_STATUS_PAID:
        msg = "{0} Paid date: {1}.".format(msg, paid_at[:10])
    return True, msg


def list_bank_accounts() -> List[Dict[str, str]]:
    """Return active bank accounts for Stripe payment posting."""
    if not is_ready():
        return []
    try:
        result = _api_request("GET", 'Accounts?where=Type=="BANK"')
    except Exception:
        return []
    accounts = result.get("Accounts") or []
    out: List[Dict[str, str]] = []
    for account in accounts:
        if account.get("Status") == "ARCHIVED":
            continue
        account_id = (account.get("AccountID") or "").strip()
        code = (account.get("Code") or "").strip() or account_id
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": (account.get("Name") or "").strip(),
                "id": account_id,
            }
        )
    return out


def default_bank_account_code() -> str:
    accounts = list_bank_accounts()
    if not accounts:
        return ""
    for account in accounts:
        name = (account.get("name") or "").lower()
        if "stripe" in name or "business" in name:
            return account["code"]
    return accounts[0]["code"]


def _payment_account_ref(account_code: str) -> Dict[str, str]:
    value = (account_code or "").strip()
    if len(value) == 36 and value.count("-") == 4:
        return {"AccountID": value}
    return {"Code": value}


def create_and_authorise_invoice_for_booking(
    booking: Dict[str, Any],
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Create or update a Xero invoice, approve it, and persist invoice number."""
    if not is_ready():
        return False, "Connect Xero and set a tenant ID first.", None

    ok, msg, inv = sync_invoice_record(booking, confirm_new=False)
    if not ok or not inv:
        return False, msg, None

    booking_id = int(booking["id"])
    invoice_id = (inv.get("InvoiceID") or "").strip()
    status = (inv.get("Status") or "").strip().upper()
    if status in ("DRAFT", ""):
        ok, msg, inv = approve_invoice(invoice_id)
        if not ok:
            return False, msg, inv

    if inv:
        persist_invoice_from_xero(booking_id, inv)
        number = (inv.get("InvoiceNumber") or "").strip()
        if number:
            msg = "Xero invoice {0} created and approved.".format(number)
        else:
            msg = "Xero invoice created and approved."
    return True, msg, inv


def ensure_invoice_ready_for_stripe_payment(
    booking: Dict[str, Any],
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Ensure an authorised Xero invoice exists before posting a Stripe payment."""
    if not is_ready():
        return False, "Xero not connected.", None

    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if not is_real_invoice_id(invoice_id):
        return create_and_authorise_invoice_for_booking(booking)

    status = resolve_invoice_status(booking)
    if status in ("DRAFT", ""):
        ok, msg, inv = approve_invoice(invoice_id)
        if ok and inv:
            persist_invoice_from_xero(int(booking["id"]), inv)
        return ok, msg, inv
    if status in ("AUTHORISED", "PAID"):
        inv = fetch_invoice(invoice_id)
        return True, "Xero invoice ready.", inv
    return False, "Cannot post payment — invoice status is {0}.".format(status), None


def post_stripe_payment_to_xero(
    booking: Dict[str, Any],
    *,
    amount: float,
    account_code: str,
    reference: str,
    payment_date: str,
) -> Tuple[bool, str, Optional[str]]:
    """Create a Xero payment against the linked invoice (Stripe card receipt)."""
    if not is_ready():
        return False, "Connect Xero and set a tenant ID first.", None

    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if not is_real_invoice_id(invoice_id):
        return False, "No Xero invoice linked to this booking.", None

    code = (account_code or "").strip()
    if not code:
        return False, "Xero payment account code not configured.", None

    date_text = (payment_date or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    ref = (reference or "Stripe")[:255]
    payload = {
        "Payments": [
            {
                "Invoice": {"InvoiceID": invoice_id},
                "Account": _payment_account_ref(code),
                "Date": date_text,
                "Amount": round(float(amount), 2),
                "Reference": ref,
            }
        ]
    }
    try:
        result = _api_request("PUT", "Payments", payload)
    except RuntimeError as exc:
        return False, str(exc), None

    payments = result.get("Payments") or []
    payment_id = None
    if payments:
        payment_id = (payments[0].get("PaymentID") or "").strip() or None
    if not payment_id:
        return False, "Xero did not return a payment ID.", None
    return True, "Payment posted to Xero ({0}).".format(payment_id[:8]), payment_id


def resolve_invoice_status(booking: Dict[str, Any]) -> str:
    """Return upper-case Xero invoice status for a linked booking."""
    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if is_real_invoice_id(invoice_id) and is_ready():
        inv = fetch_invoice(invoice_id)
        if inv and (inv.get("Status") or "").strip():
            status = (inv.get("Status") or "").strip().upper()
            booking_id = booking.get("id")
            cached = (booking.get("invoice_status") or "").strip().upper()
            if booking_id and status != cached:
                db.update_booking_invoice_fields(
                    int(booking_id), {"invoice_status": status}
                )
            return status
    cached = (booking.get("invoice_status") or "").strip().upper()
    if cached:
        return cached
    return ""


def is_draft_invoice(booking: Dict[str, Any]) -> bool:
    status = resolve_invoice_status(booking)
    return not status or status == "DRAFT"


def is_locked_invoice(booking: Dict[str, Any]) -> bool:
    return resolve_invoice_status(booking) in ("AUTHORISED", "PAID")


def _format_sync_message(action: str, inv: Dict[str, Any], totals: Dict[str, Any]) -> str:
    number = inv.get("InvoiceNumber") or inv.get("InvoiceID", "")[:8]
    gst_line = (
        "GST: {0}".format(invoice.format_aud(totals["gst_amount"]))
        if totals["gst_enabled"]
        else "GST: not applied"
    )
    return (
        "Xero invoice {0} {1}. "
        "Subtotal (ex GST): {2}, {3}, Total (incl. GST): {4}."
    ).format(
        number or "updated",
        action,
        invoice.format_aud(totals["subtotal"]),
        gst_line,
        invoice.format_aud(totals["total"]),
    )


def _sync_branding_payment_advice(
    booking: Dict[str, Any] = None,
) -> Tuple[bool, str]:
    """Push company header + bank details into Xero branding/org (not line items)."""
    if not is_connected():
        return True, ""
    try:
        return xero_branding.sync_branding_theme(_api_request, booking=booking)
    except Exception as exc:
        return False, "Invoice header sync failed: {0}".format(exc)


def sync_invoice_branding(booking: Dict[str, Any] = None) -> Tuple[bool, str]:
    """Manual sync for settings page."""
    if not is_ready():
        return False, "Connect Xero and set a tenant ID first."
    return xero_branding.sync_branding_theme(_api_request, booking=booking)


def list_branding_theme_options() -> List[Dict[str, Any]]:
    if not is_connected():
        return []
    try:
        return xero_branding.branding_status(_api_request).get("themes") or []
    except Exception:
        return []


def _log_invoice_payload(booking_id: int, request_body: Dict) -> None:
    """Persist the exact JSON sent to Xero for debugging."""
    try:
        path = Path(config.CREDENTIALS_DIR) / "xero_last_invoice_payload.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "booking_id": booking_id,
                    "sent_at": datetime.now().isoformat(),
                    "xero_module": str(Path(__file__).resolve()),
                    "request": request_body,
                },
                indent=2,
            )
        )
    except OSError:
        pass


def _post_invoice(
    booking: Dict[str, Any],
    invoice_id: str = "",
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    existing_invoice = fetch_invoice(invoice_id) if invoice_id else None
    payload, totals, booking_id, issue_date, due_date = _draft_invoice_payload(
        booking, existing_invoice=existing_invoice
    )
    if invoice_id:
        payload["InvoiceID"] = invoice_id
        payload["Status"] = "DRAFT"
        action = "updated"
        live_status = resolve_invoice_status(booking)
        if live_status and live_status not in ("DRAFT", ""):
            return (
                False,
                "This invoice is authorised/paid in Xero. "
                "Create a new invoice version instead.",
                None,
            )
    else:
        action = "created"

    theme_id = payload.get("BrandingThemeID") or ""
    if theme_id:
        company_config.save_settings({"xero_branding_theme_id": theme_id})

    _branding_ok, branding_msg = _sync_branding_payment_advice(booking)

    request_body = {"Invoices": [payload]}
    _log_invoice_payload(booking_id, request_body)

    try:
        result = _api_request("POST", "Invoices", request_body)
        invoices = result.get("Invoices") or []
        if not invoices:
            return False, "Xero returned no invoice.", None
        inv = invoices[0]
        if not inv.get("InvoiceID"):
            return False, "Xero invoice missing ID.", None

        persist_invoice_from_xero(booking_id, inv, issue_date, due_date)
        msg = _format_sync_message(action, inv, totals)
        if branding_msg:
            msg = "{0} {1}".format(msg, branding_msg)
        return True, msg, inv
    except Exception as exc:
        return False, str(exc), None


def sync_invoice_record(
    booking: Dict[str, Any],
    confirm_new: bool = False,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Create or update the Xero invoice for a booking.

    DRAFT (or unset): update the existing linked draft.
    AUTHORISED / PAID: only create a new invoice when confirm_new is True.
    """
    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if is_real_invoice_id(invoice_id):
        status = resolve_invoice_status(booking)
        if status == "DRAFT" or not status:
            return _post_invoice(booking, invoice_id=invoice_id)
        if status in ("AUTHORISED", "PAID"):
            if not confirm_new:
                return (
                    False,
                    "This invoice is authorised/paid in Xero. "
                    "Create a new invoice version instead.",
                    None,
                )
            return _post_invoice(booking)
        return False, "Cannot update invoice with status {0}.".format(status), None
    return _post_invoice(booking)


def create_draft_invoice_record(
    booking: Dict[str, Any],
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Create or update draft in Xero and persist."""
    return sync_invoice_record(booking, confirm_new=False)


def approve_invoice(
    invoice_id: str,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    try:
        result = _api_request(
            "POST",
            "Invoices",
            {
                "Invoices": [
                    {"InvoiceID": invoice_id, "Status": "AUTHORISED"},
                ]
            },
        )
        invoices = result.get("Invoices") or []
        if not invoices:
            return False, "Xero returned no invoice after approve.", None
        inv = invoices[0]
        return True, "Invoice approved.", inv
    except Exception as exc:
        return False, str(exc), None


def email_invoice(invoice_id: str) -> Tuple[bool, str]:
    try:
        _api_request("POST", "Invoices/{0}/Email".format(invoice_id), {})
        return True, "Invoice emailed to customer."
    except Exception as exc:
        return False, str(exc)


def create_draft_invoice_for_booking(booking: Dict[str, Any]) -> Tuple[bool, str]:
    if not is_configured():
        return False, "Xero is not configured — open Settings → Xero."

    if not is_connected():
        return False, "Xero is not connected — click Connect Xero on the Xero settings page."

    if not xero_config.get_tenant_id():
        return False, "Set Tenant ID on the Xero settings page."

    ok, msg, _inv = sync_invoice_record(booking, confirm_new=False)
    return ok, msg


def create_invoice_for_booking(booking: Dict[str, Any]) -> Optional[str]:
    """Legacy hook — manual draft only (no auto on save)."""
    return None
