# Kairix Cron Scripts

These scripts maintain a production kairix deployment. Copy them into `/opt/kairix/cron/` and add the corresponding crontab entries for your service user.

---

## Scripts

| Script | Recommended schedule | What it does |
|---|---|---|
| `kairix-embed.sh` | `15 * * * *` (hourly at :15) | Incremental Azure vector embedding of vault changes. Sources secrets from `/run/secrets/kairix.env`, then runs `kairix embed`. Exits quickly when nothing has changed. |
| `entity-relation-seed.sh` | `0 17 * * *` (daily at 17:00 UTC) | Runs `kairix vault crawl` to update the Neo4j entity graph, then runs `seed-entity-relations.py` to LLM-classify and seed entity relationships. |
| `qmd-reindex.sh` | `0 */6 * * *` (every 6h); `--cleanup` flag for weekly prune | Updates the QMD BM25/FTS index, then immediately restores Azure vectors (float[1536]) that `qmd update` overwrites with GGUF vectors (float[768]). Includes GGUF conflict detection and gold suite staleness check. Pass `--cleanup` on Sundays to also prune the index. |
| `kairix-nightly.sh` | `0 2 * * *` (nightly at 02:00 local) | Runs incremental embed, recall-check quality gate, and wikilink injection. Checks for GGUF/QMD embedding conflicts. Silent if green (`NO_REPLY`), prints alert summary if action is required. |

---

## Deploying

Copy scripts from this directory into `/opt/kairix/cron/` and make them executable:

```bash
sudo cp scripts/cron/*.sh /opt/kairix/cron/
sudo chmod +x /opt/kairix/cron/*.sh
```

Add crontab entries as your kairix service user (e.g. `sudo crontab -u kairix -e`):

```cron
# Hourly embedding
15 * * * * /opt/kairix/cron/kairix-embed.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log 2>&1

# Every 6h QMD reindex
0 */6 * * * /opt/kairix/cron/qmd-reindex.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex.log 2>&1

# Sunday weekly cleanup
0 3 * * 0 /opt/kairix/cron/qmd-reindex.sh --cleanup >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex.log 2>&1

# Nightly maintenance
0 2 * * * /opt/kairix/cron/kairix-nightly.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/nightly-maintenance.log 2>&1

# Nightly entity graph seed
0 17 * * * /opt/kairix/cron/entity-relation-seed.sh >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log 2>&1
```

Verify the crontab is registered:

```bash
crontab -u kairix -l
```

---

## Secrets

Cron scripts source credentials from `/run/secrets/kairix.env`, which is written at boot by `kairix-fetch-secrets.service` using the host's managed identity (Azure) or equivalent credential provider.

The secrets file is on a tmpfs mount and is recreated on every boot. Scripts warn but do not exit if the file is missing — they will retry on the next scheduled run.

For setup and rotation of the secrets provider, see [OPERATIONS.md](../../OPERATIONS.md).

---

## QMD-integrated deployments

If another tool on your host calls `qmd embed`, it will overwrite kairix's Azure vectors (float[1536]) with GGUF local vectors (float[768]), causing dimension mismatch errors on every kairix embed run.

The `qmd-reindex.sh` and `kairix-nightly.sh` scripts can detect this automatically by scanning your cron directories. Specify the directories to scan in your `service.env`:

```bash
KAIRIX_INTEGRATION_CRON_DIRS="/opt/mytool/cron /usr/local/mytool/cron"
```

The fix is always the same: remove the `qmd embed` call from the offending script (keeping `qmd update` is fine), then run `kairix embed --force` to restore correct vectors.
