"""Unit-level coverage for kairix.connect.store.stdout_store."""

from __future__ import annotations

import io

import pytest

from kairix.connect.protocols import CapturedTokens, ClientCredentials
from kairix.connect.store.stdout_store import StdoutTokenStore

pytestmark = pytest.mark.unit


def _tokens() -> CapturedTokens:
    return CapturedTokens(
        refresh_token="rt",
        access_token="at",
        token_uri="https://oauth2.googleapis.com/token",
    )


def _client() -> ClientCredentials:
    return ClientCredentials(client_id="cid", client_secret="csec")


def test_writes_tsv_lines_in_canonical_order() -> None:
    buf = io.StringIO()
    store = StdoutTokenStore(stream=buf)
    report = store.store(
        scope="connector",
        area="google-drive",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    lines = [ln for ln in buf.getvalue().splitlines() if ln]
    assert len(lines) == 4
    for line in lines:
        name, _, value = line.partition("\t")
        assert name.startswith("KAIRIX_CONNECTOR_GOOGLE_DRIVE_")
        assert value
    assert report.backend == "stdout"
    assert report.target == "<stdout>"


def test_stdout_store_canonical_names() -> None:
    buf = io.StringIO()
    store = StdoutTokenStore(stream=buf)
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID" in report.canonical_names
    assert "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN" in report.canonical_names


def test_stdout_store_emits_metadata_leaves() -> None:
    """GitHub App style metadata leaves are emitted as TSV lines alongside the base leaves."""
    buf = io.StringIO()
    store = StdoutTokenStore(stream=buf)
    tokens_with_meta = CapturedTokens(
        refresh_token="",  # GitHub App has no refresh token
        access_token="installation-token-abc",
        token_uri="https://api.github.com/app/installations/access_tokens",
        metadata={"installation-id": "12345"},
    )
    fake_pem = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
    client = ClientCredentials(client_id="42", client_secret=fake_pem)
    report = store.store(
        scope="connector",
        area="github",
        instance=None,
        tokens=tokens_with_meta,
        client=client,
    )
    out = buf.getvalue()
    # Strengthened: the installation-id metadata leaf AND the access-token
    # base leaf must both appear; the empty refresh_token must be skipped.
    assert "KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID\t12345" in out
    assert "KAIRIX_CONNECTOR_GITHUB_ACCESS_TOKEN\tinstallation-token-abc" in out
    assert "REFRESH_TOKEN" not in out, f"empty refresh_token should be skipped, got: {out!r}"
    assert "KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID" in report.canonical_names


def test_stdout_store_skips_empty_metadata_values() -> None:
    """An empty-string metadata value is not emitted as a blank-valued TSV line."""
    buf = io.StringIO()
    store = StdoutTokenStore(stream=buf)
    tokens_with_empty_meta = CapturedTokens(
        refresh_token="rt",
        access_token="at",
        token_uri="https://x/",
        metadata={"installation-id": ""},  # empty value — should be skipped
    )
    store.store(
        scope="connector",
        area="github",
        instance=None,
        tokens=tokens_with_empty_meta,
        client=_client(),
    )
    out = buf.getvalue()
    assert "INSTALLATION_ID" not in out, f"empty metadata value should be skipped, got: {out!r}"


def test_stdout_store_default_stream_is_sys_stdout() -> None:
    """The default stream is sys.stdout (covers the default-arg branch)."""
    import sys

    store = StdoutTokenStore()
    # The store's _stream attr should resolve to sys.stdout.
    assert store._stream is sys.stdout
