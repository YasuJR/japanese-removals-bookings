# Japanese Removals — Booking System

A simple, local booking manager for **Japanese Removals** (Perth, WA).

## What it does

- **Staff login** — only your team can access bookings
- **Save / edit / delete / search / export CSV**
- **Google Calendar** — auto-sync move dates (when connected)
- **Job sheet PDF** — crew printable sheet per booking
- **SMS (Twilio)** — customer confirmation texts (when configured)
- **Xero** — create draft invoices from a booking (when connected)
- **Mobile friendly** — card layout on phones, table on desktop

**Full setup for integrations:** see [SETUP.md](SETUP.md)

## Quick start

### 1. Install Python

On Mac, Python 3 is often pre-installed. Check:

```bash
python3 --version
```

You need version 3.9 or newer (3.9.6 works).

### 2. Open Terminal in this folder

```bash
cd ~/Projects/japanese-removals-bookings
```

### 3. Create a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
cp .env.example .env
```

### 5. Create a staff login

```bash
python create_staff.py admin YourSecurePassword123
```

### 6. Run the app

```bash
python3 app.py
```

### 7. Open in your browser

Go to: **http://127.0.0.1:5001**

(On many Macs, port **5000** is used by **AirPlay Receiver**, so this app uses **5001** instead.)

Use **New booking** to add jobs; **Upcoming jobs** shows what’s coming up.

## Where data is stored

Bookings are saved in **`bookings.db`** in this folder (SQLite). Back up that file to keep your data safe.

## Project layout

| File / folder | Purpose |
|---------------|---------|
| `app.py` | Web pages and forms |
| `database.py` | Save and load bookings |
| `bookings.db` | Your data (created on first run) |
| `templates/` | HTML pages |
| `static/style.css` | Look and feel |
| `integrations/google_calendar.py` | Future Calendar sync |
| `integrations/xero.py` | Future invoicing |

## Git (optional)

If you install Xcode Command Line Tools (`xcode-select --install`), you can run `git init` in this folder for version control.

## Integrations

See **[SETUP.md](SETUP.md)** for Google Calendar, Twilio SMS, and Xero (step-by-step).
