"""Step definitions for ``secrets_set.feature``.

Drives ``kairix.secrets.cli.main`` through its public ``bundle_path`` +
``value_reader`` DI seams (F46: step impls invoke the CLI entry point).
F2-clean: the target bundle is a tmp_path file passed through the seam —
no env-var manipulation. F15: the fixture value is a generic example
string and the assertions prove it never reaches stdout/stderr.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.secrets.cli import main as secrets_main

pytestmark = pytest.mark.bdd

_EXAMPLE_VALUE = "example-credential-value"  # pragma: allowlist secret — generic fixture, not a real key


@dataclass
class _SecretsSetCtx:
    bundle: Path
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    examples: tuple[str, ...] = field(
        default=("kairix-provider-llm-api-key", "kairix-connector-github-pat"),
    )


@pytest.fixture
def secrets_set_ctx(tmp_path: Path) -> _SecretsSetCtx:
    """Per-scenario state: a bundle path under tmp_path (not yet written)."""
    return _SecretsSetCtx(bundle=tmp_path / "kairix.env")


# ── givens ─────────────────────────────────────────────────────────


@given("an empty operator secrets bundle")
def _empty_bundle(secrets_set_ctx: _SecretsSetCtx) -> None:
    assert not secrets_set_ctx.bundle.exists()


# ── whens ──────────────────────────────────────────────────────────


@when(parsers.parse('the operator stores a value under "{name}"'))
def _store_value(secrets_set_ctx: _SecretsSetCtx, name: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = secrets_main(
            ["set", name],
            bundle_path=secrets_set_ctx.bundle,
            value_reader=lambda: _EXAMPLE_VALUE,
        )
    secrets_set_ctx.stdout = out.getvalue()
    secrets_set_ctx.stderr = err.getvalue()
    secrets_set_ctx.exit_code = rc if rc is not None else 0


# ── thens ──────────────────────────────────────────────────────────


@then("the secrets set output names the credential and the bundle file")
def _output_names_credential_and_path(secrets_set_ctx: _SecretsSetCtx) -> None:
    assert "kairix-provider-llm-api-key" in secrets_set_ctx.stdout, secrets_set_ctx.stdout
    assert str(secrets_set_ctx.bundle) in secrets_set_ctx.stdout, secrets_set_ctx.stdout


@then("the stored value is saved in the bundle file")
def _value_in_bundle(secrets_set_ctx: _SecretsSetCtx) -> None:
    content = secrets_set_ctx.bundle.read_text(encoding="utf-8")
    assert f"KAIRIX_PROVIDER_LLM_API_KEY={_EXAMPLE_VALUE}" in content


@then("the secrets set output does not contain the stored value")
def _value_not_in_output(secrets_set_ctx: _SecretsSetCtx) -> None:
    combined = secrets_set_ctx.stdout + secrets_set_ctx.stderr
    assert _EXAMPLE_VALUE not in combined, f"secret value leaked into output:\n{combined}"


@then("the secrets set output suggests two canonical example names")
def _output_suggests_examples(secrets_set_ctx: _SecretsSetCtx) -> None:
    combined = secrets_set_ctx.stdout + secrets_set_ctx.stderr
    for example in secrets_set_ctx.examples:
        assert example in combined, f"example {example!r} missing from:\n{combined}"


@then("nothing is written to the bundle file")
def _bundle_untouched(secrets_set_ctx: _SecretsSetCtx) -> None:
    assert not secrets_set_ctx.bundle.exists()


@then(parsers.parse("the secrets set command exits with code {code:d}"))
def _exits_with(secrets_set_ctx: _SecretsSetCtx, code: int) -> None:
    assert secrets_set_ctx.exit_code == code, (
        f"expected exit {code}; got {secrets_set_ctx.exit_code} "
        f"(stdout={secrets_set_ctx.stdout!r} stderr={secrets_set_ctx.stderr!r})"
    )
