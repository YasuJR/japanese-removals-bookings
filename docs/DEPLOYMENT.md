# Production Deployment Guide — Japanese Removals Bookings

Deploy to [Render](https://render.com) with PostgreSQL, HTTPS, and scheduled background jobs.

## Production URL

| Environment | URL |
|-------------|-----|
| Render default | `https://japanese-removals-bookings.onrender.com` |
| Custom domain (recommended) | `https://bookings.yourdomain.com.au` |

Set `APP_BASE_URL` and `PRODUCTION_DOMAIN` to your live HTTPS URL before connecting OAuth integrations.

---

## 1. Prerequisites

- Render account
- Git repository pushed to GitHub/GitLab
- Domain DNS access (optional custom domain)
- OAuth apps updated for production callbacks (Google, Xero, Stripe, Twilio)

---

## 2. Create Render services

### Option A — Blueprint (recommended)

1. Push this repo to GitHub.
2. Render Dashboard → **New** → **Blueprint** → connect repo.
3. Render reads `render.yaml` and creates:
   - Web service (`japanese-removals-bookings`)
   - PostgreSQL database (`japanese-removals-db`)

### Option B — Manual

1. Create **PostgreSQL** database on Render (Singapore region recommended for Perth).
2. Create **Web Service**:
   - Runtime: Python 3
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - Link `DATABASE_URL` from the database.

---

## 3. Environment variables

Copy `.env.production.example` into Render **Environment** (or an Environment Group).

**Required for production:**

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Flask session signing (32+ chars, random) |
| `DATABASE_URL` | Auto-linked from Render PostgreSQL |
| `APP_BASE_URL` | Public HTTPS URL |
| `STAFF_USERNAME` / `STAFF_PASSWORD` | First admin user (created on first boot) |

**Integration secrets (env only — do not commit):**

| Service | Variables |
|---------|-----------|
| Google | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_JSON` |
| Xero | `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `XERO_TENANT_ID`, `XERO_TOKEN_JSON` |
| Stripe | `STRIPE_PUBLISHABLE_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` |
| Email | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` |

On deploy, `production_bootstrap.py` writes `GOOGLE_TOKEN_JSON` and `XERO_TOKEN_JSON` to the credentials folder from env vars (Render disks are ephemeral).

---

## 4. HTTPS and custom domain

Render provides free HTTPS on `*.onrender.com`.

For a custom domain:

1. Render → Web Service → **Settings** → **Custom Domains** → add domain.
2. Add DNS CNAME to Render’s target.
3. Set `APP_BASE_URL=https://bookings.yourdomain.com.au`
4. Set `PRODUCTION_DOMAIN=bookings.yourdomain.com.au`

The app uses `ProxyFix` so Flask sees HTTPS behind Render’s proxy.

---

## 5. OAuth callback URLs

Update these **exact** URLs in each provider console after `APP_BASE_URL` is final:

| Provider | Callback / webhook |
|----------|-------------------|
| Google | `{APP_BASE_URL}/integrations/google/callback` |
| Xero | `{APP_BASE_URL}/integrations/xero/callback` |
| Stripe webhook | `{APP_BASE_URL}/integrations/stripe/webhook` |
| Twilio status | `{APP_BASE_URL}/integrations/twilio/status` |
| Twilio inbound SMS | `{APP_BASE_URL}/integrations/twilio/inbound` |

Also set env vars `GOOGLE_REDIRECT_URI` and `XERO_REDIRECT_URI` to match.

**After first OAuth in production:** copy token JSON from Settings flow into `GOOGLE_TOKEN_JSON` / `XERO_TOKEN_JSON` env vars so tokens survive redeploys.

---

## 6. Background jobs (Render Cron)

Create **Cron Jobs** in Render (or extend `render.yaml`) sharing the web service environment group:

| Job | Schedule | Command |
|-----|----------|---------|
| Gmail inbox | `*/10 * * * *` | `python scripts/run_gmail_inbox_monitor.py` |
| SMS automation | `0 * * * *` | `python scripts/run_sms_automation.py` |
| Review requests | `0 9 * * *` | `python scripts/run_review_automation.py` |
| Payment reminders | `0 8 * * *` | `python scripts/run_payment_reminder_automation.py` |

Cron jobs need the same `DATABASE_URL` and integration env vars as the web service.

---

## 7. Migrate data from local SQLite (optional)

```bash
# Export local SQLite (from project root)
sqlite3 bookings.db .dump > backup.sql

# Or use the migration helper once PostgreSQL is live:
python scripts/migrate_sqlite_to_postgres.py
```

For a fresh production start, skip migration — `wsgi.py` runs `init_db()` on boot.

---

## 8. Deploy checklist

- [ ] `render.yaml` pushed; Blueprint deployed
- [ ] PostgreSQL linked; `DATABASE_URL` set
- [ ] `SECRET_KEY` generated and set
- [ ] `APP_BASE_URL` is HTTPS
- [ ] Staff login created (`STAFF_USERNAME` / `STAFF_PASSWORD`)
- [ ] Google OAuth connected; `GOOGLE_TOKEN_JSON` saved to env
- [ ] Xero OAuth connected; `XERO_TOKEN_JSON` saved to env
- [ ] Stripe webhook registered (live mode)
- [ ] Twilio webhooks configured
- [ ] SMTP credentials set; test email sent
- [ ] Cron jobs created and linked to env group
- [ ] `/health` returns 200
- [ ] Login → CEO dashboard loads
- [ ] Public `/quote` form works
- [ ] Run `python scripts/test_production_readiness.py --production`

---

## 9. Verify

```bash
curl https://your-app.onrender.com/health
python scripts/test_production_readiness.py --production
```

See [BACKUP.md](BACKUP.md) and [ROLLBACK.md](ROLLBACK.md) for operations.
