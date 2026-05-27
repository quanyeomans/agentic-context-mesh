# How To: Upgrade the Kairix Binary

**Purpose:** Install a new tagged release of kairix safely, with benchmark gate to confirm NDCG has not regressed before committing the upgrade.

---

## Migration to v2026.5.28a1: streaming bronze (default + only model)

v2026.5.28a1 removes on-disk bronze entirely — fetched bytes are extracted in-memory and discarded; `bronze_records` carries only metadata. Disk usage drops dramatically (~6000x reduction). One config change required: remove any `bronze_mode:` line from `kairix.config.yaml`. Existing on-disk bronze blobs become unused; `rm -rf $bronze_root` reclaims that disk once any final reextract for pre-upgrade dead-letter items has run.

The cumulative upgrade path from the v2026.5.24a-series:

- [v2026.5.25a1](../../upgrades/v2026.5.25a1.md) — disk pressure fixes, bronze hygiene, version-drift fixes
- [v2026.5.26a1](../../upgrades/v2026.5.26a1.md) — per-chunk commit, MCP cold-start handoff, tenacity adoption
- [v2026.5.27a1](../../upgrades/v2026.5.27a1.md) — markitdown converter extras + `kairix worker reextract`
- [v2026.5.27a2](../../upgrades/v2026.5.27a2.md) — tmpfile cleanup + scratch off tmpfs
- [v2026.5.28a1](../../upgrades/v2026.5.28a1.md) — streaming bronze (default + only model)

If you skip from an earlier release directly to v2026.5.28a1, read each note in order — the bronze cleanup discipline + scratch relocation in v2026.5.27a2 is a prerequisite for the streaming-bronze read path.

---

## Migration to v2026.5.24a-series: alpha track with new connectors + maintenance loop

The v2026.5.24 alpha series (a1 → a3) adds the topology v2 operator-config surface, four new connectors (SharePoint, Slack, GitHub, Notion), a background maintenance loop, and a configurable `agent_knowledge_populated` onboard check. Every behaviour is gated by a default-off feature flag — pulling the alpha doesn't change anything until you flip a flag.

The cumulative upgrade path:

- [v2026.5.24a1](../../upgrades/v2026.5.24a1.md) — topology v2 operator-config + SharePoint connector
- [v2026.5.24a2](../../upgrades/v2026.5.24a2.md) — Slack, GitHub, Notion connectors + maintenance loop
- [v2026.5.24a3](../../upgrades/v2026.5.24a3.md) — configurable `agent_knowledge_populated` glob + directory

If you skip directly from a stable release to a3, read all three notes in order — each documents flags + secrets that the next builds on.

### Troubleshooting: `agent_knowledge_populated` fails after upgrade

The default glob (`**/*.md` under `04-Agent-Knowledge/`) matches most layouts, but vaults that use a different directory name or stricter pattern can pin both via `kairix.config.yaml`:

```yaml
paths:
  agent_knowledge_dir: "team-memory"           # if your vault uses a different dir name
  agent_memory_glob: "*/memory/*.md"           # if you want the strict <agent>/memory/<file>.md layout
```

The check's failure detail names the directory and glob that were searched, so the mismatch is visible at a glance.

---

## Migration to v2026.5.17: pick your provider plugin

v2026.5.17 retires the `KAIRIX_PROVIDER` env-var seam — the LLM/embed plugin is now selected by a top-level `provider:` field in `kairix.config.yaml`. The plugin is the only embed/chat path; each plugin owns its own credential-retrieval pattern (Azure → Key Vault; AWS → Secrets Manager; etc.), so secrets stay where the plugin's runbook puts them.

**Before pulling the new image / running pip install**, edit your `kairix.config.yaml` and add the field at the top:

```yaml
# kairix.config.yaml
provider: azure_foundry   # or: openai
```

If you previously set `KAIRIX_PROVIDER` in your shell / docker-compose env, remove it — it is no longer read. If you don't have a `kairix.config.yaml`, copy `kairix.example.config.yaml` from the source checkout, set `provider:`, and place it at the path `KAIRIX_CONFIG_PATH` points to (or `./kairix.config.yaml` in your run cwd).

After upgrading, verify the plugin resolves:

```bash
kairix probe-config --output /tmp/probe.json
cat /tmp/probe.json | jq .provider.name
# expect: "azure_foundry"  (or your configured plugin)
```

If the probe reports `no provider configured`, your yaml file isn't being found by the runtime — re-check `KAIRIX_CONFIG_PATH` and the file's location. If it reports a typed `ProviderNotRegistered` error, the configured name doesn't match an installed plugin; the error's `available` field lists what's currently registered.

---

## Docker Compose Upgrade (recommended)

```bash
cd /path/to/kairix/docker
docker compose pull
docker compose up -d
kairix onboard check   # verify after upgrade
```

Gate: overall >= 0.80 (current baseline: 0.8385 NDCG@10).

---

## Before You Start (pip install path)

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

## Step 1 — Install New Version (Alternative: pip install, legacy)

Kairix is installed into a virtualenv at `/opt/kairix/.venv`.

```bash
# Install new version
sudo /opt/kairix/.venv/bin/pip install kairix-agentic-knowledge-mgt==<NEW_VERSION>

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
# If secrets tests fail → check that your secrets file is populated and KAIRIX_KV_NAME is set
# If vector test fails → verify the kairix wrapper script is on PATH (not the raw Python binary)
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
# Update the version pin in your operator config/install script
git add <install-script-or-version-file>
git commit -m "chore: pin kairix to v<NEW_VERSION> (benchmark passed)"
```

---

## Step 6 — Rollback (If Benchmark Fails)

```bash
# Rollback to previous tagged release
pip install git+https://github.com/three-cubes/kairix@<PREVIOUS_TAG>

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
- [runbook-benchmark-regression](runbook-benchmark-regression.md) — if benchmark fails post-upgrade
- [INDEX](INDEX.md) — full runbook registry
