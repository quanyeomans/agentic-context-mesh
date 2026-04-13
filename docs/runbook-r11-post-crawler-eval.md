# R11 Benchmark Runbook — Post-Vault-Crawler Evaluation

**Purpose**: Run the vault crawler on the TC VM, verify entity graph health, then run the R11 benchmark to measure the expected improvements from (a) entity vault_path population and (b) the keyword hybrid fix shipped in v0.8.1.

**When to run**: After merging PR #26 to main and pulling on the VM.

---

## Prerequisites

- SSH access to TC VM
- `KAIRIX_*` env vars populated in `/opt/kairix/service.env`
- Neo4j running: `systemctl status neo4j`
- Vault mounted at `$KAIRIX_VAULT_ROOT` (default `/data/obsidian-vault`)

---

## Step 1 — Pull v0.8.1 to VM

```bash
ssh tc-vm
cd /data/tools/qmd-azure-embed
git fetch origin main
git checkout main && git pull
source /opt/kairix/service.env
```

Verify binary:
```bash
.venv/bin/kairix --version
```

---

## Step 2 — Run vault crawler

```bash
.venv/bin/kairix vault crawl --vault-root "$KAIRIX_VAULT_ROOT" --verbose
```

Expected output includes:
- `Crawled N files` — total markdown files processed
- `Upserted N organisation nodes` — should be ~18
- `Upserted N person nodes` — should be ~45
- `0 errors`

If Neo4j is unreachable, the command exits with a non-zero code and prints the connection error. Fix Neo4j first:
```bash
systemctl start neo4j
# Wait ~15s for Bolt to become available, then retry
```

---

## Step 3 — Verify entity graph health

```bash
.venv/bin/kairix vault health --json | jq '{ok, orgs_missing_vault_path, persons_missing_vault_path}'
```

**Pass criteria**:
- `ok: true`
- `orgs_missing_vault_path: 0`
- `persons_missing_vault_path: 0`

If `ok: false`, check the `issues` array:
```bash
.venv/bin/kairix vault health --json | jq '.issues[]'
```

Common issues:
- **Missing vault_path on nodes**: crawler ran but vault files not found at expected paths — check `--vault-root` matches actual vault location
- **Neo4j unreachable**: `neo4j_available: false` — start Neo4j, re-run crawl

Also check curator health:
```bash
.venv/bin/kairix curator health --no-neo4j
```
Should report `ok: true` with `missing_vault_path: 0`.

---

## Step 4 — Fix run-benchmark-v2.py binary path (if not already done)

The private VM script still points to the old binary. Check and fix:
```bash
head -20 /data/tools/qmd-azure-embed/scripts/run-benchmark-v2.py | grep MNEMOSYNE
```

If it shows `/opt/openclaw/bin/mnemosyne`, patch it:
```bash
sed -i 's|/opt/openclaw/bin/mnemosyne|/data/tools/qmd-azure-embed/.venv/bin/kairix|g' \
    /data/tools/qmd-azure-embed/scripts/run-benchmark-v2.py
sed -i 's|mnemosyne-hybrid-phase5|kairix-hybrid|g' \
    /data/tools/qmd-azure-embed/scripts/run-benchmark-v2.py
```

---

## Step 5 — Run R11 benchmark

```bash
cd /data/tools/qmd-azure-embed
source /opt/kairix/service.env

.venv/bin/python scripts/run-benchmark-v2.py r11-post-crawler \
    --suite suites/v2-real-world.yaml \
    --agent shape
```

Results saved to `benchmark-results/r11-post-crawler-*.json`.

---

## Step 6 — Compare against R10 baseline

```bash
# Print R11 summary
cat benchmark-results/r11-post-crawler-*.json | jq '{
  ndcg_at_10,
  hit_at_5,
  mrr_at_10,
  by_category: .categories
}'
```

**Expected improvements vs R10:**

| Category | R10 | R11 target | Driver |
|---|---|---|---|
| keyword | 0.439 | ≥ 0.55 | Hybrid fix (v0.8.1) |
| entity | 0.751 | ≥ 0.75 (maintain) | vault_path populated |
| multi_hop | 0.549 | ≥ 0.60 | Entity boost resolving correctly |
| overall NDCG@10 | 0.5686 | ≥ 0.60 | Combined |

**Hard floors (block promotion if breached):**

| Category | Floor |
|---|---|
| keyword | 0.45 |
| entity | 0.70 |
| multi_hop | 0.55 |
| semantic | 0.50 |
| temporal | 0.50 |
| overall | 0.55 |

---

## Step 7 — Update EVALUATION.md (public repo)

After R11 completes, update the score trajectory table in `EVALUATION.md`:

```
| R11 | kairix-hybrid | YYYY-MM-DD | 95 | TBD | TBD | TBD |
```

Open a PR to the public repo with R11 results and updated benchmark table.

---

## Troubleshooting

**Benchmark times out**: Check `KAIRIX_SEARCH_LOG` for stuck queries. Kill and retry with `--limit 10` to isolate the case.

**entity NDCG drops after crawl**: Crawler may have overwritten Neo4j nodes with incomplete data. Check node properties:
```bash
.venv/bin/kairix entity lookup "Bupa" --json | jq '{vault_path, summary}'
```
If `vault_path` is null after crawl, the vault file path pattern doesn't match — check `02-Areas/00-Clients/{name}/{name}.md` exists.

**keyword NDCG still < 0.45 (below floor)**: Confirm v0.8.1 is deployed — `skip_vector` should not appear in `hybrid.py`:
```bash
grep -n "skip_vector" .venv/lib/*/site-packages/kairix/search/hybrid.py
# Expected: no output
```
