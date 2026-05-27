# ADR-022 — Container-level secret readiness gate (replaces kairix.service systemd unit)

**Status:** Proposed 2026-05-28 — implementation deferred until Wave F completes
**Issues:** #333 (logged 2026-05-28); 2026-05-27 evening saturation incident
**Related:** #332 (Option A: fixed-shape systemd unit landed in `f365cf70` — interim solution); ADR-019 (resource governance — orthogonal but pairs because it bounds blast radius if this gate misbehaves)

## Context

`kairix.service` systemd unit exists today (post-#332 in narrowed `Type=oneshot` shape) for one job: gate the docker compose stack start on `kairix-fetch-secrets.service` having written `/run/secrets/kairix.env`. Without that gate, the kairix container would start before secrets are hydrated and vector search would come up in degraded BM25-only mode.

The systemd unit's existence has costs:
1. **Operator complexity**: operators must `systemctl enable` it during install; install steps + uninstall + upgrade docs all have to know about it.
2. **Failure mode coupling**: any future bug in the unit shape (the 2026-05-27 evening incident's `Restart=always` + `--remove-orphans` shape was caught by #332, but the *class* of bug remains possible) takes down the host.
3. **Duplication with docker `restart: unless-stopped`**: Docker daemon already handles "start containers at boot + restart on crash". The systemd unit duplicates that with worse failure semantics.

A cleaner shape moves the readiness check INTO the container, eliminating the host-side dependency entirely.

## Decision

Replace `kairix.service` with a **container-entrypoint-level secret readiness gate**. The kairix container image's entrypoint waits for `/run/secrets/kairix.env` to be readable with at least one secret line before exec'ing the kairix process. Docker's `restart: unless-stopped` policy handles the retry: if the container starts before secrets are ready, the entrypoint exits, docker restarts it, eventually `kairix-fetch-secrets.service` writes the secrets file and the next entrypoint invocation proceeds.

### Implementation shape

`scripts/docker-entrypoint.sh`:

```bash
#!/bin/sh
set -e

SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
SECRETS_WAIT_SECONDS="${KAIRIX_SECRETS_WAIT_SECONDS:-60}"

# Skip the wait if the operator deliberately ran without a secrets file
# (e.g. local dev mode where credentials are set via direct env vars).
if [ ! -e "$(dirname "$SECRETS_FILE")" ]; then
    echo "kairix: secrets directory $(dirname "$SECRETS_FILE") not mounted — proceeding (assuming direct env override)"
    exec "$@"
fi

# Wait for the secrets file to land + contain at least one assignment.
for i in $(seq 1 "$SECRETS_WAIT_SECONDS"); do
    if [ -s "$SECRETS_FILE" ] && grep -q '=' "$SECRETS_FILE" 2>/dev/null; then
        echo "kairix: secrets ready after ${i}s"
        exec "$@"
    fi
    echo "kairix: waiting for $SECRETS_FILE ($i/${SECRETS_WAIT_SECONDS})..."
    sleep 1
done

echo "kairix: ERROR — $SECRETS_FILE not populated after ${SECRETS_WAIT_SECONDS}s, exiting"
echo "kairix: docker will restart this container; ensure kairix-fetch-secrets.service is enabled on the host"
exit 1
```

Dockerfile change: `ENTRYPOINT ["/opt/kairix/bin/docker-entrypoint.sh"]` precedes the existing `CMD ["kairix", "..."]`.

`docker-compose.yml` remains identical. `kairix.service` systemd unit is **deleted from the repo**. Operations docs updated to drop the install step.

### What stays

- **`kairix-fetch-secrets.service`** remains — its job (synchronously fetch secrets from Azure Key Vault at boot + write `/run/secrets/kairix.env`) is unchanged. Its `[Install]` section changes to `WantedBy=multi-user.target` instead of being chained to `kairix.service`.
- **`kairix-alpha-deploy-webhook.service`** unaffected (separate purpose).
- **`kairix-docker-prune.service`** unaffected (separate purpose).

### Boot timing

Without this ADR:
```
boot → docker.service starts → containers come up → kairix-fetch-secrets.service runs → writes secrets
                                ↑ kairix container has already started, vector search in degraded mode
```

With this ADR:
```
boot → docker.service starts → containers come up → kairix entrypoint blocks waiting → kairix-fetch-secrets.service runs → writes secrets → entrypoint proceeds → kairix exec
```

The container restart loop is the new ordering mechanism. Typical wait is 1-5 seconds (kairix-fetch-secrets is fast). Worst case 60 seconds (entrypoint exit + docker restart + entrypoint succeeds).

## Alternatives considered

**A. Keep `kairix.service` in its #332 Option A shape forever.**
Rejected as long-term path (kept as interim — see Migration). The unit works after #332's fix, but it's a host-side dependency that operators have to know about + maintain. Eliminating it is a net simplification + removes an entire class of host-saturation incidents.

**B. Use Docker Compose `depends_on` with a healthcheck on a dummy "secrets-ready" container.**
Rejected. `depends_on` is intra-compose; it can't reference a host systemd unit. A "secrets-ready" sidecar container would have to read the same `/run/secrets/` volume, adding a container to coordinate with the kairix container — more complexity than the entrypoint wait.

**C. Make kairix tolerate missing secrets + degrade gracefully.**
Rejected. The cost of starting in degraded mode (BM25-only) is silent: vector search returns lower-quality results without surfacing the cause. The current onboard check catches it after the fact. Better to refuse to start at all so the operator sees the failure clearly.

**D. Move secrets into the docker compose `secrets:` declaration (Docker Swarm style).**
Out of scope. Docker Compose's `secrets:` works for swarm + cluster shapes but adds setup complexity for single-host deployments. The current Azure Key Vault → file pattern is the right shape for kairix's deployment surface.

## Acceptance criteria

- [ ] `scripts/docker-entrypoint.sh` created with the wait logic above. Configurable via `KAIRIX_SECRETS_WAIT_SECONDS` env (default 60).
- [ ] `Dockerfile` updated: `ENTRYPOINT ["/opt/kairix/bin/docker-entrypoint.sh"]` precedes the existing `CMD`.
- [ ] `scripts/install/kairix.service.example` deleted.
- [ ] `scripts/install/kairix-fetch-secrets.service.example` updated: `[Install]` section is `WantedBy=multi-user.target` (was `WantedBy=kairix.service`).
- [ ] `docs/operations/OPERATIONS.md` install steps updated: drop `systemctl enable kairix.service`; only `kairix-fetch-secrets.service` gets enabled.
- [ ] BDD `tests/bdd/features/container_secret_readiness_gate.feature`:
  - Scenario A (happy_path): bring up container with secrets file pre-existing → exec proceeds immediately.
  - Scenario B (wait + recover): bring up container with no secrets file; write secrets file 5 sec later; assert container exec'd after ~5 sec.
  - Scenario C (timeout): bring up container with no secrets file; wait 60 sec; assert container exited with informative message.
  - Scenario D (dev mode): bring up container with secrets dir not mounted; assert entrypoint proceeds without wait.
- [ ] Integration test verifying the new entrypoint script runs cleanly in the image.
- [ ] `docs/operations/runbooks/` migration runbook: "moving from kairix.service to entrypoint-gated readiness".

## Operational implications

**For new operators**: install becomes one-step (`systemctl enable kairix-fetch-secrets.service`) instead of two. No `kairix.service` to enable.

**For existing operators**: upgrade path needs explicit migration:
1. Pull new docker-compose.yml + image (image carries the new entrypoint).
2. `sudo systemctl disable kairix.service`.
3. `sudo rm /etc/systemd/system/kairix.service`.
4. `sudo systemctl daemon-reload`.
5. `docker compose up -d` — new entrypoint takes over.

The migration runbook + an upgrade-note in the next stable release explain this.

**Boot-time observability**: the entrypoint log lines (`waiting for /run/secrets/kairix.env (1/60)...`) appear in `docker logs app-kairix-1`. Operators triaging "kairix won't start" see the secrets-wait state directly without having to know about systemd unit ordering.

## Pairing with ADR-019 + ADR-020

This ADR is **independent** of ADR-019 and ADR-020. It addresses host-side complexity in the deployment story, not host saturation or per-tick work bounds. Sequencing matters: ADR-022's container restart loop relies on Docker daemon being responsive, which ADR-019's resource ceilings guarantee. So ADR-019 is a soft prerequisite, already shipped.

## Migration

**Phase 1 (now, shipped already in v2026.5.28)**: `kairix.service` in `Type=oneshot` shape per ADR-022's predecessor #332 Option A. Interim safety net.

**Phase 2 (next release after Wave F)**: ship the new entrypoint + delete the unit. Upgrade notes call out the migration steps.

**Phase 3 (release after Phase 2)**: drop the migration documentation; new operators only see the entrypoint-gated story.
