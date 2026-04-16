# Kairix — Vector Search Failure (`vec=0, vec_failed=True`)

**Symptom:** `kairix search "query"` returns results but shows `vec=0, vec_failed=True`. Only BM25 results are returned. Vector similarity search is silently disabled.

---

## Quick Diagnosis

```bash
# Confirm the symptom
kairix search "test query"
# Broken output: Results: 8 returned (BM25=8, vec=0, vec_failed=True) | ...
# Healthy output: Results: 8 returned (BM25=4, vec=4) | ...

# Check what the primary symlink points to
ls -la /usr/local/bin/kairix
```

**Expected (healthy):** symlink → `/opt/kairix/bin/kairix-wrapper.sh`
**Broken state:** symlink → `/opt/kairix/.venv/bin/kairix` (raw binary, no credential caching)

If your deployment uses an integration tool that adds a second kairix symlink, check that one too — see [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md).

---

## Root Cause

The kairix binary wrapper (`kairix-wrapper.sh`) caches Azure OpenAI credentials before launching the kairix Python binary. When a kairix symlink points directly to the raw Python binary instead of the wrapper:

1. No credentials are injected into the environment
2. The Azure OpenAI embedding client initialises with empty credentials
3. Vector search fails silently at query time — returns `vec_failed=True`
4. BM25 still works (no credentials required)

---

## Fix

```bash
# Verify wrapper exists
ls -la /opt/kairix/bin/kairix-wrapper.sh
# Should exist and be executable

# Fix the primary symlink (requires root)
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

# Verify symlink
ls -la /usr/local/bin/kairix
# Expected: /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh

# Test vector search is restored
kairix search "architecture decision"
# Expected: vec=N (N > 0), vec_failed not shown
```

---

## If Wrapper Doesn't Exist

The wrapper file may have been lost or not deployed. Check:

```bash
# Find wrapper in the installation
find /opt/kairix -name "kairix-wrapper*" 2>/dev/null
```

If missing, this requires a kairix reinstall or wrapper restoration. See [how-to-upgrade-kairix](how-to-upgrade-kairix.md).

---

## If Symlink is Correct But Vector Still Fails

The symlink may be correct but the wrapper itself can't load credentials. Check:

```bash
# Run wrapper manually as the service user
sudo -u kairix /opt/kairix/bin/kairix-wrapper.sh search "test"

# If it fails with credential errors:
cat /run/secrets/kairix.env  # check secrets are present
systemctl status kairix-fetch-secrets --no-pager
```

If secrets are missing → [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md)

---

## Verify Fix

```bash
kairix search "agent memory"
# Must show: BM25=N, vec=M  where M > 0
# No vec_failed in output

kairix onboard check
# Vector test must pass
```

---

## Prevent Recurrence

After any kairix reinstall or pip upgrade, verify all symlinks immediately — the pip install can overwrite symlinks in the venv bin directory. See [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) for the full procedure including any integration tool symlinks.

---

## Related

- [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) — full procedure for all kairix symlinks
- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — if wrapper loads but credentials fail
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — all failure quick-reference
