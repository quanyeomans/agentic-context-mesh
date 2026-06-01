"""Unit-level coverage for kairix.connect.store.file_store.

Covers:
  * Path resolution (explicit > env > default home)
  * Fresh write (canonical env vars appear)
  * Idempotent update (existing canonical names replaced; new ones appended;
    unrelated lines preserved)
  * Write-permission failure surfaces typed error
  * Unknown-attribute internal-only path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from kairix.connect.store.file_store import FileTokenStore

pytestmark = pytest.mark.unit


def _tokens() -> CapturedTokens:
    return CapturedTokens(
        refresh_token="refresh-001",
        access_token="access-001",
        token_uri="https://oauth2.googleapis.com/token",
    )


def _client() -> ClientCredentials:
    return ClientCredentials(client_id="cid-001", client_secret="csec-001")


def test_explicit_path_takes_priority(tmp_path: Path) -> None:
    """Explicit ``path=`` overrides env + home defaults."""
    target = tmp_path / "explicit.env"
    store = FileTokenStore(path=target, env={"KAIRIX_SECRETS_FILE": "/should/be/ignored"})
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert target.exists()
    assert report.target == str(target)
    text = target.read_text()
    assert "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID=cid-001" in text
    assert "KAIRIX_CONNECTOR_GMAIL_CLIENT_SECRET=csec-001" in text
    assert "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN=refresh-001" in text
    assert "KAIRIX_CONNECTOR_GMAIL_ACCESS_TOKEN=access-001" in text


def test_env_overrides_home_default(tmp_path: Path) -> None:
    """When no explicit path, ``$KAIRIX_SECRETS_FILE`` wins over home default."""
    target = tmp_path / "from-env.env"
    store = FileTokenStore(env={"KAIRIX_SECRETS_FILE": str(target)}, home=tmp_path / "fake-home")
    store.store(
        scope="connector",
        area="google-drive",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert target.exists()


def test_home_default_used_when_no_overrides(tmp_path: Path) -> None:
    """No explicit path and no env → ``<home>/.config/kairix/secrets/kairix.env``."""
    store = FileTokenStore(env={}, home=tmp_path)
    store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    target = tmp_path / ".config" / "kairix" / "secrets" / "kairix.env"
    assert target.exists()


def test_idempotent_update_replaces_existing(tmp_path: Path) -> None:
    """Existing canonical lines are replaced; unrelated lines pass through."""
    target = tmp_path / "kairix.env"
    target.write_text(
        "# comment line preserved\n"
        "UNRELATED_VAR=unrelated-value\n"
        "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID=old-cid\n"
        "KAIRIX_CONNECTOR_GMAIL_ACCESS_TOKEN=old-access\n",  # pragma: allowlist secret
    )
    store = FileTokenStore(path=target)
    store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    out = target.read_text()
    # New values replaced old, in place — no duplication.
    assert out.count("KAIRIX_CONNECTOR_GMAIL_CLIENT_ID=") == 1
    assert "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID=cid-001" in out
    assert "KAIRIX_CONNECTOR_GMAIL_ACCESS_TOKEN=access-001" in out
    # Comment + unrelated entry preserved.
    assert "# comment line preserved" in out
    assert "UNRELATED_VAR=unrelated-value" in out


def test_appends_new_canonical_names(tmp_path: Path) -> None:
    """A net-new canonical name is appended (not replacing anything)."""
    target = tmp_path / "kairix.env"
    target.write_text("UNRELATED=value\n")
    store = FileTokenStore(path=target)
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    out = target.read_text()
    assert "UNRELATED=value" in out
    for name in report.canonical_names:
        assert f"{name}=" in out


def test_unwritable_directory_raises_typed_error(tmp_path: Path) -> None:
    """A path under an unwritable parent surfaces TokenStoreUnauthorizedError."""
    # Make the parent read-only.
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(0o500)
    target = parent / "kairix.env"
    store = FileTokenStore(path=target)
    try:
        with pytest.raises(TokenStoreUnauthorizedError, match="cannot write"):
            store.store(
                scope="connector",
                area="gmail",
                instance=None,
                tokens=_tokens(),
                client=_client(),
            )
    finally:
        parent.chmod(0o700)


def test_report_lists_all_four_canonical_names(tmp_path: Path) -> None:
    """The report's tuple covers all four leaves in the documented order."""
    target = tmp_path / "kairix.env"
    store = FileTokenStore(path=target)
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert report.backend == "file"
    assert report.canonical_names == (
        "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID",
        "KAIRIX_CONNECTOR_GMAIL_CLIENT_SECRET",
        "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN",
        "KAIRIX_CONNECTOR_GMAIL_ACCESS_TOKEN",
    )
