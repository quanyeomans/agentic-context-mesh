"""Outcome tests for ``kairix secrets verify``.

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


def _identities_for_test() -> tuple[tuple[str, str, str | None, str], ...]:
    """Tiny identity set so the verify table stays inspectable in tests."""
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
    """Every identity resolves via the fake loader → table renders + rc=0."""
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
        identities_provider=_identities_for_test,
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
        identities_provider=_identities_for_test,
    )
    assert rc == 1
    assert "MISSING" in stdout
    # Both missing rows surfaced in the same table.
    assert stdout.count("MISSING") == 2


def test_verify_json_envelope_shape() -> None:
    """--json emits a parseable envelope with a 'secrets' list."""
    fake = FakeSecretsLoader(
        values={("connector", "m365", None, "tenant-id"): "tenant-1"},
    )
    stdout, rc = _capture(
        ["verify", "--json"],
        loader_factory=lambda: fake,
        identities_provider=lambda: (("connector", "m365", None, "tenant-id"),),
    )
    assert rc == 0
    parsed = json.loads(stdout)
    assert "secrets" in parsed
    assert isinstance(parsed["secrets"], list)
    assert parsed["secrets"][0]["status"] == "present"
    assert parsed["secrets"][0]["canonical_kv"] == "kairix-connector-m365-tenant-id"


# ── subprocess-driven outcome test (F30 strict shape) ─────────────


def test_secrets_verify_via_subprocess() -> None:
    """F30: invoke via subprocess to exercise the full dispatcher.

    Subprocess invocation prints the verify table for the registered
    identities. The default loader resolves against the empty live
    environment, so every row reports MISSING and the command exits 1
    — that's the deterministic envelope shape we assert on.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "secrets", "verify", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Exit code is 1 because no canonical env vars are set in the
    # subprocess — every registered identity comes back MISSING.
    assert result.returncode == 1, f"expected exit 1; got rc={result.returncode}\nstderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert "secrets" in parsed
    assert len(parsed["secrets"]) > 0
    assert any(r["canonical_kv"] == "kairix-connector-m365-tenant-id" for r in parsed["secrets"])


def test_verify_loads_bundle_file_before_reading_env(tmp_path) -> None:
    """Verify must hydrate the bundle file into os.environ first.

    Regression test for #360: previously the verify CLI walked
    SecretsLoader.get() over the registered identities, which reads
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
        identities_provider=lambda: (("provider", "llm", None, "api-key"),),
        bundle_path=bundle,
    )
    assert rc == 0, f"expected exit 0 (bundle hydrates key); got rc={rc}, out={out}"
    payload = json.loads(out)
    row = next(r for r in payload["secrets"] if r["leaf"] == "api-key")
    assert row["status"] == "present", f"expected key resolved via bundle hydration; got {row}"


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
        identities_provider=lambda: (("provider", "llm", None, "api-key"),),
        bundle_path=nonexistent,
    )
    assert rc == 0
    assert "secrets" in out
