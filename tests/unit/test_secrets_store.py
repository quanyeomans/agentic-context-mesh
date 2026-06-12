"""Unit tests for ``kairix.secrets.store`` — the write-side persistence
use-case behind ``kairix secrets set`` and the setup wizard.

F2-clean: every test passes an explicit ``env`` mapping / ``home`` /
``container_dir`` / ``bundle_path`` through the public seams — no
``monkeypatch.setenv`` on ``KAIRIX_*`` keys, no reads of the developer's
real environment.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from kairix.secrets.store import resolve_bundle_path, set_secret

pytestmark = pytest.mark.unit

_NAME = "kairix-provider-llm-api-key"
_ENV_VAR = "KAIRIX_PROVIDER_LLM_API_KEY"
_VALUE = "example-credential-value"  # pragma: allowlist secret — generic fixture, not a real key


# ── resolve_bundle_path ────────────────────────────────────────────


def test_resolve_bundle_path_env_override_wins(tmp_path: Path) -> None:
    """$KAIRIX_SECRETS_FILE beats both the container and pip defaults."""
    override = tmp_path / "custom" / "bundle.env"
    resolved = resolve_bundle_path(
        env={"KAIRIX_SECRETS_FILE": str(override)},
        home=tmp_path / "home",
        container_dir=tmp_path / "run-secrets",
    )
    assert resolved == override


def test_resolve_bundle_path_container_dir_when_present(tmp_path: Path) -> None:
    """An existing /run/secrets-style dir selects the container bundle."""
    container = tmp_path / "run-secrets"
    container.mkdir()
    resolved = resolve_bundle_path(env={}, home=tmp_path / "home", container_dir=container)
    assert resolved == container / "kairix.env"


def test_resolve_bundle_path_xdg_fallback_when_no_container(tmp_path: Path) -> None:
    """Without a container dir, $XDG_CONFIG_HOME hosts the pip-install bundle."""
    xdg = tmp_path / "xdg"
    resolved = resolve_bundle_path(
        env={"XDG_CONFIG_HOME": str(xdg)},
        home=tmp_path / "home",
        container_dir=tmp_path / "missing-run-secrets",
    )
    assert resolved == xdg / "kairix" / "secrets" / "kairix.env"


def test_resolve_bundle_path_home_fallback_without_xdg(tmp_path: Path) -> None:
    """No env override, no container, no XDG → ~/.config/kairix/secrets/kairix.env."""
    home = tmp_path / "home"
    resolved = resolve_bundle_path(env={}, home=home, container_dir=tmp_path / "missing")
    assert resolved == home / ".config" / "kairix" / "secrets" / "kairix.env"


# ── set_secret: happy paths ────────────────────────────────────────


def test_set_secret_rejects_bundle_path_outside_allowed_roots() -> None:
    """Write targets escaping {home, /etc/kairix, /run/*, tmp} raise before any write.

    The S2083 confinement contract — mirrors
    ``kairix.connect.store.file_store._confine_to_allowed_root``.
    """
    with pytest.raises(ValueError, match="fix:"):
        set_secret(_NAME, _VALUE, bundle_path=Path("/opt/kairix-escape/kairix.env"))


def test_set_secret_env_override_outside_allowed_roots_rejected(tmp_path: Path) -> None:
    """KAIRIX_SECRETS_FILE pointing outside the allowed roots is refused."""
    with pytest.raises(ValueError, match="fix:"):
        set_secret(
            _NAME,
            _VALUE,
            env={"KAIRIX_SECRETS_FILE": "/opt/kairix-escape/kairix.env"},
            container_dir=tmp_path / "absent",
        )


def test_set_secret_creates_file_with_0600_and_parent_dirs(tmp_path: Path) -> None:
    """A fresh bundle is created with parents and locked to 0600."""
    bundle = tmp_path / "nested" / "dir" / "kairix.env"

    returned = set_secret(_NAME, _VALUE, bundle_path=bundle)

    assert returned == bundle
    assert bundle.read_text(encoding="utf-8") == f"{_ENV_VAR}={_VALUE}\n"
    mode = stat.S_IMODE(bundle.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_set_secret_upsert_replaces_line_and_preserves_comments(tmp_path: Path) -> None:
    """Re-setting a name replaces its line in place; comments and other
    entries survive verbatim."""
    bundle = tmp_path / "kairix.env"
    bundle.write_text(
        f"# operator notes stay put\n{_ENV_VAR}=old-value\nKAIRIX_PROVIDER_LLM_ENDPOINT=https://example.invalid\n",
        encoding="utf-8",
    )

    set_secret(_NAME, "rotated-value", bundle_path=bundle)

    content = bundle.read_text(encoding="utf-8")
    assert "# operator notes stay put" in content
    assert f"{_ENV_VAR}=rotated-value" in content
    assert "old-value" not in content
    assert "KAIRIX_PROVIDER_LLM_ENDPOINT=https://example.invalid" in content
    # No duplicate line was appended.
    assert content.count(_ENV_VAR) == 1


def test_set_secret_appends_new_entry_to_existing_bundle(tmp_path: Path) -> None:
    """A new canonical name lands as a new line; existing lines untouched."""
    bundle = tmp_path / "kairix.env"
    bundle.write_text("KAIRIX_INFRA_NEO4J_PASSWORD=placeholder\n", encoding="utf-8")  # pragma: allowlist secret

    set_secret(_NAME, _VALUE, bundle_path=bundle)

    lines = bundle.read_text(encoding="utf-8").splitlines()
    assert "KAIRIX_INFRA_NEO4J_PASSWORD=placeholder" in lines  # pragma: allowlist secret
    assert f"{_ENV_VAR}={_VALUE}" in lines


def test_set_secret_round_trip_visible_to_loader_read_side(tmp_path: Path) -> None:
    """A freshly written value is visible through the read-side parser
    immediately — including after an overwrite (cache must not serve the
    stale first value)."""
    from kairix.secrets import load_secrets_file

    bundle = tmp_path / "kairix.env"
    set_secret(_NAME, "first-value", bundle_path=bundle)
    assert load_secrets_file(bundle)[_ENV_VAR] == "first-value"

    set_secret(_NAME, "second-value", bundle_path=bundle)
    assert load_secrets_file(bundle)[_ENV_VAR] == "second-value"


def test_set_secret_resolves_bundle_via_same_path_resolution(tmp_path: Path) -> None:
    """Without an explicit bundle_path, set_secret lands the value at the
    loader's own resolved location (XDG fallback in this scenario)."""
    xdg = tmp_path / "xdg"
    path = set_secret(
        _NAME,
        _VALUE,
        env={"XDG_CONFIG_HOME": str(xdg)},
        home=tmp_path / "home",
        container_dir=tmp_path / "missing-run-secrets",
    )
    assert path == xdg / "kairix" / "secrets" / "kairix.env"
    assert f"{_ENV_VAR}={_VALUE}" in path.read_text(encoding="utf-8")


# ── set_secret: rejection paths ────────────────────────────────────


def test_set_secret_rejects_non_canonical_name_with_examples(tmp_path: Path) -> None:
    """A non-canonical name raises with an F21 affordance naming two
    valid example names."""
    with pytest.raises(ValueError, match="fix:") as excinfo:
        set_secret("MY_API_KEY", _VALUE, bundle_path=tmp_path / "kairix.env")
    message = str(excinfo.value)
    assert "kairix-provider-llm-api-key" in message
    assert "kairix-connector-github-pat" in message
    assert not (tmp_path / "kairix.env").exists()


def test_set_secret_rejects_unknown_scope(tmp_path: Path) -> None:
    """kairix-<scope>- prefix with an unknown scope is rejected."""
    with pytest.raises(ValueError, match="fix:"):
        set_secret("kairix-widget-llm-api-key", _VALUE, bundle_path=tmp_path / "kairix.env")


def test_set_secret_rejects_empty_value(tmp_path: Path) -> None:
    """An empty value raises with stdin guidance and writes nothing."""
    with pytest.raises(ValueError, match="stdin"):
        set_secret(_NAME, "", bundle_path=tmp_path / "kairix.env")
    assert not (tmp_path / "kairix.env").exists()


def test_set_secret_encodes_multiline_value_onto_one_line(tmp_path: Path) -> None:
    """A multi-line value (a PEM key) lands on ONE quoted bundle line.

    Pre-#review-H2 behaviour was a hard rejection, which made the wizard's
    GitHub leg fail after successful consent. Now the bundle layer encodes;
    the parse layer decodes it back byte-for-byte.
    """
    bundle = tmp_path / "kairix.env"
    value = "line-one\nline-two\n"
    set_secret(_NAME, value, bundle_path=bundle)
    lines = bundle.read_text(encoding="utf-8").splitlines()
    entry_lines = [line for line in lines if "=" in line]
    assert lines == entry_lines, f"non KEY=VALUE lines written: {lines!r}"
    assert len(entry_lines) == 1

    from kairix.secrets import load_secrets_file

    load_secrets_file.cache_clear()
    assert load_secrets_file(bundle)[_ENV_VAR] == value
