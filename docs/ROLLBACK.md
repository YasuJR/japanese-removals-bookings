# Rollback Guide — Japanese Removals Bookings

Use this when a production deploy causes errors or data issues.

## Quick rollback (Render)

1. **Render Dashboard** → Web Service → **Events** / **Deploys**.
2. Select the last known-good deploy → **Rollback to this deploy**.
3. Confirm the service returns `200` on `/health`.

Render rollbacks restore the previous container image only — **database changes are not reverted**.

---

## Database rollback

PostgreSQL changes persist across web rollbacks.

### Option A — Render point-in-time recovery (paid plans)

1. Render Dashboard → PostgreSQL → **Backups**.
2. Restore to a snapshot before the bad deploy.
3. Update `DATABASE_URL` if a new instance is created.

### Option B — Manual restore from backup

1. Stop cron jobs temporarily (prevent automation during restore).
2. Restore from latest `pg_dump` (see [BACKUP.md](BACKUP.md)):

```bash
pg_restore --clean --if-exists --no-owner \
  --dbname="$DATABASE_URL" backup-YYYY-MM-DD.dump
```

3. Restart web service and cron jobs.
4. Verify bookings count and recent automation logs.

---

## OAuth token rollback

If OAuth tokens were overwritten:

1. Restore previous `GOOGLE_TOKEN_JSON` / `XERO_TOKEN_JSON` env var values from your secrets backup.
2. Redeploy or restart the web service.
3. Re-run **Connect Google** / **Connect Xero** in Settings if tokens are invalid.

---

## Configuration rollback

1. Export current Render env vars (screenshot or copy).
2. Restore previous env var set from backup.
3. **Manual Deploy** → deploy latest commit with restored env.

---

## When NOT to rollback

- Failed OAuth reconnect only → fix callback URLs, don’t rollback code.
- Single booking data error → fix in admin UI, don’t restore whole DB.

---

## Post-rollback verification

```bash
curl https://your-app.onrender.com/health
python scripts/test_production_readiness.py --production
```

- [ ] Login works
- [ ] Bookings list loads
- [ ] Integrations show connected (Google, Xero, Stripe, Twilio)
- [ ] Cron jobs re-enabled
