"""Step definitions for setup_cli.feature.

Drives ``kairix.platform.setup.cli.main`` and captures stdout, stderr,
and exit code. The non-interactive JSON scenario relies on the wizard's
documented contract that --non-interactive plus --preset plus --path
provides every input the wizard needs (no prompts, no live LLM check).
"""

from __future__ import annotations

import io
import json
import shlex
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

_DOCUMENTED_FLAGS = (
    "--output",
    "--non-interactive",
    "--json",
    "--preset",
    "--path",
)


@dataclass
class _SetupCliCtx:
    state_path: Path
    document_root: Path | None = None
    config_path: Path | None = None
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    json_output: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def setup_cli_ctx(tmp_path: Path) -> _SetupCliCtx:
    return _SetupCliCtx(state_path=tmp_path / ".setup-state.json")


@given("a temporary document root with one markdown file")
def _given_doc_root(setup_cli_ctx: _SetupCliCtx, tmp_path: Path) -> None:
    docroot = tmp_path / "docs"
    docroot.mkdir()
    (docroot / "hello.md").write_text("# Hello\n")
    setup_cli_ctx.document_root = docroot


@given("a config file that already lists a connected source")
def _given_config_with_connected_source(setup_cli_ctx: _SetupCliCtx, tmp_path: Path) -> None:
    import yaml

    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "topology": {"connectors": [{"type": "slack", "instance": "agent-alpha-workspace"}]},
                "agents": {"agent-alpha": {"role": "research"}},
            }
        ),
        encoding="utf-8",
    )
    setup_cli_ctx.config_path = config_path


def _run_setup(setup_cli_ctx: _SetupCliCtx, args: list[str], *, ctx: Any = None, deps: Any = None) -> None:
    from kairix.platform.setup.cli import main as setup_main
    from kairix.platform.setup.prompts import SetupContext

    # Construct a deterministic SetupContext directly so the wizard
    # never reads $XDG_CONFIG_HOME, $CI, or sys.stdout.isatty(). Mirrors
    # how prod main() builds it from --non-interactive / --json, but
    # without env-var I/O.
    if ctx is None:
        ctx = SetupContext(
            interactive=False,
            json_mode="--json" in args,
            state_path=setup_cli_ctx.state_path,
        )

    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            setup_main(args, ctx=ctx, deps=deps)
        setup_cli_ctx.exit_code = 0
    except SystemExit as e:  # NOSONAR — BDD test captures CLI exit code; reraising would defeat the test
        setup_cli_ctx.exit_code = int(e.code) if e.code is not None else 0
    setup_cli_ctx.stdout = out.getvalue()
    setup_cli_ctx.stderr = err.getvalue()
    if "--json" in args:
        try:
            setup_cli_ctx.json_output = json.loads(setup_cli_ctx.stdout)
        except json.JSONDecodeError:
            setup_cli_ctx.json_output = {}


@when(parsers.parse("the operator runs the setup CLI with `{argv}`"))
def _run_setup_argv(setup_cli_ctx: _SetupCliCtx, argv: str) -> None:
    if setup_cli_ctx.document_root is not None:
        argv = argv.replace("TMP", str(setup_cli_ctx.document_root))
    _run_setup(setup_cli_ctx, shlex.split(argv))


@when("the operator completes terminal setup and chooses to index now")
def _run_setup_interactive_with_indexing(
    setup_cli_ctx: _SetupCliCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the full interactive flow through the CLI surface, answering
    yes to indexing — the step that used to crash the wizard."""
    from kairix.platform.setup.prompts import SetupContext
    from kairix.platform.setup.wizard import WizardDeps
    from tests.fakes import FakeSetupService

    answers = iter(
        [
            "1",  # use case
            "1",  # provider pick
            "https://res.services.ai.azure.com",  # endpoint
            "fake-key-for-tests",  # API key
            "",  # embed model default
            "",  # chat model default
            "1",  # storage: default location
            "n",  # no knowledge graph
            "1",  # search everything
            "y",  # start indexing now
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers, ""))

    deps = WizardDeps(
        setup_service=lambda: FakeSetupService(),
        persist_credentials=lambda *_a: tmp_path / "kairix.env",
        provider_names=lambda: ("azure_foundry", "openai"),
        index_poll_seconds=0.0,
    )
    ctx = SetupContext(interactive=True, json_mode=False, state_path=setup_cli_ctx.state_path)
    args = ["--output", str(tmp_path / "kairix.config.yaml"), "--path", str(setup_cli_ctx.document_root)]
    _run_setup(setup_cli_ctx, args, ctx=ctx, deps=deps)


@when("the operator re-runs the setup CLI against that config file")
def _rerun_setup_against_config(setup_cli_ctx: _SetupCliCtx) -> None:
    assert setup_cli_ctx.config_path is not None, "feature is missing the config-file Given step"
    args = [
        "--non-interactive",
        "--preset",
        "general",
        "--path",
        str(setup_cli_ctx.document_root),
        "--output",
        str(setup_cli_ctx.config_path),
    ]
    _run_setup(setup_cli_ctx, args)


@then(parsers.parse("the setup CLI exits with status {code:d}"))
def _assert_setup_exit(setup_cli_ctx: _SetupCliCtx, code: int) -> None:
    assert setup_cli_ctx.exit_code == code, (
        f"expected exit {code}, got {setup_cli_ctx.exit_code}; "
        f"stdout={setup_cli_ctx.stdout[:200]!r} stderr={setup_cli_ctx.stderr[:200]!r}"
    )


@then("the help output names every documented flag")
def _assert_help_lists_flags(setup_cli_ctx: _SetupCliCtx) -> None:
    out = setup_cli_ctx.stdout + setup_cli_ctx.stderr
    for flag in _DOCUMENTED_FLAGS:
        assert flag in out, f"flag {flag!r} missing from --help output:\n{out}"


@then("stderr names the bad preset")
def _assert_stderr_names_bad_preset(setup_cli_ctx: _SetupCliCtx) -> None:
    assert "not-a-preset" in setup_cli_ctx.stderr, f"stderr did not name bad preset: {setup_cli_ctx.stderr!r}"


@then("the setup CLI stdout is parseable JSON")
def _assert_setup_json_parseable(setup_cli_ctx: _SetupCliCtx) -> None:
    assert setup_cli_ctx.json_output, (
        f"stdout was not parseable JSON; got {setup_cli_ctx.stdout[:500]!r} stderr={setup_cli_ctx.stderr[:200]!r}"
    )


@then('the JSON config "paths.document_root" matches the supplied path')
def _assert_paths_document_root(setup_cli_ctx: _SetupCliCtx) -> None:
    assert "paths" in setup_cli_ctx.json_output, f"missing 'paths' in JSON: {setup_cli_ctx.json_output}"
    paths = setup_cli_ctx.json_output["paths"]
    assert isinstance(paths, dict), f"'paths' must be an object, got {type(paths).__name__}"
    assert "document_root" in paths, f"missing 'document_root' in paths: {paths}"
    assert setup_cli_ctx.document_root is not None, (
        "fixture didn't set document_root — feature is missing the Given step"
    )
    assert paths["document_root"] == str(setup_cli_ctx.document_root), (
        f"expected paths.document_root={str(setup_cli_ctx.document_root)!r}; got {paths['document_root']!r}"
    )


@then(parsers.parse('the JSON config "{section}" section is a non-empty object'))
def _assert_section_non_empty_object(setup_cli_ctx: _SetupCliCtx, section: str) -> None:
    assert section in setup_cli_ctx.json_output, f"missing {section!r}: {setup_cli_ctx.json_output}"
    value = setup_cli_ctx.json_output[section]
    assert isinstance(value, dict), f"{section!r} must be an object, got {type(value).__name__}"
    assert value, f"{section!r} must be non-empty, got {value!r}"


@then("the JSON config names the chosen provider plugin")
def _assert_provider_plugin_named(setup_cli_ctx: _SetupCliCtx) -> None:
    """#474 defect 1: a provider-less config fails at factory
    construction — every emitted config must carry the provider field.
    Non-interactive defaults select Azure with no legacy-shaped
    endpoint, which maps to the azure_foundry plugin."""
    provider = setup_cli_ctx.json_output.get("provider")
    assert provider == "azure_foundry", (
        f"expected provider 'azure_foundry'; got {provider!r} in {setup_cli_ctx.json_output}"
    )


@then("the setup output reports that the index was built")
def _assert_index_built_reported(setup_cli_ctx: _SetupCliCtx) -> None:
    assert "Index built" in setup_cli_ctx.stdout, f"indexing outcome missing from setup output:\n{setup_cli_ctx.stdout}"
    assert "Setup complete" in setup_cli_ctx.stdout, f"setup did not finish after indexing:\n{setup_cli_ctx.stdout}"


@then("the config file still lists the connected source")
def _assert_connected_source_survives(setup_cli_ctx: _SetupCliCtx) -> None:
    import yaml

    assert setup_cli_ctx.config_path is not None
    config = yaml.safe_load(setup_cli_ctx.config_path.read_text(encoding="utf-8"))
    assert config.get("topology") == {"connectors": [{"type": "slack", "instance": "agent-alpha-workspace"}]}, (
        f"the connected source was clobbered by the re-run: {config}"
    )
    assert config.get("agents") == {"agent-alpha": {"role": "research"}}, f"agents block clobbered: {config}"
    # The re-run's own answers landed alongside, proving a merge happened.
    assert config.get("provider"), f"the re-run wrote nothing: {config}"
