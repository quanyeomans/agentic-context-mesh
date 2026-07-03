"""Unit tests for dispatcher text-mode routing (PR 2.8 / #421).

Drives :func:`kairix.agents.mcp.client_dispatcher.try_dispatch_via_mcp`
through the new composer-registry gate:

* With a composer registered AND ``--json`` absent AND MCP responsive →
  routes through warm MCP, calls ``from_envelope`` + ``format_text``,
  writes the rendered text to stdout.
* With NO composer registered AND ``--json`` absent → returns ``None``
  (falls through to in-process). This is the registry-as-gate property.
* With ``--json`` present → routes regardless of composer registration
  (JSON mode never needed the composer).

Warm-MCP text routing is now the only behaviour — the
``cli_routes_through_warm_mcp`` cutover flag retired post-validation
(PLA-287), so there is no OFF branch to gate.

F1/F2-clean: uses ``FakeMcpDispatchClient`` from ``tests/fakes.py`` — no
monkeypatch on kairix internals.
"""

from __future__ import annotations

import json

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
from tests.fakes import FakeMcpDispatchClient

pytestmark = pytest.mark.unit

# Pre-load the canonical composer wiring at module import so
# subsequent ``register_composer`` calls in tests win against the
# init module's defaults.
ensure_composers_loaded()


@pytest.fixture(autouse=True)
def _restore_canonical_search_composer():
    """Save the canonical ``search``/``timeline`` composers and restore after each test.

    Tests override these with sentinel composers; without restore the
    e2e byte-parity tests fail because the canonical wiring is gone.
    """
    from kairix.agents.mcp.text_mode_composers import (
        get_composer,
        register_composer,
    )

    saved: dict[str, TextModeComposer] = {}
    for name in ("search", "timeline"):
        entry = get_composer(name)
        if entry is not None:
            saved[name] = entry
    yield
    for name, composer in saved.items():
        register_composer(name, composer)


_SENTINEL_ENVELOPE = {"query": "needle", "results": [{"id": "doc-warm-mcp"}]}


def _register_text_composer(subcommand: str, *, rendered: str) -> None:
    """Helper that wires a recording text composer for the test subcommand."""
    register_composer(
        subcommand,
        TextModeComposer(
            from_envelope=lambda env: dict(env),
            format_text=lambda result, argv: f"{rendered}::keys={sorted(result.keys())}::argv={argv}",
            name=subcommand,
        ),
    )


def _routing_deps(client: FakeMcpDispatchClient) -> DispatcherDeps:
    return DispatcherDeps(
        client=client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
    )


# ---------------------------------------------------------------------------
# 1. Composer present + responsive → text mode routes
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the registry lookup from the
# dispatcher so text mode always fell through; this test failed
# because captured stdout was empty and exit_code was None. Restored
# the registry-then-route branch.
def test_text_mode_routes_when_composer_registered(capsys: pytest.CaptureFixture[str]) -> None:
    """With composer + responsive, text mode renders via warm MCP."""
    subcommand = "search"  # the canonical composer-equipped subcommand
    _register_text_composer(subcommand, rendered="warm-mcp-rendered")
    client = FakeMcpDispatchClient(responsive=True, envelope=_SENTINEL_ENVELOPE)
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp(subcommand, ["needle"], deps=deps)

    assert exit_code == 0, "responsive MCP with composer must produce exit 0"
    captured = capsys.readouterr()
    assert "warm-mcp-rendered" in captured.out, f"format_text output expected in stdout — got {captured.out!r}"
    assert client.calls == [(subcommand, {"query": "needle"})], (
        f"call_tool must have been invoked once — got {client.calls!r}"
    )


# ---------------------------------------------------------------------------
# 2. No composer + text mode → falls through to in-process
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): inverted the registry gate so text mode
# routed regardless of registration; this test failed because
# client.calls was non-empty and exit_code was 0. Restored the
# "composer is None → return None" branch.
def test_text_mode_falls_through_when_no_composer_registered() -> None:
    """A subcommand without a composer returns None (registry-as-gate)."""
    # Use a never-registered subcommand by clearing it via the public surface.
    from kairix.agents.mcp.text_mode_composers import unregister_composer

    unregister_composer("worker")

    client = FakeMcpDispatchClient(responsive=True, envelope={"x": 1})
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp("worker", ["status"], deps=deps)

    assert exit_code is None, "no composer must fall through to in-process"
    assert client.calls == [], "call_tool must NOT have run for unregistered subcommand"


# ---------------------------------------------------------------------------
# 3. --json always routes regardless of composer registration
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): added a guard that required composer
# even in JSON mode; this test failed because exit_code was None.
# Restored "JSON mode bypasses composer requirement" branch.
def test_json_mode_routes_without_composer(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON mode is the legacy path — composer not required.

    Backward compat: ``--json`` mode was the only routable mode in PR 2.7;
    PR 2.8 must not regress it. Subcommands like ``worker``/``features``
    that don't have composers yet still route under ``--json``.
    """
    from kairix.agents.mcp.text_mode_composers import unregister_composer

    unregister_composer("worker")

    client = FakeMcpDispatchClient(responsive=True, envelope={"status": "alive"})
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp("worker", ["status", "--json"], deps=deps)

    assert exit_code == 0, "JSON mode without composer should still route"
    captured = capsys.readouterr()
    # JSON-mode output is indent=2 JSON, not text rendering
    parsed = json.loads(captured.out)
    assert parsed == {"status": "alive"}
    assert client.calls == [("worker_status", {})]


# ---------------------------------------------------------------------------
# 4. JSON mode routes for a composer-equipped subcommand (the composer gate
#    applies to text mode only)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): extended the composer gate to JSON mode
# too; this test failed because JSON mode stopped routing for the
# composer-equipped ``search``. Restored "JSON mode bypasses the
# text-mode composer gate" branch.
def test_json_mode_routes_for_composer_equipped_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON mode routes even for a subcommand that HAS a text composer.

    The composer-availability gate only applies to text mode; ``--json``
    always routes when MCP is responsive.
    """
    subcommand = "search"
    client = FakeMcpDispatchClient(responsive=True, envelope={"q": "x"})
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp(subcommand, ["needle", "--json"], deps=deps)

    assert exit_code == 0, "JSON mode routes for a composer-equipped subcommand"
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"q": "x"}


# ---------------------------------------------------------------------------
# 6. Composer + NOT responsive → falls through (probe failure)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): swapped the probe order so responsiveness
# was checked AFTER call_tool; this test failed because call_tool ran
# despite the probe returning False. Restored probe-first order.
def test_text_mode_falls_through_when_mcp_not_responsive() -> None:
    """A non-responsive MCP probe falls through even with a composer registered."""
    subcommand = "search"
    _register_text_composer(subcommand, rendered="should-not-render")
    client = FakeMcpDispatchClient(responsive=False)
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp(subcommand, ["needle"], deps=deps)

    assert exit_code is None
    assert client.responsive_calls, "probe must have run"
    assert client.calls == [], "call_tool must NOT have run when probe returns False"


# ---------------------------------------------------------------------------
# 7. Text-mode renderer receives the envelope keys it expects
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): swapped the order so from_envelope was
# called with the empty dict instead of the result payload; this test
# failed because the rendered text reported keys=[]. Restored the
# from_envelope(payload) call.
def test_text_mode_renderer_sees_envelope_payload(capsys: pytest.CaptureFixture[str]) -> None:
    """The composer's from_envelope receives the MCP tool payload, not an empty dict."""
    subcommand = "search"
    _register_text_composer(subcommand, rendered="payload-receiver")
    payload = {"query": "needle", "results": [], "diagnostic": "ok"}
    client = FakeMcpDispatchClient(responsive=True, envelope=payload)
    deps = _routing_deps(client)

    try_dispatch_via_mcp(subcommand, ["needle"], deps=deps)

    captured = capsys.readouterr()
    # Our recording composer surfaces keys=[...] in the rendered string
    assert "keys=['diagnostic', 'query', 'results']" in captured.out, (
        f"composer must receive the full envelope keys — got {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# 8. The composer's format_text receives the argv so it can read flags
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): hardcoded argv=[] into format_text in
# the dispatcher; this test failed because the rendered string did
# not include the --limit value. Restored argv pass-through.
def test_text_mode_renderer_receives_argv(capsys: pytest.CaptureFixture[str]) -> None:
    """format_text(result, argv) receives the original argv slice for flag extraction."""
    subcommand = "timeline"
    _register_text_composer(subcommand, rendered="argv-receiver")
    payload = {"original_query": "agent-alpha joined", "results": []}
    client = FakeMcpDispatchClient(responsive=True, envelope=payload)
    deps = _routing_deps(client)

    try_dispatch_via_mcp(subcommand, ["agent-alpha joined", "--limit", "5"], deps=deps)

    captured = capsys.readouterr()
    # The recording composer echoes argv into its render
    assert "argv=['agent-alpha joined', '--limit', '5']" in captured.out, (
        f"composer must receive the argv list — got {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# 9. is_error envelope under text mode still surfaces exit 1
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made the exit-code branch always return
# 0; this test failed reporting exit_code == 0. Restored conditional.
def test_text_mode_is_error_returns_exit_code_one(capsys: pytest.CaptureFixture[str]) -> None:
    """isError envelope under text mode → exit 1, error rendered in text."""
    subcommand = "search"
    _register_text_composer(subcommand, rendered="err-render")
    cold_envelope = {"error_code": "KAIRIX_COLD_START"}
    client = FakeMcpDispatchClient(responsive=True, envelope=cold_envelope, is_error=True)
    deps = _routing_deps(client)

    exit_code = try_dispatch_via_mcp(subcommand, ["topic"], deps=deps)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "err-render" in captured.out, "text composer still renders the envelope on isError"
