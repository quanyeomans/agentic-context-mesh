"""MCP-facing wrapper for the ``kairix secrets verify`` envelope.

Thin adapter so the MCP server and the CLI return byte-identical
shapes for "what secrets does the deployed kairix resolve, and how?"

Security boundary: this surface NEVER returns secret VALUES. Each row
carries the canonical KV name + status — all metadata, no secrets.
Operators asking the agent "is auth healthy?" get a structured
answer; agents trying to exfiltrate keys get nothing useful.
"""

from __future__ import annotations

from typing import Any


def tool_secrets_verify() -> dict[str, Any]:
    """Return the same JSON envelope as ``kairix secrets verify --json``.

    Walks every registered canonical credential identity, asks
    ``SecretsLoader`` to resolve it, and reports per-row status
    (``present`` | ``MISSING``). Hydrates the bundle file first (same
    #360 fix as the CLI) so the envelope matches what the embed and
    MCP paths actually resolve.

    Exceptions propagate to the MCP frame; FastMCP's tool layer
    converts them to a JSON-RPC error response. No double-wrapping.
    """
    from dataclasses import asdict

    from kairix.secrets.cli import (
        _default_identities_provider,
        _default_loader_factory,
        _ensure_bundle_loaded,
        _row,
    )

    _ensure_bundle_loaded()
    loader = _default_loader_factory()
    identities = _default_identities_provider()
    rows = [_row(scope, area, instance, leaf, loader) for scope, area, instance, leaf in identities]
    return {
        "secrets": [asdict(row) for row in rows],
        "missing_count": sum(1 for r in rows if r.status == "MISSING"),
        "error": "",
    }
