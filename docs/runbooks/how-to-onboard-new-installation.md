# How To: Onboard a New Kairix Installation

**Purpose:** Verify a new or existing kairix installation is correctly configured. Detects conflicts with QMD-based tools that cause vector search to silently fail or drift. Provides guided fixes or flags issues requiring manual action.

**When to use:**
- Fresh install on a new host
- After a kairix version upgrade
- After a platform migration or VM rebuild
- When `vec=0` or `vec_failed=True` appears with no obvious cause
- After any change to integration tool cron or config infrastructure

---

## Step 1 — Run Built-In Onboard Check

```bash
kairix onboard check
```

This confirms: binary on PATH, wrapper installed, secrets loaded, vault accessible, vector search working, Neo4j reachable. Fix any `✗` items before continuing.

If secrets fail → set up your secrets provider (see OPERATIONS.md for Azure Key Vault setup)
If symlink fails → [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md)
If vector search fails with `vec_failed=True` → continue to Step 3

---

## Step 2 — Confirm All Symlinks Are Correct

Kairix must be reachable via the primary path, and via any additional symlink added by integration tools. All must point to the credential-caching wrapper, not the raw binary:

```bash
ls -la /usr/local/bin/kairix
# Expected: /usr/local/bin/kairix -> /opt/kairix/bin/kairix-wrapper.sh
```

If your integration tool adds a second kairix symlink (any tool that installs a `kairix` entry in its own `bin/` directory):

```bash
ls -la /opt/<tool>/bin/kairix 2>/dev/null
# Expected: -> /opt/kairix/bin/kairix-wrapper.sh
```

If any symlink points to the raw Python binary or to an old/deprecated wrapper:

```bash
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix
# Repeat for any integration tool symlink
```

**Why every symlink matters:** Different execution contexts (user shell, cron, integration tool) may use different PATH entries. If any symlink bypasses the wrapper, that execution context will silently fail to load credentials.

---

## Step 3 — Check for GGUF/QMD Embedding Conflict

This is the most common cause of `vec=0` in environments that run a QMD-based integration tool alongside kairix. QMD's built-in GGUF embedder produces 768-dim vectors. If a cron or script calls `qmd embed`, it overwrites kairix's 1536-dim Azure vectors, causing a dimension mismatch on every search.

**Check for the conflict:**

```bash
# Scan integration cron directories for qmd embed calls
for dir in /opt/openclaw/cron /usr/local/openclaw/cron /opt/homebrew/opt/openclaw/cron "${HOME}/.openclaw/cron"; do
  [[ -d "$dir" ]] && grep -rl 'qmd embed' "$dir" 2>/dev/null
done
```

**If `qmd embed` is found in any cron script:**

This call must be removed. `qmd embed` should never be called in environments where kairix owns vector search. `qmd update` (BM25/FTS re-indexing) is fine and useful — only `qmd embed` (GGUF vector embedding) must be removed.

```bash
# Confirm the specific script and line
grep -n 'qmd embed' /path/to/offending-cron-script.sh
```

Edit the script to remove the `qmd embed` call and any associated timeout/exit-code handling. Keep `qmd update`. Add a comment explaining why (prevents future re-introduction):

```bash
# NOTE: qmd embed (GGUF) is intentionally NOT called here.
# Vector search is owned by kairix (Azure text-embedding-3-large, float[1536]).
# Running qmd embed resets vectors_vec to float[768] and breaks kairix vector search.
```

After editing, deploy the fixed script and kill any currently running `qmd embed` process:

```bash
# Check for running qmd embed
ps aux | grep 'qmd.*embed' | grep -v grep

# Kill if running (SIGTERM — waits for clean exit)
sudo kill -SIGTERM <PID>
```

---

## Step 4 — Check qmd-reindex.sh Credential Method

`qmd-reindex.sh` runs `qmd update` every 6 hours, then calls `kairix embed` to restore Azure vectors. The credential injection method matters: the old pattern uses az CLI (broken in most environments), the new pattern uses `/run/secrets/kairix.env`.

```bash
# Check which credential method is in use
grep -A5 'Azure vector refresh' /opt/kairix/cron/qmd-reindex.sh | head -10
```

**Bad pattern (az CLI — will silently skip embed when az auth fails):**
```bash
ENDPOINT=$(az keyvault secret show --vault-name ...)
if [[ -z "$ENDPOINT" ]]; then
  log_warn "credentials unavailable — skipping"
```

**Good pattern (/run/secrets — correct):**
```bash
KAIRIX_SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
if [ ! -f "$KAIRIX_SECRETS_FILE" ]; then
  log_warn "Secrets file not found..."
else
  set -a && source "$KAIRIX_SECRETS_FILE" && set +a
```

If the bad pattern is present, redeploy `qmd-reindex.sh` from `scripts/cron/qmd-reindex.sh` in this repository — it uses the correct credential method.

**Why this matters:** If az CLI auth fails, the Azure vector restore step is skipped silently after every `qmd update`. Each `qmd update` run resets `vectors_vec` to 768-dim (GGUF). Without the restore step succeeding, kairix vector search stays broken.

---

## Step 5 — Verify Embedding Dimension Consistency

Check the sqlite index for mixed embedding models — a symptom of the GGUF/Azure conflict having been active:

```bash
sqlite3 ~/.cache/qmd/index.sqlite \
  'SELECT model, COUNT(*) FROM content_vectors GROUP BY model;'
```

**Healthy output:** Only one model, only `text-embedding-3-large`:
```
text-embedding-3-large|20188
```

**Problematic output:** Mixed models:
```
embeddinggemma|1979
text-embedding-3-large|18209
```

If mixed models are present, the `vectors_vec` sqlite-vec table will be initialised at the dimension of whichever model ran first. If the GGUF model (768-dim) ran first, all subsequent 1536-dim queries will fail with a dimension mismatch error.

**Fix:** Force a full re-embed at the correct dimensions. This clears `content_vectors`, drops and recreates `vectors_vec` at 1536-dim, and re-embeds all chunks:

```bash
# Run in screen or nohup — takes 20-60 min for a large vault
screen -S kairix-embed
kairix embed --force
# Ctrl+A D to detach
```

Monitor progress:
```bash
tail -f ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
# Or check the sqlite directly (while embed is running):
sqlite3 ~/.cache/qmd/index.sqlite 'SELECT model, COUNT(*) FROM content_vectors GROUP BY model;'
```

**Do not run `kairix embed --force` while `qmd embed` is also running** — they share the same lock file (`/tmp/qmd-embed.lock`) and the force embed will fail to acquire it.

---

## Step 6 — Verify Integration Config References Correct Binary

If your integration tool's config references a kairix binary path directly, it must reference the wrapper or the `/usr/local/bin/kairix` symlink (which now points to the wrapper):

```bash
grep -r 'kairix' /opt/<tool>/config/ 2>/dev/null | grep -v '#'
```

Any reference to the raw venv binary should be updated to `/usr/local/bin/kairix` or `/opt/kairix/bin/kairix-wrapper.sh`.

---

## Step 7 — End-to-End Validation

```bash
# Full onboard check
kairix onboard check
# Expected: all checks passed

# Vector search must show vec > 0
kairix search "test"
# Expected: Results: N returned (BM25=X, vec=Y) — vec must be > 0

# Entity graph
kairix vault health
# Expected: entity count > 0
```

---

## Common Failure Patterns Summary

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `vec=0, vec_failed=True` on every search | Symlink points to raw binary | Step 2 |
| `vec=0` intermittently (every 30-60 min) | `qmd embed` cron resetting vectors to 768-dim | Step 3 |
| `Dimension mismatch: expected 768, received 1536` | GGUF ran before Azure; index stuck at 768-dim | Step 5 |
| `vec=0` after VM reboot | `/run/secrets/kairix.env` not recreated (tmpfs cleared) | [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) |
| Azure vector restore silently skipped in qmd-reindex | `qmd-reindex.sh` using az CLI credential method | Step 4 |
| `kairix onboard check` secrets_loaded fails | Azure credentials not in `/run/secrets/kairix.env` | OPERATIONS.md secrets setup |

---

## Related

- [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) — symlink fix detail
- [runbook-vector-search-failure](runbook-vector-search-failure.md) — vec=0 incident response
- [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) — secrets missing after reboot
- [how-to-run-benchmark](how-to-run-benchmark.md) — validate retrieval quality after install
