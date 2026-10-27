# CLAUDE.md — Japanese Removals Booking App

Developer handbook for Claude Code sessions. **Read this whole file before changing anything.**
It is the single source of truth for how the app is built, deployed, and tested.
Older docs: `README.md` / `SETUP.md` (local setup, partly outdated), `docs/DEPLOYMENT.md`, `docs/BACKUP.md`, `docs/ROLLBACK.md`.

Owner: Yasu (Japanese Removals / JR West Pty Ltd, Perth WA). Staff UI is bilingual (English primary, Japanese sublabels).

---

## 0. Session start checklist (do this first, every session)

```bash
cd ~/Projects/japanese-removals-bookings
git status && git log --oneline -5 && git log origin/main..main --oneline   # unpushed work?
curl -s https://japanese-removals-bookings.onrender.com/health | python3 -m json.tool  # what is live?
```

- Compare `/health.git_commit` with `git rev-parse origin/main` — tells you whether production is up to date.
- **Never** `export DATABASE_URL=...` in your shell while running tests — every test script would write to that Postgres (production!).
- Commit / push / deploy only when Yasu asks. Pushing `main` = deploying to production (Render auto-deploys).
- Check **§19 TODO** for open tasks.

---

## 1. Architecture

| Item | Value |
|---|---|
| Stack | Python + Flask (single `app.py`, no blueprints), Jinja templates, vanilla JS/CSS, ReportLab PDFs |
| Python | Local `.venv` = **3.9.6**; Render = **3.11.9**. Write 3.9-compatible code (no `match`, use `from __future__ import annotations` for `X \| None` hints) |
| DB | Local: SQLite `bookings.db` (gitignored). Production: Render PostgreSQL `japanese-removals-db` |
| Prod web | Render service `japanese-removals-bookings`, `gunicorn wsgi:app --workers 2 --timeout 120`, health check `/health` |
| Prod URL | https://japanese-removals-bookings.onrender.com |
| Cron | `render.yaml` defines cron `xero-payment-sync` (every 15 min → `scripts/run_xero_payment_sync.py`), but **as of 2026-10-17 it does not exist on Render**. Only the web service is deployed, and the dashboard shows "Last Xero Sync: 24 Aug 2026". See §17 |
| Repo | https://github.com/YasuJR/japanese-removals-bookings (branch `main`) |
| Timezone | `Australia/Perth` everywhere (`config.TIMEZONE`, `perth_today()`) |

### Module map

| Area | Files |
|---|---|
| App / routes | `app.py` (all `@app.route`s, form parsing, `before_request` → `db.init_db()` + `auth.load_logged_in_user()`), `wsgi.py` (prod entry: bootstrap → init_db → app) |
| Config | `config.py` (env vars, `.env` then `.env.production`, no override), `production_bootstrap.py` (writes OAuth files from env, seeds admin, Stripe/Xero settings into DB) |
| DB | `database.py` (schema, `init_db`, all queries), `db_backend.py` (SQLite/Postgres switch, SQL adaptation, Postgres DDL) |
| Auth | `auth.py` (office login, `login_required`, `admin_required`), `staff_auth.py` (Staff Portal cookie), `create_staff.py` |
| Booking logic | `booking_times.py` (start/finish/duration, sort keys), `booking_helpers.py`, `validators.py`, `job_status.py`, `services.py` (post-save side effects: calendar, SMS, invoice refresh) |
| Pricing | `mover_pricing.py`, `callout_pricing.py`, `extra_charges.py`, `invoice.py`, `invoice_numbering.py`, `booking_profit.py` |
| Conflicts | `double_booking.py`, `trucks.py`, `resource_conflicts.py`, `crew_overlap.py`, `crew.py` |
| Staff Portal | `staff_portal.py` (read-only view builder), `staff_portal_owner.py` (owner writes), `staff_job_times.py` (all hour maths), `star_points.py` |
| Calendar / schedules | `calendar_data.py`, `weekly_schedule_data.py`, `daily_jobs_data.py`, `display_dates.py`, `crew_schedule_data.py`, `driver_run_sheet_data.py` |
| Dashboards | `ceo_dashboard_data.py`, `dashboard_data.py`, `executive_dashboard_data.py`, `sales_dashboard.py`, `profit_data.py`, `outstanding_invoices_data.py` |
| Integrations | `integrations/` — `google_calendar.py`, `gmail_*`, `xero*.py`, `stripe*.py`, `sms*.py`, `invoice_pdf.py`, `invoice_send.py`, `weekly_schedule_pdf.py`, `job_sheet.py`, `company_config.py` (DB company settings override env defaults) |
| Front-end | `templates/` (partials start with `_`), `static/*.js|css`. `static/app.js`, `invoice_pricing.js`, `callout_pricing.js`, `calendar.js` |
| Scripts | `scripts/` — tests (`test_*.py`), prod verification (`verify_production_*.py`), migrations, cron entrypoints, diagnostics |

---

## 2. Flask routes (all in `app.py`)

Auth key: **P** public · **L** `login_required` (office) · **A** `admin_required` · **O** `owner_required` (office admin, else 403) · **S** `staff_login_required` (bypassed while `STAFF_PORTAL_OPEN != "0"`, default open).

| Area | Routes |
|---|---|
| Public / health | `/health` P · `/quote` GET/POST P (website quote form, rate limited) · `/furniture`, `/furniture/start` P · `/furniture/<token>[/save|/photos|/photos/<id>[/delete]]` P (secret token, see §7b) · `/review/go/<token>`, `/review/done/<token>` P · `/pay/<token>`, `/pay/<token>/success`, `/pay/<token>/cancel` P |
| Office auth | `/login`, `/logout` |
| Settings (L) | `/settings`, `/settings/review`, `/settings/company`, `/settings/crew` (**A**), `/settings/trucks`, `/settings/sms`, `/settings/bank-transfers`, `/settings/xero`, `/settings/stripe`, `/settings/gmail` |
| Integrations | `/integrations/google/connect` L, `/integrations/google/callback` L, `/integrations/xero/connect` L, `/integrations/xero/callback` P (state-checked), `/integrations/stripe/webhook` P (signature-verified), `/integrations/twilio/status` P, `/integrations/twilio/inbound` P |
| Dashboards (L) | `/` (CEO dashboard), `/dashboard`, `/leads`, `/automation`, `/executive`, `/invoices`, `/profit`, `/profit/export.csv`, `/driver`, `/driver/run-sheet.pdf`, `/crew-schedule`, `/daily-checklist` |
| Calendar (L) | `/calendar`, `/calendar/daily/<date_iso>`, `/calendar/weekly[/<date_iso>]`, `/calendar/weekly/<date_iso>/schedule.pdf` |
| Bookings (L) | `/bookings/new`, `/bookings/search` (also matches `INV56`, `inv 56`, `56`, `INV-56`), `/bookings/export.csv`, `/bookings/upcoming`, `/bookings/all`, `/bookings/<id>`, `/bookings/<id>/edit`, `/bookings/<id>/payment` POST, `/bookings/<id>/status` POST, `/bookings/<id>/delete`, `/bookings/<id>/job-sheet.pdf`, `/bookings/<id>/actual-times` POST (**O**) |
| Invoice (L) | `/bookings/invoice/preview-calculate` POST, `/bookings/<id>/invoice/calculate` POST, `/bookings/<id>/invoice/preview`, `/bookings/<id>/invoice.pdf` (served as attachment for iOS share), `/bookings/<id>/stripe/checkout` POST, `/bookings/<id>/stripe/success` |
| Staff Portal | `/staff/login`, `/staff/logout` P · `/staff` S · `/staff/weekly/schedule.pdf` S · `/staff/crew/<id>/rename` O · `/staff/crew/<id>/star-points/adjust` O · `/staff/crew/<id>/star-points/reset` O · `/staff/bookings/<id>/edit` O |

When adding a route: put it in `app.py` near its area, use the right decorator, and run `scripts/test_navigation_e2e.py`. `app.py __main__` asserts key endpoints exist.

---

## 3. Database

### Backend switch (`db_backend.py`)
- `is_postgres()` ⇔ `config.get_database_url()` (first of `DATABASE_URL`, `POSTGRES_URL`, `POSTGRESQL_URL`, `INTERNAL_DATABASE_URL` starting with `postgres`). Otherwise SQLite `<repo>/bookings.db`.
- `adapt_sql()` (Postgres only): `?`→`%s` (**naive — never put a literal `?` in SQL**), `datetime('now')`→`CURRENT_TIMESTAMP`, `INSERT OR REPLACE`→`INSERT … ON CONFLICT` (gmail table only).
- Postgres INSERTs get `RETURNING id` appended automatically, except `processed_gmail_messages`, `integration_settings`, `invoice_sequence`, `furniture_item_volumes`, and statements with `ON CONFLICT`. A new table without an `id` column must be added to that skip list. Fallback `SELECT lastval()` in a savepoint.
- Inside a request one connection lives on `g._db_connection` (`close()` is a no-op, closed in `teardown_request`).
- **Postgres can return `date`/`datetime` objects** for date-like values. Normalize with `display_dates.normalize_move_date()` / `dashboard_data.job_move_date_iso()`. Never call `.strip()` on a raw DB value.

### Tables (key columns)
- **bookings** — customer_name, phone, email, pickup_address, delivery_address, `move_date` TEXT `YYYY-MM-DD`, num_movers, notes, status, `crew` (CSV of names), `truck_assigned`, `start_time`, `finish_time`, `duration_hours`, `actual_start_time`, `actual_finish_time`, `actual_duration` (min), `hourly_rate`, `callout_fee`, `callout_minutes`, `gst_enabled`, invoice_* (number, status, issue/due date, description, custom text, bank overrides, sent_at/to/method), payment_* / paid_at / payment_token, stripe_*, xero_invoice_id, xero_payment_id, double_booking_override_at, gmail_message_id, source, review_* / SMS / reminder timestamps, profit fields (staff_cost, fuel_cost, …, estimated_profit, profit_margin_percent). ~85 columns total; list in `database.py BOOKING_EXTRA_COLUMNS`.
- **booking_extra_charges** — booking_id, description, quantity, unit_price, sort_order (breaks live here, see §6).
- **booking_crew_hours** — per-crew time overrides (see §5).
- **crew_members** — name UNIQUE, phone, role, active, `star_points`. **crew_star_point_events** — crew_id, change_type, points_delta, points_after, reason, created_at, created_by_user_id.
- **trucks** — name UNIQUE, registration, truck_type, capacity, active.
- **staff_users** — username, password_hash, display_name, `is_admin` (users `admin` and `STAFF_USERNAME` auto-promoted).
- **invoice_sequence** — single row id=1, next_number.
- **integration_settings** — setting_key (`stripe`, `xero`) → setting_json (prod OAuth/Stripe state lives here, not on disk).
- Others: automation_log, review_requests, sms_delivery_log, processed_gmail_messages, quote_rate_limit, draft_leads, bank_transactions.
- Seeds when empty: crew Yasu, Tom, Ken; trucks "Truck 1", "Truck 2".

### Migrations (no framework — "ensure on boot")
`database.init_db()` runs once per process (on Postgres under `pg_advisory_lock(824739161)`):
1. Postgres: `db_backend.postgres_ddl()`; SQLite: inline `CREATE TABLE IF NOT EXISTS` in `init_db`.
2. `_ensure_*` chain: bank_transactions → `_ensure_columns` (bookings) → staff columns → crew columns → star point history → booking_crew_hours → invoice_sequence → seed → indexes → `_complete_existing_paid_jobs`. Postgres also runs `_reassign_mistaken_invoice_number_one`.

**How to add a column:** append `(name, type)` to `BOOKING_EXTRA_COLUMNS` / `STAFF_EXTRA_COLUMNS` / `CREW_EXTRA_COLUMNS` in `database.py`. It is added with `ALTER TABLE … ADD COLUMN` on next boot (guarded by `db_backend.table_columns()`). Use types valid in both SQLite and Postgres.

**How to add a table:** add it to **both** the SQLite DDL (`database.py`) and `db_backend.postgres_ddl()`, or write an `_ensure_<table>` that **branches on `db_backend.is_postgres()`** (`SERIAL` / `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` / `DOUBLE PRECISION` vs `INTEGER PRIMARY KEY AUTOINCREMENT` / `datetime('now')` / `REAL`). Copy `_ensure_booking_crew_hours_table` or `_ensure_star_point_history_table`. Then run `scripts/test_postgres_init_ddl_e2e.py`.

**Production migration flow:** commit → push `main` → Render deploys → `wsgi.py` runs `init_db()` → check `/health` (`booking_crew_hours_table`, `database_schema_error`). Data backfills go in `init_db` (idempotent) or a one-off `scripts/migrate_*.py` run from the **Render Shell**. Take a `pg_dump` first for anything destructive (`docs/BACKUP.md`).

---

## 4. Staff Portal (`/staff`)

- **Read-only builder** `staff_portal.build_staff_portal()`. It must **never expose pricing, invoice, cost or profit** (`_serialize_job` = operational fields only).
- **Tabs** (`RANGE_TABS`): Today / Calendar (month or week, `cal_view`) / Weekly / History. "All Staff" is not a tab: it is `staff_id=all` in the staff switcher.
  - Date windows: Today = today. Calendar month = whole month. Calendar week = Mon–Sun ± `week` offset (clamped −104..+12). Weekly = Mon–Sun. History = today−400 days … yesterday.
  - Query params: `staff_id` (crew id or `all`), `range`, `week`, `year`, `month`, `day`, `cal_view`.
- **Individual view:** only bookings whose `crew` CSV contains the name, Cancelled hidden. Today shows the Next Job card plus "Total Paid Hours". Weekly shows Work Days / Scheduled / Actual / Call Out / Paid, plus a per-day paid figure. History is grouped by week.
- **All Staff view:**
  - Today has a block per staff member with their own paid totals (from their `booking_crew_hours`).
  - Weekly reuses the office Weekly Schedule layout (`_weekly_schedule_days.html`). Cancelled jobs are shown on Weekly only.
- **Access:**
  - `STAFF_PORTAL_OPEN` defaults to `"1"` = open with no login. With `0`, staff log in at `/staff/login` with their crew name plus the shared `STAFF_PORTAL_PASSWORD`. That password is rejected if it equals `STAFF_PASSWORD`.
  - The staff identity is in a separate signed cookie `jr_staff_portal` (path `/staff`, 14 days), **not** in the Flask session.
- **Owner controls** (`staff_portal_owner.can_manage_staff_portal` = office user with `is_admin`): ✎ Edit on job cards, rename crew (also rewrites `bookings.crew` CSVs), Star Points admin.
- **Owner edit semantics** (`/staff/bookings/<id>/edit`):
  - `staff_id=<crew id>` → writes **only** `booking_crew_hours` for that person (`save_staff_crew_job_edit`; plus `add_extra_pay` / `clear_extra_pay`). If everything equals the booking's and there is no Extra Pay, the row is deleted. `action=reset_staff_times` ("Use Job Time") clears personal times but keeps Extra Pay. `action=clear_actual` blanks that person's actuals.
  - `staff_id=all` → `save_owner_job_edit` updates the booking itself (times, actuals, call-out, status, crew, notes). Known quirks: it recomputes `callout_fee` from minutes (drops any manual override), and `gst_enabled=int(x or 1)` turns GST 0 into 1.

---

## 5. `booking_crew_hours` (per-crew times)

```
booking_crew_hours(id, booking_id, crew_id, start_time, finish_time,
                   actual_start_time, actual_finish_time,
                   extra_paid_minutes INTEGER NOT NULL DEFAULT 0,   -- Extra Pay (ac82c0b)
                   UNIQUE(booking_id, crew_id))
```
- Postgres has FKs with `ON DELETE CASCADE`. SQLite has no FKs, so orphans are possible. Index on `booking_id`.
- DB API: `get_booking_crew_hours`, `upsert_booking_crew_hours` (ON CONFLICT replace), `delete_booking_crew_hours`, `map_booking_crew_hours(ids, crew_id)`.
- **Always read times through `staff_job_times.get_staff_job_times(booking, crew_id=, crew_hours=)`.**
  - `crew_id=None` (All Staff) → the booking row as-is.
  - With a crew id → the person's non-empty fields are overlaid on a copy of the booking.
  - **Invariant:** if the person's row has a personal start/finish but no actual times, booking-level actuals are **cleared** for them. A row that only carries Extra Pay (no personal start/finish) keeps the booking actuals (`_crew_hours_has_personal_schedule`). Nobody ever inherits another crew member's row.
  - `extra_paid_minutes` is **only** read from the crew row (`booking_for_crew_member` pops any booking-level value).
  - "Use Job Time" (`reset_staff_crew_times_to_job`) and "clear actual" keep Extra Pay. A row is deleted only when everything, Extra Pay included, matches the job (`_upsert_or_delete_crew_hours`).
- **Invariants:**
  - A per-crew edit must never write to `bookings`.
  - A booking-level edit must never touch `booking_crew_hours`.
  - One person's edit must never change another person's hours (the bug fixed in 50ee72d / d7c04ed).

---

## 6. Paid Hours, Break, Call out, Extra pay

### Staff Portal paid hours (`staff_job_times.paid_hours`)
```
paid = round(actual_hours − break_hours + callout_hours + extra_pay_hours, 2)   # None if actual_hours is None
```
Actual and Extra Pay come from the per-crew `effective_row`. Break and call-out come from the booking.

- **Extra Pay** (ac82c0b) is a per-staff "+0.5hr" (`DEFAULT_EXTRA_PAID_MINUTES = 30`), stored in `booking_crew_hours.extra_paid_minutes`.
  - The owner toggles it in the individual-staff Edit panel (`action=add_extra_pay` / `clear_extra_pay` → `staff_portal_owner.set_staff_extra_pay`).
  - It is staff pay only. It **never** touches the invoice, booking times, or All Staff job hours.

**`actual_hours` precedence** (`staff_job_times.actual_hours`):
1. Cancelled → `None`.
2. `actual_start_time` + `actual_finish_time` → minutes / 60 (finish ≤ start → 0).
3. `actual_duration` (minutes > 0).
4. Status Completed / Invoiced / Paid → scheduled `start_time`–`finish_time`, else `duration_hours` (0 < h ≤ 24).
5. **Invoice issued and move_date ≤ today** (even if the status is still Confirmed) → same as step 4. Fixed in ef0a456. "Issued" = `outstanding_invoices_data.invoice_has_been_issued`.
6. Otherwise `None` → "Not completed". It is excluded from totals, **not counted as 0**. A quote schedule never counts as actual.

**Components:**
- **Callout hours** (`callout_pricing.callout_hours`) = `callout_minutes / 60`. Legacy fallback: infer minutes from fee = rate × min/60, else `callout_fee / hourly_rate`.
- **Break hours** (`extra_charges.break_hours_from_booking`) = the sum over `booking_extra_charges` rows whose description contains **both "break" and "deduction"**. Minutes are parsed from `"N min break deduction"` × qty, else `|unit_price| × qty / hourly_rate × 60`.
  - Breaks are entered on the booking edit invoice panel ("Break deduction" button plus "Break (min)"). That creates a negative line `"N min break deduction"`, qty 1, unit −rate×N/60.
  - So **one row reduces both the invoice total and the staff paid hours**. Start, finish and duration are not changed. There is no break input in the Staff Portal.
- **No minimum-hours rule exists.** Other extra charges do not affect hours.
- Totals (day / week / history) sum non-None values. Display uses `format_hours_short` (`2.5hr`).

⚠️ **Different formula elsewhere:** `daily_jobs_data._serialize_job` (Daily Jobs, office Weekly Schedule and its PDF) uses `paid = start→finish (default 08:00/18:00) + callout`. It has **no break deduction**, ignores status, and includes cancelled jobs. Don't assume the two match.

### Call out
- Presets 0 / 30 / 45 / 60 / 90 min, default 30. Mover defaults: 2 movers = $180/hr, 3 movers = $235/hr (`mover_pricing.py`).
- Auto fee = `round(hourly_rate × callout_minutes / 60, 2)` (`callout_pricing.fee_amount`). `callout_fee_override` (≥ 0) wins (`resolve_callout_fee`).
- Invoice line "Callout fee (30 min)" has qty = hours and unit = rate when the fee matches the auto fee; otherwise it is a flat line.
- `callout_minutes` was added in 32b3313. A startup backfill infers it for legacy rows.

### Finish time / duration (source of truth)
- **Finish time is the source of truth.** Duration is derived from start→finish everywhere: invoice, edit, staff.
- The browser can send `HH:MM:SS`, which must be parsed (`booking_times.py`). Fixed in d1cbc00 / 279b4e7.

---

## 7. Invoice generation

- **Totals** (`invoice.calculate_invoice_totals`):
  - hours = `effective_duration_hours(booking, default=1.0)`
  - `labour = hourly_rate × hours + callout_fee`
  - `gross = labour + Σ extra charges`, where negative adjustments and breaks are allowed
  - Missing rate / fee / GST fall back to company settings (DB) → env (180 / 90 / GST on).
- **GST** is 10%. The company setting `gst_pricing_mode` defaults to **inclusive** (total = gross, subtotal = gross / 1.1); exclusive adds GST on top; GST off means none.
- **Labour line:** "Moving Labour — 8:00 AM - 12:00 PM — 4 hrs @ $180.00/hr", or the saved `invoice_description`.
- **Invoice numbers** (`invoice_numbering.py`, `database.allocate_invoice_number`):
  - The counter is in the DB table `invoice_sequence`, row-locked (`FOR UPDATE` on Postgres, `BEGIN IMMEDIATE` on SQLite). It is always `max(next_number, max_used + 1)`.
  - Stored as a plain number string (`"25"`), displayed as `INV25`. If nothing is stored, the display is `INV{booking id}`.
  - Only `services.refresh_invoice_after_booking_save` assigns numbers. Preview, PDF and edit-render must not.
- **Edit → Save unifies the invoice** (e124a0f): a successful `edit_booking` POST calls `services.refresh_invoice_after_booking_save`. That assigns a number if missing, prepares the pay link, recalculates profit, and syncs the Xero draft if one is linked and still DRAFT. The edit panel has no separate Update / Send / Preview buttons.
- **PDF:** `integrations/invoice_pdf.build_invoice_document` (shared with the HTML preview), ReportLab. Due date = issue date + `INVOICE_DUE_DAYS` (7). Bank details: booking override → company settings. Payment reference = invoice number.
- **Bank transfer only:** `INVOICE_CARD_PAYMENTS_ENABLED` defaults to false. The Stripe card / Pay Now option is hidden and checkout refuses. The `/pay/<token>` page shows bank details.
- **Xero:** a DRAFT invoice with AccountCode `200`, TaxType `OUTPUT` or `NONE`, `LineAmountTypes` Inclusive / Exclusive / NoTax. On update, line items are recreated. The cron `xero-payment-sync` marks bookings paid, and "paid" sets status Completed.
- **Sending** (`integrations/invoice_send.py`): email with the PDF via SMTP, else SMS with the pay link. The company's own phone or email is rejected as a recipient.
- Bank CSV import and matching: `bank_transfer_match.py`, `/settings/bank-transfers`.

### Dashboard Jobs sections (`/dashboard`)

- Each job is in exactly one group (`dashboard_data.dashboard_section_group`, mirrored in `database._dashboard_section_group` for the SQL sort; keep the two in sync):
  0. **TODAY & UPCOMING**: not Paid, job date today or later (Perth). Upcoming WG jobs stay here.
  1. **REGULAR UNPAID**: not Paid, past date, not WG.
  2. **WG OUTDOOR LIFE**: not Paid, past date, WG contract job. WG pays in batches weeks or months later, so these are never hidden by age.
  3. **PAID**: Payment status Paid, any date.
- Unpaid groups 1 and 2 sort **oldest date first**, then true start time. Upcoming and Paid keep status priority, then date.
- The dividers show "N jobs · Total: $X" (`dashboard_unpaid_summaries`, invoice totals over the whole filtered list, not just the visible page). The WG block has navy 6px bars above and below it and a tinted row background.
- **WG detection** (`wg_jobs.is_wg_outdoor_life_name`): there is no company / client field, so it uses `customer_name`. It matches when the first word is "WG" or the name contains the words "WG Outdoor Life" (case-, space- and punctuation-insensitive). Production spellings on 2026-10-25 were "WG", "WG Outdoor Life" and "WG Outdoor life". It never uses a raw substring, so "Wgrace" and "WGM Logistics" don't match.
- The top "Unpaid Amount" stat card (`sales_dashboard.outstanding_invoices`) still includes WG.

---

## 7b. Customer furniture lists

- Files: `furniture_catalog.py` (rooms, items, `sanitize_content`, `freeze_units`, `summarize`, frozen `LEGACY_M3`), `furniture_volume_refs.py` (where every standard m³ comes from), `furniture_volumes.py` (admin overrides + change log), `furniture_lists.py` (storage, tokens, photos, linking), `static/furniture.js|css`, `templates/furniture_*.html`, `_booking_furniture_panel.html`, `static/furniture-embed.js` (website button).
- Tables (`_ensure_furniture_list_tables`): **furniture_lists** has `public_id` (FL-XXXXXX), `access_token` (secret), `booking_id` (nullable, one list per booking), `source` (`booking`/`website`), `status` (`not_started`/`in_progress`/`submitted`), contact_* (website only), `content_json`, `estimated_m3`, counts, ISO-UTC `created_at`/`updated_at`/`submitted_at`, and `created_ip` (rate limit). **furniture_list_photos** stores JPEG bytes in the DB (`BYTEA`/`BLOB`), so photos survive redeploys. There is no disk.
- **Access:**
  - Customers reach their list only through `/furniture/<access_token>`. The 43-character token is never derived from the FL id. A photo is served only when `photo.list_id` matches the token's list.
  - `public_id` is a reference only; only office users can open a list by it.
  - Office routes (`/furniture-lists…`, `/bookings/<id>/furniture…`) are `login_required`.
  - Staff open `/staff/furniture/<signed>`, a 30-day itsdangerous token per booking. It is added to Staff Portal job cards (`attach_staff_links`) only when the list has content.
  - Public pages send `no-store`, `noindex` and `no-referrer`.
- **m³** is an *estimate* of truck load volume only. Beds come as "bed + mattress", "frame only" or "mattress only" (mutually exclusive), so nothing is counted twice. Custom items count only when L×W×H are all given. It never sets price or time.
- **Standard m³ sources** (`furniture_volume_refs.py`, reviewed 2026-10-27):
  - Load volumes come only from Australian removalists' published per-item tables (You Pack, Goldline, Easy Peasy, Your Moving Solution, Quick 'n' Slick, Moving With Sam, TT Moving, Get n Go). A value is used only when item, size and condition match (e.g. "bed **with mattress**", cot **assembled**). Otherwise it goes in `excluded` with the reason.
  - 2+ matching values → median; 1 → that value; bed frame only = bed+mattress − mattress (so frame + mattress == bed); none → `provisional` (old value or an interpolation, clearly marked). Ranges (A1) and tape-measure/manufacturer sizes (`outer`) are shown for comparison and never used.
  - `test_volume_references_are_consistent` enforces this. Change a number by editing the refs (with source, label and date), not by typing a new value into the catalogue.
- **Admin overrides:** `/furniture-lists/volumes` (L to view, office admin to change; others get 403) → table `furniture_item_volumes` (item_key PK, no `id`, so it is in `db_backend`'s RETURNING skip list) + `furniture_item_volume_log`. Setting the researched value again deletes the override.
- **Existing lists never change:** each room stores `unit_m3` per item. `freeze_units` keeps the stored m³ for items already in that room (quantity changes included) and gives newly added items the current standard. Browser-sent `unit_m3` is ignored. Lists saved before 2026-10-27 have no `unit_m3`; they read as `LEGACY_M3` (the old unsourced values), so their totals stay the same.
- **Photos:** max 30 per list and 15 MB per upload. Pillow verifies JPEG/PNG/WebP/GIF, applies EXIF rotation, strips metadata (GPS) and re-encodes as JPEG at ≤2000px.
- **Two flows (WordPress contract: website repo `docs/24-booking-app-instructions.md`):**
  - **A. Website quote flow** (`source=website`, no booking yet → `is_quote_flow`): the list is a *draft*. The editor shows "Save" and **"Save & Return to Quote"** (always saves, then goes back to the quote form; a failed save stays on the page). A customer `submit` is ignored, and nothing emails or SMSes on save. The list is received only when the website sends the enquiry and fetches the PDF.
  - **B. Existing-booking link** (`source=booking`): unchanged. "Send list" submits, and the list can be updated any time.
- **Enquiry PDF API** `GET /api/enquiry-list/<FL-ID>.pdf` (`furniture_enquiry.py`, `furniture_pdf.py`), called server to server by WordPress (`jr_furniture_pdf` in the theme's `inc/enquiry.php`):
  - Header `X-JR-Enquiry-Key` must equal env `FURNITURE_ENQUIRY_KEY` (same value as `JR_FURNITURE_KEY` in wp-config.php). Unset → 503. Missing/wrong → 401. 10 bad keys/IP/hour, or 60 requests/IP/hour → 429.
  - Only `source=website` lists are served (a booking list → 404). The first fetch sets `enquiry_received_at`, status Submitted, editor "website enquiry". Re-fetches (retry/double send) are OK for 24 h, then 409, so an FL-ID can't ride along with a later enquiry. Every request is logged in `furniture_api_log` (also the rate-limit source).
  - PDF (ReportLab, CID font HeiseiKakuGo-W5): List ID, rooms → items, qty, m³ each/subtotal, room totals, other items, total m³, notes, "写真あり / has photos (N)" (not embedded), "数量・体積は概算（推定値）です".
- **Changes after the first submission are yellow** (office list page + booking panel note, staff view, PDF):
  - The first submitted version is frozen in `baseline_json`/`baseline_at`. That happens on the first customer submit (flow B), the first enquiry fetch (flow A) or office "Submitted". A list submitted before this existed takes its pre-edit version on the next save.
  - `catalog.compare_to_baseline` matches rooms by id, items by key and "Other items" by name. It reports added/changed qty, removed items, added/changed/removed other items and notes. `annotate_changes` marks summary lines.
  - Customers never see the highlight. PDFs: `/furniture-lists/<id>/list.pdf` (L) and `/staff/furniture/<signed>/list.pdf` (S), same renderer as the enquiry PDF.
- `static/furniture-embed.js` is kept identical to the website's self-hosted copy (`theme/japanese-removals/assets/js/furniture-embed.js`). The website does not load it from this app.
- **Website flow:**
  - `/furniture?return=<https://japaneseremovals.com.au/...>`, then POST `/furniture/start` creates a `website` list (20 per IP per hour). Without a return URL, it returns to `DEFAULT_RETURN_URL` (`/contact-us/`).
  - "Back to booking form" returns to the page with `?furniture_list=FL-XXXXXX#quote`. `safe_return_url` only allows https japaneseremovals.com.au.
  - The WordPress quote form keeps its own draft (`jr_quote_draft_v1`). `furniture-embed.js` adds the button, re-opens step 2, and appends `Furniture list: FL-XXXXXX` to the message on send.
  - **Linking:** opening a booking whose notes contain an FL id auto-links an unlinked list (`auto_link_from_booking_text`). Manual linking is on the booking page or `/furniture-lists/<id>`.
- The live website (theme `japanese-removals`) self-hosts the embed script and sets `data-app` to this app's URL. Don't ask for the old `<script src=…onrender.com…>` snippet.

---

## 8. Truck conflict / double booking

- **Blocking statuses** (`double_booking.blocks_availability`): Confirmed, On Route, Paid, Completed. Pending, Quote, Cancelled, In Progress and Invoiced never block.
- **Window:** start = effective start; finish = start + `duration_hours` if set, else `finish_time`, else default duration.
  - Overlap is strict (`a_start < b_end and b_start < a_end`), so back-to-back jobs are OK.
- **Override required only when** the same date + a blocking job + a time overlap **and** `trucks.schedule_overlap_requires_override(a, b)` (fixed in 1af9e1c):
  - both bookings have a truck → conflict only if it is the **same truck** (matched by DB id via a case- and space-insensitive name);
  - both unassigned → conflict (legacy rule);
  - exactly one assigned → no conflict.
- The save error asks the user to tick "I understand this may be double booked" (`double_booking_override_confirm`). Ticking it sets `double_booking_override_at`.
- **Crew / truck warnings** (`resource_conflicts.py`) are flash warnings only and never block a save.
- ⚠️ In `edit_booking`, `_update_booking_from_form` writes to the DB **before** `_validate_double_booking`. A rejected edit still saves its field changes; only the invoice refresh and integrations are skipped. `new_booking` validates first.
- Dashboard badges (conflict / clear / override) come from `badges_for_bookings`, a batched range query via `db.list_by_move_dates_grouped`.

---

## 9. Calendar / Weekly / PDF

- **Weeks always run Monday→Sunday** (`display_dates.monday_week_bounds`, `monday_week_grid_bounds`, `dashboard_data.week_range`). Do not reintroduce Sunday-start grids.
- **Sort by true start time:** `booking_times.job_start_sort_key` → `(job_start_minutes, id)`. Missing or TBC start sorts last (1440).
  - Use this key everywhere: portal, office calendar, daily jobs, weekly schedule. **Never sort on display strings** (the bug fixed in 8cbc91e).
- **Office calendar:** `calendar_data.build_calendar_context` gives month / week / day views with status, crew, truck and payment filters. `static/calendar.js` makes day cells navigate to `/calendar/daily/<iso>`.
- **Office weekly schedule:** `weekly_schedule_data.build_weekly_schedule` calls `build_daily_jobs` for each of the 7 days. The PDF is `integrations/weekly_schedule_pdf.render_weekly_schedule_pdf`.
- **Staff calendar:** `_build_staff_calendar_month` / `_build_staff_calendar_week`. Week bounds come **only from the week offset**, not from a stale `day` param (fixed in e9d87fd). Jobs are deduped by id. The 7 columns must fit without horizontal scroll (090fcce, b73416d).
- **Staff PDF** `/staff/weekly/schedule.pdf` (generated only when this route is hit, never on `/staff` page views):
  - **Data:** `staff_portal.build_staff_weekly_pdf_schedule` calls `build_staff_portal(range=week)` and converts its `owner_weekly` / `week_days`. So the PDF shares the screen's query, staff filter, cancelled rule (All Staff shows Cancelled, Individual hides it), per-staff overrides, `job_start_sort_key` order and Paid Hours. **Never give the PDF its own query or maths again.**
  - All Staff → booking times, 1 card per booking, `weekly-schedule-<monday>.pdf`. Individual → that person's `booking_crew_hours` times, Extra Pay, Daily Paid and a header with Work Days / Scheduled / Actual / Call Out / Weekly Paid Hours, `staff-weekly-<id>-<monday>.pdf`.
  - Each card: time + duration, status, customer, (Movers ·) Crew (everyone), P / D / Ph, Note, Actual · Break · Call Out · Extra Pay, Paid.
  - **Layout** (`integrations/weekly_schedule_pdf.render_staff_weekly_schedule_pdf`): Mon→Sun columns on A4 landscape, zero-padding Frame. It tries font scales `STAFF_SCALES` (7.4pt → 5.6pt floor) and uses the largest that fits on one page. If nothing fits at the floor, the grid continues on page 2 with the day headers repeated (`splitInRow`); jobs are never cut.
  - Long unspaced tokens are broken to the column width (`_break_long_words`), and Japanese runs use the CID font `HeiseiKakuGo-W5` (DejaVu, used on Render, has no CJK glyphs).
  - The office `/calendar/weekly/<date>/schedule.pdf` still uses the older `render_weekly_schedule_pdf` + `build_weekly_schedule` (daily_jobs data).
- Other PDFs: invoice (`invoice_pdf.py`), job sheet (`job_sheet.py`), driver run sheet (`driver_run_sheet_pdf.py`).

---

## 10. Star Points

- Stored in `crew_members.star_points` (0..10, `star_points.STAR_POINTS_MAX`, `clamp_star_points`). At 10 the display shows "⭐ BONUS READY".
- `db.adjust_crew_star_points` only accepts a delta of ±1. At the bounds nothing changes and no event is written.
- `db.reset_crew_star_points` → 0. It logs a `reset` event only if the value was > 0.
- Events live in `crew_star_point_events`. History shows the latest 50; the reason is stored up to 500 chars (form max 200).
- Routes `/staff/crew/<id>/star-points/adjust` (`action=increment|decrement`) and `/reset` (`confirm=1`) are **owner only**. The display and history are visible to any portal viewer.

---

## 11. Environment variables

The full list with defaults is in `config.py`. Templates: `.env.example` (local), `.env.production.example` (Render). Production values live in the **Render Dashboard → Environment** (`render.yaml` marks secrets `sync: false`).

| Group | Vars |
|---|---|
| Core | `SECRET_KEY` (32+ chars in prod), `PRODUCTION`, `RENDER` (auto on Render), `APP_BASE_URL`, `PRODUCTION_DOMAIN`, `TIMEZONE`, `PORT` (local default 5001), `COMPANY_NAME`, `COMPANY_LEGAL_NAME`, `COMPANY_PHONE` |
| DB | `DATABASE_URL` (auto-linked on Render) |
| Office login | `STAFF_USERNAME`, `STAFF_PASSWORD`, `STAFF_DISPLAY_NAME` (admin created on boot if no users) |
| Staff Portal | `STAFF_PORTAL_OPEN` (default `1` = open), `STAFF_PORTAL_PASSWORD` (must differ from `STAFF_PASSWORD`) |
| Google | `GOOGLE_CALENDAR_ENABLED`, `GMAIL_INBOX_ENABLED`, `GOOGLE_CLIENT_ID/SECRET`, `GOOGLE_OAUTH_JSON`, `GOOGLE_TOKEN_JSON`, `GOOGLE_REDIRECT_URI`, `GOOGLE_CALENDAR_ID`, `GOOGLE_CREDENTIALS_FILE`, `GOOGLE_TOKEN_FILE`, `CALENDAR_ORGANIZER_EMAILS`, `GOOGLE_MAPS_API_KEY` |
| Xero | `XERO_ENABLED`, `XERO_CLIENT_ID/SECRET`, `XERO_TENANT_ID`, `XERO_TOKEN_JSON`, `XERO_REDIRECT_URI`, `XERO_TOKEN_FILE` (prod tokens live in DB `integration_settings`) |
| Stripe | `STRIPE_ENABLED`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_SETTINGS_JSON`, `INVOICE_CARD_PAYMENTS_ENABLED` (default false) |
| SMS / Email | `SMS_ENABLED`, `SMS_ON_BOOKING_CREATE/UPDATE`, `TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER`, `EMAIL_ENABLED`, `SMTP_HOST/PORT/USER/PASSWORD/USE_TLS`, `EMAIL_FROM` |
| Invoice / profit defaults | `INVOICE_DEFAULT_HOURLY_RATE` (180), `INVOICE_DEFAULT_CALLOUT_FEE` (90), `INVOICE_GST_ENABLED_DEFAULT`, `INVOICE_DUE_DAYS` (7), `INVOICE_BANK_*`, `PROFIT_*` (company settings in DB override these) |
| Tooling only | `RENDER_API_KEY` (or `~/.render/cli.yaml` from `render login`), `PRODUCTION_TEST_USERNAME/PASSWORD`, `APP_BASE_URL`/`BASE_URL` overrides for verify scripts, `TEST_ARTIFACT_DIR` |

`/health` reports `git_commit` (from `RENDER_GIT_COMMIT`), `database` (`postgres` / `sqlite`), `database_url_set`, `booking_crew_hours_table`/`_rows`, `database_schema_error` and `stripe`. If it says `"database":"sqlite"` in prod, run `python scripts/render_link_database.py --deploy`.

Never commit `.env`, `credentials/*.json`, `bookings.db` or DB dumps (all gitignored).

---

## 12. Local development

```bash
cd ~/Projects/japanese-removals-bookings
source .venv/bin/activate          # or call .venv/bin/python directly
pip install -r requirements.txt
python3 app.py                     # http://127.0.0.1:5001 (port 5000 = macOS AirPlay)
python create_staff.py admin 'Password123'   # local office login
```

---

## 13. Tests

There is no pytest. Each `scripts/test_*.py` is a standalone script using `app.test_client()`. It prints `PASS:` / `FAIL:` and `N/M passed`, and exits non-zero on failure. No server is needed.

- Tests use the **real local `bookings.db`**; there is no temp DB. They add uniquely named rows and never wipe. Thousands of leftover test bookings exist locally, which is fine.
- `.env` enables Google Calendar / Gmail locally, so a test that saves bookings could hit the **real Google API**. **Always run tests with integrations disabled:**

```bash
cd ~/Projects/japanese-removals-bookings
unset DATABASE_URL
export GOOGLE_CALENDAR_ENABLED=false GMAIL_INBOX_ENABLED=false XERO_ENABLED=false \
       SMS_ENABLED=false STRIPE_ENABLED=false EMAIL_ENABLED=false
.venv/bin/python scripts/test_staff_portal_e2e.py
```

### Core regression suite (run what touches your change, then the rest before pushing)

| Area | Script |
|---|---|
| Staff Portal, paid hours, booking_crew_hours, Star Points, calendar | `test_staff_portal_e2e.py` (66 tests, the most important one) |
| Call out minutes / fee / paid hours | `test_callout_minutes_e2e.py` |
| Finish time = source of truth | `test_finish_time_source_of_truth_e2e.py`, `test_edit_finish_time_e2e.py`, `test_invoice_duration_from_times_e2e.py` |
| Invoice | `test_edit_booking_save_unifies_invoice_e2e.py`, `test_invoice_numbering_e2e.py`, `test_invoice_edit_e2e.py`, `test_invoice_negative_adjustments_e2e.py`, `test_invoice_pdf_layout_e2e.py` |
| Double booking | `test_truck_double_booking_e2e.py`, `test_phase14_double_booking_e2e.py` |
| Calendar / weekly | `test_calendar_data_e2e.py`, `test_weekly_schedule_e2e.py`, `test_daily_jobs_e2e.py` |
| Dashboard sections | `test_dashboard_payment_status_divider_e2e.py`, `test_dashboard_today_upcoming_divider_e2e.py`, `test_dashboard_wg_unpaid_e2e.py` (WG name matching, Regular vs WG split, no duplicates, oldest first, counts and totals, Paid leaves WG) |
| Staff weekly PDF | `test_staff_weekly_pdf_e2e.py` (screen ↔ PDF job/paid match, sort, Sunday, overrides, break, 45-min call out, Extra Pay, no duplicates, long text/Japanese inside columns, year crossing, busy week) |
| Postgres boot | `test_postgres_init_ddl_e2e.py`, `test_postgres_invoice_sequence_init.py` |
| Furniture lists | `test_furniture_list_e2e.py` (m³ maths and no double count, volume sources/medians, legacy lists keep their m³, admin m³ change applies to new items only, admin page permissions/validation, save → re-edit → submit, photos and EXIF strip, cross-list isolation, office/staff access, website return link, auto-link from notes, rate limit). Passes on SQLite and Postgres |
| Misc | `test_booking_cancel_e2e.py`, `test_mover_pricing_e2e.py`, `test_payment_sets_completed_e2e.py`, `test_navigation_e2e.py`, `test_search_invoice_number_e2e.py`, `test_job_note_display_e2e.py` |

**Known test-baseline issues (as of 2026-10-17, verified identical on clean worktrees of `origin/main` = production code):**
- **Stale since 32b3313 (callout_minutes):** these tests post `callout_fee` without `callout_minutes`. The server now derives the fee from minutes, and missing minutes count as 0, so the expected totals are off:
  - `test_invoice_edit_e2e.py` (5/8), `test_invoice_duration_from_times_e2e.py` (10/12), `test_edit_booking_save_unifies_invoice_e2e.py` (4/5), `test_invoice_negative_adjustments_e2e.py` (8/9), `test_booking_details_e2e.py` (9/11), `test_edit_finish_time_e2e.py` (1–3/4).
  - Fix by adding `callout_minutes` to their form fixtures (pending Yasu's OK). Real forms always send `callout_minutes`.
- `test_daily_jobs_e2e.py` passes 8/8 on a fresh DB but fails 2 tests against the cluttered local `bookings.db`.
- Tip: for a clean baseline, run tests in a throwaway worktree (`git worktree add /tmp/x HEAD`). It gets its own fresh `bookings.db`; call the main repo's `.venv/bin/python scripts/...` from inside it.
- `test_phase14_double_booking_e2e.py` Test D fails locally. Leftover bookings from earlier runs sit on the same relative date (today+63, no truck), so they conflict. It is a test-isolation problem, not a code bug, and fails on clean `main` too.
- `test_staff_portal_e2e.py` (66 tests): on a clean DB, 2 tests fail on HEAD both before and after the 2026-10-18 PDF change. `test_this_week_tab_includes_later_week_jobs` fails on Sundays (its "later this week" date falls back to today), and `test_week_calendar_matches_month_for_same_week_all_staff` is date-dependent. It was flaky once before (63/65, then 65/65).
- `test_truck_double_booking_e2e.py` Case D fails only on the cluttered local DB (5/5 clean).
- `test_weekly_schedule_e2e.py` and `test_booking_details_pricing_visual.py` need Playwright for screenshots. Artifacts go to `test_results/artifacts/` (override with `TEST_ARTIFACT_DIR`).

**Tests that hit production or real APIs (run only when asked):**
- `verify_production_*.py`, `test_production_readiness.py --production`, `deploy_production_and_verify.py`, `diagnose_production_environment.py`, `smoke_live_booking.py`.
- **Write to production data — do not run without explicit permission:**
  - `test_production_booking_cancel.py` creates and cancels a booking.
  - `verify_production_finish_time.py` creates and edits a booking.
  - `verify_production_star_points.py` **resets a real crew member's Star Points to 0** and then increments them.
- Read-only and safe: `verify_production_daily_jobs.py`, `verify_production_crew_times.py`, `verify_production_calendar_sort.py`, `verify_production_staff_calendar.py`, `verify_production_xero_payment_sync.py`, `verify_production_paste.py` (Analyse only, no save), `deploy_production_and_verify.py`.
- Xero / Stripe / Google scripts: `test_xero_*`, `test_phase7/8_*`, `verify_stripe_integration.py`, `verify_google_calendar.py`, `resync_xero_invoices.py`, and similar.
- Results land in `test_results/<phase>/*.json`; these are committed, and re-runs dirty them.

---

## 14. Git / GitHub push

- Remote `origin` = `https://github.com/YasuJR/japanese-removals-bookings.git`. Auth: `gh` is logged in as `YasuJR`.
  - If plain `git push` fails with `could not read Username … Device not configured` (the keychain is not reachable from the Claude Code shell), push with:
    `git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin main`
- Work directly on `main` (the historical workflow), or on a branch with a PR via `gh pr create` if Yasu prefers review.
- Commit style: imperative one-line summary ending with a period (e.g. `Fix double booking override to require same truck, not just time overlap.`), a short body explaining why, and the Claude `Co-Authored-By` trailer.
- Before pushing: run the relevant tests above, `git status` (don't commit `test_results` noise unless intended), then `git push origin main`.

## 15. Render deploy

1. `git push origin main` → Render auto-deploys the web service and cron (Blueprint `render.yaml`, branch `main`).
2. Verify: `python scripts/deploy_production_and_verify.py`. It polls `/health` until `git_commit` matches `origin/main` (up to 15 min).
   - To force a deploy: `TRIGGER_RENDER_DEPLOY=1 python scripts/deploy_production_and_verify.py`, which calls `render_link_database.py --deploy --clear-cache`. This needs `RENDER_API_KEY` or `render login`.
3. Optional prod checks: `verify_production_*.py` for the area you changed.
4. **Rollback:** Render Dashboard → Deploys → "Rollback to this deploy". This does **not** revert the DB (`docs/ROLLBACK.md`).
- Render CLI: a vendored binary is at `.bin/cli_v2.17.0` (gitignored); `render` is not on PATH.
  - Scripts read the API key from `RENDER_API_KEY` or `~/.render/cli.yaml`. That CLI token **expires** (check `expires_at`); expired means Render API calls return 401.
  - Refresh with `.bin/cli_v2.17.0 login`, a browser device-code flow that Yasu approves. Run it in the background and give Yasu the printed URL and code.
- Production office credentials for verify scripts come from the Render env vars `STAFF_USERNAME` / `STAFF_PASSWORD` via the API. Never print them.
- Xero credentials in prod live in the DB (`integration_settings`), not in env vars. `verify_production_xero_payment_sync.py` reporting "env vars missing" is expected.
- Production DB access: Render Dashboard → `japanese-removals-db` → connection string / Shell. Back up with `pg_dump "$DATABASE_URL" -Fc -f backup-YYYYMMDD.dump` before risky data changes.

---

## 16. Major past bugs (don't reintroduce)

| Commit | Bug → fix |
|---|---|
| 216914a | Render boot crash: `_ensure_booking_crew_hours_table` ran SQLite `AUTOINCREMENT` DDL on Postgres → the DDL now branches by backend |
| d339eaf | Postgres boot abort: an `invoice_sequence` `ON CONFLICT` insert went through RETURNING/lastval → those are skipped for that table |
| 963c8fa / d6e7866 / 4466a1c | `create_booking` 500 on Postgres: RETURNING injection missed multiline INSERTs → `lastval()` fallback |
| 2a5b1cf, d0e77a1, (2026-10-17) | Postgres returns `date`/`datetime` objects → normalize before `.strip()` / compare. The 2026-10-17 fix covers `database.list_by_move_dates_grouped` |
| 1af9e1c | Override was demanded for any time overlap → only the **same truck** requires an override |
| 8cbc91e | Calendar order wrong (sorted on display strings) → shared `job_start_sort_key` |
| e9d87fd | Staff week calendar missed jobs (stale `day` param) → bounds come only from the week offset |
| 090fcce / b73416d | Week columns clipped or horizontal scroll → layout fit |
| 50ee72d / d7c04ed | One crew member's time edit changed everyone's → `booking_crew_hours` plus `get_staff_job_times` |
| a9c29b4 | Paid hours ignored breaks → Paid = Actual − Break + Call out (+ Extra Pay since ac82c0b) |
| ef0a456 | Invoiced-but-Confirmed jobs had 0 paid hours → invoice-issued fallback |
| 18c37ae / 5b46f43 | Weekly worked showed 0hr → use start/finish or duration |
| d1cbc00 / 279b4e7 | Finish time overwritten by a stale duration, `HH:MM:SS` not parsed → Finish is the source of truth |
| 81138a7 / 1e4a2f8 | Redeploy reset the invoice counter, giving a duplicate INV1 → max(existing)+1 and a startup INV1 reassignment |
| a648f6a / e1f136a | Dashboard missing invoice numbers → `INV{id}` fallback |
| 9f82585 | Cancel 500 (expired Google creds, re-sync on cancel) → cancel deletes the calendar event |
| a7f9210 / 80e909c / c7f5e14 | Typo'd stored Stripe key overrode env → validate the key, prefer env |
| c7fbbab / 970a790 | Invoice sent to the company's own phone/email → rejected as recipient |
| 43565b3 / f84beb6 | Paste-enquiry parser leaked fields into notes; HTML5 validation blocked Analyse |
| 30b2f97 | Missing Jinja filter `margin_badge` in prod → register filters in `app.py` |
| (2026-10-18) | Staff weekly PDF drifted from the screen. The All Staff PDF used the office pipeline (`build_weekly_schedule` → `db.list_by_date` exact-string match per day, default 08:00 for TBC, no break deduction). The Individual PDF dropped Phone / Actual / Break / Call Out / Extra Pay / Paid. One Paragraph per day overflowed into the next column on long tokens, Japanese rendered as tofu, and `KeepInFrame(shrink)` made busy weeks unreadable → the PDF is now built from `build_staff_portal(range=week)` with a new card renderer |

## 17. Known quirks / open issues

- With `STAFF_PORTAL_OPEN=1` (the default), anyone with the URL can view every schedule and star history.
- Staff Portal "All Staff" owner edit recomputes `callout_fee` (losing overrides) and forces GST 0 → 1 (`staff_portal_owner.save_owner_job_edit`).
- An invoiced job whose status is still Confirmed shows a "CONFIRMED" badge and counts as upcoming in the portal, even though its hours are counted.
- In `edit_booking`, a rejected double-booking edit still persists the field changes (see §8).
- **Xero payment sync cron is not deployed.** The Render service list (2026-10-17) only shows the web service `japanese-removals-bookings`. The blueprint cron `xero-payment-sync` was never created, so bank-transfer payments in Xero are not auto-marked Paid; there is a manual sync button on `/dashboard`. Creating it (Render Dashboard → New → Cron Job, or Blueprint sync) costs money, so it is Yasu's decision.
- Cron scripts for Gmail, SMS, review and payment reminders exist (`scripts/run_*.py`) but are **not** scheduled in `render.yaml`.
- `CRON_SECRET` is defined but unused.

## 18. Tooling notes

- This project was developed with Cursor until 2026-10. The ~113 old commits with a `Co-authored-by: Cursor` trailer are history only. There are no Cursor config files, rules or MCP dependencies in the repo.
- **Watch for Cursor background agents still pushing to `main`.** ac82c0b (2026-10-14, author `Cursor Agent`) was pushed straight to `main` and auto-deployed. Always `git fetch` and check `git log main..origin/main` before starting work. If Yasu wants Claude Code only, disconnect the Cursor GitHub integration (github.com → Settings → Applications → Cursor, and Cursor Dashboard → Background Agents / Integrations).
- Use Claude Code for everything: Bash / git / `gh` / Render API scripts.
- Browser checks: use Playwright (as the visual test scripts do) or Claude in Chrome against `http://127.0.0.1:5001`.
- Keep this file updated. When you fix a notable bug or change a rule (pay, invoice, conflict, schema), update the relevant section and §16 in the same commit.

## 19. TODO (open tasks)

Remove an item from this list in the same commit that finishes it.

- [ ] **Xero payment sync cron.** `render.yaml` defines cron `xero-payment-sync` (every 15 min, `python scripts/run_xero_payment_sync.py`), but it was never created on Render (checked 2026-10-17). The last automatic sync was 24 Aug 2026.
  - To do: create it (Render Dashboard → New → Cron Job, or Blueprint sync). It needs the same env vars as the web service: `DATABASE_URL`, the Xero settings, and `SECRET_KEY`. Xero OAuth credentials are stored in the DB.
  - Then confirm the first run marks Xero-paid bookings as Paid.
  - Costs money on Render, so get Yasu's OK first. Until then, use the manual sync button on `/dashboard`. See §1 and §17.
- [ ] **Furniture enquiry PDF secret.** `FURNITURE_ENQUIRY_KEY` was set on Render on 2026-10-27, from the local file `~/.config/japanese-removals/furniture-enquiry-key` (600). Still to do: add the same value to WordPress, using the ready snippet `~/.config/japanese-removals/wp-config-furniture-snippet.txt`. That means `define( 'JR_FURNITURE_KEY', '…' );` plus `define( 'JR_FURNITURE_API', 'https://japanese-removals-bookings.onrender.com' );` in WordPress `wp-config.php`. Until WordPress has the key (and runs theme v1.5.15+), the site does not fetch the PDF: the enquiry email notes the List ID instead of attaching it. With a wrong key the API answers 401. The site FTPS login stored in the Keychain (`jr-ftps`) was rejected on 2026-10-27 (rotated), so WordPress changes are manual.
- [ ] **Stale call-out tests.** Six tests post `callout_fee` without `callout_minutes` (pre-32b3313 style), so they fail on production code: `test_invoice_edit_e2e.py`, `test_invoice_duration_from_times_e2e.py`, `test_edit_booking_save_unifies_invoice_e2e.py`, `test_invoice_negative_adjustments_e2e.py`, `test_booking_details_e2e.py`, `test_edit_finish_time_e2e.py`.
  - To do: add `callout_minutes` to their form fixtures, update the expected totals (fee = rate × minutes / 60), and confirm they pass in a clean worktree. Details in §13.
