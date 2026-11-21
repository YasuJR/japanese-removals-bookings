#!/usr/bin/env python3
"""E2E — Company Gmail → Check to Confirm (integrations/company_gmail.py). Fake Gmail + fake Claude.

  A  sent "Confirmed" email → one Check to Confirm booking with the extracted fields, Gmail ids
     stored, missing / uncertain fields kept for the yellow highlights; no calendar, SMS or email
  B  duplicates: the same message twice → nothing new; a 2nd "Confirmed" in the same thread → linked
     to the booking for review (no new booking); same customer + same date in another thread → linked
  C  skipped: emails sent before checking was turned on, "Confirmed" only in a quoted reply,
     payment confirmations; cancellation → linked to the customer's booking (nothing changed)
  D  hidden from staff: Staff Portal (all + crew), daily jobs / weekly, driver sheet, crew schedule;
     owner calendar shows it orange; dashboard counter, list and Gmail link; dashboard quick status
     cannot confirm it
  E  edit page: review panel + highlight list; Confirm Booking blocked while name / date / movers
     missing; schedule conflict needs the override tick; Confirm → Confirmed, Google Calendar sync
     once, visible on the Staff Portal, no SMS / email
  F  review list: amendment shown on the dashboard, "Checked" clears it
  G  company Gmail login: wrong Google account refused; callback keeps the Calendar token file
  H  one worker at a time (lease); turning checking on starts from now
  I  parser: quoted replies cut, HTML mail, invalid values become missing
  J  normal bookings unchanged (Confirmed still syncs, Pending skips, inline status works)
"""

import base64
import json
import os
import random
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")
for _flag in ("GOOGLE_CALENDAR_ENABLED", "GMAIL_INBOX_ENABLED", "XERO_ENABLED", "SMS_ENABLED", "STRIPE_ENABLED", "EMAIL_ENABLED"):
    os.environ.setdefault(_flag, "false")
os.environ["COMPANY_GMAIL_POLLING"] = "false"

import auth
import config
import database as db
import job_status
import services
from app import app
from dashboard_data import perth_today
from integrations import company_gmail, email_send, gmail_confirmed_parser as parser, google_calendar, sms, sms_automation

COMPANY = config.COMPANY_GMAIL_ADDRESS
_n = 0
_rng = random.Random()
_used = set()
CALENDAR_CALLS = []
SENT = []


def _tag():
    global _n
    _n += 1
    return "{0}-{1}-{2}".format(os.getpid(), int(time.time()) % 100000, _n)


def _client():
    db.init_db()
    label = "ctc-{0}".format(_tag())
    uid = db.create_staff_user(label, auth.hash_password("t"), "CTC Test", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _free_day():
    while True:
        day = (perth_today() + timedelta(days=_rng.randint(400, 5000))).isoformat()
        if day not in _used and not db.list_by_date(day):
            _used.add(day)
            return day


# --- fakes ------------------------------------------------------------------------------


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def gmsg(mid, tid, frm, to, subject, body, sent_ms, html=False):
    return {
        "id": mid,
        "threadId": tid,
        "internalDate": str(sent_ms),
        "labelIds": ["SENT"] if COMPANY in frm else ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": frm},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": time.strftime("%a, %d %b %Y %H:%M:%S +0800", time.gmtime(sent_ms / 1000))},
            ],
            "parts": [{"mimeType": "text/html" if html else "text/plain", "body": {"data": _b64(body)}}],
        },
    }


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    """Just enough of the Gmail API: messages.list/get, threads.get/list (read-only)."""

    def __init__(self, store):
        self.store = store

    def users(self):
        return self

    def messages(self):
        gmail = self

        class M:
            def list(self, userId, q, maxResults=100, pageToken=None):
                after = int(re.search(r"after:(\d+)", q).group(1)) * 1000
                ids = [m["id"] for m in gmail.store.values()
                       if "SENT" in m["labelIds"] and int(m["internalDate"]) >= after
                       and "confirmed" in json.dumps(m).lower() + _plain(m).lower()]
                return _Exec({"messages": [{"id": i} for i in ids]})

            def get(self, userId, id, format="full"):
                return _Exec(gmail.store[id])

        return M()

    def threads(self):
        gmail = self

        class T:
            def get(self, userId, id, format="full"):
                msgs = sorted((m for m in gmail.store.values() if m["threadId"] == id), key=lambda m: int(m["internalDate"]))
                return _Exec({"id": id, "messages": msgs})

            def list(self, userId, q, maxResults=5):
                addr = re.search(r'"([^"]+)"', q).group(1)
                tids = sorted({m["threadId"] for m in gmail.store.values() if addr in json.dumps(m["payload"]["headers"])})
                return _Exec({"threads": [{"id": t} for t in tids]})

        return T()


def _plain(m):
    part = m["payload"]["parts"][0]
    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")


ANSWERS = {}


def fake_ask(thread_text, context_text, today_iso):
    """Claude stand-in: the answer registered for the case marker found in the thread."""
    for marker, answer in ANSWERS.items():
        if marker in thread_text:
            answer = dict(answer)
            answer.setdefault("_context", context_text)
            return answer
    raise AssertionError("no fake answer for thread")


def _answer(**fields):
    base = {"kind": "booking", "kind_reason": "confirms a job", "summary": "", "customer_name": None, "email": None,
            "phone": None, "move_date": None, "start_time": None, "pickup_address": None, "delivery_address": None,
            "num_movers": None, "hourly_rate": None, "callout_fee": None, "callout_minutes": None,
            "additional_charges": [], "furniture": None, "notes": None, "missing": [], "uncertain": []}
    base.update(fields)
    return base


def _spy():
    CALENDAR_CALLS.clear()
    SENT.clear()
    google_calendar.sync_booking_to_calendar = lambda b: CALENDAR_CALLS.append(int(b["id"])) or None
    sms.send_booking_notification = lambda *a, **k: SENT.append(("sms", a)) or None
    sms_automation.send_template_sms = lambda *a, **k: (SENT.append(("sms-template", a)) or (False, "blocked in test"))
    email_send.send_email = lambda *a, **k: (SENT.append(("email", a)) or (False, "blocked in test"))


_ORIGINAL_SETTINGS = None


def _start_checking(minutes_ago=60):
    """Checking turned on `minutes_ago` minutes ago (fake token; the fake service is passed in)."""
    global _ORIGINAL_SETTINGS
    db.init_db()
    if _ORIGINAL_SETTINGS is None:
        _ORIGINAL_SETTINGS = db.get_integration_settings(company_gmail.STORAGE_KEY)
    start = int(time.time() * 1000) - minutes_ago * 60000
    company_gmail._update(enabled=True, start_after_ms=start, token_json="{}", attempts={})
    return start


def _restore_settings():
    if _ORIGINAL_SETTINGS is not None:
        db.save_integration_settings(company_gmail.STORAGE_KEY, _ORIGINAL_SETTINGS)


def _poll(store):
    return company_gmail.poll_once(service=FakeGmail(store), ask=fake_ask, today_iso=perth_today().isoformat())


def _thread(tag, start_ms, final_body, *, marker=None, customer=None, extra=()):
    """Quote request → our reply → customer books → our "Confirmed" email."""
    marker = marker or "[case {0}]".format(tag)
    customer = customer or "ctc-{0}@example.com".format(tag)
    tid = "t-" + tag
    cust = "Test Customer {0} <{1}>".format(tag, customer)
    us = "Yasuharu Sugimoto <{0}>".format(COMPANY)
    msgs = [
        gmsg("m1-" + tag, tid, cust, us, "Quote request " + marker, "Hi, moving 2 bedroom unit. " + marker, start_ms + 1000),
        gmsg("m2-" + tag, tid, us, cust, "Re: Quote request " + marker, "Our rate is $180/hr for 2men plus 30min call out.", start_ms + 2000),
        gmsg("m3-" + tag, tid, cust, us, "Re: Quote request " + marker, "Please book us in.", start_ms + 3000),
        gmsg("m4-" + tag, tid, us, cust, "Re: Quote request " + marker, final_body, start_ms + 4000),
    ]
    msgs.extend(extra)
    return {m["id"]: m for m in msgs}, tid, customer


def _booking_for_message(mid):
    rows = [r for r in db.list_gmail_confirmations(500) if r["message_id"] == mid]
    return rows[0] if rows else None


# --- tests --------------------------------------------------------------------------------


def test_case_a_creates_check_to_confirm():
    _spy()
    start = _start_checking()
    tag = _tag()
    day = _free_day()
    store, tid, customer = _thread(tag, start, "Hi\nConfirmed your booking on {0} starting at 8am.\nSee you then.".format(day))
    ANSWERS["[case {0}]".format(tag)] = _answer(
        summary="Booking {0} 8am, 2 men".format(day), customer_name="Test Customer " + tag, email=customer,
        phone="0411 000 111", move_date=day, start_time="08:00", pickup_address="1 Test St, Karrinyup WA",
        delivery_address=None, num_movers=2, hourly_rate=180, callout_minutes=30,
        additional_charges=[{"description": "Upright piano", "amount": 66}], furniture="2 bedroom unit",
        notes="Stairs at pickup", missing=["delivery_address"],
        uncertain=[{"field": "start_time", "reason": "8am was only proposed"}])
    result = _poll(store)
    assert result["ok"] and result["created"] == 1, result
    rec = _booking_for_message("m4-" + tag)
    assert rec and rec["outcome"] == "created" and rec["thread_id"] == tid, rec
    b = dict(db.get_booking(rec["booking_id"]))
    assert b["status"] == job_status.CHECK_TO_CONFIRM
    assert b["customer_name"] == "Test Customer " + tag and b["email"] == customer and b["phone"] == "0411 000 111"
    assert b["move_date"] == day and b["start_time"] == "08:00" and int(b["num_movers"]) == 2
    assert float(b["hourly_rate"]) == 180 and int(b["callout_minutes"]) == 30 and float(b["callout_fee"]) == 90
    assert b["pickup_address"].startswith("1 Test St") and b["delivery_address"] == ""
    assert "Stairs at pickup" in b["notes"] and "Furniture: 2 bedroom unit" in b["notes"]
    assert b["gmail_message_id"] == "m4-" + tag and b["gmail_thread_id"] == tid and b["source"] == "gmail_confirmed"
    charges = db.list_extra_charges(int(b["id"]))
    assert len(charges) == 1 and charges[0]["description"] == "Upright piano" and float(charges[0]["unit_price"]) == 66
    review = company_gmail.review_for_booking(b)
    assert [m["field"] for m in review["missing"]] == ["delivery_address"], review
    assert review["uncertain"][0]["field"] == "start_time" and "proposed" in review["uncertain"][0]["reason"]
    assert set(review["highlight_fields"]) == {"delivery_address", "start_time"}
    assert tid in review["gmail_url"] and "authuser=" in review["gmail_url"]
    assert CALENDAR_CALLS == [] and SENT == [], (CALENDAR_CALLS, SENT)
    # whole thread + older threads reach Claude
    assert "Please book us in." in parser.transcript(FakeGmail(store).threads().get("me", tid).execute()["messages"], [COMPANY])
    return True


def test_case_b_duplicates_and_amendments():
    _spy()
    start = _start_checking()
    tag = _tag()
    day = _free_day()
    store, tid, customer = _thread(tag, start, "Confirmed your booking on {0}.".format(day))
    marker = "[case {0}]".format(tag)
    ANSWERS[marker] = _answer(customer_name="Dup " + tag, email=customer, move_date=day, num_movers=2, summary="first")
    assert _poll(store)["created"] == 1
    again = _poll(store)
    assert again["created"] == 0 and again["linked"] == 0, again  # same message never twice
    count_before = len(db.bookings_for_gmail_thread(tid))
    # 2nd "Confirmed" in the same thread → linked (Confirmed again / change), no new booking
    later = gmsg("m5-" + tag, tid, "Y <{0}>".format(COMPANY), customer, "Re: " + marker,
                 "Sure, confirmed your new time 1pm on {0}.".format(day), start + 9000)
    store[later["id"]] = later
    result = _poll(store)
    assert result["linked"] == 1 and result["created"] == 0, result
    assert len(db.bookings_for_gmail_thread(tid)) == count_before == 1
    rec = _booking_for_message("m5-" + tag)
    first = db.bookings_for_gmail_thread(tid)[0]
    assert rec["related_booking_id"] == first["id"] and rec["kind"] == "duplicate" and rec["outcome"] == "linked"
    # same customer, same date, another thread → linked, not a second booking
    tag2 = _tag()
    store2, _tid2, _ = _thread(tag2, start, "Yes confirmed your booking on {0}.".format(day), customer=customer)
    ANSWERS["[case {0}]".format(tag2)] = _answer(customer_name="Dup " + tag, email=customer, move_date=day, num_movers=2)
    r2 = _poll(store2)
    assert r2["linked"] == 1 and r2["created"] == 0, r2
    assert len([b for b in db.bookings_for_customer_email(customer)]) == 1
    return True


def test_case_c_skips_old_quoted_payment_and_cancellation():
    _spy()
    start = _start_checking()
    tag = _tag()
    day = _free_day()
    customer = "ctc-c-{0}@example.com".format(tag)
    us = "Y <{0}>".format(COMPANY)
    old = gmsg("old-" + tag, "t-old-" + tag, us, customer, "Old", "Confirmed your booking on Friday. [case old-{0}]".format(tag), start - 3600000)
    quoted = gmsg("q-" + tag, "t-q-" + tag, us, customer, "Re: x",
                  "Thanks, see you then.\n\nOn Mon, 1 Jan 2026 at 9:00, Y <{0}> wrote:\n> Confirmed your booking".format(COMPANY),
                  start + 5000)
    pay = gmsg("p-" + tag, "t-p-" + tag, us, customer, "Re: invoice", "Thank you, confirmed your payment. [case pay-{0}]".format(tag), start + 6000)
    ANSWERS["[case pay-{0}]".format(tag)] = _answer(kind="payment", summary="payment received")
    ANSWERS["[case old-{0}]".format(tag)] = _answer(customer_name="OLD SHOULD NOT IMPORT")
    store = {m["id"]: m for m in (old, quoted, pay)}
    result = _poll(store)
    assert result["created"] == 0 and result["skipped"] == 2, result
    assert _booking_for_message("old-" + tag) is None  # before checking was turned on: never looked at
    assert _booking_for_message("q-" + tag)["outcome"] == "skipped"
    assert _booking_for_message("p-" + tag)["kind"] == "payment"
    # cancellation → linked to the customer's booking, booking unchanged
    existing = db.create_booking("Cancel " + tag, "", customer, "1 A St", "2 B St", day, 2, "", hourly_rate=180, status="Confirmed")
    cancel = gmsg("c-" + tag, "t-c-" + tag, us, customer, "Re: cancel", "Confirmed your cancellation. [case can-{0}]".format(tag), start + 7000)
    ANSWERS["[case can-{0}]".format(tag)] = _answer(kind="cancellation", email=customer, move_date=day, summary="cancelled")
    r = _poll({cancel["id"]: cancel})
    assert r["linked"] == 1, r
    rec = _booking_for_message("c-" + tag)
    assert rec["related_booking_id"] == existing and rec["kind"] == "cancellation"
    assert dict(db.get_booking(existing))["status"] == "Confirmed"
    assert {"id": rec["id"]} and rec["id"] in [x["id"] for x in db.list_open_gmail_reviews()]
    assert SENT == [] and CALENDAR_CALLS == []
    return True


def _make_check_booking(day=None, crew="", **overrides):
    _spy()
    start = _start_checking()
    tag = _tag()
    day = day or _free_day()
    store, tid, customer = _thread(tag, start, "Confirmed your booking on {0} at 9am.".format(day))
    fields = dict(customer_name="Check " + tag, email=customer, phone="0400 111 222", move_date=day, start_time="09:00",
                  pickup_address="5 Pick St, Perth WA", delivery_address="6 Drop St, Perth WA", num_movers=2,
                  hourly_rate=180, callout_minutes=30, summary="Booking " + day)
    fields.update(overrides)
    ANSWERS["[case {0}]".format(tag)] = _answer(**fields)
    assert _poll(store)["created"] == 1
    booking_id = _booking_for_message("m4-" + tag)["booking_id"]
    if crew:
        with db.get_connection() as conn:
            conn.execute("UPDATE bookings SET crew = ? WHERE id = ?", (crew, booking_id))
            conn.commit()
    return booking_id, tag, day, tid


def test_case_d_hidden_from_staff_visible_to_owner():
    client = _client()
    today = perth_today().isoformat()
    crew_name = "CtcCrew-" + _tag()
    crew_id = db.create_crew_member(crew_name, role="Mover", active=1)
    booking_id, tag, _day, tid = _make_check_booking(day=today, crew=crew_name)
    try:
        name = "Check " + tag
        for url in ("/staff?staff_id={0}&range=today".format(crew_id), "/staff?staff_id=all&range=today",
                    "/staff?staff_id={0}&range=week".format(crew_id)):
            html = app.test_client().get(url).get_data(as_text=True)
            assert name not in html, url
        for url in ("/daily-jobs?date={0}".format(today), "/weekly-schedule?date={0}".format(today)):
            resp = client.get(url)
            if resp.status_code == 200:
                assert name not in resp.get_data(as_text=True), url
        import crew_schedule_data
        import daily_jobs_data
        import driver_run_sheet_data
        assert name not in json.dumps(daily_jobs_data.build_daily_jobs(today), default=str)
        assert name not in json.dumps(crew_schedule_data.build_crew_schedule("today", today=perth_today()), default=str)
        assert name not in json.dumps(driver_run_sheet_data.build_driver_run_sheet(crew_name, today), default=str)
        # owner calendar: shown, orange
        y, m, d = today.split("-")
        cal = client.get("/calendar?view=day&year={0}&month={1}&day={2}".format(y, int(m), int(d))).get_data(as_text=True)
        assert name in cal and "cal-check-to-confirm" in cal
        # dashboard: counter, list, Gmail link
        dash = client.get("/dashboard").get_data(as_text=True)
        assert 'id="check-to-confirm"' in dash and name in dash and tid in dash
        assert re.search(r'class="check-confirm-count">\s*\d+', dash)
        assert "status-check-to-confirm" in dash
        # dashboard quick status cannot confirm it
        resp = client.post("/bookings/{0}/status".format(booking_id), json={"status": "Confirmed"})
        assert resp.status_code == 400 and dict(db.get_booking(booking_id))["status"] == job_status.CHECK_TO_CONFIRM
        assert CALENDAR_CALLS == []
    finally:
        db.delete_booking(booking_id)
    return True


def test_case_e_edit_and_confirm():
    client = _client()
    booking_id, tag, day, tid = _make_check_booking(
        customer_name=None, uncertain=[{"field": "num_movers", "reason": "2 or 3 men not agreed"}])
    page = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert 'id="check-to-confirm"' in page and "Not in the email" in page and "Customer name" in page
    assert "2 or 3 men not agreed" in page and '"num_movers"' in page and '"customer_name"' in page
    assert "Before confirming, fill in: Customer name" in page and "Confirm Booking</button>" not in page
    resp = client.post("/bookings/{0}/confirm-check".format(booking_id), follow_redirects=True)
    assert "Fill in before confirming" in resp.get_data(as_text=True)
    assert dict(db.get_booking(booking_id))["status"] == job_status.CHECK_TO_CONFIRM
    with db.get_connection() as conn:
        conn.execute("UPDATE bookings SET customer_name = ? WHERE id = ?", ("Check " + tag, booking_id))
        conn.commit()
    page = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Confirm Booking</button>" in page and '"customer_name"' not in page.split('id="check-review-fields">')[1][:200]
    # conflicting Confirmed job (both without truck, same time) → needs the tick
    other = db.create_booking("Other " + tag, "", "", "1 A St", "2 B St", day, 2, "", start_time="09:00",
                              duration_hours="3", hourly_rate=180, status="Confirmed")
    page = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Other jobs at the same time" in page and "Other " + tag in page and "I understand this may be double booked" in page
    resp = client.post("/bookings/{0}/confirm-check".format(booking_id), follow_redirects=True)
    assert "Schedule conflict" in resp.get_data(as_text=True)
    assert dict(db.get_booking(booking_id))["status"] == job_status.CHECK_TO_CONFIRM
    _spy()
    resp = client.post("/bookings/{0}/confirm-check".format(booking_id),
                       data={"double_booking_override_confirm": "on"}, follow_redirects=True)
    text = resp.get_data(as_text=True)
    assert "confirmed — now on the Staff Portal" in text, text[:300]
    b = dict(db.get_booking(booking_id))
    assert b["status"] == "Confirmed" and CALENDAR_CALLS == [booking_id], CALENDAR_CALLS
    assert SENT == [], SENT
    page = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert 'id="check-to-confirm"' not in page  # review panel gone once confirmed
    # now visible to staff
    crew_name = "CtcCrewE-" + _tag()
    crew_id = db.create_crew_member(crew_name, role="Mover", active=1)
    with db.get_connection() as conn:
        conn.execute("UPDATE bookings SET crew = ?, move_date = ? WHERE id = ?", (crew_name, perth_today().isoformat(), booking_id))
        conn.commit()
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    assert "Check " + tag in html
    db.delete_booking(booking_id)
    db.delete_booking(other)
    return True


def test_case_f_review_list_checked():
    client = _client()
    _spy()
    start = _start_checking()
    tag = _tag()
    day = _free_day()
    customer = "ctc-f-{0}@example.com".format(tag)
    existing = db.create_booking("Amend " + tag, "", customer, "1 A St", "2 B St", day, 2, "", hourly_rate=180, status="Confirmed")
    msg = gmsg("f-" + tag, "t-f-" + tag, "Y <{0}>".format(COMPANY), customer, "Re: change",
               "Sure confirmed your rescheduling for 2pm. [case f-{0}]".format(tag), start + 5000)
    ANSWERS["[case f-{0}]".format(tag)] = _answer(kind="amendment", email=customer, move_date=day, start_time="14:00",
                                                  summary="moved to 2pm")
    assert _poll({msg["id"]: msg})["linked"] == 1
    dash = client.get("/dashboard").get_data(as_text=True)
    assert "Email updates to check" in dash and "Amend " + tag in dash and "moved to 2pm" in dash
    rec = _booking_for_message("f-" + tag)
    resp = client.post("/gmail-reviews/{0}/done".format(rec["id"]), data={"next": "//evil.example"}, follow_redirects=False)
    assert resp.status_code in (302, 303) and "evil" not in resp.headers["Location"]
    assert rec["id"] not in [x["id"] for x in db.list_open_gmail_reviews()]
    assert (dict(db.get_booking(existing))["start_time"] or "") == ""  # booking itself untouched
    db.delete_booking(existing)
    return True


def test_case_g_login_account_and_calendar_token():
    client = _client()

    class FakeCreds:
        scopes = list(company_gmail.SCOPES)

        def to_json(self):
            return json.dumps({"token": "x", "refresh_token": "y", "scopes": self.scopes})

    class FakeFlow:
        credentials = FakeCreds()

        def fetch_token(self, authorization_response):
            return None

        def authorization_url(self, **kwargs):
            FakeFlow.kwargs = kwargs
            return "https://accounts.example/auth", "state-1"

    import googleapiclient.discovery as discovery

    original_flow, original_build = company_gmail.google_oauth.make_flow_for_scopes, discovery.build
    before = _ORIGINAL_SETTINGS if _ORIGINAL_SETTINGS is not None else db.get_integration_settings(company_gmail.STORAGE_KEY)
    try:
        company_gmail.google_oauth.make_flow_for_scopes = lambda uri, scopes: FakeFlow()
        for account, ok in (("sales.sugimoto@gmail.com", False), (COMPANY, True)):
            discovery.build = lambda *a, **k: type("S", (), {
                "users": lambda self: type("U", (), {"getProfile": lambda self, userId: _Exec({"emailAddress": account})})()})()
            company_gmail._update(token_json="")
            result_ok, msg = company_gmail.complete_connect("http://127.0.0.1:5001/integrations/google/callback", "http://x/?code=1")
            assert result_ok is ok, (account, msg)
            assert company_gmail.is_connected() is ok
        url, _state = company_gmail.begin_connect("http://127.0.0.1:5001/integrations/google/callback")
        assert FakeFlow.kwargs.get("login_hint") == COMPANY and FakeFlow.kwargs.get("access_type") == "offline"
        # callback route: company login branch never writes the Calendar token file
        token_file = Path(config.GOOGLE_TOKEN_FILE)
        before_token = token_file.read_bytes() if token_file.is_file() else None
        resp = client.get("/integrations/company-gmail/connect")
        assert resp.status_code in (302, 303) and resp.headers["Location"].startswith("https://accounts.example")
        resp = client.get("/integrations/google/callback?state=state-1&code=abc", follow_redirects=False)
        assert "/settings/company-gmail" in resp.headers["Location"]
        after_token = token_file.read_bytes() if token_file.is_file() else None
        assert before_token == after_token
        page = client.get("/settings/company-gmail").get_data(as_text=True)
        assert "read-only" in page and COMPANY in page
        assert client.get("/settings").status_code == 200
    finally:
        company_gmail.google_oauth.make_flow_for_scopes, discovery.build = original_flow, original_build
        db.save_integration_settings(company_gmail.STORAGE_KEY, before)
    return True


def test_case_h_lease_and_start_from_now():
    key = "test_lease_" + _tag()
    assert db.try_acquire_lease(key, 60) is True
    assert db.try_acquire_lease(key, 60) is False  # the other worker waits
    with db.get_connection() as conn:
        conn.execute("UPDATE job_leases SET expires_at = '' WHERE lease_key = ?", (key,))
        conn.commit()
    assert db.try_acquire_lease(key, 60) is True  # expired lease can be taken again
    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_leases WHERE lease_key = ?", (key,))
        conn.commit()
    # one check at a time: while one runs, Check now is refused
    assert db.try_acquire_lease(company_gmail.RUNNING_KEY, 60) is True
    busy = company_gmail.run_check(max_messages=1)
    assert busy["ok"] is False and "already running" in busy["message"], busy
    db.release_lease(company_gmail.RUNNING_KEY)
    _start_checking()
    before = int(time.time() * 1000)
    company_gmail.set_enabled(True)
    start = int(db.get_integration_settings(company_gmail.STORAGE_KEY)["start_after_ms"])
    assert start >= before - 5
    company_gmail.set_enabled(False)
    result = company_gmail.poll_once(service=FakeGmail({}), ask=fake_ask, today_iso="2026-01-01")
    assert result["ok"] is False and "off" in result["message"]
    assert company_gmail.start_background_polling() is False  # COMPANY_GMAIL_POLLING=false in tests
    return True


def test_case_i_parser():
    plain = {"mimeType": "text/plain", "body": {"data": _b64(
        "Hi Colleen\nYes confirmed your booking.\n\n    On Tuesday, 29 September 2026 at 08:06:59 pm GMT+8, Y <a@b.c> wrote:\n Hi\nold text")}}
    assert parser.new_text(plain) == "Hi Colleen\nYes confirmed your booking."
    html_part = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": _b64(
        "<div>Hi Fletcher<br>Confirmed your booking on Friday.</div><blockquote>Confirmed earlier quote</blockquote>")}}]}
    assert parser.new_text(html_part) == "Hi Fletcher\nConfirmed your booking on Friday."
    msg = {"payload": {"mimeType": "text/plain", "body": {"data": _b64("Thanks!\n\nOn Mon, 1 Jan, X <x@y.z> wrote:\n> Confirmed")}}}
    assert parser.says_confirmed(msg) is False
    assert parser.says_confirmed({"payload": plain}) is True
    cleaned = parser.clean({"kind": "weird", "move_date": "next Friday", "start_time": "25:00", "num_movers": "3 men",
                            "hourly_rate": "$287", "callout_minutes": 30, "email": "A@B.COM",
                            "uncertain": [{"field": "bogus", "reason": "x"}]})
    assert cleaned["kind"] == "other" and cleaned["move_date"] == "" and cleaned["start_time"] == ""
    assert cleaned["num_movers"] == 3 and cleaned["hourly_rate"] == 287.0 and cleaned["email"] == "a@b.com"
    assert "move_date" in cleaned["missing"] and "start_time" in cleaned["missing"] and "callout_fee" not in cleaned["missing"]
    assert cleaned["uncertain"] == []
    assert parser.customer_address({"payload": {"headers": [{"name": "To", "value": "wordpress@japaneseremovals.com.au, Bob <bob@x.com>"}]}},
                                   [COMPANY]) == "bob@x.com"
    return True


def test_case_j_normal_bookings_unchanged():
    client = _client()
    _spy()
    tag = _tag()
    day = _free_day()
    confirmed = db.create_booking("Normal " + tag, "0400000000", "", "1 A St", "2 B St", day, 2, "", hourly_rate=180, status="Confirmed")
    services.after_booking_created(confirmed)
    pending = db.create_booking("Pending " + tag, "", "", "1 A St", "2 B St", day, 2, "", hourly_rate=180, status="Pending")
    services.after_booking_created(pending)
    assert CALENDAR_CALLS == [confirmed], CALENDAR_CALLS
    resp = client.post("/bookings/{0}/status".format(confirmed), json={"status": "Completed"})
    assert resp.status_code == 200 and dict(db.get_booking(confirmed))["status"] == "Completed"
    assert job_status.normalize("Check to Confirm") == "Check to Confirm" and job_status.normalize("Pending") == "Pending"
    assert job_status.sort_priority("Check to Confirm") < job_status.sort_priority("Pending")
    page = client.get("/bookings/{0}/edit".format(confirmed)).get_data(as_text=True)
    assert 'id="check-to-confirm"' not in page
    db.delete_booking(confirmed)
    db.delete_booking(pending)
    return True


def test_case_k_public_pages_and_delete_clears_email_details():
    anon = app.test_client()
    for path, must in (("/about", ("JR Booking System", "read-only", "/privacy")),
                       ("/privacy", ("Limited Use", "gmail.readonly", "Anthropic", "myaccount.google.com/permissions",
                                     "Clear stored email details", COMPANY))):
        resp = anon.get(path)
        text = resp.get_data(as_text=True)
        assert resp.status_code == 200, path
        for word in must:
            assert word in text, (path, word)
    assert "/privacy" in anon.get("/login").get_data(as_text=True)
    booking_id, tag, _day, _tid = _make_check_booking()
    rec = _booking_for_message("m4-" + tag)
    assert rec["summary"] and rec["customer_email"]
    db.delete_booking(booking_id)
    rec = _booking_for_message("m4-" + tag)
    assert rec is not None and rec["summary"] == "" and rec["details_json"] == "" and rec["customer_email"] == ""
    assert db.gmail_confirmation_exists("m4-" + tag)  # never imported again
    return True


def main():
    tests = [
        test_case_a_creates_check_to_confirm,
        test_case_b_duplicates_and_amendments,
        test_case_c_skips_old_quoted_payment_and_cancellation,
        test_case_d_hidden_from_staff_visible_to_owner,
        test_case_e_edit_and_confirm,
        test_case_f_review_list_checked,
        test_case_g_login_account_and_calendar_token,
        test_case_h_lease_and_start_from_now,
        test_case_i_parser,
        test_case_j_normal_bookings_unchanged,
        test_case_k_public_pages_and_delete_clears_email_details,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                if test():
                    print("PASS:", test.__name__)
                    passed += 1
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                print("FAIL:", test.__name__, "—", exc)
    finally:
        _restore_settings()
        # remove this run's Check to Confirm bookings left behind by failures
        for b in db.list_check_to_confirm_bookings():
            if (b.get("customer_name") or "").startswith(("Check ", "Dup ", "Test Customer ")) and (b.get("email") or "").startswith("ctc-"):
                db.delete_booking(int(b["id"]))
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
