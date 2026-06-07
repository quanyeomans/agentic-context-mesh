"""Step definitions for caches_status.feature (PR 3.1 / #422).

Drives :func:`kairix.quality.probe.caches_cli.main` through the public
``CachesDeps`` injection seam (F46-clean — the BDD step impl composes
via the public CLI entrypoint with an injected fake dispatcher and
``FakeMcpDispatchClient``, never reaching into module internals).

F1-clean (no @patch), F2-clean (no env var), F5-clean (only public
surface imports), F13-clean (no implementation symbols leaked into
the .feature file).
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from kairix.quality.probe.caches_cli import CachesDeps
from kairix.quality.probe.caches_cli import main as caches_main
from tests.fakes import FakeMcpDispatchClient

scenarios("../features/mcp_caches_status.feature")


_WARM_BRIEF_HITS = 17
_BANNER_FRAGMENT = "MCP server not responsive"


def _warm_envelope() -> dict[str, Any]:
    """Build the canonical warm-MCP envelope with non-zero brief hits."""
    return {
        "caches": [
            {
                "name": "query_result_cache",
                "size": 3,
                "hits": 22,
                "misses": 2,
                "evictions": 0,
                "hit_rate_pct": 91.7,
            },
            {
                "name": "brief_output_cache",
                "size": 5,
                "hits": _WARM_BRIEF_HITS,
                "misses": 3,
                "evictions": 0,
                "hit_rate_pct": 85.0,
            },
        ],
        "process_pid": 9999,
        "process_uptime_s": 600.5,
    }


@dataclass
class _CachesCtx:
    deps: CachesDeps | None = None
    stdout: str = ""
    stderr: str = ""
    rc: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def caches_ctx() -> _CachesCtx:
    return _CachesCtx()


def _dispatch_returning(envelope: dict[str, Any] | None):
    """Build a dispatch callable that simulates the warm-MCP path.

    When ``envelope`` is None the callable returns None (fall-through);
    otherwise it prints the JSON envelope to stdout and returns 0,
    mirroring the dispatcher's ``_render_envelope_as_json`` path.
    """

    def _dispatch(_subcommand: str, argv: list[str]) -> int | None:
        if envelope is None:
            return None
        if "--json" in argv:
            print(json.dumps(envelope, indent=2))
        else:
            # Render text from the envelope via the composer path.
            from kairix.agents.mcp.client_dispatcher import ensure_composers_loaded
            from kairix.agents.mcp.text_mode_composers import get_composer

            ensure_composers_loaded()
            composer = get_composer("caches")
            assert composer is not None, "caches composer must be registered"
            result = composer.from_envelope(envelope)
            print(composer.format_text(result, argv))
        return 0

    return _dispatch


@given("a warm MCP server with non-zero brief_output_cache hits")
def _given_warm_mcp(caches_ctx: _CachesCtx) -> None:
    envelope = _warm_envelope()
    fake_client = FakeMcpDispatchClient(responsive=True, envelope=envelope)
    caches_ctx.deps = CachesDeps(
        dispatch=_dispatch_returning(envelope),
        is_mcp_responsive=lambda: True,
        client=fake_client,
    )
    caches_ctx.extra["envelope"] = envelope


@given("no responsive MCP server")
def _given_cold_mcp(caches_ctx: _CachesCtx) -> None:
    caches_ctx.deps = CachesDeps(
        dispatch=_dispatch_returning(None),
        is_mcp_responsive=lambda: False,
    )


@when("kairix caches is run")
def _when_run(caches_ctx: _CachesCtx) -> None:
    assert caches_ctx.deps is not None
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        caches_ctx.rc = caches_main([], deps=caches_ctx.deps)
    caches_ctx.stdout = out_buf.getvalue()
    caches_ctx.stderr = err_buf.getvalue()


@when("kairix caches with the json flag is run")
def _when_run_json(caches_ctx: _CachesCtx) -> None:
    assert caches_ctx.deps is not None
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        caches_ctx.rc = caches_main(["--json"], deps=caches_ctx.deps)
    caches_ctx.stdout = out_buf.getvalue()
    caches_ctx.stderr = err_buf.getvalue()


@then("stdout shows brief_output_cache with the warm hits count")
def _then_warm_hits(caches_ctx: _CachesCtx) -> None:
    out = caches_ctx.stdout
    assert "brief_output_cache" in out, f"brief_output_cache missing from stdout: {out!r}"
    assert str(_WARM_BRIEF_HITS) in out, f"expected warm hits {_WARM_BRIEF_HITS} in stdout: {out!r}"


@then("no fall-through banner appears")
def _then_no_banner(caches_ctx: _CachesCtx) -> None:
    assert _BANNER_FRAGMENT not in caches_ctx.stderr
    assert _BANNER_FRAGMENT not in caches_ctx.stdout


@then("stderr contains the not-responsive banner")
def _then_banner_on_stderr(caches_ctx: _CachesCtx) -> None:
    assert _BANNER_FRAGMENT in caches_ctx.stderr, f"banner missing from stderr: {caches_ctx.stderr!r}"


@then("stdout shows the in-process collectors output")
def _then_inprocess_output(caches_ctx: _CachesCtx) -> None:
    assert "kairix caches" in caches_ctx.stdout
    # The in-process path emits every W-B cache name.
    for name in (
        "query_result_cache",
        "brief_output_cache",
        "brief_source_cache",
    ):
        assert name in caches_ctx.stdout, f"expected cache name {name!r} in in-process output"


@then("stdout is a valid JSON envelope with caches and process metadata")
def _then_json_with_metadata(caches_ctx: _CachesCtx) -> None:
    envelope = json.loads(caches_ctx.stdout)
    assert "caches" in envelope
    assert "process_pid" in envelope
    assert "process_uptime_s" in envelope
    # The warm envelope's hits flowed through to stdout.
    brief_row = next(row for row in envelope["caches"] if row["name"] == "brief_output_cache")
    assert brief_row["hits"] == _WARM_BRIEF_HITS
