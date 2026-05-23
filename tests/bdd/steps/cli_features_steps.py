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
def _registry_is_empty(_features_state: dict[str, Any]) -> None:
    """Pin the scenario to an empty-registry view via the CLI's
    ``status_provider`` DI seam.

    The CLI's ``main`` accepts a ``status_provider`` kwarg specifically
    so tests can substitute the snapshot without touching the global
    REGISTRY. Storing the empty-tuple provider in the per-scenario
    state lets the @when steps thread it through unchanged.
    """
    _features_state["status_provider"] = lambda: ()


def _run_features_status(state: dict[str, Any], *, json_mode: bool) -> None:
    """Invoke ``kairix features status`` and capture stdout + exit code.

    Wraps the call so both scenarios share one path — the JSON-mode
    scenario only differs in the argv it passes. ``state[status_provider]``
    is the DI seam set by the @given that pins the registry view.
    """
    argv = ["status", "--json"] if json_mode else ["status"]
    buf = io.StringIO()
    provider = state.get("status_provider")
    kwargs = {"status_provider": provider} if provider is not None else {}
    with redirect_stdout(buf):
        exit_code = features_main(argv, **kwargs)
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
