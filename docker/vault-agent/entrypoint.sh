#!/bin/sh
# vault-agent entrypoint
# Runs the Python secrets fetcher. Any startup errors are logged to stdout.
set -e

echo "vault-agent starting — KV: ${KAIRIX_KV_NAME:-<not set>}"
exec python /app/fetch_secrets.py
