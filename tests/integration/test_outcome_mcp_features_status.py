"""F30 outcome test — ``tool_features_status`` MCP tool.

Per the feature-flag-architecture spec §3.5 + §6 (F53), the MCP tool
returns the same JSON envelope as ``kairix features status --json``.
The F30 contract: call ``tool_features_status`` directly and assert on
the returned envelope via Subscript / Attribute access — not internal
call-counts.

DI seam: ``tool_features_status`` calls into
:func:`kairix.core.features.status`, which reads the registry +
overlays at call time. At PR-2 landing the registry is empty, so the
envelope is the canonical "no flags" shape. Future PRs that add
entries will read this test as the canonical contract — adding a flag
means adding a per-flag assertion alongside.

Sabotage-proof anchor: removing the ``"flags"`` key build in
:func:`tool_features_status` (e.g. returning ``{}``) makes both
assertions below fail. Mutating ``error`` to a non-empty string when
no exception occurred fails the empty-error assertion. Verified
locally.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.server import tool_features_status

pytestmark = pytest.mark.integration


def test_tool_features_status_envelope_carries_obsidian_connector_primary_at_pr6() -> None:
    """PR-6 registry → envelope's ``flags`` list contains the
    obsidian_connector_primary entry; ``error == ""``.

    Drives the production happy path: the MCP tool delegates to
    :func:`kairix.core.features.status`; the resolver projects every
    registry entry to a :class:`FlagStatus`; ``asdict`` yields the
    JSON-serialisable envelope. Pinning the per-flag name proves the
    composed surface flows live registry data through the tool — not
    just any list.
    """
    envelope = tool_features_status()

    assert envelope["error"] == "", f"expected empty 'error' string; got: {envelope['error']!r}"
    names = [entry["name"] for entry in envelope["flags"]]
    assert "obsidian_connector_primary" in names, (
        f"expected obsidian_connector_primary entry at PR-6 landing; got: {names!r}"
    )


def test_tool_features_status_envelope_has_documented_keys() -> None:
    """Envelope must carry ``flags`` and ``error`` keys per spec §3.5.

    The CLI ``--json`` mode and the MCP tool share this envelope shape
    — operators and agents see the same payload. This test pins the
    keys; per-flag contents are pinned in PR-6+ when entries land.
    """
    envelope = tool_features_status()

    assert "flags" in envelope, f"expected 'flags' key; got keys: {list(envelope)}"
    assert "error" in envelope, f"expected 'error' key; got keys: {list(envelope)}"
    assert isinstance(envelope["flags"], list), f"expected 'flags' to be a list; got {type(envelope['flags']).__name__}"
    assert isinstance(envelope["error"], str), f"expected 'error' to be a str; got {type(envelope['error']).__name__}"
