#!/bin/sh
# apply-alpha.sh — box-side alpha apply step.
#
# This is the on-VM apply script invoked by the canonical tc-pipelines
# reusable workflow `azure-vm-deploy.yml@v1` via `az vm run-command`
# (WIF login -> [disk snapshot, skipped for kairix] -> THIS script). It is the
# SINGLE source of the box-side alpha deploy sequence. The manual fallback,
# when CI is unavailable, is to run this script directly on the box
# (`sh apply-alpha.sh <image-tag>` from the compose dir).
#
# Usage:  apply-alpha.sh <image-tag>
#   <image-tag> may carry a leading 'v' (e.g. v2026.6.8a1); it is stripped
#   to match the GHCR image tag convention (YYYY.M.D[.N]aN, no 'v').
#
# Sequence (each step fails the script non-zero on error; a container-health
# failure at step (c)/(d) AUTO-ROLLS-BACK to the prior known-good tag — see
# "Auto-rollback" below):
#   (a) systemctl restart kairix-fetch-secrets.service  — NON-FATAL
#   (b) persist KAIRIX_IMAGE_TAG into ./.env (idempotent, atomic)
#   (c) docker compose pull + up -d --force-recreate --wait the kairix service
#   (d) kairix onboard check --json   -> require fully_passed: true
#   (e) kairix benchmark run --suite reflib -> parse "Weighted total: X.XXX";
#       if a baseline + tolerance are supplied via env, fail on regression.
#
# Env (optional regression gate):
#   KAIRIX_BASELINE_WEIGHTED      baseline weighted-total to compare against
#   KAIRIX_REGRESSION_TOLERANCE   max allowed delta below baseline (default 0.05)
#
# Unified-container default (v2026.6.8+): a single compose file
# (docker-compose.yml), a single `kairix` service, container `app-kairix-1`,
# compose dir /opt/kairix/app.
#
# Auto-rollback: a failed deploy must never leave production DOWN (the
# 2026-06-28 incident — `up --force-recreate --wait` failed and left the stack
# stopped, with no path back). Before mutating anything we capture the prior
# image TAG + DIGEST; an EXIT trap (on_exit) re-pins .env to that tag and
# recreates the kairix service on the LOCALLY-CACHED prior image (no pull —
# offline-resilient, the exact incident gap), then verifies it. Only
# container-health failures (steps c/d) roll back; a step-(e) regression leaves
# the HEALTHY build serving for a human call. The GitHub run still FAILS on the
# non-zero exit — only the box end-state changes. Exit codes: 10 = auto-healed,
# 11 = rollback impossible (manual), 12 = rollback failed / prod down (page).

set -eu

# --- arguments -------------------------------------------------------------

if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
	echo "FAIL apply-alpha: missing image tag argument" >&2
	echo "usage: apply-alpha.sh <image-tag>" >&2
	exit 2
fi

# Strip any leading 'v' — the GHCR image tag has no leading 'v'
# (matches docker-publish.yml's ${REF_NAME#v} convention).
RAW_TAG="$1"
IMAGE_TAG="${RAW_TAG#v}"

# Operational constants. COMPOSE_DIR is overridable (KAIRIX_COMPOSE_DIR) purely
# as a test seam; the prod default is unchanged (/opt/kairix/app).
COMPOSE_DIR="${KAIRIX_COMPOSE_DIR:-/opt/kairix/app}"
COMPOSE_SERVICE="kairix"
CONTAINER="app-kairix-1"
BENCHMARK_SUITE="reflib"
REGRESSION_TOLERANCE="${KAIRIX_REGRESSION_TOLERANCE:-0.05}"
# Standing reflib weighted-total baseline (ROADMAP); overridable via env. Kept
# always-set so the regression gate stays armed on every deploy — an unset
# baseline silently disarmed it.
BASELINE_WEIGHTED="${KAIRIX_BASELINE_WEIGHTED:-0.808}"

# All compose commands run from the compose dir.
cd "$COMPOSE_DIR" || {
	echo "FAIL apply-alpha: compose dir $COMPOSE_DIR not found" >&2
	exit 1
}

# --- rollback state: capture the prior known-good image before we mutate -----
#
# This is the only recoverable window: step (b) overwrites .env and step (c)
# --force-recreate replaces the running container. Capture the prior TAG (what
# compose interpolates) and the running image DIGEST (the rollback witness).
PREV_TAG=""
PREV_DIGEST=""
ROLLBACK_DONE=0
ROLLBACK_ARMED=0
ROLLBACK_TMP=""
# 1) Prefer the persisted .env tag.
if [ -f .env ] && grep -q '^KAIRIX_IMAGE_TAG=' .env; then
	PREV_TAG="$(grep '^KAIRIX_IMAGE_TAG=' .env | head -n1 | cut -d= -f2- | tr -d '\r' | sed 's/[[:space:]]*$//')"
fi
# 2) Capture the running container's resolved digest (the rollback witness) and,
#    only if .env had no tag, its image reference as a fallback tag source.
#    Reject a digest-pinned ref (...@sha256:...) — it carries no usable tag.
PREV_DIGEST="$(docker inspect --format '{{ .Image }}' "$CONTAINER" 2>/dev/null || true)"
if [ -z "$PREV_TAG" ]; then
	_live_ref="$(docker inspect --format '{{ index .Config.Image }}' "$CONTAINER" 2>/dev/null || true)"
	case "$_live_ref" in
		*@sha256:*) : ;;
		*:*) PREV_TAG="$(printf '%s' "$_live_ref" | sed 's/.*://')" ;;
	esac
fi
# 3) Validate the tag grammar (alpha YYYY.M.D[.N]aN or a mutable channel);
#    discard anything else so rollback is reported impossible, not fed garbage.
if [ -n "$PREV_TAG" ] && ! printf '%s' "$PREV_TAG" | grep -Eq '^([0-9]{4}\.[0-9]+\.[0-9]+(\.[0-9]+)?a[0-9]+|main|latest)$'; then
	echo "::warning::apply-alpha: prior tag '$PREV_TAG' is not a recognised tag; rollback will be treated as impossible" >&2
	PREV_TAG=""
fi

# Compose -f file list. The kairix image TAG is interpolated in
# docker-compose.override.yml (image: ...:${KAIRIX_IMAGE_TAG:-latest}); the base
# docker-compose.yml pins :latest and the override also carries the /run/secrets
# mount. BOTH must be passed — the base alone
# deploys :latest and drops the secrets wiring. Include the override only when
# present so a unified host shipping the base alone still works. IMAGE_TAG is
# already captured above, so reusing the positional params here is safe.
set -- -f docker-compose.yml
[ -f docker-compose.override.yml ] && set -- "$@" -f docker-compose.override.yml

# --- rollback machinery ----------------------------------------------------
#
# rollback_to_prev: re-pin .env to PREV_TAG and force-recreate the kairix
# service on the cached prior image (NO pull), then verify the running digest
# matches the prior good one and onboard passes. Returns: 0 = rolled back +
# verified; 3 = impossible (no distinct prior tag); 4 = attempted but prod
# still down. Idempotent (ROLLBACK_DONE) and set-e-safe (every write guarded).
rollback_to_prev() {
	if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
	ROLLBACK_DONE=1
	_failed_tag="$IMAGE_TAG"
	if [ -z "$PREV_TAG" ] || [ "$PREV_TAG" = "$_failed_tag" ]; then
		echo "::error::apply-alpha: deploy of '$_failed_tag' FAILED and there is no distinct prior tag to roll back to (PREV_TAG='$PREV_TAG'). Box left on '$_failed_tag' — MANUAL INTERVENTION REQUIRED." >&2
		return 3
	fi
	echo "::warning::apply-alpha: deploy of '$_failed_tag' FAILED — rolling back to prior tag '$PREV_TAG' (cached image, no re-pull)" >&2
	# Atomic .env re-pin (same tmp-then-mv idiom as step b); guard every write.
	_rb_tmp="$(mktemp "${COMPOSE_DIR}/.env.tmp.XXXXXX")" || {
		echo "::error::apply-alpha: rollback mktemp failed — prod left on '$_failed_tag'. PAGE A HUMAN." >&2
		return 4
	}
	ROLLBACK_TMP="$_rb_tmp"
	if grep -q '^KAIRIX_IMAGE_TAG=' .env 2>/dev/null; then
		if ! sed "s|^KAIRIX_IMAGE_TAG=.*|KAIRIX_IMAGE_TAG=${PREV_TAG}|" .env >"$_rb_tmp"; then
			rm -f "$_rb_tmp"; ROLLBACK_TMP=""
			echo "::error::apply-alpha: rollback .env rewrite failed. PAGE A HUMAN." >&2
			return 4
		fi
	else
		if ! { cat .env >"$_rb_tmp" 2>/dev/null && printf 'KAIRIX_IMAGE_TAG=%s\n' "$PREV_TAG" >>"$_rb_tmp"; }; then
			rm -f "$_rb_tmp"; ROLLBACK_TMP=""
			echo "::error::apply-alpha: rollback .env rewrite failed. PAGE A HUMAN." >&2
			return 4
		fi
	fi
	if ! mv "$_rb_tmp" .env; then
		rm -f "$_rb_tmp"; ROLLBACK_TMP=""
		echo "::error::apply-alpha: rollback .env mv failed. PAGE A HUMAN." >&2
		return 4
	fi
	ROLLBACK_TMP=""
	# Recreate on the prior tag — SAME -f args ($@) + up flags as step (c), but
	# NO pull: the prior image is already in the local store.
	if ! KAIRIX_IMAGE_TAG="$PREV_TAG" docker compose "$@" up -d --force-recreate --wait --wait-timeout 90 "$COMPOSE_SERVICE"; then
		echo "::error::apply-alpha: ROLLBACK to '$PREV_TAG' FAILED at compose up — prod is DOWN. PAGE A HUMAN." >&2
		return 4
	fi
	# Guard a mutable tag (e.g. 'main') that moved to the SAME bad digest.
	_new_digest="$(docker inspect --format '{{ .Image }}' "$CONTAINER" 2>/dev/null || true)"
	if [ -n "$PREV_DIGEST" ] && [ -n "$_new_digest" ] && [ "$_new_digest" != "$PREV_DIGEST" ]; then
		echo "::error::apply-alpha: ROLLBACK resolved '$PREV_TAG' to a different digest than the prior good image — cannot confirm known-good binary. PAGE A HUMAN." >&2
		return 4
	fi
	# Verify onboard fully_passed (don't trust --wait alone); jq->grep fallback.
	_rb_json="$(docker exec "$CONTAINER" sh -c 'kairix onboard check --json 2>/dev/null' 2>/dev/null)" || _rb_json=""
	_rb_ok=0
	if command -v jq >/dev/null 2>&1; then
		if [ "$(printf '%s' "$_rb_json" | jq -r '.fully_passed // false' 2>/dev/null)" = "true" ]; then _rb_ok=1; fi
	else
		if printf '%s' "$_rb_json" | grep -q '"fully_passed"[[:space:]]*:[[:space:]]*true'; then _rb_ok=1; fi
	fi
	if [ "$_rb_ok" -ne 1 ]; then
		echo "::error::apply-alpha: ROLLBACK to '$PREV_TAG' came up but FAILED onboard check — prod DEGRADED/DOWN. PAGE A HUMAN." >&2
		return 4
	fi
	echo "::warning::apply-alpha: rollback to '$PREV_TAG' SUCCEEDED — bad deploy '$_failed_tag' reverted, prod restored. Deploy still reported FAILED." >&2
	return 0
}

# on_exit: the single EXIT trap. Rolls back ONLY for armed container-health
# failures (steps c/d). Steps a/b never recreated the container; step (e) is a
# regression of a HEALTHY build (leave it for a human). $@ is the compose -f
# list (set below); the trap MUST forward it: `trap 'on_exit "$@"' EXIT`.
on_exit() {
	_st=$?
	rm -f "$ROLLBACK_TMP" 2>/dev/null || true
	if [ "$_st" -eq 0 ]; then exit 0; fi
	if [ "$ROLLBACK_ARMED" -ne 1 ]; then exit "$_st"; fi
	_rb=0
	rollback_to_prev "$@" || _rb=$?
	case "$_rb" in
		0) exit 10 ;;
		3) exit 11 ;;
		*) exit 12 ;;
	esac
}

# --- image prune (best-effort, success-path only) --------------------------
#
# Old ghcr.io/three-cubes/kairix image tags accumulate on the box — the
# 2026-07-04 incident hit ~28 GB across 9 tags before a manual cleanup. Keep
# the 3 NEWEST kairix images (the just-deployed one + 2 prior rollback targets,
# so the rollback trap always has a cached image) and remove older tags. NEVER
# remove an image any container references (running OR stopped): we skip refs
# listed by `docker ps -a`, and `docker rmi` (no -f) additionally refuses to
# delete an image still used by a container, so an in-use image is doubly safe.
# Resilient by contract: called as `prune_old_images || true` (set -e suspended
# for the body) AND every step guarded, so a prune failure logs a WARN and the
# deploy — already validated healthy — still succeeds. Runs ONLY on the clean
# success path (after the trap is disarmed), never inside the rollback trap.
prune_old_images() {
	_repo="ghcr.io/three-cubes/kairix"
	_keep=3

	# Refs held by ANY container (running or stopped). Empty on docker error —
	# keep-3 plus rmi's own in-use refusal remain as guards.
	_inuse="$(docker ps -a --format '{{.Image}}' 2>/dev/null || true)"  # in-use refs; empty on docker error still leaves keep-3 + rmi refusal as guards
	# All kairix images as "CreatedAt|ID|repo:tag" rows. CreatedAt leads so a
	# reverse lexical sort is newest-first; '|' can appear in none of the fields.
	# ID lets us keep N DISTINCT images even when several tags share one digest.
	_rows="$(docker images "$_repo" --format '{{.CreatedAt}}|{{.ID}}|{{.Repository}}:{{.Tag}}' 2>/dev/null || true)"  # image inventory; empty on docker error -> no-op below

	if [ -z "$_rows" ]; then
		echo "OK apply-alpha: image prune — no $_repo images to prune"
		docker image prune -f >/dev/null 2>&1 || true  # dangling-only sweep is best-effort; must never fail the deploy
		return 0
	fi

	_sorted="$(printf '%s\n' "$_rows" | sort -r)" || _sorted=""  # newest-first; a sort failure yields an empty list -> loop no-ops
	_seen_ids=" "  # space-fenced list of kept distinct image IDs (word-boundary match)
	_kept=0
	_removed=0
	_failed=0
	# Feed the loop via a here-doc (not a pipe) so the counters below survive in
	# the current shell rather than dying in a pipeline subshell.
	while IFS='|' read -r _created _id _ref; do
		[ -n "$_id" ] || continue
		case "$_seen_ids" in
			*" $_id "*) continue ;;  # another tag of an already-kept image — leave it
		esac
		if [ "$_kept" -lt "$_keep" ]; then
			_seen_ids="${_seen_ids}${_id} "
			_kept=$((_kept + 1))
			continue
		fi
		# Removal candidate. Skip if a container still references this exact ref.
		if printf '%s\n' "$_inuse" | grep -qxF "$_ref"; then
			echo "apply-alpha: image prune — keeping in-use image $_ref"
			continue
		fi
		if docker rmi "$_ref" >/dev/null 2>&1; then
			_removed=$((_removed + 1))
			echo "apply-alpha: image prune — removed $_ref"
		else
			_failed=$((_failed + 1))
			echo "::warning::apply-alpha: image prune — could not remove $_ref (still referenced?); continuing" >&2
		fi
	done <<PRUNE_EOF
$_sorted
PRUNE_EOF

	# Sweep dangling (untagged) layers left by the rmi calls / prior pulls.
	docker image prune -f >/dev/null 2>&1 || true  # dangling-only sweep is best-effort; must never fail the deploy

	if [ "$_failed" -gt 0 ]; then
		echo "::warning::apply-alpha: image prune — kept $_kept, removed $_removed, $_failed rmi failure(s); deploy unaffected" >&2
	else
		echo "OK apply-alpha: image prune — kept newest $_kept $_repo image(s), removed $_removed old tag(s)"
	fi
	return 0
}

echo "apply-alpha: deploying image tag '$IMAGE_TAG' from $COMPOSE_DIR"

# --- (a) refresh secrets (NON-FATAL) --------------------------------------
#
# Re-run the systemd oneshot that hydrates /run/secrets/kairix.env from
# Azure Key Vault BEFORE compose pulls/recreates the container, so a Key
# Vault rotation is picked up without a manual restart. On hosts where the
# unit isn't installed (dev/fresh hosts), this returns non-zero — we WARN
# and continue. A real
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
# .env intact.
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
# Hand the EXIT trap from the step-(b) tmp-cleanup to on_exit, which now owns
# rollback. A step-(b) abort before this point keeps the original tmp-cleanup
# trap and triggers no rollback — the container was never touched.
trap 'on_exit "$@"' EXIT

# --- (c) docker compose pull + up -----------------------------------------
#
# The override's ${KAIRIX_IMAGE_TAG:-latest} resolves the alpha image once the
# tag is exported (see the compose-file note above for why both files are passed).
# Arm rollback: from here the container is (re)created, so a failure at step
# (c)/(d) must restore the prior good tag.
ROLLBACK_ARMED=1
echo "apply-alpha: docker compose pull $COMPOSE_SERVICE (tag=$IMAGE_TAG)"
if ! KAIRIX_IMAGE_TAG="$IMAGE_TAG" docker compose "$@" pull "$COMPOSE_SERVICE"; then
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
if ! KAIRIX_IMAGE_TAG="$IMAGE_TAG" docker compose "$@" up -d --force-recreate --wait --wait-timeout 90 "$COMPOSE_SERVICE"; then
	echo "FAIL apply-alpha: docker compose up failed" >&2
	exit 1
fi

# --- (d) onboard check -----------------------------------------------------
#
# Capture stdout only — kairix's onboard CLI mixes deprecation warnings onto
# stderr; 2>/dev/null isolates the JSON payload.
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
# Disarm: the new build is healthy. A step-(e) regression is a HEALTHY build
# scoring low — do NOT force-recreate it away; leave it for a human call.
ROLLBACK_ARMED=0

# --- (e) benchmark + regression gate --------------------------------------
echo "apply-alpha: kairix benchmark run --suite $BENCHMARK_SUITE"
benchmark_out="$(docker exec "$CONTAINER" sh -c "cd /opt/kairix && kairix benchmark run --suite ${BENCHMARK_SUITE}")" || {
	echo "FAIL apply-alpha: benchmark run exec failed" >&2
	exit 1
}

# Parse the "Weighted total: X.XXX" line. The benchmark CLI emits a stable
# text line (no JSON for the suite run), so we parse text like
# take the 3rd field of the matching line.
weighted="$(printf '%s\n' "$benchmark_out" \
	| awk '/Weighted total:/ { print $3; exit }')"
if [ -z "$weighted" ]; then
	echo "FAIL apply-alpha: benchmark output missing 'Weighted total:' line" >&2
	printf 'benchmark output (tail):\n%s\n' "$(printf '%s\n' "$benchmark_out" | tail -n 20)" >&2
	exit 1
fi

# Regression gate — always armed (BASELINE_WEIGHTED defaults to the standing
# baseline). delta = baseline - weighted; regress if delta > tolerance.
# awk does the float compare.
if awk -v w="$weighted" -v b="$BASELINE_WEIGHTED" -v tol="$REGRESSION_TOLERANCE" \
	'BEGIN { delta = b - w; exit !(delta > tol) }'; then
	verdict=regress
else
	verdict=pass
fi

# Machine-readable eval marker on STDOUT, emitted on BOTH the pass and regress
# paths. The migrated deploy plane (release-vm-deploy.yml's post-reflib-status
# job) reads this off the box-side run-command output to publish the
# `vm-reflib-regression` commit status (success/failure + the achieved score)
# that release.yml's stable-release alpha-gate requires. Kept here (not a
# gh-api call) so NO GitHub token ever rides in the box-side az-run-command
# payload — the workflow owns the POST. Stable single-line key=value contract.
printf 'KAIRIX_REFLIB verdict=%s weighted=%s baseline=%s tolerance=%s\n' \
	"$verdict" "$weighted" "$BASELINE_WEIGHTED" "$REGRESSION_TOLERANCE"

if [ "$verdict" = regress ]; then
	printf 'FAIL apply-alpha: regression: weighted=%s vs baseline=%s (delta>%s tolerance)\n' \
		"$weighted" "$BASELINE_WEIGHTED" "$REGRESSION_TOLERANCE" >&2
	exit 1
fi

trap - EXIT  # clean success — disarm rollback

# --- (f) prune stale kairix images (best-effort) ---------------------------
# Only here, on the fully-validated success path with the rollback trap already
# disarmed. `|| true` suspends set -e for the body so cleanup can never fail the
# deploy that already succeeded.
prune_old_images || true  # image prune is best-effort cleanup; the deploy already succeeded and must not fail on it

printf 'alpha %s validated — weighted=%s\n' "$IMAGE_TAG" "$weighted"
