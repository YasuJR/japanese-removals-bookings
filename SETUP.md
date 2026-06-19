# Japanese Removals — Integration setup (step by step)

## Before you start

```bash
cd ~/Projects/japanese-removals-bookings
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your values.

---

## 1. Google Calendar

**What it does:** When you save or update a booking, an all-day event is added to your Google Calendar (Perth timezone). Deleting a booking removes the event.

### Steps

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (e.g. `japanese-removals`).
3. **APIs & Services → Library** → enable **Google Calendar API**.
4. **OAuth consent screen** → External → add your Gmail as a **test user**.
5. **Credentials → Create credentials → OAuth client ID** → type **Web application**.
6. Add **Authorized redirect URIs** (both recommended for local dev):
   - `http://127.0.0.1:5001/integrations/google/callback`
   - `http://localhost:5001/integrations/google/callback`
7. Download JSON → save as:
   - `credentials/google_credentials.json`
8. In `.env`:
   ```env
   GOOGLE_CALENDAR_ENABLED=true
   ```
9. Run the app, log in → **Settings → Connect Google** → sign in with Google.
10. Create a test booking → check your calendar.

---

## 2. Job sheet PDF

**What it does:** Printable one-page job sheet for your crew (customer, addresses, movers, notes).

### Steps

1. No API keys needed (uses ReportLab).
2. Open any booking → **Edit** → **Download job sheet (PDF)**.
3. Print or send to drivers via email/WhatsApp.

---

## 3. Staff login

**What it does:** Only logged-in staff can see bookings (runs on your Mac or server).

### Steps

1. Create the first user (8+ character password):
   ```bash
   python create_staff.py admin YourSecurePassword123
   ```
2. In `.env`, set a strong secret:
   ```env
   SECRET_KEY=long-random-string-here
   ```
3. Run `python3 app.py` → open `http://127.0.0.1:5001` → log in.
4. Add more staff: run `create_staff.py` again with a new username.

---

## 4. Customer SMS (Twilio)

**What it does:** Sends an SMS to the customer when a booking is created (and optionally when updated).

### Steps

1. Sign up at [Twilio](https://www.twilio.com/).
2. Get a phone number (Australian mobile recommended).
3. From the Twilio console copy:
   - Account SID
   - Auth Token
   - Your Twilio phone number (E.164, e.g. `+61412345678`)
4. In `.env`:
   ```env
   SMS_ENABLED=true
   SMS_ON_BOOKING_CREATE=true
   SMS_ON_BOOKING_UPDATE=false
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM_NUMBER=+61...
   COMPANY_PHONE=04xx xxx xxx
   ```
5. Trial accounts: verify customer phone numbers in Twilio first.
6. On **Edit booking**, use **Send SMS** to test manually.

---

## 5. Xero draft invoices

**What it does:** Creates a **draft** sales invoice in Xero from a booking (manual button — not automatic on save).

### Steps

1. [Xero Developer](https://developer.xero.com/) → **New app** → OAuth 2.0.
2. Redirect URI: `http://127.0.0.1:5001/integrations/xero/callback`
3. Copy **Client ID** and **Client secret** into `.env`:
   ```env
   XERO_ENABLED=true
   XERO_CLIENT_ID=...
   XERO_CLIENT_SECRET=...
   XERO_DEFAULT_LINE_AMOUNT=150.00
   ```
4. Log in → **Settings → Connect Xero** → authorise.
5. Copy **tenantId** from the box on Settings into:
   ```env
   XERO_TENANT_ID=...
   ```
6. Restart the app. Open a booking → **Edit** → **Create Xero draft**.
7. In Xero, open **Business → Invoices → Draft** to review and send.

**Note:** Default sales account code is `200`. If Xero rejects it, edit `integrations/xero.py` and set your account code from **Chart of accounts**.

---

## Quick reference

| Feature        | Auto on save? | Where to configure      |
|----------------|---------------|-------------------------|
| Google Calendar| Yes           | Settings + `.env`       |
| Job sheet PDF  | On demand     | Edit booking            |
| Staff login    | Always on     | `create_staff.py`       |
| SMS            | Optional      | `.env` + Edit (manual)  |
| Xero draft     | Manual button | Settings + Edit booking |
