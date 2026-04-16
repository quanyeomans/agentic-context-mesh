# Kairix — Entity Graph Stale

**Symptom:** `kairix entity <id>` returns outdated information, relationships are missing, or entities that exist in the vault don't appear in entity queries. Expected: entity data refreshes daily (entity-relation-seed runs at 03:00 local / 17:00 UTC by default).

---

## Quick Diagnosis

```bash
# Check last entity seed run
tail -30 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log

# Check cron is scheduled
crontab -u kairix -l | grep entity
# Expected: 0 17 * * * /opt/kairix/cron/entity-relation-seed.sh

# Query entity to confirm staleness
kairix entity "<entity_id>"
# Compare to what you know is in the vault

# Check graph population (row counts)
kairix onboard check
# Entity-related tests show counts
```

---

## Root Cause A — Entity Seed Cron Not Running

```bash
# Verify cron entry
crontab -u kairix -l | grep entity-relation

# If missing: redeploy the cron script and add crontab entry
sudo cp scripts/cron/entity-relation-seed.sh /opt/kairix/cron/
sudo chmod +x /opt/kairix/cron/entity-relation-seed.sh
sudo crontab -u kairix -e
# Add: 0 17 * * * /opt/kairix/cron/entity-relation-seed.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log 2>&1
```

---

## Root Cause B — Seed Script Failing

```bash
# Run seed manually
sudo -u kairix /opt/kairix/cron/entity-relation-seed.sh

# Check for errors:
# - Neo4j auth error → runbook-secrets-fetch-failure
# - Vault crawler error → vault sync issue
# - Wikilink extraction failed → check vault structure

# Check Neo4j connectivity
systemctl status neo4j --no-pager 2>/dev/null || echo "neo4j not managed by systemd — may be embedded"
```

---

## Root Cause C — Vault Wikilinks Not Populated

Entity relationships depend on `[[Wikilink]]` syntax in vault files. If vault files were added without wikilinks, relationships won't be extracted.

```bash
# Check for wikilinks in a known file
grep -c '\[\[' "${KAIRIX_VAULT_ROOT}/path/to/a-known-file.md"
# Should return > 0

# If vault files are missing wikilinks: content authoring issue, not a kairix bug
```

---

## Fix — Trigger Entity Seed Manually

```bash
# Run the full entity seed now
sudo -u kairix /opt/kairix/cron/entity-relation-seed.sh

# Watch for completion (may take 2-5 minutes on large vault)
# Healthy output: "seeded N entities", "N relationships extracted"

# Verify entity is now current
kairix entity "<entity_id>"
```

---

## Fix — Full Graph Rebuild

If the graph is corrupted or significantly out of sync:

```bash
# Full vault crawl + entity seed
kairix vault crawl --vault-root "${KAIRIX_VAULT_ROOT}"
sudo -u kairix /opt/kairix/cron/entity-relation-seed.sh

# Verify graph health
kairix vault health
```

---

## Verify Fix

```bash
# Check entity seed completed successfully
tail -10 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log
# Should show: "completed", count of entities/relationships

# Test entity retrieval
kairix entity "<known entity id>"

# Check via search (entity-enriched results)
kairix search "a known entity name"
# Entity context should appear in results
```

---

## Related

- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — if Neo4j auth fails
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — quick-reference
