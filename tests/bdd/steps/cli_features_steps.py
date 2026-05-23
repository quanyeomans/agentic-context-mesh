"""Step definitions for cli_features.feature.

Drives the ``kairix features`` CLI subcommand through its public
adapter ``kairix.core.features.cli.main``. The empty-registry state is
the natural PR-2 fixture — no flags are declared yet, so the resolver
returns an empty tuple and the CLI renders the "no flags" friendly
line / empty JSON ``flags`` list.

F1-clean: no @patch on kairix internals. F2-clean: no env-var
manipulation. F4-clean: paths.py owns env-var reads. F46-compliant:
the step impls invoke the CLI ``main`` entry point.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.core.features.cli import main as features_main

pytestmark = pytest.mark.bdd


@pytest.fixture
def _features_state() -> dict[str, Any]:
    """Per-scenario fresh state container."""
    return {
        "stdout": "",
        "exit_code": -1,
    }


@given("the kairix features registry is empty")
def _registry_is_empty() -> None:
    """No-op — the registry is empty at PR-2 landing.

    Future PRs that add flags will need a setup hook here that captures
    and restores the registry's snapshot per scenario. For now the
    assertion is implicit: ``REGISTRY`` is the empty dict on import.
    """


def _run_features_status(state: dict[str, Any], *, json_mode: bool) -> None:
    """Invoke ``kairix features status`` and capture stdout + exit code.

    Wraps the call so both scenarios share one path — the JSON-mode
    scenario only differs in the argv it passes.
    """
    argv = ["status", "--json"] if json_mode else ["status"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = features_main(argv)
    state["stdout"] = buf.getvalue()
    state["exit_code"] = exit_code if exit_code is not None else 0


@when("the operator runs the kairix features status command")
def _operator_runs_status(_features_state: dict[str, Any]) -> None:
    _run_features_status(_features_state, json_mode=False)


@when("the operator runs the kairix features status command with the --json flag")
def _operator_runs_status_json(_features_state: dict[str, Any]) -> None:
    _run_features_status(_features_state, json_mode=True)


@then("the kairix features stdout reports no feature flags registered")
def _stdout_reports_no_flags(_features_state: dict[str, Any]) -> None:
    assert "No feature flags registered" in _features_state["stdout"], (
        f"expected friendly empty-registry message; got: {_features_state['stdout']!r}"
    )


@then("the kairix features stdout parses as JSON with a flags key")
def _stdout_parses_as_json(_features_state: dict[str, Any]) -> None:
    parsed = json.loads(_features_state["stdout"])
    assert "flags" in parsed, f"expected 'flags' key in JSON envelope; got keys: {list(parsed)}"
    assert isinstance(parsed["flags"], list), f"expected 'flags' to be a list; got {type(parsed['flags'])}"


@then("the kairix features command exits with code 0")
def _command_exits_zero(_features_state: dict[str, Any]) -> None:
    assert _features_state["exit_code"] == 0, f"expected exit 0; got {_features_state['exit_code']}"
