#!/usr/bin/env bash
# kairix-mcp-register.sh — Register kairix MCP server in OpenClaw tool manifest
#
# Called by apply-kairix-config.sh after the kairix-mcp.service is installed.
# Writes the kairix MCP server entry to OpenClaw's tool manifest file.
#
# Usage: kairix-mcp-register.sh [--tool-manifest PATH]
set -euo pipefail

TOOL_MANIFEST="${OPENCLAW_TOOL_MANIFEST:-/opt/openclaw/config/mcp-tools.json}"
MCP_HOST="${KAIRIX_MCP_HOST:-127.0.0.1}"
MCP_PORT="${KAIRIX_MCP_PORT:-7443}"
KAIRIX_BIN="${KAIRIX_VENV:-/opt/kairix/.venv}/bin/kairix"

echo "[kairix-mcp-register] Registering kairix MCP server in ${TOOL_MANIFEST}"

# Verify kairix mcp command exists
if ! "${KAIRIX_BIN}" mcp --help >/dev/null 2>&1; then
    echo "[kairix-mcp-register] WARN: kairix mcp command not available — skipping registration"
    exit 0
fi

# Create manifest directory if needed
mkdir -p "$(dirname "${TOOL_MANIFEST}")"

# Build the tool entry
TOOL_ENTRY=$(cat <<JSON
{
  "name": "kairix",
  "description": "Hybrid search + entity graph memory system",
  "transport": "http",
  "url": "http://${MCP_HOST}:${MCP_PORT}/mcp",
  "tools": ["search", "entity", "prep", "timeline", "usage_guide"],
  "enabled": true,
  "installed_version": "$(${KAIRIX_BIN} --version 2>/dev/null || echo unknown)"
}
JSON
)

# Merge into existing manifest (or create new)
if [ -f "${TOOL_MANIFEST}" ]; then
    # Use python to merge — keeps existing entries intact
    python3 - <<PYEOF
import json, sys
with open("${TOOL_MANIFEST}") as f:
    manifest = json.load(f)
if "tools" not in manifest:
    manifest["tools"] = []
# Remove any existing kairix entry
manifest["tools"] = [t for t in manifest["tools"] if t.get("name") != "kairix"]
manifest["tools"].append(${TOOL_ENTRY//$'\n'/ })
with open("${TOOL_MANIFEST}", "w") as f:
    json.dump(manifest, f, indent=2)
print("[kairix-mcp-register] Updated existing manifest")
PYEOF
else
    python3 - <<PYEOF
import json
manifest = {"tools": [${TOOL_ENTRY//$'\n'/ }]}
with open("${TOOL_MANIFEST}", "w") as f:
    json.dump(manifest, f, indent=2)
print("[kairix-mcp-register] Created new manifest")
PYEOF
fi

echo "[kairix-mcp-register] Done — kairix MCP server registered at http://${MCP_HOST}:${MCP_PORT}"
