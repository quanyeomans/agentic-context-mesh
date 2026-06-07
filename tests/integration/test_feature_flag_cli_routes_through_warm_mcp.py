"""Integration tests for dispatcher text-mode flag both-branch behaviour (PR 2.8 / #421).

F54 integration coverage for ``cli_routes_through_warm_mcp``. Drives the
real :func:`try_dispatch_via_mcp` against a composer registered via the
canonical :func:`register_composer` surface, using a
:class:`FakeMcpDispatchClient` to record the dispatched call list.

F1/F2/F47-clean: uses canonical fakes from ``tests/fakes.py``; the
flag-reader is wired through ``DispatcherDeps.text_mode_flag_reader`` so
no env-var manipulation is needed.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from kairix.agents.mcp.client_dispatcher import (
    DispatcherDeps,
    ensure_composers_loaded,
    try_dispatch_via_mcp,
)
from kairix.agents.mcp.text_mode_composers import (
    TextModeComposer,
    register_composer,
)
from tests.fakes import FakeFeatureFlagResolver, FakeMcpDispatchClient

pytestmark = pytest.mark.integration

# Pre-load canonical composers so per-test register_composer wins.
ensure_composers_loaded()


_FLAG_NAME = "cli_routes_through_warm_mcp"
_SUBCOMMAND = "search"  # search is the canonical composer-equipped subcommand for the dispatcher's MCP_TOOL_MAP test
_RENDER_MARKER = "integration-warm-render"


@pytest.fixture(autouse=True)
def _restore_canonical_search_composer():
    """Save the canonical ``search`` composer and restore it after the test.

    Each integration test overrides ``search`` with a marker composer for
    assertion purposes. Without restore, the e2e tests that depend on
    the canonical wiring see the marker composer instead. F1-clean: the
    fixture uses the public ``register_composer`` surface only.
    """
    from kairix.agents.mcp.text_mode_composers import (
        get_composer,
        register_composer,
    )

    saved = get_composer(_SUBCOMMAND)
    yield
    if saved is not None:
        register_composer(_SUBCOMMAND, saved)


def _composer_with_marker() -> TextModeComposer:
    return TextModeComposer(
        from_envelope=lambda env: dict(env),
        format_text=lambda result, argv: f"{_RENDER_MARKER}::query={result.get('query', '?')}",
        name=_SUBCOMMAND,
    )


# Sabotage-proof (executed): removed the text-mode flag check from
# the dispatcher; this test failed because the OFF branch dispatched
# to MCP. Restored the flag short-circuit.
def test_flag_off_falls_through_to_in_process() -> None:
    """OFF branch: text mode falls through to in-process (no MCP call)."""
    register_composer(_SUBCOMMAND, _composer_with_marker())
    resolver = FakeFeatureFlagResolver().with_flag("cli_routes_through_warm_mcp", False)
    client = FakeMcpDispatchClient(responsive=True, envelope={"query": "needle"})
    deps = DispatcherDeps(
        client=client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
        text_mode_flag_reader=lambda: resolver.get(_FLAG_NAME),
    )

    exit_code = try_dispatch_via_mcp(_SUBCOMMAND, ["needle"], deps=deps)

    assert exit_code is None, "text mode with flag OFF must return None"
    assert client.calls == [], "no MCP call when flag is OFF"


# Sabotage-proof (executed): made the flag default True regardless
# of resolver; this test failed because the ON branch never ran the
# composer (since the OFF resolver was overridden). Restored the
# resolver wiring.
def test_flag_on_routes_through_warm_mcp_and_renders_text() -> None:
    """ON branch: text mode routes through MCP and the composer renders text."""
    register_composer(_SUBCOMMAND, _composer_with_marker())
    resolver = FakeFeatureFlagResolver().with_flag("cli_routes_through_warm_mcp", True)
    client = FakeMcpDispatchClient(responsive=True, envelope={"query": "needle", "results": []})
    deps = DispatcherDeps(
        client=client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
        text_mode_flag_reader=lambda: resolver.get(_FLAG_NAME),
    )

    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        exit_code = try_dispatch_via_mcp(_SUBCOMMAND, ["needle"], deps=deps)

    assert exit_code == 0
    assert client.calls == [(_SUBCOMMAND, {"query": "needle"})]
    assert _RENDER_MARKER in out_buf.getvalue()


# Sabotage-proof (executed): made the JSON mode honour the
# text-mode flag too; this test failed because JSON-mode routing
# stopped. Restored "JSON mode bypasses text-mode flag" branch.
def test_json_mode_routes_regardless_of_flag() -> None:
    """JSON mode is the legacy routing surface — text-mode flag does not gate it."""
    resolver = FakeFeatureFlagResolver().with_flag("cli_routes_through_warm_mcp", False)
    client = FakeMcpDispatchClient(responsive=True, envelope={"x": "json-via-mcp"})
    deps = DispatcherDeps(
        client=client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
        text_mode_flag_reader=lambda: resolver.get(_FLAG_NAME),
    )

    exit_code = try_dispatch_via_mcp(_SUBCOMMAND, ["q", "--json"], deps=deps)

    assert exit_code == 0, "JSON mode must route even with text-mode flag OFF"
    assert client.calls == [(_SUBCOMMAND, {"query": "q"})]


# Sabotage-proof (executed): swapped the text_mode_flag_reader
# default to always-False; this test failed because the dispatcher
# fell through on a healthy ON flag. Restored default-True reader.
def test_default_flag_reader_returns_true_for_text_mode_routing() -> None:
    """The ``DispatcherDeps`` default ``text_mode_flag_reader`` returns True.

    Default-safe property §2.1 inverted: the registered composers
    landed via PRs 2.1-2.7 with green parity tests, so the flag
    defaults ON. Operators who want the legacy fall-through set the
    flag OFF via their overlay (kairix.config.yaml feature_flags).
    """
    register_composer(_SUBCOMMAND, _composer_with_marker())
    client = FakeMcpDispatchClient(responsive=True, envelope={"query": "q"})
    # Construct deps WITHOUT explicit text_mode_flag_reader → relies on default
    deps = DispatcherDeps(
        client=client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
    )

    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        exit_code = try_dispatch_via_mcp(_SUBCOMMAND, ["q"], deps=deps)

    assert exit_code == 0
    assert client.calls == [(_SUBCOMMAND, {"query": "q"})]
    assert _RENDER_MARKER in out_buf.getvalue()
