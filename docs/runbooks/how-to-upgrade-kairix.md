# How To: Upgrade the Kairix Binary

**Purpose:** Install a new tagged release of kairix safely, with benchmark gate to confirm NDCG has not regressed before committing the upgrade.

---

## Before You Start

```bash
# Record current version and baseline benchmark score
kairix --version

kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
# Note the output filename — compare against this after upgrade

# Verify current search is healthy
kairix search "test"
# Confirm vec > 0, no vec_failed
```

---

## Step 1 — Install New Version

Kairix is installed into a virtualenv at `/opt/kairix/.venv`.

```bash
# Install new version
sudo /opt/kairix/.venv/bin/pip install kairix==<NEW_VERSION>

kairix --version
# Should show new version
```

---

## Step 2 — Verify All Symlinks Still Intact

Pip install can overwrite or reset the bin directory. Re-check all symlinks immediately.

```bash
# Confirm /usr/local/bin/kairix still points to wrapper (not raw binary)
ls -la /usr/local/bin/kairix
# Must be: /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh

# If your integration tool adds a second kairix symlink, check that too
ls -la /opt/<tool>/bin/kairix 2>/dev/null || echo "no integration symlink"

# If any symlink was overwritten — fix immediately
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix
# Repeat for any integration symlinks

# Verify wrapper exists and is executable
ls -la /opt/kairix/bin/kairix-wrapper.sh
```

If wrapper is missing → the new version may not have installed it:
```bash
find /opt/kairix -name "kairix-wrapper*" 2>/dev/null
# Restore from repo if missing
sudo cp scripts/kairix-wrapper.sh /opt/kairix/bin/
sudo chmod +x /opt/kairix/bin/kairix-wrapper.sh
```

---

## Step 3 — Run Onboard Check

```bash
kairix onboard check
# All tests must pass before running benchmark
# If secrets tests fail → runbook-secrets-fetch-failure
# If vector test fails → how-to-fix-binary-symlink or runbook-vector-search-failure
```

---

## Step 4 — Run Benchmark (Upgrade Gate)

```bash
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/

# Compare against pre-upgrade baseline
kairix benchmark compare \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<before>.json \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<after>.json

# If any metric regressed significantly → rollback (Step 6), investigate regression
# See runbook-benchmark-regression for diagnosis
```

---

## Step 5 — Commit Upgrade (If Benchmark Passes)

```bash
# Update the version pin in your config/scripts
# Edit your install script or version config file to pin the new version
git add <install-script-or-version-file>
git commit -m "chore: pin kairix to v<NEW_VERSION> (benchmark passed)"

# Copy updated cron scripts to deployment location (if any changed)
sudo cp scripts/cron/*.sh /opt/kairix/cron/
sudo chmod +x /opt/kairix/cron/*.sh
```

---

## Step 6 — Rollback (If Benchmark Fails)

```bash
# Rollback to previous version
sudo /opt/kairix/.venv/bin/pip install kairix==<PREVIOUS_VERSION>

# Re-verify symlinks
ls -la /usr/local/bin/kairix

# Re-run onboard check and benchmark to confirm baseline restored
kairix onboard check
kairix benchmark run --suite suites/your-suite.yaml
```

---

## Verify Upgrade Complete

```bash
kairix --version
# Shows new version

kairix search "platform architecture"
# vec > 0, no vec_failed

kairix onboard check
# All green

kairix benchmark run --suite suites/your-suite.yaml
# Scores not regressed vs pre-upgrade baseline
```

---

## Related

- [how-to-run-benchmark](how-to-run-benchmark.md) — detailed benchmark procedure
- [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) — if symlink breaks after install
- [runbook-benchmark-regression](runbook-benchmark-regression.md) — if benchmark fails post-upgrade
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — all-in-one quick reference
