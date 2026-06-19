# Backup Strategy — Japanese Removals Bookings

## What to back up

| Asset | Method | Frequency |
|-------|--------|-----------|
| PostgreSQL database | `pg_dump` | Daily |
| Environment variables | Render export / password manager | After every change |
| OAuth tokens | `GOOGLE_TOKEN_JSON`, `XERO_TOKEN_JSON` in secrets store | After OAuth reconnect |
| Company settings JSON | Export from Settings or DB | Weekly |

SQLite (`bookings.db`) is **local dev only** — production uses PostgreSQL on Render.

---

## Render managed backups

Enable on the PostgreSQL instance:

- **Starter/Basic**: daily automatic backups (check Render plan details).
- Retain backups per Render policy; test restore quarterly.

---

## Manual PostgreSQL backup

From a machine with network access to the database:

```bash
# Custom format (recommended for restore)
pg_dump "$DATABASE_URL" -Fc -f "backup-$(date +%Y%m%d).dump"

# Plain SQL
pg_dump "$DATABASE_URL" > "backup-$(date +%Y%m%d).sql"
```

Store backups encrypted (1Password, AWS S3 with encryption, etc.).  
**Never commit dumps to git.**

---

## Restore test (quarterly)

1. Create a temporary Render PostgreSQL instance.
2. Restore latest dump:

```bash
pg_restore --no-owner --dbname="$TEST_DATABASE_URL" backup-YYYYMMDD.dump
```

3. Point a staging web service at the test DB; verify booking count and login.

---

## Environment secrets backup

Document in a secure vault:

- `SECRET_KEY`
- All integration keys (Google, Xero, Stripe, Twilio, SMTP)
- `STAFF_PASSWORD` (or rotate after backup)

Render: Settings → Environment → copy values after initial setup.

---

## Local development backup

```bash
cp bookings.db "bookings-backup-$(date +%Y%m%d).db"
```

---

## Disaster recovery RTO/RPO (targets)

| Metric | Target |
|--------|--------|
| RPO (max data loss) | 24 hours (daily backup) |
| RTO (time to restore) | 2 hours (manual restore + verification) |

Improve RPO by increasing backup frequency or Render PITR on higher plans.
