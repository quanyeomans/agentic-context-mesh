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
