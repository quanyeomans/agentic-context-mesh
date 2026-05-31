"""Outcome tests for ``kairix secrets {verify, migrate-list}``.

F30-compliant: asserts on stdout / stderr / exit-code envelope, not
just returncode. F2-clean: passes a :class:`FakeSecretsLoader` through
the CLI's ``loader_factory`` DI seam — no ``monkeypatch.setenv``.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from kairix.secrets.cli import main as secrets_main
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.unit


def _aliases_for_test() -> tuple[tuple[str, str, str | None, str], ...]:
    """Tiny alias set so the verify table stays inspectable in tests."""
    return (
        ("connector", "m365", None, "tenant-id"),
        ("connector", "m365", None, "client-secret"),
        ("provider", "llm", None, "api-key"),
    )


def _capture(argv: list[str], **kwargs) -> tuple[str, int]:
    """Run secrets_main(argv, ...) and capture (stdout, exit_code)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = secrets_main(argv, **kwargs)
    return buf.getvalue(), rc if rc is not None else 0


# ── verify: all present ────────────────────────────────────────────


def test_verify_all_present_exits_zero() -> None:
    """Every alias resolves via the fake loader → table renders + rc=0."""
    fake = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "tenant-1",
            ("connector", "m365", None, "client-secret"): "secret-1",
            ("provider", "llm", None, "api-key"): "key-1",
        },
    )
    stdout, rc = _capture(
        ["verify"],
        loader_factory=lambda: fake,
        aliases_provider=_aliases_for_test,
        env_provider=lambda: {
            "KAIRIX_CONNECTOR_M365_TENANT_ID": "x",
            "KAIRIX_CONNECTOR_M365_CLIENT_SECRET": "x",  # pragma: allowlist secret
            "KAIRIX_PROVIDER_LLM_API_KEY": "x",
        },
    )
    assert rc == 0
    assert "present" in stdout
    assert "kairix-connector-m365-tenant-id" in stdout
    assert "kairix-provider-llm-api-key" in stdout
    assert "MISSING" not in stdout


def test_verify_missing_exits_nonzero_and_marks_row() -> None:
    """A missing secret marks the row MISSING and returns exit 1."""
    fake = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "tenant-1",
            # client-secret + llm api-key absent
        },
    )
    stdout, rc = _capture(
        ["verify"],
        loader_factory=lambda: fake,
        aliases_provider=_aliases_for_test,
        env_provider=lambda: {"KAIRIX_CONNECTOR_M365_TENANT_ID": "x"},
    )
    assert rc == 1
    assert "MISSING" in stdout
    # Both missing rows surfaced in the same table.
    assert stdout.count("MISSING") == 2


def test_verify_present_via_legacy_alias_flagged_in_output() -> None:
    """Value resolves but canonical env var is absent → row is
    'present-via-legacy' and the legacy alias name is named in output.
    """
    fake = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "tenant-via-legacy",
        },
    )
    stdout, rc = _capture(
        ["verify"],
        loader_factory=lambda: fake,
        # Canonical env-var name NOT in env, but legacy alias IS.
        env_provider=lambda: {"M365_TENANT_ID": "tenant-via-legacy"},
        aliases_provider=lambda: (("connector", "m365", None, "tenant-id"),),
    )
    assert rc == 0
    assert "present-via-legacy" in stdout
    assert "M365_TENANT_ID" in stdout
    assert "please migrate" in stdout


def test_verify_json_envelope_shape() -> None:
    """--json emits a parseable envelope with a 'secrets' list."""
    fake = FakeSecretsLoader(
        values={("connector", "m365", None, "tenant-id"): "tenant-1"},
    )
    stdout, rc = _capture(
        ["verify", "--json"],
        loader_factory=lambda: fake,
        env_provider=lambda: {"KAIRIX_CONNECTOR_M365_TENANT_ID": "x"},
        aliases_provider=lambda: (("connector", "m365", None, "tenant-id"),),
    )
    assert rc == 0
    parsed = json.loads(stdout)
    assert "secrets" in parsed
    assert isinstance(parsed["secrets"], list)
    assert parsed["secrets"][0]["status"] == "present"
    assert parsed["secrets"][0]["canonical_kv"] == "kairix-connector-m365-tenant-id"


# ── migrate-list ───────────────────────────────────────────────────


def test_migrate_list_tsv_has_header_and_rows() -> None:
    """Default output is TSV with a header line + every legacy alias mapped."""
    stdout, rc = _capture(["migrate-list"])
    assert rc == 0
    lines = stdout.strip().split("\n")
    assert lines[0] == "LEGACY_ENV_VAR\tCANONICAL_KV_NAME"
    assert len(lines) > 1  # at least one data row
    # M365 tenant id has three legacy aliases — at least one shows up.
    assert any("M365_TENANT_ID" in line and "kairix-connector-m365-tenant-id" in line for line in lines)


def test_migrate_list_json_envelope() -> None:
    """--json emits {'mapping': [{legacy_env_var, canonical_kv_name}, ...]}."""
    stdout, rc = _capture(["migrate-list", "--json"])
    assert rc == 0
    parsed = json.loads(stdout)
    assert "mapping" in parsed
    assert isinstance(parsed["mapping"], list)
    # Each row has both keys.
    for row in parsed["mapping"]:
        assert "legacy_env_var" in row
        assert "canonical_kv_name" in row


# ── subprocess-driven outcome test (F30 strict shape) ─────────────


def test_secrets_migrate_list_via_subprocess() -> None:
    """F30: invoke via subprocess to exercise the full dispatcher.

    Uses ``migrate-list`` because it has no I/O dependencies — pure
    static-table emission. The verify subcommand is exercised via the
    in-process tests above so we can pass FakeSecretsLoader.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "secrets", "migrate-list"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"expected exit 0; got rc={result.returncode}\nstderr={result.stderr!r}"
    assert "LEGACY_ENV_VAR\tCANONICAL_KV_NAME" in result.stdout
    assert "kairix-connector-m365-tenant-id" in result.stdout


def test_verify_loads_bundle_file_before_reading_env(tmp_path) -> None:
    """Verify must hydrate the bundle file into os.environ first.

    Regression test for #360: previously the verify CLI walked
    SecretsLoader.get() over the legacy alias map, which reads
    os.environ. Connectors + providers running in production load
    the bundle file implicitly via kairix.secrets.get_secret(), but
    'kairix secrets verify' invoked from `docker exec` did not — so
    every bundle-only secret showed as MISSING despite working at
    runtime. Fix: _ensure_bundle_loaded() calls load_secrets() at the
    top of _run_verify.

    Sabotage target: removing _ensure_bundle_loaded() makes this test
    fail because the canonical env var won't be present (the bundle
    file has 'KAIRIX_PROVIDER_LLM_API_KEY=...' but env doesn't until
    hydration).

    F2-clean: bundle_path is passed explicitly via the secrets_main
    kwarg seam — no monkeypatch.setenv on KAIRIX_* keys.
    """
    bundle = tmp_path / "kairix.env"
    bundle.write_text(
        "KAIRIX_PROVIDER_LLM_API_KEY=hydrated-from-bundle\n",  # pragma: allowlist secret
        encoding="utf-8",
    )

    out, rc = _capture(
        ["verify", "--json"],
        aliases_provider=lambda: (("provider", "llm", None, "api-key"),),
        bundle_path=bundle,
    )
    assert rc == 0, f"expected exit 0 (bundle hydrates key); got rc={rc}, out={out}"
    payload = json.loads(out)
    row = next(r for r in payload["secrets"] if r["leaf"] == "api-key")
    assert row["status"] in ("present", "present-via-legacy"), f"expected key resolved via bundle hydration; got {row}"


def test_verify_with_nonexistent_bundle_passes_through_to_loader(tmp_path) -> None:
    """When bundle_path points at a non-existent file, verify still runs.

    Covers the load_secrets "file absent → return 0" branch via the
    public secrets_main seam (no internal-name imports). The verify
    walk completes against env-only resolution; the test just asserts
    the path runs cleanly.
    """
    nonexistent = tmp_path / "no-such.env"
    fake = FakeSecretsLoader(values={("provider", "llm", None, "api-key"): "k"})
    out, rc = _capture(
        ["verify", "--json"],
        loader_factory=lambda: fake,
        aliases_provider=lambda: (("provider", "llm", None, "api-key"),),
        bundle_path=nonexistent,
    )
    assert rc == 0
    assert "secrets" in out
