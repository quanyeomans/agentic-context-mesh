#!/bin/bash
set -e

# Load secrets if available (Docker secrets or sidecar pattern)
if [[ -f /run/secrets/kairix.env ]]; then
    set -a && . /run/secrets/kairix.env && set +a
fi

# Load .env if mounted (Docker Compose env_file alternative)
if [[ -f /opt/kairix/.env ]]; then
    set -a && . /opt/kairix/.env && set +a
fi

MODE="${1:-serve}"

case "$MODE" in
    serve)
        echo "Starting kairix MCP server on port 8080..."
        exec kairix mcp serve --transport sse --host 0.0.0.0 --port 8080
        ;;
    embed)
        echo "Running incremental embed..."
        exec kairix embed
        ;;
    setup)
        echo "Starting setup wizard..."
        exec kairix setup
        ;;
    worker)
        echo "Starting background worker (embed hourly, entity seed nightly)..."
        exec python -m kairix.worker
        ;;
    eval)
        echo "Indexing reference library..."
        kairix embed
        echo "Running reference library benchmark..."
        exec kairix benchmark run --suite /opt/kairix/suites/reflib-gold-v1.yaml
        ;;
    *)
        # Pass through to kairix CLI
        exec kairix "$@"
        ;;
esac
