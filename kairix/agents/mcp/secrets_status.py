"""MCP-facing wrapper for the ``kairix secrets verify`` envelope.

Thin adapter so the MCP server and the CLI return byte-identical
shapes for "what secrets does the deployed kairix resolve, and how?"

Security boundary: this surface NEVER returns secret VALUES. Each row
carries the canonical KV name + status + (optionally) the legacy
alias env-var that resolved — all metadata, no secrets. Operators
asking the agent "is auth healthy?" get a structured answer; agents
trying to exfiltrate keys get nothing useful.
"""

from __future__ import annotations

from typing import Any


def tool_secrets_verify() -> dict[str, Any]:
    """Return the same JSON envelope as ``kairix secrets verify --json``.

    Walks every registered ``LEGACY_ALIASES`` identity, asks
    ``SecretsLoader`` to resolve it, and reports per-row status
    (``present`` | ``present-via-legacy`` | ``MISSING``). Hydrates the
    bundle file first (same #360 fix as the CLI) so the envelope
    matches what the embed and MCP paths actually resolve.

    Exceptions propagate to the MCP frame; FastMCP's tool layer
    converts them to a JSON-RPC error response. No double-wrapping.
    """
    from dataclasses import asdict

    from kairix.secrets.cli import (
        _default_aliases_provider,
        _default_env_provider,
        _default_loader_factory,
        _ensure_bundle_loaded,
        _row,
    )

    _ensure_bundle_loaded()
    loader = _default_loader_factory()
    env = _default_env_provider()
    aliases = _default_aliases_provider()
    rows = [_row(scope, area, instance, leaf, loader, env) for scope, area, instance, leaf in aliases]
    # Redact legacy_used — the per-secret alias env-var name is operator
    # metadata that agents don't need. They get scope/area/instance/leaf,
    # the canonical KV name, and a status. The agent-callable surface
    # therefore answers "which secrets resolve?" without revealing which
    # legacy env-vars are in use (mitigates the F25 alias-map-leak concern
    # the original allow-listing flagged).
    redacted: list[dict[str, Any]] = []
    for row in rows:
        row_dict = asdict(row)
        row_dict.pop("legacy_used", None)
        redacted.append(row_dict)
    return {
        "secrets": redacted,
        "missing_count": sum(1 for r in rows if r.status == "MISSING"),
        "legacy_alias_count": sum(1 for r in rows if r.status == "present-via-legacy"),
        "error": "",
    }
