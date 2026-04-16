#!/usr/bin/env bash
# kairix-embed.sh — Thin hourly embed wrapper
#
# Deployed to: /opt/kairix/cron/kairix-embed.sh
# Schedule: :15 of every hour
#
# Credentials: sourced from /run/secrets/kairix.env (populated by kairix-fetch-secrets.service)

set -euo pipefail

_SERVICE_ENV="${KAIRIX_SERVICE_ENV:-/opt/kairix/service.env}"
[ -f "$_SERVICE_ENV" ] && set -a && source "$_SERVICE_ENV" && set +a

KAIRIX_SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
if [ -f "$KAIRIX_SECRETS_FILE" ]; then
    set -a && source "$KAIRIX_SECRETS_FILE" && set +a
fi

exec /usr/local/bin/kairix embed "$@"
