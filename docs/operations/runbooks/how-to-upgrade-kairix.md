# How To: Upgrade Kairix

**Purpose:** Move a running deployment to a new tagged release safely. Pull the new image, run the health gate, and confirm retrieval quality has not regressed before you keep the upgrade.

Docker Compose is the recommended deployment. For systemd/host installs, see the short section at the end.

---

## Docker Compose upgrade (recommended)

The app runs as the `kairix` service alongside `neo4j`. The image tag is set by the `KAIRIX_IMAGE_TAG` environment variable (`ghcr.io/three-cubes/kairix:${KAIRIX_IMAGE_TAG:-main}`), so an upgrade is: change the tag, pull, restart, gate.

1. **Set the new image tag.** Edit the `KAIRIX_IMAGE_TAG` value in your `.env` (or your compose environment) to the release you want, for example:

   ```bash
   # .env
   KAIRIX_IMAGE_TAG=v2026.6.18
   ```

2. **Pull and restart.** From the directory holding your `docker-compose.yml`:

   ```bash
   docker compose pull
   docker compose up -d
   ```

3. **Run the health gate.** This is the upgrade gate — it must pass before you treat the upgrade as done:

   ```bash
   docker compose exec kairix kairix onboard check
   ```

   `onboard check` verifies the service is healthy: config resolves, the provider plugin loads, each extractor's libraries import, secrets are reachable, and search returns vector hits. If anything fails, the output names what was checked so the fix is obvious. Roll back (step 5) if it does not pass.

4. **Confirm retrieval quality (recommended).** Run the benchmark suite and compare against the score you captured before the upgrade, to catch a silent recall regression that a health check won't:

   ```bash
   docker compose exec kairix kairix benchmark run \
     --suite suites/your-suite.yaml \
     --output /var/lib/kairix/logs/benchmark-results/

   docker compose exec kairix kairix benchmark compare \
     /var/lib/kairix/logs/benchmark-results/<before>.json \
     /var/lib/kairix/logs/benchmark-results/<after>.json
   ```

   Gate: overall NDCG@10 stays at or above your baseline (don't accept a meaningful drop). If it regressed, roll back and see [runbook-benchmark-regression](runbook-benchmark-regression.md).

5. **Roll back if a gate fails.** Pin the previous tag and restart:

   ```bash
   # .env — set KAIRIX_IMAGE_TAG back to the prior release
   KAIRIX_IMAGE_TAG=v<PREVIOUS_TAG>

   docker compose pull
   docker compose up -d
   docker compose exec kairix kairix onboard check
   ```

> Tip: capture the baseline benchmark score *before* you change the tag, so step 4 has something to compare against. Run the same `benchmark run` command on the running deployment first and keep the output filename.

---

## systemd / host upgrade

For a host install, the layout follows the filesystem standard: config under `/etc/kairix`, data under `/var/lib/kairix`, with a systemd unit running the service.

- **Fresh bootstrap.** `kairix init --system` lays down the directory tree, config template, and systemd unit (run as root). Use `--user` for a per-user install under `~/.config/kairix` and `~/.local/share/kairix`.
- **In-place upgrade.** Follow [kairix-systemd-update](../../runbooks/kairix-systemd-update.md) for the host upgrade procedure.

The health gate is the same either way — after the unit is back up, run:

```bash
kairix onboard check
```

and, to confirm retrieval quality, `kairix benchmark run` plus a `kairix benchmark compare` against your pre-upgrade baseline.

---

## Related

- [how-to-run-benchmark](how-to-run-benchmark.md) — detailed benchmark procedure
- [runbook-benchmark-regression](runbook-benchmark-regression.md) — if the benchmark gate fails post-upgrade
- [kairix-systemd-update](../../runbooks/kairix-systemd-update.md) — in-place systemd/host upgrade
- [INDEX](INDEX.md) — full runbook registry
