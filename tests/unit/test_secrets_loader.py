"""Unit tests for :mod:`kairix.secrets.loader` — SecretsLoader resolution.

Coverage:
  - canonical env-var hit
  - KV mount per-file hit
  - chain miss returns None (get) / raises SecretNotFoundError (require)
  - resolution priority ordering (env > KV mount)

All tests pass an explicit env dict + temp kv_mount path — no
``monkeypatch.setenv("KAIRIX_*")`` (F2-clean).
"""

from __future__ import annotations

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
) -> SecretsLoader:
    """Build a SecretsLoader scoped to an explicit env + kv_mount."""
    return SecretsLoader(env=env or {}, kv_mount=kv_mount)


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


# ── KV mount per-file hit ──────────────────────────────────────────


def test_get_resolves_via_kv_mount_file(tmp_path: Path) -> None:
    """When env misses, the loader reads ``<kv_mount>/<canonical>``."""
    canonical = "kairix-connector-m365-tenant-id"
    (tmp_path / canonical).write_text("tenant-via-mount\n", encoding="utf-8")
    loader = _make_loader(env={}, kv_mount=tmp_path)

    value = loader.get("connector", "m365", None, "tenant-id")
    assert value == "tenant-via-mount"


def test_get_kv_mount_returns_none_when_file_absent(tmp_path: Path) -> None:
    """KV mount file absent → returns None (no further fallback)."""
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
        # No exception bubbled — falls through to None.
        assert loader.get("connector", "m365", None, "tenant-id") is None
    finally:
        target.chmod(0o644)


# ── priority: env beats KV mount ──────────────────────────────────


def test_canonical_env_var_beats_kv_mount(tmp_path: Path) -> None:
    """When both env + KV mount have the value, env wins."""
    canonical = "kairix-connector-m365-tenant-id"
    (tmp_path / canonical).write_text("from-kv-mount", encoding="utf-8")
    env = {"KAIRIX_CONNECTOR_M365_TENANT_ID": "from-env"}
    loader = _make_loader(env=env, kv_mount=tmp_path)
    assert loader.get("connector", "m365", None, "tenant-id") == "from-env"


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


def test_default_construction_uses_os_environ_live(monkeypatch) -> None:
    """Default ctor (no env= kwarg) reads os.environ live on every
    get(). monkeypatch.setenv used here on a non-KAIRIX_ name so F2
    stays clean — the production env-var space is gated elsewhere;
    this test just proves the ctor honours the live mapping.
    """
    monkeypatch.setenv("UNRELATED_TEST_VAR", "abc")
    loader = SecretsLoader()
    # Confirm a non-KAIRIX_ env var landed in the live mapping via os.environ.
    # We don't assert on KAIRIX_* values to avoid coupling to the real
    # environment under CI.
    assert "UNRELATED_TEST_VAR" in loader._env
