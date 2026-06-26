#!/bin/sh
# apply-alpha.sh — box-side alpha apply step.
#
# This is the on-VM apply script invoked by the canonical tc-pipelines
# reusable workflow `azure-vm-deploy.yml@v1` via `az vm run-command`
# (WIF login -> disk snapshot -> THIS script -> smoke `systemctl is-active`).
# It faithfully replicates the deploy sequence of the (retained, fallback)
# Go HMAC-webhook in
#   services/alpha-deploy-webhook/internal/deploy/deploy.go
# so the deploy plane can move off the bespoke webhook without changing the
# box-side behaviour. The Go webhook stays installed as a documented
# fallback; this script is the path the workflow now drives.
#
# Usage:  apply-alpha.sh <image-tag>
#   <image-tag> may carry a leading 'v' (e.g. v2026.6.8a1); it is stripped
#   to match the GHCR image tag convention (YYYY.M.D[.N]aN, no 'v').
#
# Sequence (each step fails the script non-zero on error, except 4a):
#   (a) systemctl restart kairix-fetch-secrets.service  — NON-FATAL
#   (b) persist KAIRIX_IMAGE_TAG into ./.env (idempotent, atomic)
#   (c) docker compose pull + up -d --force-recreate --wait the kairix service
#   (d) kairix onboard check --json   -> require fully_passed: true
#   (e) kairix benchmark run --suite reflib -> parse "Weighted total: X.XXX";
#       if a baseline + tolerance are supplied via env, fail on regression.
#
# Env (optional regression gate, mirrors deploy.go's Service config):
#   KAIRIX_BASELINE_WEIGHTED      baseline weighted-total to compare against
#   KAIRIX_REGRESSION_TOLERANCE   max allowed delta below baseline (default 0.05)
#
# Unified-container default (v2026.6.8+): a single compose file
# (docker-compose.yml), a single `kairix` service, container `app-kairix-1`,
# compose dir /opt/kairix/app — matching the webhook's unified-container
# config defaults.

set -eu

# --- arguments -------------------------------------------------------------

if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
	echo "FAIL apply-alpha: missing image tag argument" >&2
	echo "usage: apply-alpha.sh <image-tag>" >&2
	exit 2
fi

# Strip any leading 'v' — the GHCR image tag has no leading 'v'
# (matches docker-publish.yml's ${REF_NAME#v} convention). deploy.go does
# strings.TrimPrefix(version, "v").
RAW_TAG="$1"
IMAGE_TAG="${RAW_TAG#v}"

# Operational constants — kept in lock-step with the webhook's
# unified-container config defaults (internal/config/config.go).
COMPOSE_DIR="/opt/kairix/app"
COMPOSE_FILE="docker-compose.yml"
COMPOSE_SERVICE="kairix"
CONTAINER="app-kairix-1"
BENCHMARK_SUITE="reflib"
REGRESSION_TOLERANCE="${KAIRIX_REGRESSION_TOLERANCE:-0.05}"

# All compose commands run from the compose dir (deploy.go sets c.Dir).
cd "$COMPOSE_DIR" || {
	echo "FAIL apply-alpha: compose dir $COMPOSE_DIR not found" >&2
	exit 1
}

echo "apply-alpha: deploying image tag '$IMAGE_TAG' from $COMPOSE_DIR"

# --- (a) refresh secrets (NON-FATAL) --------------------------------------
#
# Re-run the systemd oneshot that hydrates /run/secrets/kairix.env from
# Azure Key Vault BEFORE compose pulls/recreates the container, so a Key
# Vault rotation is picked up without a manual restart. On hosts where the
# unit isn't installed (dev/fresh hosts), this returns non-zero — we WARN
# and continue, exactly as deploy.go's refreshSecrets does. A real
# misconfiguration (stale secrets) surfaces at the onboard check (step d).
echo "apply-alpha: kairix-fetch-secrets restart (Key Vault -> /run/secrets/kairix.env)"
if ! systemctl restart kairix-fetch-secrets.service 2>/dev/null; then
	echo "::warning::apply-alpha: kairix-fetch-secrets restart skipped (unit missing or systemctl unavailable); continuing" >&2
fi

# --- (b) persist KAIRIX_IMAGE_TAG into .env (idempotent, atomic) -----------
#
# Persist the tag so any subsequent ad-hoc `docker compose` on the VM
# interpolates the same tag, avoiding the version-drift fallback to :latest
# (#313). Replace the existing KAIRIX_IMAGE_TAG line when present, else
# append. Atomic via tmp-then-rename so a crash mid-write leaves the prior
# .env intact (deploy.go's persistImageTag).
echo "apply-alpha: persist KAIRIX_IMAGE_TAG=$IMAGE_TAG to .env"
touch .env
tmp_env="$(mktemp "${COMPOSE_DIR}/.env.tmp.XXXXXX")"
trap 'rm -f "$tmp_env"' EXIT
if grep -q '^KAIRIX_IMAGE_TAG=' .env; then
	sed "s|^KAIRIX_IMAGE_TAG=.*|KAIRIX_IMAGE_TAG=${IMAGE_TAG}|" .env >"$tmp_env"
else
	cat .env >"$tmp_env"
	printf 'KAIRIX_IMAGE_TAG=%s\n' "$IMAGE_TAG" >>"$tmp_env"
fi
mv "$tmp_env" .env
trap - EXIT

# --- (c) docker compose pull + up -----------------------------------------
#
# docker-compose.yml on the VM uses ${KAIRIX_IMAGE_TAG:-latest} for the
# kairix service; export it so pull/up resolve the alpha image rather than
# :latest. Unified-container default: single compose file, single service.
echo "apply-alpha: docker compose pull $COMPOSE_SERVICE (tag=$IMAGE_TAG)"
if ! KAIRIX_IMAGE_TAG="$IMAGE_TAG" docker compose -f "$COMPOSE_FILE" pull "$COMPOSE_SERVICE"; then
	echo "FAIL apply-alpha: docker compose pull failed" >&2
	exit 1
fi

# --wait blocks until docker considers the kairix container "healthy" per its
# compose healthcheck (which runs `kairix onboard check`), eliminating the
# race where the onboard check below fires before the MCP server has warmed
# and bound port 8080. --wait-timeout 90 gives headroom over the observed
# ~13-15s warm+bind without hanging the deploy.
#
# --force-recreate makes every deploy idempotent on the running-process side
# (the v2026.5.17a9 incident: a bind-mounted config change with an unchanged
# image digest left compose seeing the container as "running" and skipping
# the restart, so the stale-config container stayed up). Costs one ~10s
# restart per deploy in exchange for eliminating the stale-container trap.
echo "apply-alpha: docker compose up -d --force-recreate --wait $COMPOSE_SERVICE"
if ! KAIRIX_IMAGE_TAG="$IMAGE_TAG" docker compose -f "$COMPOSE_FILE" up -d --force-recreate --wait --wait-timeout 90 "$COMPOSE_SERVICE"; then
	echo "FAIL apply-alpha: docker compose up failed" >&2
	exit 1
fi

# --- (d) onboard check -----------------------------------------------------
#
# Capture stdout only — kairix's onboard CLI mixes deprecation warnings onto
# stderr; 2>/dev/null isolates the JSON payload (deploy.go's onboardCheck).
echo "apply-alpha: kairix onboard check"
onboard_json="$(docker exec "$CONTAINER" sh -c 'kairix onboard check --json 2>/dev/null')" || {
	echo "FAIL apply-alpha: onboard check exec failed" >&2
	exit 1
}

# Require fully_passed: true. Prefer jq; fall back to a python json parse so
# the script works on hosts without jq.
fully_passed=""
if command -v jq >/dev/null 2>&1; then
	fully_passed="$(printf '%s' "$onboard_json" | jq -r '.fully_passed // false')"
elif command -v python3 >/dev/null 2>&1; then
	fully_passed="$(printf '%s' "$onboard_json" | python3 -c 'import sys,json; print(str(json.load(sys.stdin).get("fully_passed", False)).lower())' 2>/dev/null || echo parse_error)"
else
	echo "FAIL apply-alpha: neither jq nor python3 available to parse onboard JSON" >&2
	exit 1
fi

if [ "$fully_passed" = "parse_error" ]; then
	echo "FAIL apply-alpha: could not parse onboard check JSON" >&2
	printf 'onboard output: %s\n' "$onboard_json" >&2
	exit 1
fi
if [ "$fully_passed" != "true" ]; then
	echo "FAIL apply-alpha: onboard check not fully_passed" >&2
	printf 'onboard output: %s\n' "$onboard_json" >&2
	exit 1
fi
echo "apply-alpha: onboard check fully_passed"

# --- (e) benchmark + regression gate --------------------------------------
echo "apply-alpha: kairix benchmark run --suite $BENCHMARK_SUITE"
benchmark_out="$(docker exec "$CONTAINER" sh -c "cd /opt/kairix && kairix benchmark run --suite ${BENCHMARK_SUITE}")" || {
	echo "FAIL apply-alpha: benchmark run exec failed" >&2
	exit 1
}

# Parse the "Weighted total: X.XXX" line. The benchmark CLI emits a stable
# text line (no JSON for the suite run), so we parse text like
# parseWeightedTotal in deploy.go: take the 3rd field of the matching line.
weighted="$(printf '%s\n' "$benchmark_out" \
	| awk '/Weighted total:/ { print $3; exit }')"
if [ -z "$weighted" ]; then
	echo "FAIL apply-alpha: benchmark output missing 'Weighted total:' line" >&2
	printf 'benchmark output (tail):\n%s\n' "$(printf '%s\n' "$benchmark_out" | tail -n 20)" >&2
	exit 1
fi

# Regression gate: only when a baseline is supplied. delta = baseline -
# weighted; fail if delta > tolerance (deploy.go's BaselineWeightedTotal
# check). awk does the float compare; print a clear message on regression.
if [ -n "${KAIRIX_BASELINE_WEIGHTED:-}" ]; then
	if awk -v w="$weighted" -v b="$KAIRIX_BASELINE_WEIGHTED" -v tol="$REGRESSION_TOLERANCE" \
		'BEGIN { delta = b - w; exit !(delta > tol) }'; then
		printf 'FAIL apply-alpha: regression: weighted=%s vs baseline=%s (delta>%s tolerance)\n' \
			"$weighted" "$KAIRIX_BASELINE_WEIGHTED" "$REGRESSION_TOLERANCE" >&2
		exit 1
	fi
fi

printf 'alpha %s validated — weighted=%s\n' "$IMAGE_TAG" "$weighted"
