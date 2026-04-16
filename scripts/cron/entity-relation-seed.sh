#!/usr/bin/env bash
# entity-relation-seed.sh — Nightly entity extract + LLM-typed relationship seed
#
# Deployed to: /opt/kairix/cron/entity-relation-seed.sh
# Schedule: 03:00 local / 17:00 UTC daily (adjust for your timezone)
#
# Sequence:
#   1. kairix vault crawl -- update Neo4j entity graph from vault
#   2. seed-entity-relations.py -- LLM-classify and seed entity relationships
#
# Credentials: sourced from /run/secrets/kairix.env (populated by kairix-fetch-secrets.service)
# Logs: ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log

set -euo pipefail

# Load non-secret config
[ -f /opt/kairix/service.env ] && set -a && source /opt/kairix/service.env && set +a

# Load secrets from tmpfs (populated by kairix-fetch-secrets.service at boot)
KAIRIX_SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
if [ -f "$KAIRIX_SECRETS_FILE" ]; then
    set -a && source "$KAIRIX_SECRETS_FILE" && set +a
else
    echo "WARNING: secrets file not found at $KAIRIX_SECRETS_FILE — credentials may be missing" >&2
fi

LOG_FILE="${LOG_FILE:-${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log}"
KAIRIX="${KAIRIX:-/usr/local/bin/kairix}"
SEED_SCRIPT="${SEED_SCRIPT:-/opt/kairix/scripts/seed-entity-relations.py}"

timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log()         { printf '[%s] [%s] %s\n' "$(timestamp)" "$1" "$2" | tee -a "$LOG_FILE"; }
log_info()    { log "INFO " "$1"; }
log_warn()    { log "WARN " "$1"; }
log_error()   { log "ERROR" "$1"; }
log_success() { log "OK   " "$1"; }

log_info "entity-relation-seed: starting"

if [[ -z "${AZURE_OPENAI_API_KEY:-}" || -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
    log_warn "Azure OpenAI credentials not available — LLM classifier will fall back to pattern matching"
fi

# Step 1: Vault crawl (update Neo4j entity graph)
log_info "Running vault crawl (update entity graph)..."
if "$KAIRIX" vault crawl --vault-root "${KAIRIX_VAULT_ROOT}" 2>&1 | \
        while IFS= read -r line; do log_info "vault-crawl: $line"; done; then
    log_success "Vault crawl complete"
else
    log_warn "Vault crawl returned non-zero — continuing to seed step"
fi

# Step 2: Seed entity relationships
log_info "Seeding entity relationships (LLM-classified)..."
if [[ -f "$SEED_SCRIPT" ]]; then
    if /opt/kairix/.venv/bin/python "$SEED_SCRIPT" 2>&1 | \
            while IFS= read -r line; do log_info "seed-relations: $line"; done; then
        log_success "Entity relationship seed complete"
    else
        log_error "Entity relationship seed failed"
        exit 1
    fi
else
    log_warn "seed-entity-relations.py not found at $SEED_SCRIPT — skipping"
fi

log_success "entity-relation-seed: complete"
