"""Unit tests for first-boot operator-token provisioning (#500).

``ensure_operator_token`` mints + persists an operator token only when one
is not already configured (env or bundle), and is idempotent across boots.
Tests drive it through the ``env=`` / ``token_factory=`` / ``bundle_path=``
seams (F1/F2-clean — no monkeypatch, no ``KAIRIX_*`` setenv, and the
bundle write is pinned to ``tmp_path`` so the Linux-only ``/run/secrets``
mount is never touched).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.platform.setup.operator_token import (
    ensure_operator_token,
    main,
    onboarding_message,
)
from kairix.secrets import load_secrets_file
from kairix.secrets.naming import canonical_env_var

pytestmark = pytest.mark.unit

_ENV_VAR = canonical_env_var("infra", "operator", None, "token")
# A reference token for the onboarding-message + env-supplied cases, not a
# real credential.
_FIXED_TOKEN = "reference-fake-token-value"  # pragma: allowlist secret — fake fixture


def _bundle(tmp_path: Path) -> Path:
    return tmp_path / "secrets" / "kairix.env"


def test_mints_and_persists_a_token_when_absent(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    load_secrets_file.cache_clear()
    token, minted = ensure_operator_token(env={}, bundle_path=bundle)
    assert minted is True
    # The real secrets.token_urlsafe(32) yields a non-trivial urlsafe string.
    assert len(token) >= 32
    # The exact minted value landed in the bundle under the canonical name.
    load_secrets_file.cache_clear()
    assert load_secrets_file(bundle).get(_ENV_VAR) == token
    # 0600 — the leak-safe mode set_secret enforces.
    assert oct(bundle.stat().st_mode & 0o777) == "0o600"


def test_honours_an_operator_supplied_env_token(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    token, minted = ensure_operator_token(env={_ENV_VAR: _FIXED_TOKEN}, bundle_path=bundle)
    assert minted is False
    assert token == _FIXED_TOKEN
    # Nothing was written — the env token wins and the bundle stays absent.
    assert not bundle.exists()


def test_is_idempotent_across_boots(tmp_path: Path) -> None:
    """A second call reuses the persisted token — a restart must not rotate
    it and invalidate the operator's bookmarked tokened URL."""
    bundle = _bundle(tmp_path)
    load_secrets_file.cache_clear()
    first, minted_first = ensure_operator_token(env={}, bundle_path=bundle)
    second, minted_second = ensure_operator_token(env={}, bundle_path=bundle)
    assert minted_first is True
    assert minted_second is False
    assert first == second  # the persisted token is reused, not regenerated


def test_onboarding_message_builds_the_tokened_url_for_minted_and_existing() -> None:
    minted_line = onboarding_message(_FIXED_TOKEN, minted=True, env={"KAIRIX_MCP_BIND_HOST": "host.internal"})
    assert "generated a first-boot operator token" in minted_line
    assert f"http://host.internal:8080/setup/?operator_token={_FIXED_TOKEN}" in minted_line

    existing_line = onboarding_message(_FIXED_TOKEN, minted=False, env={})
    assert "already configured" in existing_line
    assert f"operator_token={_FIXED_TOKEN}" in existing_line


def test_main_mints_persists_and_emits_the_tokened_url(tmp_path: Path) -> None:
    """The s6 entrypoint mints when absent and prints the tokened URL once."""
    bundle = _bundle(tmp_path)
    load_secrets_file.cache_clear()
    lines: list[str] = []
    code = main(env={"KAIRIX_MCP_BIND_HOST": "host.internal"}, bundle_path=bundle, writer=lines.append)
    assert code == 0
    assert len(lines) == 1
    load_secrets_file.cache_clear()
    minted_token = load_secrets_file(bundle).get(_ENV_VAR)
    assert minted_token is not None
    assert f"http://host.internal:8080/setup/?operator_token={minted_token}" in lines[0]
