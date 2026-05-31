"""Unit tests for :mod:`kairix.secrets.loader` — SecretsLoader resolution.

Coverage:
  - canonical env-var hit
  - legacy alias hit emits DeprecationWarning
  - KV mount per-file hit
  - chain miss returns None (get) / raises SecretNotFoundError (require)
  - resolution priority ordering (env > legacy > KV mount > legacy chain)

All tests pass an explicit env dict + temp kv_mount path — no
``monkeypatch.setenv("KAIRIX_*")`` (F2-clean).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from kairix.secrets.loader import (
    SecretNotFoundError,
    SecretsLoader,
    SecretsResolver,
)

pytestmark = pytest.mark.unit


def _make_loader(
    *,
    env: dict[str, str] | None = None,
    kv_mount: Path | None = None,
    legacy_value: str | None = None,
) -> SecretsLoader:
    """Build a SecretsLoader with a stub legacy chain that returns
    ``legacy_value`` regardless of input — keeps every test free of
    the historical resolver chain.
    """

    def _stub_chain(_canonical_kv: str) -> str | None:
        return legacy_value

    return SecretsLoader(env=env or {}, kv_mount=kv_mount, legacy_chain=_stub_chain)


# ── canonical env hit ──────────────────────────────────────────────


def test_get_resolves_via_canonical_env_var() -> None:
    """Step 1: the canonical KAIRIX_* env var is the first source."""
    env = {"KAIRIX_CONNECTOR_M365_TENANT_ID": "tenant-via-canonical"}
    loader = _make_loader(env=env)
    value = loader.get("connector", "m365", None, "tenant-id")
    assert value == "tenant-via-canonical"


def test_require_returns_canonical_env_var_value() -> None:
    env = {"KAIRIX_CONNECTOR_SLACK_BOT_TOKEN": "xoxb-test"}
    loader = _make_loader(env=env)
    value = loader.require("connector", "slack", None, "bot-token")
    assert value == "xoxb-test"


# ── legacy alias hit ───────────────────────────────────────────────


def test_get_resolves_via_legacy_alias_emits_deprecation_warning() -> None:
    """Legacy alias hit returns the value AND emits DeprecationWarning."""
    # M365_TENANT_ID is registered as a legacy alias for the m365 tenant id.
    env = {"M365_TENANT_ID": "tenant-via-legacy"}
    loader = _make_loader(env=env)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        value = loader.get("connector", "m365", None, "tenant-id")

    assert value == "tenant-via-legacy"
    deprecation_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == 1, (
        f"expected 1 DeprecationWarning naming the alias; got {[str(w.message) for w in captured]!r}"
    )
    assert "M365_TENANT_ID" in str(deprecation_warnings[0].message)
    assert "KAIRIX_CONNECTOR_M365_TENANT_ID" in str(deprecation_warnings[0].message)


def test_canonical_env_var_beats_legacy_alias() -> None:
    """When both canonical + legacy are set, canonical wins and no warning fires."""
    env = {
        "KAIRIX_CONNECTOR_M365_TENANT_ID": "canonical-wins",
        "M365_TENANT_ID": "legacy-loses",
    }
    loader = _make_loader(env=env)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        value = loader.get("connector", "m365", None, "tenant-id")

    assert value == "canonical-wins"
    assert not any(issubclass(w.category, DeprecationWarning) for w in captured)


# ── KV mount per-file hit ──────────────────────────────────────────


def test_get_resolves_via_kv_mount_file(tmp_path: Path) -> None:
    """When env + aliases miss, the loader reads ``<kv_mount>/<canonical>``."""
    canonical = "kairix-connector-m365-tenant-id"
    (tmp_path / canonical).write_text("tenant-via-mount\n", encoding="utf-8")
    loader = _make_loader(env={}, kv_mount=tmp_path)

    value = loader.get("connector", "m365", None, "tenant-id")
    assert value == "tenant-via-mount"


def test_get_kv_mount_returns_none_when_file_absent(tmp_path: Path) -> None:
    """KV mount file absent → falls through to legacy chain (which is also None)."""
    loader = _make_loader(env={}, kv_mount=tmp_path)
    assert loader.get("connector", "m365", None, "tenant-id") is None


def test_get_kv_mount_returns_none_for_empty_file(tmp_path: Path) -> None:
    """Empty / whitespace-only KV file is treated as a miss, not as ''."""
    canonical = "kairix-connector-m365-tenant-id"
    (tmp_path / canonical).write_text("   \n", encoding="utf-8")
    loader = _make_loader(env={}, kv_mount=tmp_path)
    assert loader.get("connector", "m365", None, "tenant-id") is None


def test_get_kv_mount_handles_oserror(tmp_path: Path) -> None:
    """Unreadable KV file falls through cleanly (no crash)."""
    canonical = "kairix-connector-m365-tenant-id"
    target = tmp_path / canonical
    target.write_text("locked-value\n", encoding="utf-8")
    target.chmod(0o000)
    loader = _make_loader(env={}, kv_mount=tmp_path)

    try:
        # No exception bubbled — falls through to legacy chain (None).
        assert loader.get("connector", "m365", None, "tenant-id") is None
    finally:
        target.chmod(0o644)


# ── legacy chain hit ───────────────────────────────────────────────


def test_get_resolves_via_legacy_chain_when_others_miss(tmp_path: Path) -> None:
    """Last resort: the injected legacy_chain callable returns a value."""
    loader = _make_loader(env={}, kv_mount=tmp_path, legacy_value="chain-value")
    assert loader.get("connector", "m365", None, "tenant-id") == "chain-value"


# ── miss / require ────────────────────────────────────────────────


def test_get_returns_none_when_nothing_resolves(tmp_path: Path) -> None:
    loader = _make_loader(env={}, kv_mount=tmp_path)
    assert loader.get("connector", "m365", None, "tenant-id") is None


def test_require_raises_with_canonical_name_in_message(tmp_path: Path) -> None:
    """require() raises SecretNotFoundError naming the canonical KV +
    env var so the operator gets an actionable next step.
    """
    loader = _make_loader(env={}, kv_mount=tmp_path)

    with pytest.raises(SecretNotFoundError) as exc:
        loader.require("connector", "m365", None, "tenant-id")

    msg = str(exc.value)
    assert "kairix-connector-m365-tenant-id" in msg
    assert "KAIRIX_CONNECTOR_M365_TENANT_ID" in msg
    # Actionable affordance markers (F21 spirit, not enforced for runtime errors).
    assert "fix:" in msg
    assert "next:" in msg


# ── Protocol shape ────────────────────────────────────────────────


def test_secrets_loader_satisfies_secrets_resolver_protocol() -> None:
    """SecretsLoader is a runtime-checkable :class:`SecretsResolver`."""
    loader = _make_loader(env={})
    assert isinstance(loader, SecretsResolver)


# ── DI seam: default ctor reads os.environ (smoke) ────────────────


def test_default_construction_uses_os_environ_snapshot(monkeypatch) -> None:
    """Default ctor (no env= kwarg) takes a snapshot of os.environ at
    construction time. monkeypatch.setenv used here on a non-KAIRIX_
    name so F2 stays clean — the production env-var space is gated
    elsewhere; this test just proves the ctor honours the snapshot.
    """
    monkeypatch.setenv("UNRELATED_TEST_VAR", "abc")
    loader = SecretsLoader()
    # Confirm a non-KAIRIX_ env var landed in the snapshot via os.environ.
    # We don't assert on KAIRIX_* values to avoid coupling to the real
    # environment under CI.
    assert "UNRELATED_TEST_VAR" in loader._env
