# Kairix — Embedding Lag (New Content Not Searchable)

**Symptom:** Vault content added or modified more than 30 minutes ago is not appearing in `kairix search` results. Expected: new/changed chunks appear within ~15 minutes (one embedding cycle).

---

## Quick Diagnosis

```bash
# Check last embedding cron run
tail -30 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log

# Check cron is scheduled
crontab -u kairix -l | grep embed
# Expected: 15 * * * * /opt/kairix/cron/kairix-embed.sh

# Check if cron script is deployed
ls -la /opt/kairix/cron/kairix-embed.sh

# Check secrets are present (required for Azure OpenAI embedding calls)
ls -la /run/secrets/kairix.env
```

---

## Root Cause A — Cron Not Running

```bash
# Verify cron entry exists
crontab -u kairix -l | grep kairix-embed
# If missing: cron was not deployed or was cleared

# Redeploy cron scripts (copy from repo, chmod +x)
sudo cp scripts/cron/kairix-embed.sh /opt/kairix/cron/
sudo chmod +x /opt/kairix/cron/kairix-embed.sh

# Add crontab entry
sudo crontab -u kairix -e
# Add: 15 * * * * /opt/kairix/cron/kairix-embed.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log 2>&1

# Verify cron entry
crontab -u kairix -l | grep kairix-embed
```

---

## Root Cause B — Embedding Script Failing

```bash
# Run embedding manually to see errors
sudo -u kairix /opt/kairix/cron/kairix-embed.sh

# Common errors:
# - "Authorization failed" → secrets missing (see runbook-secrets-fetch-failure)
# - "Rate limit exceeded" → Azure OpenAI quota hit (see Fix B below)
# - "No changed chunks" → vault not updated (check vault sync)
```

---

## Fix B — Azure OpenAI Rate Limit

If embedding is failing due to rate limits (`429 Too Many Requests`):

```bash
# Check Azure OpenAI quota usage in your Azure portal
# Navigate: Azure OpenAI → your subscription → Deployments → text-embedding-3-large

# Temporary: reduce embedding frequency from 15min to 30min
# Edit the crontab entry: change "15 * * * *" to "*/30 * * * *"
sudo crontab -u kairix -e

# Verify embedding succeeds at lower rate
sudo -u kairix /opt/kairix/cron/kairix-embed.sh
```

---

## Root Cause C — Vault Sync Not Running

If vault content isn't syncing to the host, nothing new will be indexed.

```bash
# Check vault sync service (if using a sync daemon)
systemctl status vault-sync --no-pager | head -10

# Manual vault check — look for recently modified files
find "${KAIRIX_VAULT_ROOT}" -name "*.md" -mmin -60 2>/dev/null | head -20
```

---

## Fix — Trigger Embedding Manually

```bash
# Run embedding cron immediately (doesn't wait for schedule)
sudo -u kairix /opt/kairix/cron/kairix-embed.sh

# Watch output for errors
# Healthy output includes: "embedded N chunks", no auth errors

# Then test search
kairix search "recently added topic"
```

---

## Verify Fix

```bash
# Confirm last embedding completed recently
tail -5 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
# Should show a recent successful run

# Test with known-new content
kairix search "title of recently added document"
# Should appear in results
```

---

## Related

- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — if embedding fails with auth errors
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — quick-reference for all failures
