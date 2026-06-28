# Runbook — Kairix systemd update and rollback

**Severity:** P1 — wrong-order or interrupted updates break the MCP api, leak in-flight embeds, or leave a deployment with `kairix-fetch-secrets.service` disabled after a reboot inside the update window.

You are the operator (human or agent) rolling a new kairix image, a unit-file change, or a secrets-fetcher change onto a systemd-managed VM. On the current model the VM runs the Docker stack: `kairix.service` is a `Type=oneshot` unit that gates `docker compose up -d` on `kairix-fetch-secrets.service` having hydrated `/run/secrets/kairix.env`. A single unified `kairix` container runs both the MCP api and the background worker as s6-supervised siblings (alongside `neo4j`); there is no separate `kairix-mcp.service` and no pip-venv MCP process. This runbook is the safe path for update plus rollback. Every step ends with a concrete next action — if a step does not give you one, stop and escalate.

Closes [#257](https://github.com/three-cubes/kairix/issues/257) (migrated from a sibling infrastructure repo issue).

---

## 1. When to use this runbook

Reach for this runbook when you are about to:

- Bump the deployed kairix image tag (`KAIRIX_IMAGE_TAG` → `docker compose pull` → restart `kairix.service`, or your operator-owned `kairix-deploy.sh` from a sibling infrastructure repo).
- Change the `kairix.service` unit file (dependency ordering, environment, `ExecStartPre`, `Restart=` policy) or the compose stack it drives.
- Change the `kairix-fetch-secrets.service` unit file or its rendered `/run/secrets/kairix.env` writer (the docker-compose `vault-agent` analogue for the VM).

This runbook covers the systemd-on-VM deployment, where systemd owns the *gating* (`kairix.service` waits on `kairix-fetch-secrets.service`) and Docker's `restart: unless-stopped` owns per-container lifecycle.

Do NOT use this runbook for:

- Pure secrets rotation (no image change) — link forward to `kairix-secrets-rotation.md` (the next runbook the operator owes; will live in this same directory).
- Retrieval going bad without an update — see [`kairix-retrieval-health.md`](kairix-retrieval-health.md). If retrieval went bad *as a result of* an update, run the update rollback in §5 first, then branch into retrieval-health for diagnosis.
- A plain docker-compose deployment without systemd gating — see [`how-to-upgrade-kairix.md`](../operations/runbooks/how-to-upgrade-kairix.md) for the bare `docker compose pull && up -d` upgrade. This runbook is the systemd-managed-VM path, where unit ordering and the secrets-fetcher are in play.

---

## 2. Pre-update state capture

You can only roll back what you measured. Capture four artefacts before touching anything; they become the comparison baseline in §3 step 6 and the evidence attached to any escalation issue in §6.

```bash
# 1. Current image tag — the rollback target. Capture both the configured
#    tag and the running version so a cached/mismatched image is visible.
grep '^KAIRIX_IMAGE_TAG=' /opt/kairix/app/.env > /tmp/kairix-preupdate-version.txt
docker compose exec kairix kairix --version >> /tmp/kairix-preupdate-version.txt
cat /tmp/kairix-preupdate-version.txt

# 2. Full subsystem envelope — the gate baseline.
docker compose exec kairix kairix onboard check --json > /tmp/kairix-preupdate-onboard.json
jq '{passed, total, fully_passed}' /tmp/kairix-preupdate-onboard.json
# Expected: {"passed": 9, "total": 9, "fully_passed": true}
# If not every check passes BEFORE the update, stop. Fix the deployment first via
# kairix-retrieval-health.md; do not update on top of a degraded baseline.

# 3. Worker phase + counters — proves nothing is mid-embed.
docker compose exec kairix kairix worker status > /tmp/kairix-preupdate-worker.txt
cat /tmp/kairix-preupdate-worker.txt

# 4. Last known-good recall score — the regression gate.
docker compose exec kairix kairix benchmark run --suite reflib \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
# Note the filename printed; this is your pre-update score for §3 step 6.
```

**Next action:** confirm `fully_passed: true` and `passed: 9`. If the envelope is not green, stop and run [`kairix-retrieval-health.md`](kairix-retrieval-health.md) before continuing.

---

## 3. Update sequence (current path)

This is the order that matters: pause the worker, pull the new image, restart secrets first then the kairix stack, gate on onboard check, then resume. Skipping the pause causes in-flight embeds to die mid-batch; restarting the stack before secrets gives the container an empty environment and a degraded first request.

### Step 1 — Pause the worker

The worker runs as an s6-supervised sibling inside the `kairix` container, so pause it through the container:

```bash
# Stops the embed loop cleanly before the image swaps underfoot.
docker compose exec kairix kairix worker pause
docker compose exec kairix kairix worker status
# Expected: phase=paused (or phase=idle if it was already idle).
```

**Next action:** confirm `phase=paused` or `phase=idle`. If `phase=embedding` persists for more than 30 seconds, the worker is stuck in a batch — wait one more cycle. The pause flag persists on the data volume, so the worker comes back paused after the container restart in step 3 and you resume it explicitly in step 5.

### Step 2 — Pull the new image

Bump the image tag, then pull. The worker is paused, so this is safe.

```bash
# Preferred — operator-owned deploy script (lives in your infrastructure repo,
# not in the kairix repo — e.g. a separate ops repo with systemd units + apply scripts).
sudo /opt/kairix/bin/kairix-deploy.sh --version <NEW_VERSION>

# Fallback — bump KAIRIX_IMAGE_TAG in the operator .env, then pull the image.
# The stack is ghcr.io/three-cubes/kairix:${KAIRIX_IMAGE_TAG}; restarting the
# unit in step 3 recreates the container from the pulled image.
sudo sed -i 's/^KAIRIX_IMAGE_TAG=.*/KAIRIX_IMAGE_TAG=<NEW_VERSION>/' /opt/kairix/app/.env
docker compose pull kairix
```

**Known gap:** today `kairix-deploy.sh` (whatever your equivalent is called) typically ignores the `kairix onboard check` exit code and has no `--rollback` flag. Track the fix in your infrastructure repo's issue tracker; until it lands, you are the gate — run §3 step 4 manually and refuse to proceed unless every check passes.

**Next action:** confirm `docker compose pull kairix` reports the new tag downloaded (or "up to date" if already cached). The version check happens after the restart in step 4.

### Step 3 — Restart units in order: secrets first, then the kairix stack

Order matters. The `kairix` container reads `/run/secrets/kairix.env` at startup via the unit's `EnvironmentFile=` / compose `env_file:`; if you restart the stack before the secrets unit has written the file, the container boots with an empty environment and the first onboard check fails on `secrets_loaded`. `kairix.service` declares `Requires=`/`After=kairix-fetch-secrets.service` for exactly this reason, but restart them in order by hand so you can verify the secrets file between steps.

```bash
# 1. Secrets first — repopulates /run/secrets/kairix.env on tmpfs.
sudo systemctl restart kairix-fetch-secrets.service
sudo systemctl status kairix-fetch-secrets.service --no-pager
# Expected: Active: active (exited) — it's a oneshot.

# 2. Confirm the rendered secrets file exists and has both required keys
#    BEFORE you restart the stack.
sudo grep -E '^(KAIRIX_PROVIDER_LLM_API_KEY|KAIRIX_LLM_ENDPOINT)=' /run/secrets/kairix.env
# Expected: two lines, one per key, non-empty values.

# 3. kairix.service second — its `docker compose up -d` recreates the
#    container from the pulled image with the freshly written environment.
sudo systemctl restart kairix.service
sudo systemctl status kairix.service --no-pager
# Expected: Active: active (exited) — it's a Type=oneshot wrapper; the
# containers run detached under dockerd. Check the stack itself:
docker compose ps
# Expected: kairix and neo4j both Up (kairix healthy after warm-up).
```

**Next action:** confirm `kairix-fetch-secrets.service` is `active (exited)`, `kairix.service` is `active`, and `docker compose ps` shows both containers Up. If `kairix-fetch-secrets.service` is `disabled`, jump to §4 — failure mode "secrets unit ends up disabled."

### Step 4 — Refuse to proceed unless every onboard check passes

This is the hard gate. The update is not "applied" until this returns green.

```bash
# Confirm the running container is the target version first.
docker compose exec kairix kairix --version

docker compose exec kairix kairix onboard check --json > /tmp/kairix-postupdate-onboard.json
jq '{passed, total, fully_passed}' /tmp/kairix-postupdate-onboard.json
# Expected: {"passed": 9, "total": 9, "fully_passed": true}
```

**Next action:** if `fully_passed: false`, stop and follow [`kairix-retrieval-health.md`](kairix-retrieval-health.md) §3 with the failure list from `/tmp/kairix-postupdate-onboard.json`. Do NOT resume the worker. Do NOT mark the update complete. If retrieval-health cannot restore green within your incident window, roll back via §5.

### Step 5 — Resume the worker

```bash
docker compose exec kairix kairix worker resume
docker compose exec kairix kairix worker status
# Expected: phase=idle or phase=embedding (a fresh cycle has started).
```

**Next action:** confirm `phase` is not `paused`. If the worker refuses to resume, check `docker compose logs kairix --tail 50` for embed-pipeline errors and branch into [`kairix-retrieval-health.md`](kairix-retrieval-health.md) §4.

### Step 6 — Post-update validation: recall regression check

```bash
docker compose exec kairix kairix benchmark run --suite reflib \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
# Note the new filename.

docker compose exec kairix kairix benchmark compare \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<pre-update>.json \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<post-update>.json
```

Acceptance: `weighted_total` not lower than the pre-update value by more than 0.05. If it drops further, the update introduced a regression even though every subsystem probe is green.

**Next action:** if the regression exceeds 0.05, roll back via §5 and file the benchmark JSONs against the kairix repo with title `benchmark regression on v<NEW_VERSION>`. See [`runbook-benchmark-regression.md`](../operations/runbooks/runbook-benchmark-regression.md) for the bisect workflow.

---

## 4. Failure modes and recovery

These are the failure modes seen in the field; reach for the matching section when the symptom shows up. Each one ends with a concrete next action.

### `kairix-fetch-secrets.service` ends up `disabled` after a reboot during the update window

**Symptom:** post-reboot, `/run/secrets/kairix.env` does not exist and `systemctl is-enabled kairix-fetch-secrets.service` returns `disabled`. The container boots without credentials; onboard check fails `secrets_loaded`.

**Root cause:** the systemd unit was installed without `WantedBy=multi-user.target` (or the enable step was skipped) and got dropped on the first reboot. `kairix.service` now ships with `Requires=kairix-fetch-secrets.service`, so the gating ordering is in place once the secrets unit is enabled; the remaining hardening is the SRE worker design in [#243](https://github.com/three-cubes/kairix/issues/243), which folds the secrets fetch into `kairix.service` rather than a separate oneshot that can be left disabled.

**Manual remediation today:**

```bash
sudo systemctl enable --now kairix-fetch-secrets.service
sudo systemctl status kairix-fetch-secrets.service --no-pager
# Confirm Active: active (exited) and Loaded: ...; enabled.

# Then restart the stack so the container picks up the rendered environment.
sudo systemctl restart kairix.service
```

**Next action:** confirm `systemctl is-enabled kairix-fetch-secrets.service` prints `enabled`, then re-run §3 step 4.

### `/run/secrets/kairix.env` empty after a restart (tmpfs cleared)

**Symptom:** `/run/secrets/kairix.env` exists but is empty (or missing the API key line); the container boots, onboard check fails `secrets_loaded`. Reproduced in the 2026-05-10.5 and 2026-05-14 incidents — `/run` is a tmpfs and the kairix-fetch-secrets unit hadn't been re-run since the reboot.

**Manual remediation today:**

```bash
# Force a fresh fetch.
sudo systemctl restart kairix-fetch-secrets.service

# Confirm both required keys landed.
sudo grep -E '^(KAIRIX_PROVIDER_LLM_API_KEY|KAIRIX_LLM_ENDPOINT)=' /run/secrets/kairix.env
# Expected: two non-empty lines.

# Restart the stack so the container picks up the fresh environment.
sudo systemctl restart kairix.service
```

**Next action:** re-run §3 step 4 (`kairix onboard check --json`) and confirm `secrets_loaded: true`. If the secrets file is still empty after the fetch unit runs, escalate per §6 — the upstream secrets source has dropped the credentials.

### `kairix onboard check` returns 7/9 after the update

**Symptom:** secrets passed but two checks fail — typically `vector_search_working` and `chunk_date_populated`, or `bm25_search_working` indirectly and `agent_knowledge_populated`. Indicates retrieval state went bad during the swap, not the systemd plumbing.

**Next action:** stop the update flow and follow [`kairix-retrieval-health.md`](kairix-retrieval-health.md) §3, branching on the first failed check in `/tmp/kairix-postupdate-onboard.json`. Do not run §3 step 5 (resume) until retrieval-health returns all checks green. If retrieval-health cannot restore green inside your incident window, roll back via §5.

### Vector index out of sync after the update

**Symptom:** onboard check passes every check but search results are obviously stale or missing recently-added documents; `kairix embed status` shows the last embed run predates the update.

**Manual remediation today:**

```bash
# Pause first (per §3 step 1) if the worker has already resumed.
docker compose exec kairix kairix worker pause

# Force re-embed — destructive of derived vectors, NOT of source documents.
docker compose exec kairix kairix embed --force

# Resume.
docker compose exec kairix kairix worker resume
```

See [`kairix-retrieval-health.md`](kairix-retrieval-health.md) §4 ("No vectors indexed yet" and "Embed pipeline failing mid-run") for the per-failure recovery commands.

**Next action:** confirm `docker compose exec kairix kairix embed status` shows a fresh timestamp and `embedded_chunks > 0`, then re-run §3 step 4.

### Entity graph drift after the update

**Symptom:** onboard check passes every check but `kairix entity suggest` returns junk, or the reflib suite regresses on entity-heavy categories.

**Next action:** follow [`kairix-entity-audit.md`](../operations/runbooks/kairix-entity-audit.md) — that runbook walks detect → repair-paths → enrichment → safe-purge in order of safety. Do not skip the dry-run step.

---

## 5. Rollback

Rollback today is manual — there is no `kairix-deploy.sh --rollback` flag yet. Track the gap in your infrastructure repo's issue tracker. If your operator-side checklist for deployment lives in that sibling repo (e.g. `infra/config/kairix-deployment-checklist.md`), it should follow the same procedure below.

Use rollback when:

- §3 step 4 (onboard check) cannot return all checks green within your incident window after the manual remediations in §4.
- §3 step 6 (benchmark) shows `weighted_total` regression greater than 0.05.
- A failure mode in §4 cannot be cleared from the failure-mode commands alone.

### Rollback sequence

```bash
# 1. Pause the worker (same as §3 step 1).
docker compose exec kairix kairix worker pause

# 2. Pin back to the pre-update image tag captured in §2 artefact 1.
PREV_VERSION=$(grep '^KAIRIX_IMAGE_TAG=' /tmp/kairix-preupdate-version.txt | cut -d= -f2)
sudo sed -i "s/^KAIRIX_IMAGE_TAG=.*/KAIRIX_IMAGE_TAG=${PREV_VERSION}/" /opt/kairix/app/.env
docker compose pull kairix

# 3. Restart in order — secrets first, then the kairix stack (same as §3 step 3).
#    `kairix.service`'s `docker compose up -d` recreates the container from the
#    rolled-back image.
sudo systemctl restart kairix-fetch-secrets.service
sudo grep -E '^(KAIRIX_PROVIDER_LLM_API_KEY|KAIRIX_LLM_ENDPOINT)=' /run/secrets/kairix.env
sudo systemctl restart kairix.service

# Confirm the running container matches the prior version.
docker compose exec kairix kairix --version

# 4. Hard gate: onboard check must pass every check against the rolled-back version.
docker compose exec kairix kairix onboard check --json | jq '{passed, total, fully_passed}'
# Expected: {"passed": 9, "total": 9, "fully_passed": true}

# 5. Resume the worker.
docker compose exec kairix kairix worker resume

# 6. Confirm recall matches the pre-update baseline captured in §2 artefact 4.
docker compose exec kairix kairix benchmark run --suite reflib \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
docker compose exec kairix kairix benchmark compare \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<pre-update>.json \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<post-rollback>.json
# Acceptance: weighted_total within 0.01 of the pre-update value.
```

**Next action:** confirm rollback restored `fully_passed: true` and the benchmark is back at baseline. File an incident issue per §6 with both pre-update and post-update onboard envelopes attached so the regression can be triaged before re-attempting the update.

**Forward link:** once your infrastructure repo's deploy-script gap is closed, rollback becomes `sudo /opt/kairix/bin/kairix-deploy.sh --rollback` (or your equivalent); this section will collapse to that one command and the gating sequence above will move into the script.

---

## 6. Escalation

File an issue at https://github.com/three-cubes/kairix/issues titled `systemd update: <symptom>` when:

- §5 (Rollback) does not restore `fully_passed: true`.
- A failure mode appears that doesn't match any branch in §4.
- The same symptom recurs within 24 hours of a clean recovery.
- The benchmark regression in §3 step 6 exceeds 0.05 and rollback does not bring it back to baseline.

Attach to the issue:

```bash
# Pre-update and post-update onboard envelopes for diff.
cat /tmp/kairix-preupdate-onboard.json
cat /tmp/kairix-postupdate-onboard.json

# Worker state at the time of failure.
docker compose exec kairix kairix worker status

# Systemd unit state for both units.
sudo systemctl status kairix-fetch-secrets.service --no-pager
sudo systemctl status kairix.service --no-pager

# Container state.
docker compose ps

# Last 50 journal lines from both units, plus container logs.
sudo journalctl -u kairix-fetch-secrets.service --no-pager -n 50
sudo journalctl -u kairix.service --no-pager -n 50
docker compose logs kairix --tail 50

# Benchmark JSONs from §3 step 6 (or §5 step 6 if rolled back).
ls ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
```

Tag the issue with whichever dogfood agent first reported the symptom — the primary signal for whether the recovery was real.

### Full reset path (worst case)

If rollback restores the image but onboard check still cannot return all checks green, the deployment's derived state (vectors, FTS, entity graph) has drifted away from what the rolled-back version expects. Run the full retrieval reset:

1. Roll back the image per §5 steps 1-3.
2. Re-run `docker compose exec kairix kairix onboard check --json` and capture the failure list.
3. If `neo4j_reachable: false` or the entity graph is wiped, run `docker compose exec kairix kairix store crawl --document-root "${KAIRIX_DOCUMENT_ROOT}"` to repopulate from the document store.
4. Follow [`kairix-retrieval-health.md`](kairix-retrieval-health.md) §6 (Full reset) for the embed + FTS + canary rebuild sequence.
5. File the incident issue with the full structured diagnostic envelope attached.

**Next action:** open the issue with the artefacts above pasted in, then watch for triage. Do not re-attempt the update against the same version until the issue is triaged and a fix or workaround is published.

---

## See also

- [`kairix-retrieval-health.md`](kairix-retrieval-health.md) — diagnose any onboard-check failure that surfaces during or after the update window.
- [`kairix-entity-audit.md`](../operations/runbooks/kairix-entity-audit.md) — audit the entity graph if entity-driven queries regress post-update.
- [`how-to-upgrade-kairix.md`](../operations/runbooks/how-to-upgrade-kairix.md) — Docker-compose upgrade procedure; this runbook is the systemd-on-VM counterpart.
- [`runbook-benchmark-regression.md`](../operations/runbooks/runbook-benchmark-regression.md) — bisect workflow when §3 step 6 or §5 step 6 shows a regression.
- Your infrastructure repo's `kairix-deploy.sh` resilience tracker — rollback flag, onboard-check exit-code gating.
- [kairix#243](https://github.com/three-cubes/kairix/issues/243) — SRE worker design: collapses `kairix-fetch-secrets.service` into a managed step inside `kairix.service`, eliminating the disabled-after-reboot failure mode in §4.
- `kairix-secrets-rotation.md` — the next runbook the operator owes; covers `KAIRIX_PROVIDER_LLM_API_KEY` / `KAIRIX_LLM_ENDPOINT` rotation without an image change. Will live in this same directory.
