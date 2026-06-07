"""Unit tests for ``kairix caches`` MCP-routing fallback banner (PR 3.1 / #422).

Asserts on:

* When the warm-MCP dispatcher succeeds (returns a non-None exit code)
  the CLI exits with that code; the in-process collectors are not
  invoked and no banner appears on stderr.

* When the warm-MCP dispatcher returns ``None`` AND MCP is NOT
  responsive, the in-process collectors run and a "MCP server not
  responsive" banner is written to stderr (stdout stays clean for JSON
  piping).

* When the warm-MCP dispatcher returns ``None`` but MCP IS responsive
  (e.g. the operator opted out of routing via the flag), no banner
  appears — the in-process state is the operator's chosen source of
  truth.

F1/F2-clean: no monkeypatch on kairix internals; uses ``CachesDeps``
injection seam + ``FakeMcpDispatchClient`` from ``tests/fakes.py``.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from kairix.quality.probe.caches_cli import (
    CachesDeps,
    default_dispatch,
    default_is_mcp_responsive,
)
from kairix.quality.probe.caches_cli import main as caches_main
from tests.fakes import FakeMcpDispatchClient

pytestmark = pytest.mark.unit


_WARM_BANNER_FRAGMENT = "MCP server not responsive"


def _capture(argv: list[str], *, deps: CachesDeps) -> tuple[int, str, str]:
    """Run ``caches_main(argv, deps=deps)`` and return (rc, stdout, stderr)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = caches_main(argv, deps=deps)
    return rc, out_buf.getvalue(), err_buf.getvalue()


# Sabotage-proof (executed): removed the early-return after dispatcher
# success in ``caches_cli.main`` so the in-process collectors ran on
# top of the dispatcher's output; this test failed because stdout
# carried both the warm payload AND the in-process table. Restored.
def test_dispatcher_success_short_circuits_in_process_collectors() -> None:
    """When the dispatcher returns an int exit code, the CLI uses it and
    does NOT run the in-process collectors or print a banner."""
    fake_client = FakeMcpDispatchClient(
        responsive=True,
        envelope={
            "caches": [
                {
                    "name": "query_result_cache",
                    "size": 9,
                    "hits": 41,
                    "misses": 4,
                    "evictions": 1,
                    "hit_rate_pct": 91.1,
                },
            ],
            "process_pid": 4242,
            "process_uptime_s": 314.15,
        },
    )
    deps = CachesDeps(
        dispatch=lambda subcommand, argv: 0,
        is_mcp_responsive=lambda: True,
        client=fake_client,
    )
    rc, stdout, stderr = _capture(["--json"], deps=deps)

    assert rc == 0
    assert _WARM_BANNER_FRAGMENT not in stderr
    # In-process collectors emit "query_result_cache" with current
    # (cold) counters — but they DID NOT run, so stdout must be empty.
    assert stdout == "", f"in-process collectors leaked into stdout: {stdout!r}"


# Sabotage-proof (executed): removed the banner write from the
# fall-through branch; this test failed because stderr was empty. Restored.
def test_cold_mcp_prints_banner_to_stderr_and_runs_in_process() -> None:
    """When the dispatcher returns None AND MCP is unreachable, the
    CLI prints the banner to stderr and runs the in-process collectors
    so stdout still carries the report."""
    deps = CachesDeps(
        dispatch=lambda subcommand, argv: None,
        is_mcp_responsive=lambda: False,
    )
    rc, stdout, stderr = _capture([], deps=deps)

    assert rc == 0
    assert _WARM_BANNER_FRAGMENT in stderr, f"banner missing from stderr: {stderr!r}"
    # In-process collectors did run — stdout has the canonical header.
    assert "kairix caches" in stdout


# Sabotage-proof (executed): made the banner write to stdout instead of
# stderr; the assertion that stdout parses as JSON failed because the
# banner text contaminated the JSON document. Restored.
def test_cold_mcp_json_mode_keeps_stdout_clean() -> None:
    """In ``--json`` mode the banner stays on stderr so stdout remains a
    pipe-safe JSON envelope (e.g. ``kairix caches --json | jq``)."""
    deps = CachesDeps(
        dispatch=lambda subcommand, argv: None,
        is_mcp_responsive=lambda: False,
    )
    rc, stdout, stderr = _capture(["--json"], deps=deps)

    assert rc == 0
    assert _WARM_BANNER_FRAGMENT in stderr
    # stdout is parseable JSON — the banner did not contaminate it.
    envelope = json.loads(stdout)
    assert "caches" in envelope


# Sabotage-proof (executed): made ``default_dispatch`` raise instead
# of return None when the dispatcher is unreachable; this test failed
# because ``default_dispatch("caches", [])`` propagated the exception
# instead of returning the int|None contract. Restored.
def testdefault_dispatch_returns_none_when_mcp_absent() -> None:
    """``default_dispatch`` returns None when MCP is not reachable.

    Exercises the production-default branch so the cold-fall-through
    path has a unit-layer outcome assertion (the integration F30 test
    exercises the same path end-to-end through subprocess).
    """
    # No MCP server bound in unit-test env → the underlying
    # ``try_dispatch_via_mcp`` returns None (not responsive).
    result = default_dispatch("caches", [])
    assert result is None


# Sabotage-proof (executed): swapped the inner ``except Exception``
# in ``default_is_mcp_responsive`` with a bare ``raise``; this test
# failed because the helper propagated the connection error instead of
# returning False. Restored.
def testdefault_is_mcp_responsive_returns_false_when_unbound() -> None:
    """``default_is_mcp_responsive`` returns False when no MCP listener
    binds the configured endpoint — defensive helper, never raises."""
    assert default_is_mcp_responsive() is False


# Sabotage-proof (executed): inverted the responsiveness check so the
# banner emitted when MCP WAS responsive; this test failed because
# stderr carried the banner when it shouldn't. Restored.
def test_warm_mcp_no_routing_no_banner() -> None:
    """When the dispatcher returns None but MCP IS responsive (e.g. the
    operator opted out of routing via flag), the in-process collectors
    run silently — no banner, because in-process is the operator's
    chosen path, not a fall-through."""
    deps = CachesDeps(
        dispatch=lambda subcommand, argv: None,
        is_mcp_responsive=lambda: True,
    )
    rc, stdout, stderr = _capture([], deps=deps)

    assert rc == 0
    assert _WARM_BANNER_FRAGMENT not in stderr
    # The in-process report still emits.
    assert "kairix caches" in stdout
