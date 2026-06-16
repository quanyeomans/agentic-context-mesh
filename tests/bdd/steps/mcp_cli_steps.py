"""Step definitions for mcp_cli.feature.

Drives ``kairix.agents.mcp.cli.main`` and captures stdout / stderr / exit
code. ``serve`` actually starts a server (blocks), so the BDD covers only
the surface contracts: --help, no-subcommand, argparse rejection of bad
transport. The serve runtime path is exercised by integration tests.
"""

from __future__ import annotations

import io
import logging
import shlex
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

_TRANSPORTS = ("stdio", "http", "sse")

# F5 keeps tests off internal ``_x`` imports; these are the PUBLIC env-var
# names (the operator-facing contract). Production single-sources them from
# onboard.check's ``_CANONICAL_SECRETS`` (F85).
_LLM_API_KEY_VAR = "KAIRIX_PROVIDER_LLM_API_KEY"  # pragma: allowlist secret — env-var NAME, not a credential
_LLM_ENDPOINT_VAR = "KAIRIX_PROVIDER_LLM_ENDPOINT"

# A real-looking credential value used to prove the F15 contract in the
# placeholder-warning scenario: it must never appear in the warning text.
_PLACEHOLDER_KEY_VALUE = "your-api-key-here"
_REAL_ENDPOINT = "https://prod.openai.azure.com"
_STARTUP_LOGGER_NAME = "kairix.mcp.startup"


@dataclass
class _McpCliCtx:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    serve_env: dict[str, str] = field(default_factory=dict)
    warnings: str = ""
    server_built: bool = False


@pytest.fixture
def mcp_cli_ctx() -> _McpCliCtx:
    return _McpCliCtx()


def _run_mcp(mcp_cli_ctx: _McpCliCtx, args: list[str]) -> None:
    from kairix.agents.mcp.cli import main as mcp_main

    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            mcp_main(args)
        mcp_cli_ctx.exit_code = 0
    except SystemExit as e:  # NOSONAR — BDD test captures CLI exit code; reraising would defeat the test
        mcp_cli_ctx.exit_code = int(e.code) if e.code is not None else 0
    mcp_cli_ctx.stdout = out.getvalue()
    mcp_cli_ctx.stderr = err.getvalue()


@when(parsers.parse("the operator runs the mcp CLI with `{argv}`"))
def _run_mcp_argv(mcp_cli_ctx: _McpCliCtx, argv: str) -> None:
    _run_mcp(mcp_cli_ctx, shlex.split(argv))


@when("the operator runs the mcp CLI with no arguments")
def _run_mcp_no_args(mcp_cli_ctx: _McpCliCtx) -> None:
    _run_mcp(mcp_cli_ctx, [])


@then(parsers.parse("the mcp CLI exits with status {code:d}"))
def _assert_mcp_exit(mcp_cli_ctx: _McpCliCtx, code: int) -> None:
    assert mcp_cli_ctx.exit_code == code, (
        f"expected exit {code}, got {mcp_cli_ctx.exit_code}; "
        f"stdout={mcp_cli_ctx.stdout[:200]!r} stderr={mcp_cli_ctx.stderr[:200]!r}"
    )


@then("the help output names the serve subcommand")
def _assert_help_names_serve(mcp_cli_ctx: _McpCliCtx) -> None:
    out = mcp_cli_ctx.stdout + mcp_cli_ctx.stderr
    assert "serve" in out, f"missing 'serve' in output:\n{out}"


@then("the help output names every transport choice")
def _assert_help_names_transports(mcp_cli_ctx: _McpCliCtx) -> None:
    out = mcp_cli_ctx.stdout + mcp_cli_ctx.stderr
    for transport in _TRANSPORTS:
        assert transport in out, f"transport {transport!r} missing from --help output:\n{out}"


@then("the output names the serve subcommand")
def _assert_output_names_serve(mcp_cli_ctx: _McpCliCtx) -> None:
    out = mcp_cli_ctx.stdout + mcp_cli_ctx.stderr
    assert "serve" in out, f"missing 'serve' in output:\n{out}"


@then("stderr names the bad transport")
def _assert_stderr_names_bad_transport(mcp_cli_ctx: _McpCliCtx) -> None:
    assert "not-a-transport" in mcp_cli_ctx.stderr, f"stderr did not name the bad transport: {mcp_cli_ctx.stderr!r}"


# ---------------------------------------------------------------------------
# #449 — boot-time credential preflight (warn-and-degrade vs fatal)
# ---------------------------------------------------------------------------


class _FakeServeServer:
    """Minimal FakeMcp server: records run() so serve doesn't block a BDD run."""

    def run(self, *, transport: str) -> None:
        """Record the stdio serve call; never blocks (transport arg unused)."""
        _ = transport


@given("the LLM api key is still the placeholder value")
def _given_placeholder_llm_key(mcp_cli_ctx: _McpCliCtx) -> None:
    mcp_cli_ctx.serve_env = {
        _LLM_API_KEY_VAR: _PLACEHOLDER_KEY_VALUE,
        _LLM_ENDPOINT_VAR: _REAL_ENDPOINT,
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "kairix-local-dev",  # pragma: allowlist secret — fake fixture
    }


@given("the neo4j password is empty but a neo4j URI is configured")
def _given_empty_neo4j_password(mcp_cli_ctx: _McpCliCtx) -> None:
    mcp_cli_ctx.serve_env = {
        _LLM_API_KEY_VAR: "sk-live-bdd-real-key",  # pragma: allowlist secret — fake fixture
        _LLM_ENDPOINT_VAR: _REAL_ENDPOINT,
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "",
    }


@when("the operator starts the mcp server")
def _when_start_mcp_server(mcp_cli_ctx: _McpCliCtx, caplog: pytest.LogCaptureFixture) -> None:
    from kairix.agents.mcp.cli import McpCliDeps
    from kairix.agents.mcp.cli import main as mcp_main

    build_calls: list[dict[str, Any]] = []

    def _fake_build_server(**kwargs: Any) -> _FakeServeServer:
        build_calls.append(kwargs)
        return _FakeServeServer()

    deps = McpCliDeps(
        build_server_factory=lambda: _fake_build_server,
        serve_env=mcp_cli_ctx.serve_env,
    )

    out = io.StringIO()
    err = io.StringIO()
    try:
        with caplog.at_level(logging.WARNING, logger=_STARTUP_LOGGER_NAME), redirect_stdout(out), redirect_stderr(err):
            mcp_main(["serve", "--transport", "stdio"], deps=deps)
        mcp_cli_ctx.exit_code = 0
    except SystemExit as e:  # NOSONAR — BDD captures the CLI exit code; reraising defeats the test
        mcp_cli_ctx.exit_code = int(e.code) if e.code is not None else 0
    mcp_cli_ctx.stdout = out.getvalue()
    mcp_cli_ctx.stderr = err.getvalue()
    mcp_cli_ctx.warnings = "\n".join(rec.getMessage() for rec in caplog.records)
    mcp_cli_ctx.server_built = bool(build_calls)


@then("the mcp server starts without exiting")
def _assert_server_starts(mcp_cli_ctx: _McpCliCtx) -> None:
    assert mcp_cli_ctx.exit_code == 0, f"serve exited with {mcp_cli_ctx.exit_code}; stderr={mcp_cli_ctx.stderr!r}"
    assert mcp_cli_ctx.server_built, "the server must still be built (warn-and-degrade, not exit)"


@then("a warning names the LLM api key variable")
def _assert_warning_names_llm_var(mcp_cli_ctx: _McpCliCtx) -> None:
    assert _LLM_API_KEY_VAR in mcp_cli_ctx.warnings, (
        f"warning did not name the LLM api key var: {mcp_cli_ctx.warnings!r}"
    )
    assert any(marker in mcp_cli_ctx.warnings for marker in ("fix:", "next:", "run:")), (
        f"warning missing an action marker: {mcp_cli_ctx.warnings!r}"
    )


@then("the warning never prints the credential value")
def _assert_warning_hides_value(mcp_cli_ctx: _McpCliCtx) -> None:
    assert _PLACEHOLDER_KEY_VALUE not in mcp_cli_ctx.warnings, (
        f"credential VALUE leaked into the warning: {mcp_cli_ctx.warnings!r}"
    )


@then("stderr names the neo4j password variable")
def _assert_stderr_names_neo4j(mcp_cli_ctx: _McpCliCtx) -> None:
    assert "KAIRIX_NEO4J_PASSWORD" in mcp_cli_ctx.stderr, (
        f"stderr did not name the neo4j password var: {mcp_cli_ctx.stderr!r}"
    )
