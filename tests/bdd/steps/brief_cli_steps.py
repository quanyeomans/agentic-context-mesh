"""Step definitions for brief_cli.feature.

Drives ``kairix.agents.briefing.cli.main`` and captures stdout, stderr,
and exit code. The full briefing pipeline (LLM synthesis + memory
fetch) is out of scope for BDD — these scenarios cover the
operator-visible CLI surface: any configured agent is briefable
(PLA-265), an agent with no surface is rejected with an actionable
error, and a missing argument is a usage error.

Composition (F46): steps drive the CLI ``main`` entry point with
``deps=BriefDeps(...)`` injected — the F2-clean config seam carries the
``agents:`` block so no real ``kairix.config.yaml`` is written and no
LLM is called.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.briefing.cli import main as brief_main
from kairix.core.health import HealthDeps
from kairix.use_cases.brief import BriefDeps


@dataclass
class _BriefCliCtx:
    config: dict[str, object] = field(default_factory=dict)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@pytest.fixture
def brief_cli_ctx() -> _BriefCliCtx:
    # The process-shared brief output + health-probe caches are reset
    # between tests by the autouse ``_reset_workstream_b_caches`` fixture
    # in tests/conftest.py, so each scenario starts from a cold cache.
    return _BriefCliCtx()


def _healthy_health_deps() -> HealthDeps:
    """Probes report all-green so a configured agent proceeds to generate."""
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def _surface_config(agent: str) -> dict[str, object]:
    return {"agents": {agent: {"surfaces": [{"path": f"memory/{agent}", "label": "memory"}]}}}


def _no_surface_config(agent: str) -> dict[str, object]:
    return {"agents": {agent: {"surfaces": []}}}


def _capture_main(brief_cli_ctx: _BriefCliCtx, args: list[str], *, deps: BriefDeps | None = None) -> None:
    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            brief_main(args, deps=deps)
        brief_cli_ctx.exit_code = 0
    except SystemExit as e:  # NOSONAR — BDD test captures CLI exit code; reraising would defeat the test
        brief_cli_ctx.exit_code = int(e.code) if e.code is not None else 0
    brief_cli_ctx.stdout = out.getvalue()
    brief_cli_ctx.stderr = err.getvalue()


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('the brief CLI has agent "{agent}" configured with a memory surface'))
def _given_agent_with_surface(brief_cli_ctx: _BriefCliCtx, agent: str) -> None:
    brief_cli_ctx.config = _surface_config(agent)


@given(parsers.parse('the brief CLI has agent "{agent}" configured with no surfaces'))
def _given_agent_without_surface(brief_cli_ctx: _BriefCliCtx, agent: str) -> None:
    brief_cli_ctx.config = _no_surface_config(agent)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('the operator briefs the configured agent "{agent}"'))
def _when_brief_configured_agent(brief_cli_ctx: _BriefCliCtx, agent: str) -> None:
    deps = BriefDeps(
        # F19: ``_``-prefixed unused kwargs allowed in lambdas matching the seam shape.
        generate_fn=lambda _agent, **_kw: "configured-agent briefing body",
        briefing_dir_fn=lambda: Path("brief-out"),
        config_fn=lambda: brief_cli_ctx.config,
        health_deps=_healthy_health_deps(),
    )
    _capture_main(brief_cli_ctx, [agent], deps=deps)


@when("the operator runs the brief CLI with no arguments")
def _when_brief_no_args(brief_cli_ctx: _BriefCliCtx) -> None:
    _capture_main(brief_cli_ctx, [])


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the brief CLI exits with status {code:d}"))
def _assert_brief_exit(brief_cli_ctx: _BriefCliCtx, code: int) -> None:
    assert brief_cli_ctx.exit_code == code, (
        f"expected exit {code}, got {brief_cli_ctx.exit_code}; "
        f"stdout={brief_cli_ctx.stdout[:200]!r} stderr={brief_cli_ctx.stderr[:200]!r}"
    )


@then("stderr does not report an invalid agent")
def _assert_no_invalid_agent(brief_cli_ctx: _BriefCliCtx) -> None:
    assert "InvalidAgent" not in brief_cli_ctx.stderr, (
        f"a configured agent was wrongly rejected: {brief_cli_ctx.stderr!r}"
    )


@then(parsers.parse('stderr names the rejected agent "{agent}"'))
def _assert_stderr_names_agent(brief_cli_ctx: _BriefCliCtx, agent: str) -> None:
    assert agent in brief_cli_ctx.stderr, f"stderr did not name the rejected agent {agent!r}: {brief_cli_ctx.stderr!r}"


@then("stderr explains how to configure the agent")
def _assert_stderr_has_affordance(brief_cli_ctx: _BriefCliCtx) -> None:
    assert "kairix onboard agent" in brief_cli_ctx.stderr, (
        f"stderr missing the onboarding affordance: {brief_cli_ctx.stderr!r}"
    )
