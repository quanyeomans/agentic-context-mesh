# Runbook — Migrate to layered config (base + overlay)

**Status:** required for operators running kairix from the Docker image with a host-mounted `kairix.config.yaml`. Closes the shadow-mount class of bug that broke `v2026.5.17a9` on the alpha host.

## Why this changed

The image now ships a **complete canonical config** at `/opt/kairix/kairix.config.yaml` — every required key (`_schema_version`, `provider:`, default `collections:`, default `retrieval:` block) lives there. The previous pattern of bind-mounting a host-side `kairix.config.yaml` directly over that file **shadowed** the bundled config and silently dropped any required keys the host file didn't replicate. The `provider:` field that became required in `v2026.5.17` was missing from the alpha host's host-side file; the container failed warm-up with `ValueError: kairix.config.yaml is missing the required 'provider:' field` and never went healthy.

The fix is structural, not just configurational: split the operator's overrides into a separate **overlay** file that only contains the keys they want to change, and let kairix deep-merge it on top of the image's base at startup.

## What you have to do

If you bind-mount `kairix.config.yaml` into your kairix container, do this once before upgrading past `v2026.5.18`:

### 1. Rename your host-side file

```bash
cd /opt/kairix/app  # or wherever your compose lives
mv kairix.config.yaml kairix.config.local.yaml
```

### 2. Strip required keys you don't actually need to override

Open `kairix.config.local.yaml` and **remove** any of these blocks if you're happy with the image-bundled defaults:

- `_schema_version`
- `provider`
- The full default `collections.shared` list — keep only the entries you've genuinely customised
- Boost / fusion / rerank settings — keep only the ones that differ from the image

The overlay should be small: vault paths, agent registry, anything that's *yours*, not anything the image already ships.

### 3. Update `docker-compose.override.yml`

Change the volume mount target and add the env var. The new shape:

```yaml
services:
  kairix:
    volumes:
      # WAS: - ./kairix.config.yaml:/opt/kairix/kairix.config.yaml:ro   ← drop this
      - ./kairix.config.local.yaml:/opt/kairix/kairix.config.local.yaml:ro
    environment:
      - KAIRIX_CONFIG_OVERLAY_PATH=/opt/kairix/kairix.config.local.yaml

  kairix:
    # same two changes
```

The `docker-compose.example.yml` in the repo carries the canonical shape.

### 4. Restart with `--force-recreate`

```bash
docker compose up -d --force-recreate --wait kairix
```

`--force-recreate` is important: without it, compose treats a running unhealthy container as "running" and skips the restart, so your config change doesn't take effect. The box-side deploy script (`scripts/deploy/apply-alpha.sh`) does this automatically.

### 5. Verify

```bash
docker compose exec kairix kairix probe-config
```

The output's `merged_config` section should show your overlay values on top of the image defaults. The `_schema_version` field should match what the image ships (currently `1`).

## Pin a minimum schema (optional)

If you've explicitly designed your overlay against a specific schema version and want kairix to refuse upgrades that ship a lower version, add this to the top of your overlay:

```yaml
_schema_version_required_min: 1
```

kairix will refuse to start when the image's `_schema_version` is below the overlay's `required_min`, with an actionable error pointing here. Bump the value when you've migrated your overlay forward to a new schema.

## Rolling back

If something goes wrong:

```bash
# Restore the legacy single-file shape.
unset KAIRIX_CONFIG_OVERLAY_PATH  # in your compose env
mv kairix.config.local.yaml kairix.config.yaml
# Re-add the image's required keys to it manually (provider:, _schema_version: 1, ...).
# Update docker-compose.override.yml to mount over /opt/kairix/kairix.config.yaml again.
docker compose up -d --force-recreate --wait kairix
```

The legacy single-file path remains supported indefinitely (via `KAIRIX_CONFIG_PATH`); rollback is a no-deploy operation.

## Why this is layered instead of "just ship a complete config"

A complete host-side config sounds simpler but it's exactly the shape that broke `v2026.5.17a9`: when a new release adds a required key, every host-side complete config goes stale, and the operator has to manually copy the new key forward. Layering inverts the responsibility — the image owns required keys; the operator only owns their overrides. Adding a required key to the image is now safe by construction.

## What this does NOT change

- `kairix.example.config.yaml` in the repo still shows the complete shape with comments — it's the documentation reference, not a host-side mount target.
- Existing single-file `KAIRIX_CONFIG_PATH` deployments keep working unchanged. The new layered mode is opt-in.
- The schema-version check is only active when an overlay is present — single-file deployments don't need to declare versions.

## Forward-compatible additions in the v2026.5.24 alpha series

These keys are safe to add to your overlay — they default to image-bundled values, so leaving them out is identical to using the defaults:

- `paths.agent_knowledge_dir` (default `"04-Agent-Knowledge"`) — directory name under `document_root` for the agent-memory tree. From v2026.5.24a3.
- `paths.agent_memory_glob` (default `"**/*.md"`) — recursive glob (relative to the agent-knowledge dir) for memory log files. From v2026.5.24a3.
- `topology.connectors:` entries for `kind: slack`, `kind: github`, `kind: notion`. From v2026.5.24a2. Each entry is inert unless the matching `connector_<name>` flag is on, so adding entries ahead of enabling is safe.
- `topology.credentials:` entries naming the slack / github / notion logical secrets (see [`docs/getting-started/agent-driven-setup.md`](../../getting-started/agent-driven-setup.md) for the secret-name → env-var-name map). From v2026.5.24a2.
- `features.connector_slack`, `connector_github`, `connector_notion`, plus `topology_<connector>` flags for each, plus `maintenance_loop`. All default-off.

The image-bundled `kairix.config.yaml` carries each of these as commented examples — you can copy a block out, edit it for your deployment, and paste it into your overlay.
