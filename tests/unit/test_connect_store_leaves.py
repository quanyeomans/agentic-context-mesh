"""Unit coverage for the shared leaf-derivation helper.

Per ADR-032 Phase 2 §"TokenStore widening": the three stores share
:func:`kairix.connect.store.leaves.leaf_pairs` so they all derive the
canonical-leaf list dynamically from the dataclass fields.

Tests pin the Google + Slack shapes and the empty-field skipping
behaviour so a refactor that breaks per-service canonical-name
emission surfaces here.
"""

from __future__ import annotations

import pytest

from kairix.connect.protocols import CapturedTokens, ClientCredentials
from kairix.connect.store.leaves import leaf_pairs, unknown_attribute_error

pytestmark = pytest.mark.unit


def test_google_shape_emits_four_leaves() -> None:
    """A Google-shape CapturedTokens emits exactly 4 leaves in canonical order."""
    client = ClientCredentials(client_id="cid-g", client_secret="csec-g")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="rt-g",
        access_token="at-g",
        token_uri="https://oauth2.googleapis.com/token",
    )
    pairs = leaf_pairs(client, tokens)
    assert pairs == (
        ("client-id", "cid-g"),
        ("client-secret", "csec-g"),
        ("refresh-token", "rt-g"),
        ("access-token", "at-g"),
    )


def test_slack_shape_emits_three_leaves_skipping_empty_google_fields() -> None:
    """A Slack-shape CapturedTokens emits 3 leaves; empty Google slots are skipped."""
    client = ClientCredentials(client_id="cid-s", client_secret="csec-s")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="",  # Slack returns no refresh_token — skipped.
        access_token="",  # Slack carries the credential in bot_token instead.
        token_uri="https://slack.com/api/oauth.v2.access",
        bot_token="xoxb-test",
    )
    pairs = leaf_pairs(client, tokens)
    assert pairs == (
        ("client-id", "cid-s"),
        ("client-secret", "csec-s"),
        ("bot-token", "xoxb-test"),
    )


def test_slack_with_app_token_emits_four_leaves() -> None:
    """A Slack CapturedTokens with both bot_token + app_token emits 4 leaves."""
    client = ClientCredentials(client_id="cid", client_secret="csec")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="",
        access_token="",
        token_uri="https://slack.com/api/oauth.v2.access",
        bot_token="xoxb-bot",
        app_token="xapp-app",
    )
    pairs = leaf_pairs(client, tokens)
    assert pairs == (
        ("client-id", "cid"),
        ("client-secret", "csec"),
        ("bot-token", "xoxb-bot"),
        ("app-token", "xapp-app"),
    )


def test_token_uri_metadata_field_is_skipped() -> None:
    """``token_uri`` is a metadata field — never emitted as a canonical leaf."""
    client = ClientCredentials(client_id="cid", client_secret="csec")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="rt",
        access_token="at",
        token_uri="https://some-endpoint.example/",
    )
    pairs = leaf_pairs(client, tokens)
    leaf_names = {leaf for leaf, _ in pairs}
    # The URL must not leak into the canonical leaves — it's runtime metadata.
    assert "token-uri" not in leaf_names
    assert "https://some-endpoint.example/" not in {value for _, value in pairs}


def test_expires_in_int_field_is_skipped() -> None:
    """``expires_in`` is an int metadata field — skipped because not a string leaf."""
    client = ClientCredentials(client_id="cid", client_secret="csec")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="rt",
        access_token="at",
        token_uri="https://x/",
        expires_in=3600,
    )
    pairs = leaf_pairs(client, tokens)
    # Four leaves; expires_in not present.
    assert len(pairs) == 4


def test_empty_client_id_skipped() -> None:
    """Empty client_id is skipped — no canonical-leaf with an empty value."""
    client = ClientCredentials(client_id="", client_secret="csec")  # pragma: allowlist secret
    tokens = CapturedTokens(
        refresh_token="rt",
        access_token="at",
        token_uri="https://x/",
    )
    pairs = leaf_pairs(client, tokens)
    leaf_names = {leaf for leaf, _ in pairs}
    assert "client-id" not in leaf_names


def test_emission_order_is_stable_across_shapes() -> None:
    """Client identity is emitted before token material — same across Google + Slack."""
    google_client = ClientCredentials(client_id="g", client_secret="g")  # pragma: allowlist secret
    google_tokens = CapturedTokens(refresh_token="r", access_token="a", token_uri="https://x/")
    slack_client = ClientCredentials(client_id="s", client_secret="s")  # pragma: allowlist secret
    slack_tokens = CapturedTokens(
        refresh_token="",
        access_token="",
        token_uri="https://slack/",
        bot_token="xoxb-z",
    )
    google_leaves = [leaf for leaf, _ in leaf_pairs(google_client, google_tokens)]
    slack_leaves = [leaf for leaf, _ in leaf_pairs(slack_client, slack_tokens)]
    # Both shapes start with client identity in the same order.
    assert google_leaves[:2] == ["client-id", "client-secret"]
    assert slack_leaves[:2] == ["client-id", "client-secret"]


def test_unknown_attribute_error_carries_f21_markers() -> None:
    """Bug-path error helper carries F21 fix/next/run markers."""
    err = unknown_attribute_error("bogus_field")
    msg = str(err)
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg
    assert "bogus_field" in msg
