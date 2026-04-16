# How To: Fix the Kairix Binary Symlink

**Purpose:** Restore the correct kairix symlink(s) to point to `kairix-wrapper.sh` after they have been overwritten to point directly at the raw Python binary. This is the fix for `vec=0, vec_failed=True` in search results.

**Why this happens:** A kairix reinstall or pip upgrade can overwrite symlinks to point directly at `/opt/kairix/.venv/bin/kairix`. The wrapper (`kairix-wrapper.sh`) is responsible for injecting Azure OpenAI credentials into the environment before launching the binary. Without it, all vector search calls fail silently.

Integration tools that add their own kairix symlink (e.g. any tool that installs a second `kairix` entry in its own `bin/` directory) can also overwrite to the raw binary when they update. Both the primary symlink and any integration symlinks must point through the wrapper.

---

## Step 1 — Confirm the Problem

```bash
# Check what the primary symlink points to
ls -la /usr/local/bin/kairix

# Broken state (raw binary):
# /usr/local/bin/kairix -> /opt/kairix/.venv/bin/kairix

# Healthy state (wrapper):
# /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh

# Confirm vec_failed symptom
kairix search "test"
# Broken: Results: 8 returned (BM25=8, vec=0, vec_failed=True)
# Healthy: Results: 8 returned (BM25=4, vec=4)
```

If your deployment uses an integration tool that adds a second kairix symlink, check that one too:

```bash
# Example: if your integration tool installs at /opt/<tool>/bin/kairix
ls -la /opt/<tool>/bin/kairix 2>/dev/null || echo "no integration symlink found"
```

---

## Step 2 — Verify the Wrapper Exists

```bash
# Wrapper must exist and be executable
ls -la /opt/kairix/bin/kairix-wrapper.sh
# Expected: -rwxr-xr-x ... /opt/kairix/bin/kairix-wrapper.sh

# If NOT found — check alternate locations
find /opt/kairix -name "kairix-wrapper*" 2>/dev/null
```

If the wrapper is missing → see [how-to-upgrade-kairix](how-to-upgrade-kairix.md) (wrapper is part of the install).

---

## Step 3 — Fix the Primary Symlink

```bash
# Fix primary symlink (requires root)
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

# Verify
ls -la /usr/local/bin/kairix
# Expected: /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh
```

---

## Step 4 — Fix Integration Symlinks (if applicable)

If your deployment has an integration tool that adds a second `kairix` symlink in its own `bin/` directory, that symlink must also point to the wrapper. Any tool that adds a second kairix entry is a candidate for this step.

```bash
# General pattern — adapt the path for your integration tool
INTEGRATION_LINK="/opt/<tool>/bin/kairix"

if [[ -L "$INTEGRATION_LINK" || -e "$INTEGRATION_LINK" ]]; then
  CURRENT=$(readlink "$INTEGRATION_LINK" 2>/dev/null || echo "missing")
  echo "Current: $INTEGRATION_LINK -> $CURRENT"

  if [[ "$CURRENT" != "/opt/kairix/bin/kairix-wrapper.sh" ]]; then
    sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh "$INTEGRATION_LINK"
    echo "Fixed: $INTEGRATION_LINK -> /opt/kairix/bin/kairix-wrapper.sh"
  fi
fi
```

**Why integration symlinks matter:** Integration tools often add their own `bin/` path to the execution environment. If a second kairix symlink points to the raw binary, it bypasses credential injection in that execution context — cron jobs and agents running through the integration tool's PATH will silently fail vector search.

---

## Step 5 — Fix the Cron Scripts (Critical — Don't Skip)

The cron scripts contain a `KAIRIX=` variable that defaults to `/usr/local/bin/kairix`. If they have been manually edited to point to the raw binary, cron jobs will fail with auth errors.

```bash
# Check what KAIRIX= is set to in the embed cron
grep "^KAIRIX=" /opt/kairix/cron/kairix-embed.sh

# Broken state:
# KAIRIX=/opt/kairix/.venv/bin/kairix

# Healthy state (uses /usr/local/bin/kairix which now points to wrapper):
# KAIRIX=/usr/local/bin/kairix
# OR
# KAIRIX=/opt/kairix/bin/kairix-wrapper.sh

# Fix if needed
sudo sed -i 's|KAIRIX=.*|KAIRIX=/usr/local/bin/kairix|' /opt/kairix/cron/kairix-embed.sh
sudo sed -i 's|KAIRIX=.*|KAIRIX=/usr/local/bin/kairix|' /opt/kairix/cron/entity-relation-seed.sh

# Verify both
grep "^KAIRIX=" /opt/kairix/cron/kairix-embed.sh
grep "^KAIRIX=" /opt/kairix/cron/entity-relation-seed.sh
```

---

## Step 6 — Verify Fix

```bash
# Test search (vector must be non-zero)
kairix search "agent memory"
# Required: BM25=N, vec=M where M > 0, no vec_failed

# Test wrapper directly
sudo -u kairix /opt/kairix/bin/kairix-wrapper.sh search "test"

# Full health check
kairix onboard check
# Vector test must pass
```

---

## Prevent Recurrence

After any kairix install or pip upgrade, immediately verify all symlinks:

```bash
ls -la /usr/local/bin/kairix
# If your deployment has integration symlinks, check those too
```

The `qmd-reindex.sh` cron script (runs every 6h) includes an integration symlink check. Configure it via `KAIRIX_INTEGRATION_CRON_DIRS` in `/opt/kairix/service.env` if your integration tool installs in a non-standard location.

---

## Related

- [runbook-vector-search-failure](runbook-vector-search-failure.md) — symptom reference
- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — if wrapper loads but credential fetch fails
- [how-to-upgrade-kairix](how-to-upgrade-kairix.md) — if wrapper is missing (reinstall required)
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — all-in-one quick reference
