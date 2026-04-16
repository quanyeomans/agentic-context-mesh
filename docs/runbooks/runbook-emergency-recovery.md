# Kairix — Emergency Recovery Quick Reference

**Use this first.** If you don't know what's broken, run the health check. Then use the symptom table.

---

## Step 0 — Health Check

```bash
# Full onboard check (shows what's failing)
kairix onboard check

# Quick search test (must show vec=N, not vec_failed=True)
kairix search "test"

# Secrets present?
ls -la /run/secrets/kairix.env

# Fetch service status
systemctl status kairix-fetch-secrets --no-pager | head -10

# Embedding cron last run
tail -5 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
```

---

## Symptom → Fix Table

| Symptom | Fix Command | Full Runbook |
|---|---|---|
| `vec=0, vec_failed=True` | Fix symlink → see below | [runbook-vector-search-failure](runbook-vector-search-failure.md) |
| `Neo4j auth failed` | `sudo systemctl restart kairix-fetch-secrets && sleep 5 && systemctl status kairix-fetch-secrets` | [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) |
| `/run/secrets/kairix.env` missing | `sudo systemctl start kairix-fetch-secrets` | [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) |
| New vault content not searchable | `tail -20 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log` | [runbook-embedding-lag](runbook-embedding-lag.md) |
| `kairix entity` returns stale data | `tail -20 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log` | [runbook-entity-graph-stale](runbook-entity-graph-stale.md) |
| NDCG dropped | `kairix benchmark run --suite suites/your-suite.yaml` | [runbook-benchmark-regression](runbook-benchmark-regression.md) |

---

## All Service Restart Commands

```bash
# Secrets fetch (must run before kairix services)
sudo systemctl restart kairix-fetch-secrets

# Verify secrets populated (wait ~10s after restart)
ls -la /run/secrets/kairix.env

# Kairix embedding cron — trigger manually
sudo -u kairix /opt/kairix/cron/kairix-embed.sh

# Entity graph seed — trigger manually
sudo -u kairix /opt/kairix/cron/entity-relation-seed.sh
```

---

## All Log Commands

```bash
# Embedding cron
tail -50 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log

# Entity seed
tail -50 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log

# Secrets fetch service
journalctl -u kairix-fetch-secrets -n 30 --no-pager

# Nightly maintenance
tail -50 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/nightly-maintenance.jsonl
```

---

## Fix: Binary Symlink (vector search broken)

```bash
# Check what the symlink points to (should be wrapper, not raw binary)
ls -la /usr/local/bin/kairix

# Fix: point to wrapper (run as root)
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

# If your integration tool adds a second kairix symlink, fix that too — see how-to-fix-binary-symlink

# Verify vector search works
kairix search "test"
# Expected: vec=N (not vec_failed=True)
```

Full procedure: [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md)

---

## Fix: Secrets Not Populated

```bash
# Restart fetch service
sudo systemctl restart kairix-fetch-secrets

# Wait for it to complete (it's a oneshot service)
sleep 10

# Check it succeeded
systemctl status kairix-fetch-secrets --no-pager
ls -la /run/secrets/kairix.env   # should exist, 640 root:kairix

# If still failing, check RBAC and VM/host identity
az account show  # ensure correct subscription (Azure example)
az keyvault secret show --vault-name "${KAIRIX_KV_NAME}" --name azure-openai-api-key --query value --output tsv
```

Full procedure: [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md)

---

## Related

- [INDEX](INDEX.md) — full runbook registry
- [how-to-restart-services](how-to-restart-services.md) — safe restart order
