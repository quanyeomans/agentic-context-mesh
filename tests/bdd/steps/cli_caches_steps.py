"""Step definitions for cli_caches.feature (#396 Workstream B).

Drives :func:`kairix.quality.probe.caches_cli.main` and asserts on the
operator-facing report shape. The W-B caches are process-global
singletons; the per-scenario ``ctx`` fixture resets them via the
public accessors so cross-scenario state doesn't bleed.

F1-clean (no @patch on kairix internals), F2-clean (no env var),
F5-clean (only public-surface imports), F13-clean (no implementation
symbols in the feature file).

Sabotage notes per scenario (mutate prod → confirm fail → restore):

* "lists every cache by name" — remove one collector entry from
  ``_collect_all_rows`` in ``caches_cli.py``; the cache name vanishes
  from stdout and the scenario fails on the missing name. Confirmed
  during development; restored.

* "JSON envelope" — change ``json.dumps`` to ``str(payload)`` in
  ``main()``; scenario fails when ``json.loads`` raises. Confirmed
  during development; restored.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.agents.briefing.sources import reset_brief_source_cache
from kairix.core.health import reset_health_probe_cache
from kairix.quality.probe.caches_cli import main as caches_main
from kairix.use_cases.brief import reset_brief_output_cache
from kairix.use_cases.prep import reset_prep_summary_cache

scenarios("../features/cli_caches.feature")


@pytest.fixture
def ctx() -> dict[str, Any]:
    """Per-scenario context — captured stdout + the return code."""
    # Start with every cache cleared so the report doesn't ride on
    # state from a previous scenario.
    reset_brief_output_cache()
    reset_prep_summary_cache()
    reset_brief_source_cache()
    reset_health_probe_cache()
    return {"stdout": "", "rc": 0}


@given("the kairix process is freshly started")
def _given_fresh_process(ctx: dict[str, Any]) -> None:
    # The fixture already resets every cache. Nothing else to set up;
    # the caches are constructed lazily on first ``stats()`` call.
    _ = ctx


@when("the operator runs the caches command")
def _when_caches_command(ctx: dict[str, Any]) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx["rc"] = caches_main([])
    ctx["stdout"] = buf.getvalue()


@when("the operator runs the caches command with the json flag")
def _when_caches_command_json(ctx: dict[str, Any]) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx["rc"] = caches_main(["--json"])
    ctx["stdout"] = buf.getvalue()


@then("the report lists every cache by name")
def _then_lists_every_cache(ctx: dict[str, Any]) -> None:
    out = ctx["stdout"]
    for name in (
        "query_result_cache",
        "prep_summary_cache",
        "brief_output_cache",
        "brief_source_cache",
        "health_probe_cache",
    ):
        assert name in out, f"expected cache name {name!r} in report; missing.\n{out}"


@then(parsers.parse("each cache row shows size, hits, misses, evictions, and hit_rate percent"))
def _then_row_columns(ctx: dict[str, Any]) -> None:
    out = ctx["stdout"]
    for header in ("size", "hits", "misses", "evictions", "hit_rate"):
        assert header in out.lower(), f"expected column header {header!r} in text report"


@then("stdout is a valid JSON object with a caches array")
def _then_json_envelope(ctx: dict[str, Any]) -> None:
    envelope = json.loads(ctx["stdout"])
    assert isinstance(envelope, dict)
    assert "caches" in envelope
    assert isinstance(envelope["caches"], list)
    assert envelope["caches"], "caches array must be non-empty"
