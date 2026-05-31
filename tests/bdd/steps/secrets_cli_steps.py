"""Step definitions for ``secrets_cli.feature``.

Drives ``kairix.secrets.cli.main`` through its public ``loader_factory``
+ ``aliases_provider`` + ``env_provider`` DI seams. F46-compliant: the
step impls invoke the CLI ``main`` entry point. F2-clean: no env-var
manipulation — a :class:`FakeSecretsLoader` is constructed with the
values the scenario needs.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.secrets.cli import main as secrets_main
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.bdd


# Three-alias subset is enough to exercise present / missing / TSV
# without coupling the scenarios to the full LEGACY_ALIASES map.
_TEST_ALIASES = (
    ("connector", "m365", None, "tenant-id"),
    ("connector", "m365", None, "client-secret"),
    ("provider", "llm", None, "api-key"),
)


@pytest.fixture
def _secrets_state() -> dict[str, Any]:
    """Per-scenario state container — loader, env, and capture buffers."""
    return {
        "loader": None,
        "env": {},
        "stdout": "",
        "exit_code": -1,
    }


def _run_secrets(state: dict[str, Any], argv: list[str]) -> None:
    """Invoke ``kairix.secrets.cli.main`` and capture stdout + exit code."""
    buf = io.StringIO()
    kwargs: dict[str, Any] = {}
    if state.get("loader") is not None:
        kwargs["loader_factory"] = lambda: state["loader"]
    if state.get("env") is not None:
        kwargs["env_provider"] = lambda: dict(state["env"])
    # Only thread the alias provider for verify scenarios — migrate-list
    # doesn't take one and ignores the kwarg, but keep the seam consistent.
    if argv[0] == "verify":
        kwargs["aliases_provider"] = lambda: _TEST_ALIASES

    with redirect_stdout(buf):
        rc = secrets_main(argv, **kwargs)
    state["stdout"] = buf.getvalue()
    state["exit_code"] = rc if rc is not None else 0


# ── givens ─────────────────────────────────────────────────────────


@given("the kairix secrets loader resolves every registered alias")
def _loader_with_all_values(_secrets_state: dict[str, Any]) -> None:
    _secrets_state["loader"] = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "tenant-1",
            ("connector", "m365", None, "client-secret"): "secret-1",
            ("provider", "llm", None, "api-key"): "key-1",
        },
    )
    _secrets_state["env"] = {
        "KAIRIX_CONNECTOR_M365_TENANT_ID": "tenant-1",
        "KAIRIX_CONNECTOR_M365_CLIENT_SECRET": "secret-1",  # pragma: allowlist secret
        "KAIRIX_PROVIDER_LLM_API_KEY": "key-1",  # pragma: allowlist secret
    }


@given("the kairix secrets loader is missing one required alias")
def _loader_with_one_missing(_secrets_state: dict[str, Any]) -> None:
    _secrets_state["loader"] = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "tenant-1",
            ("connector", "m365", None, "client-secret"): "secret-1",
            # api-key intentionally absent
        },
    )
    _secrets_state["env"] = {
        "KAIRIX_CONNECTOR_M365_TENANT_ID": "tenant-1",  # pragma: allowlist secret
        "KAIRIX_CONNECTOR_M365_CLIENT_SECRET": "secret-1",  # pragma: allowlist secret
    }


# ── whens ──────────────────────────────────────────────────────────


@when("the operator runs the kairix secrets verify command")
def _operator_runs_verify(_secrets_state: dict[str, Any]) -> None:
    _run_secrets(_secrets_state, ["verify"])


@when("the operator runs the kairix secrets migrate-list command")
def _operator_runs_migrate_list(_secrets_state: dict[str, Any]) -> None:
    _run_secrets(_secrets_state, ["migrate-list"])


# ── thens ──────────────────────────────────────────────────────────


@then("the kairix secrets stdout marks every row as present")
def _stdout_all_present(_secrets_state: dict[str, Any]) -> None:
    stdout = _secrets_state["stdout"]
    assert "MISSING" not in stdout, f"unexpected MISSING row in:\n{stdout}"
    # Three test aliases → exactly three 'present' rows.
    assert stdout.count("present") >= 3, f"expected 3+ present rows; got:\n{stdout}"


@then("the kairix secrets stdout marks one row as MISSING")
def _stdout_one_missing(_secrets_state: dict[str, Any]) -> None:
    stdout = _secrets_state["stdout"]
    assert stdout.count("MISSING") == 1, f"expected exactly one MISSING row; got:\n{stdout}"


@then("the kairix secrets stdout includes the TSV header")
def _stdout_has_tsv_header(_secrets_state: dict[str, Any]) -> None:
    assert "LEGACY_ENV_VAR\tCANONICAL_KV_NAME" in _secrets_state["stdout"]


@then("the kairix secrets stdout includes at least one mapping row")
def _stdout_has_mapping_row(_secrets_state: dict[str, Any]) -> None:
    lines = _secrets_state["stdout"].strip().split("\n")
    assert len(lines) > 1, "expected at least one data row after the TSV header"


@then("the kairix secrets command exits with code 0")
def _command_exits_zero(_secrets_state: dict[str, Any]) -> None:
    assert _secrets_state["exit_code"] == 0, f"expected exit 0; got {_secrets_state['exit_code']}"


@then("the kairix secrets command exits with code 1")
def _command_exits_one(_secrets_state: dict[str, Any]) -> None:
    assert _secrets_state["exit_code"] == 1, f"expected exit 1; got {_secrets_state['exit_code']}"
