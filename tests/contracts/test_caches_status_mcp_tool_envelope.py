"""Envelope-shape contract for ``tool_caches_status`` (PR 3.1 / #422).

Pins the dict shape the MCP tool returns so agents and the CLI
dispatcher can rely on the keys. Also confirms the capability registry
carries one entry for ``caches_status`` with category ``diagnostic`` —
F25 keeps the registry in sync with the actual registered tool.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.server import (
    CAP_CATEGORY_DIAGNOSTIC,
    tool_caches_status,
    tool_capabilities,
)

pytestmark = pytest.mark.contract


# Sabotage-proof (executed): mutated ``tool_caches_status`` to drop the
# ``caches`` key from its return dict; the assertion below failed
# because ``"caches" in envelope`` evaluated False. Restored.
def test_tool_caches_status_returns_dict_with_caches_key() -> None:
    """``tool_caches_status`` returns a dict carrying ``caches`` as the
    list of per-cache stat envelopes — agents and the CLI dispatcher
    iterate this list."""
    envelope = tool_caches_status()
    assert isinstance(envelope, dict)
    assert "caches" in envelope
    assert isinstance(envelope["caches"], list)


# Sabotage-proof (executed): hard-coded ``process_pid`` to None; the
# isinstance check below failed. Restored.
def test_tool_caches_status_envelope_carries_process_pid_and_uptime() -> None:
    """``tool_caches_status`` includes ``process_pid`` (int) and
    ``process_uptime_s`` (float) so operators can confirm the envelope
    reflects the warm MCP process, not their CLI invocation."""
    envelope = tool_caches_status()
    assert "process_pid" in envelope
    assert isinstance(envelope["process_pid"], int)
    assert envelope["process_pid"] > 0
    assert "process_uptime_s" in envelope
    assert isinstance(envelope["process_uptime_s"], float)
    assert envelope["process_uptime_s"] >= 0.0


# Sabotage-proof (executed): mutated the per-cache projection to omit
# ``hit_rate_pct``; the per-row key assertion fired. Restored.
def test_tool_caches_status_per_cache_row_carries_canonical_keys() -> None:
    """Every per-cache row carries the documented keys so downstream
    consumers (CLI text rendering, agent inspection) don't have to
    guess the projection."""
    envelope = tool_caches_status()
    assert envelope["caches"], "caches list must be non-empty (W-B caches always present)"
    for row in envelope["caches"]:
        assert set(row.keys()) >= {
            "name",
            "size",
            "hits",
            "misses",
            "evictions",
            "hit_rate_pct",
        }, f"row missing canonical keys: {row}"


# Sabotage-proof (executed): dropped the _cap(name="caches_status", ...)
# entry from tool_capabilities → the assertion below failed; restored.
def test_capability_registry_carries_caches_status() -> None:
    """The capability catalogue exposes ``caches_status`` — agents call
    ``tool_capabilities`` to discover what kairix surfaces, so the
    catalogue must list this tool."""
    cat = tool_capabilities()
    names = {c["name"] for c in cat["capabilities"]}
    assert "caches_status" in names


# Sabotage-proof (executed): registered the tool under the
# ``configuration`` category instead of ``diagnostic`` → the equality
# assertion failed; restored.
def test_capability_registry_categorises_caches_status_as_diagnostic() -> None:
    """``caches_status`` sits under the ``diagnostic`` category so
    agents grouping by category find it alongside the other health
    probes (worker_status / features_status / dead_letter_status)."""
    cat = tool_capabilities()
    by_name = {c["name"]: c for c in cat["capabilities"]}
    assert by_name["caches_status"]["category"] == CAP_CATEGORY_DIAGNOSTIC
    assert by_name["caches_status"]["mcp_tool"] == "caches_status"
    assert by_name["caches_status"]["cli"] == "kairix caches"
