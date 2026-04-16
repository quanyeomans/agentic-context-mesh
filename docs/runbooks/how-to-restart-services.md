# How To: Restart Kairix Services (Safe Sequence)

**Purpose:** Restart one or more kairix services safely, in the correct order, without losing in-flight embedding or entity seed work.

**When to use:** After a config change, after a deploy, after a failed service, or after VM reboot to verify services came up correctly.

---

## Before You Start

```bash
# Check what is currently running
systemctl status kairix-fetch-secrets --no-pager

# Check if embedding cron is mid-run
tail -5 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
# If "embedding in progress..." — wait for completion before restarting

# Check if entity seed is running
tail -5 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log
# If "seeding in progress..." — wait for completion (2-5 min)
```

---

## Service Restart Order

Restart in this order. Each service depends on the previous.

### Step 1 — Secrets (required before all other services)

```bash
# Restart secrets fetch (recreates /run/secrets/kairix.env)
sudo systemctl restart kairix-fetch-secrets

# Wait for completion (oneshot service)
sleep 10

# Verify secrets file exists
ls -la /run/secrets/kairix.env
# Expected: -rw-r----- 1 root kairix ... /run/secrets/kairix.env
```

If this fails → [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md)

### Step 2 — Vault Sync (required before embedding, if applicable)

```bash
# Check sync is running (if your deployment uses a vault sync daemon)
systemctl status vault-sync --no-pager | head -5

# Restart only if it was stopped or errored
sudo systemctl restart vault-sync

# Verify
systemctl is-active vault-sync
# Expected: active
```

### Step 3 — Verify Kairix Binary Symlink

```bash
# Confirm symlink points to wrapper (not raw binary)
ls -la /usr/local/bin/kairix
# Expected: /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh

# If wrong → fix immediately before continuing
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

# If your integration tool adds a second kairix symlink, check that too
# (see how-to-fix-binary-symlink for the full procedure)
```

If symlink issue persists → [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md)

### Step 4 — Trigger Embedding (optional, after config change)

```bash
# Trigger embedding manually (does not wait for cron schedule)
sudo -u kairix /opt/kairix/cron/kairix-embed.sh

# Watch output
tail -f ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
# Healthy: "embedded N chunks", exits 0
# Wait for completion before testing search
```

### Step 5 — Verify End-to-End

```bash
# Full health check
kairix onboard check

# Search test (confirms credentials injected + vector working)
kairix search "test"
# Must show: BM25=N, vec=M (vec > 0)
```

---

## After VM Reboot

On a fresh boot, run through Steps 1–5 in order. The secrets service runs automatically at boot (`kairix-fetch-secrets.service` with `After=network-online.target`) but verify it succeeded before trusting any kairix operations.

```bash
# Quick post-boot verify
systemctl status kairix-fetch-secrets --no-pager
ls -la /run/secrets/kairix.env
kairix search "test"
```

---

## Related

- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — if Step 1 fails
- [runbook-vector-search-failure](runbook-vector-search-failure.md) — if vec=0 after Step 5
- [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) — if symlink is wrong at Step 3
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — all-in-one quick reference
