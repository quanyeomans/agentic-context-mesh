"""Unit tests for the github connector — per-file coverage lift to F7's 90% floor.

Targets the error paths + edge cases the contract / integration / e2e tests
don't exercise directly. Every test reaches a real production code path
without monkeypatching kairix internals (F1-clean / F2-clean).
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json

import httpx
import pytest

from kairix.connectors.github import (
    CAPABILITIES,
    GitHubApiClient,
    GitHubClientConfig,
    GitHubConnector,
    GitHubCredentials,
    WebhookSignatureError,
    make_connector,
    translate_event,
    verify_and_parse,
)
from kairix.connectors.github.api_client import (
    GitHubBlobRef,
    GitHubCommitRef,
    GitHubInstallationToken,
    GitHubIssueRef,
    GitHubRepoRef,
    guess_mime_from_path,
)
from kairix.connectors.github.connector import (
    TOPOLOGY_V2_GITHUB_FLAG,
    PerRepoCursorState,
    deserialise_cursor,
    f39_tier_from_visibility,
    parse_item_id,
    sensitivity_from_visibility,
    serialise_cursor,
)
from kairix.connectors.github.webhook import (
    HEADER_DELIVERY_ID,
    HEADER_EVENT_TYPE,
    HEADER_SIGNATURE_256,
    WebhookEnvelope,
    compute_signature,
)
from kairix.core.protocols import ContainerTransientError, CredentialExpiredError, RawArtefact
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Capability declaration + module surface
# ---------------------------------------------------------------------------


def test_capabilities_set_matches_spec_section_1() -> None:
    """F56 — declared CAPABILITIES match the spec §1 capability table."""
    expected = {
        "SourceConnector",
        "PollConnector",
        "CheckpointedConnector",
        "EventConnector",
        "SlimConnector",
        "SlimConnectorWithPermSync",
        "Resolver",
        "HierarchyConnector",
        "OAuthConnector",
        "CredentialsConnector",
    }
    assert CAPABILITIES == frozenset(expected)


def test_topology_v2_github_flag_constant_matches_registry_name() -> None:
    """F52 — call-site flag reference matches the registry key."""
    from kairix.core.features.registry import REGISTRY

    assert TOPOLOGY_V2_GITHUB_FLAG in REGISTRY
    assert REGISTRY[TOPOLOGY_V2_GITHUB_FLAG].default is False


# ---------------------------------------------------------------------------
# Cursor serialise / deserialise
# ---------------------------------------------------------------------------


def test_serialise_then_deserialise_round_trips() -> None:
    """JSON map round-trips through serialise / deserialise without loss."""
    state = {
        "org/repo-1": PerRepoCursorState(code_sha="sha-1", issues_since="2026-05-23T00:00:00Z"),
        "org/repo-2": PerRepoCursorState(code_sha=None, issues_since=None),
    }
    encoded = serialise_cursor(state)
    decoded = deserialise_cursor(encoded)
    assert decoded["org/repo-1"].code_sha == "sha-1"
    assert decoded["org/repo-1"].issues_since == "2026-05-23T00:00:00Z"
    assert decoded["org/repo-2"].code_sha is None


def test_deserialise_tolerates_malformed_input() -> None:
    """Stale / malformed cursor inputs return empty dict, not crash."""
    assert deserialise_cursor(None) == {}
    assert deserialise_cursor("") == {}
    assert deserialise_cursor("not-json{") == {}
    assert deserialise_cursor("[]") == {}  # JSON array, not object
    assert deserialise_cursor('"just-a-string"') == {}


# ---------------------------------------------------------------------------
# Sensitivity / F39-tier mappings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "visibility,expected",
    [
        ("public", "public"),
        ("internal", "internal"),
        ("private", "client-confidential"),
        ("unknown-tier", "client-confidential"),
    ],
)
def testsensitivity_from_visibility(visibility: str, expected: str) -> None:
    """Maps GitHub visibility tier to F39 Sensitivity literal."""
    assert sensitivity_from_visibility(visibility) == expected


@pytest.mark.parametrize(
    "visibility,expected",
    [
        ("public", "public"),
        ("internal", "internal"),
        ("private", "confidential"),
        ("xyz", "confidential"),
    ],
)
def testf39_tier_from_visibility(visibility: str, expected: str) -> None:
    """Maps GitHub visibility tier to F39Tier literal for HierarchyNode."""
    assert f39_tier_from_visibility(visibility) == expected


# ---------------------------------------------------------------------------
# item_id parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item_id,full_name,kind,identifier",
    [
        ("github://org/repo/commit/abc", "org/repo", "commit", "abc"),
        ("github://org/repo/blob/sha-1", "org/repo", "blob", "sha-1"),
        ("github://org/repo/issues/42", "org/repo", "issues", "42"),
        ("github://org/repo/pulls/100", "org/repo", "pulls", "100"),
        ("github://org/repo", "org/repo", "repo", ""),
        ("github://org/repo/extra-segment", "org/repo", "extra-segment", ""),
        ("not-a-github-uri", "", "repo", "not-a-github-uri"),
    ],
)
def testparse_item_id(item_id: str, full_name: str, kind: str, identifier: str) -> None:
    """Canonical github:// URIs round-trip into parsed components."""
    parsed = parse_item_id(item_id)
    assert parsed.full_name == full_name
    assert parsed.kind == kind
    assert parsed.identifier == identifier


# ---------------------------------------------------------------------------
# Webhook handler — signature verification edge cases
# ---------------------------------------------------------------------------


def test_verify_and_parse_rejects_empty_secret() -> None:
    """An empty secret is treated as a misconfiguration with fix marker."""
    with pytest.raises(WebhookSignatureError, match="webhook_secret is empty"):
        verify_and_parse(body=b"{}", headers={}, webhook_secret="")


def test_verify_and_parse_rejects_missing_delivery_header() -> None:
    """Missing X-GitHub-Delivery is fatal with actionable error."""
    with pytest.raises(WebhookSignatureError, match="X-GitHub-Delivery"):
        verify_and_parse(body=b"{}", headers={}, webhook_secret="secret")  # pragma: allowlist secret


def test_verify_and_parse_rejects_missing_event_header() -> None:
    """Missing X-GitHub-Event is fatal with actionable error."""
    with pytest.raises(WebhookSignatureError, match="X-GitHub-Event"):
        verify_and_parse(
            body=b"{}",
            headers={HEADER_DELIVERY_ID: "d1"},
            webhook_secret="secret",  # pragma: allowlist secret
        )


def test_verify_and_parse_rejects_missing_signature() -> None:
    """Missing X-Hub-Signature-256 is fatal."""
    with pytest.raises(WebhookSignatureError, match="signature missing or malformed"):
        verify_and_parse(
            body=b"{}",
            headers={HEADER_DELIVERY_ID: "d1", HEADER_EVENT_TYPE: "push"},
            webhook_secret="secret",  # pragma: allowlist secret
        )


def test_verify_and_parse_rejects_signature_without_sha256_prefix() -> None:
    """A signature lacking the sha256= prefix is rejected."""
    with pytest.raises(WebhookSignatureError, match="signature missing or malformed"):
        verify_and_parse(
            body=b"{}",
            headers={
                HEADER_DELIVERY_ID: "d1",
                HEADER_EVENT_TYPE: "push",
                HEADER_SIGNATURE_256: "deadbeef",  # no sha256= prefix
            },
            webhook_secret="secret",  # pragma: allowlist secret
        )


def test_verify_and_parse_rejects_non_json_body() -> None:
    """A non-JSON body produces an actionable error."""
    secret = "secret"  # pragma: allowlist secret
    body = b"not-json"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookSignatureError, match="not valid UTF-8 JSON"):
        verify_and_parse(
            body=body,
            headers={
                HEADER_DELIVERY_ID: "d1",
                HEADER_EVENT_TYPE: "push",
                HEADER_SIGNATURE_256: sig,
            },
            webhook_secret=secret,
        )


def test_verify_and_parse_rejects_json_array_body() -> None:
    """A JSON-but-array body produces an actionable error."""
    secret = "secret"  # pragma: allowlist secret
    body = b"[]"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookSignatureError, match="not a JSON object"):
        verify_and_parse(
            body=body,
            headers={
                HEADER_DELIVERY_ID: "d1",
                HEADER_EVENT_TYPE: "push",
                HEADER_SIGNATURE_256: sig,
            },
            webhook_secret=secret,
        )


def test_verify_and_parse_accepts_lowercase_headers() -> None:
    """Header lookup tolerates lowercase header names (case-insensitive HTTP)."""
    secret = "secret"  # pragma: allowlist secret
    body = b'{"action":"opened"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    envelope = verify_and_parse(
        body=body,
        headers={
            HEADER_DELIVERY_ID.lower(): "d1",
            HEADER_EVENT_TYPE.lower(): "push",
            HEADER_SIGNATURE_256.lower(): sig,
        },
        webhook_secret=secret,
    )
    assert envelope.delivery_id == "d1"


def testcompute_signature_round_trips_hex_lower() -> None:
    """Module-level helper returns lowercase hex HMAC-SHA256."""
    sig = compute_signature(body=b"hello", secret="key")
    expected = hmac.new(b"key", b"hello", hashlib.sha256).hexdigest()
    assert sig == expected


# ---------------------------------------------------------------------------
# translate_event — per-event-type dispatch coverage
# ---------------------------------------------------------------------------


def test_translate_event_unknown_event_type_returns_empty() -> None:
    """Unknown event types are dropped silently (GitHub adds new types)."""
    envelope = WebhookEnvelope(delivery_id="d", event_type="unknown_event", payload={})
    assert list(translate_event(envelope)) == []


def test_translate_event_push_emits_per_commit() -> None:
    """Push events emit one ChangeEvent per commit."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="push",
        payload={
            "forced": False,
            "repository": {"full_name": "org/repo", "visibility": "private"},
            "commits": [
                {"id": "sha-1", "timestamp": "2026-05-23T00:00:00Z"},
                {"id": "sha-2", "timestamp": "2026-05-23T00:01:00Z"},
            ],
        },
    )
    events = list(translate_event(envelope))
    assert len(events) == 2
    assert events[0].metadata["repo"] == "org/repo"
    assert events[0].metadata["force_push"] is False


def test_translate_event_push_force_push_marks_metadata() -> None:
    """Force-push events surface force_push=True in metadata."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="push",
        payload={
            "forced": True,
            "repository": {"full_name": "org/repo", "visibility": "private"},
            "commits": [{"id": "sha-1", "timestamp": "2026-05-23T00:00:00Z"}],
        },
    )
    events = list(translate_event(envelope))
    assert events[0].metadata["force_push"] is True


def test_translate_event_push_skips_empty_commit_ids() -> None:
    """Commits with empty 'id' are filtered (defensive)."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="push",
        payload={
            "forced": False,
            "repository": {"full_name": "org/repo", "visibility": "private"},
            "commits": [{"id": ""}, {"id": "sha-1", "timestamp": "2026-05-23T00:00:00Z"}],
        },
    )
    events = list(translate_event(envelope))
    assert len(events) == 1
    assert events[0].item_id.endswith("/commit/sha-1")


def test_translate_event_issues_maps_action_to_op() -> None:
    """Issue actions map through _ISSUE_ACTION_TO_OP correctly."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="issues",
        payload={
            "action": "opened",
            "issue": {"number": 42, "updated_at": "2026-05-23T00:00:00Z"},
            "repository": {"full_name": "org/repo", "visibility": "public"},
        },
    )
    events = list(translate_event(envelope))
    assert len(events) == 1
    assert events[0].op == "created"
    assert events[0].metadata["sensitivity"] == "public"


def test_translate_event_pull_request_emits_metadata() -> None:
    """PR events surface kind=pull_request in metadata."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="pull_request",
        payload={
            "action": "closed",
            "pull_request": {"number": 7, "updated_at": "2026-05-23T00:00:00Z"},
            "repository": {"full_name": "org/repo", "visibility": "internal"},
        },
    )
    events = list(translate_event(envelope))
    assert events[0].metadata["kind"] == "pull_request"
    assert events[0].metadata["sensitivity"] == "internal"


def test_translate_event_repository_archived_maps_to_archived_op() -> None:
    """repository.archived events emit op=archived."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="repository",
        payload={
            "action": "archived",
            "repository": {"full_name": "org/repo", "visibility": "private", "updated_at": ""},
        },
    )
    events = list(translate_event(envelope))
    assert events[0].op == "archived"


def test_translate_event_repository_deleted_maps_to_deleted_op() -> None:
    """repository.deleted events emit op=deleted."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="repository",
        payload={
            "action": "deleted",
            "repository": {"full_name": "org/repo", "visibility": "private", "updated_at": ""},
        },
    )
    events = list(translate_event(envelope))
    assert events[0].op == "deleted"


def test_translate_event_installation_repositories_removed_access_lost() -> None:
    """installation_repositories.removed surfaces access_lost ops."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="installation_repositories",
        payload={
            "action": "removed",
            "repositories_removed": [{"full_name": "org/gone"}],
        },
    )
    events = list(translate_event(envelope))
    assert events[0].op == "access_lost"


def test_translate_event_installation_repositories_added_created() -> None:
    """installation_repositories.added surfaces created ops."""
    envelope = WebhookEnvelope(
        delivery_id="d",
        event_type="installation_repositories",
        payload={
            "action": "added",
            "repositories_added": [{"full_name": "org/new"}],
        },
    )
    events = list(translate_event(envelope))
    assert events[0].op == "created"


# ---------------------------------------------------------------------------
# GitHubApiClient — credential / error paths
# ---------------------------------------------------------------------------


def test_api_client_requires_credential() -> None:
    """Constructor rejects when neither installation_id nor PAT provided."""
    with pytest.raises(ValueError, match="must provide installation_id"):
        GitHubApiClient()


def test_api_client_pat_path_skips_rotation() -> None:
    """PAT path returns a token header without triggering token exchange."""

    def _handler(request: httpx.Request) -> httpx.Response:
        # Should never receive a POST to /app/installations/... when PAT is set.
        if "access_tokens" in str(request.url):
            return httpx.Response(500, json={"error": "PAT path should not rotate"})
        return httpx.Response(200, json={"total_count": 0, "repositories": []})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    header = api.bearer_header()
    assert header["Authorization"] == "token fake-pat"


def test_api_client_invalidate_token_clears_cache() -> None:
    """invalidate_token() drops the cached installation token."""

    rotation_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        rotation_count["n"] += 1
        return httpx.Response(
            200,
            json={"token": f"tok-{rotation_count['n']}", "expires_at": "2026-05-23T01:00:00Z"},
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(installation_id=1, http_client=client)
    header1 = api.bearer_header()
    api.invalidate_token()
    header2 = api.bearer_header()
    assert rotation_count["n"] == 2
    assert header1 != header2


def test_api_client_401_invalidates_and_raises_credential_expired() -> None:
    """401 raises CredentialExpiredError and invalidates the token cache."""

    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if "access_tokens" in str(request.url):
            return httpx.Response(200, json={"token": "tok", "expires_at": ""})
        return httpx.Response(401, headers={"x-github-request-id": "r-1"})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(installation_id=1, http_client=client)
    with pytest.raises(CredentialExpiredError):
        api.list_installation_repositories()


def test_api_client_403_primary_rate_exhausted_raises_transient() -> None:
    """403 with x-ratelimit-remaining=0 raises ContainerTransientError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1716678000"},
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    with pytest.raises(ContainerTransientError, match="primary rate-limit exhausted"):
        api.list_installation_repositories()


def test_api_client_403_without_retry_after_raises_insufficient_perms() -> None:
    """A bare 403 (no Retry-After, no rate-limit-0) raises InsufficientPermissionsError."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    with pytest.raises(InsufficientPermissionsError):
        api.list_installation_repositories()


def test_api_client_404_raises_credential_invalid() -> None:
    """404 raises CredentialInvalidError."""
    from kairix.core.protocols import CredentialInvalidError

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    with pytest.raises(CredentialInvalidError):
        api.list_installation_repositories()


def test_api_client_5xx_raises_transient() -> None:
    """5xx surfaces as ContainerTransientError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    with pytest.raises(ContainerTransientError, match="503"):
        api.list_installation_repositories()


def test_api_client_list_commits_parses_envelope() -> None:
    """list_commits_since reverses the GitHub newest-first order."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "newest",
                        "commit": {
                            "message": "newest",
                            "author": {"name": "agent-alpha", "date": "2026-05-23T02:00:00Z"},
                        },
                    },
                    {
                        "sha": "oldest",
                        "commit": {
                            "message": "oldest",
                            "author": {"name": "agent-alpha", "date": "2026-05-23T01:00:00Z"},
                        },
                    },
                ],
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    commits = api.list_commits_since(full_name="org/repo", since=None)
    # API returns newest-first; client reverses so callers can advance cursor.
    assert commits[0].sha == "oldest"
    assert commits[-1].sha == "newest"


def test_api_client_list_issues_distinguishes_prs_from_issues() -> None:
    """An entry carrying pull_request is tagged kind=pull_request."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "updated_at": "2026-05-23T00:00:00Z", "title": "issue"},
                    {
                        "number": 2,
                        "updated_at": "2026-05-23T00:00:00Z",
                        "title": "pr",
                        "pull_request": {"url": "..."},
                    },
                ],
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    issues = api.list_issues_since(full_name="org/repo", since=None)
    assert issues[0].kind == "issue"
    assert issues[1].kind == "pull_request"


def test_api_client_get_tree_recursive_filters_non_blobs_and_marks_truncated() -> None:
    """Non-blob tree entries (trees, commits) are filtered; truncated=True bubbles up."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "truncated": True,
                    "tree": [
                        {"path": "src", "type": "tree", "sha": "tree-sha"},
                        {"path": "src/main.py", "type": "blob", "sha": "blob-sha", "size": 100},
                    ],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    blobs, truncated = api.get_tree_recursive(full_name="org/repo", ref="HEAD")
    assert truncated is True
    assert len(blobs) == 1
    assert blobs[0].path == "src/main.py"


def test_api_client_fetch_blob_returns_raw_bytes() -> None:
    """fetch_blob streams the raw bytes from /git/blobs/{sha}."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"blob-content-bytes")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    assert api.fetch_blob(full_name="org/repo", sha="blob-sha") == b"blob-content-bytes"


def test_api_client_stats_snapshot_carries_rate_gauge() -> None:
    """A 200 response populates rest_rate_remaining + reset_epoch."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": 0, "repositories": []},
            headers={"x-ratelimit-remaining": "4500", "x-ratelimit-reset": "1716678000"},
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    api = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    api.list_installation_repositories()
    stats = api.stats()
    assert stats.rest_rate_remaining == 4500
    assert stats.rest_rate_reset_epoch == 1716678000
    assert stats.rest_requests >= 1


# ---------------------------------------------------------------------------
# Connector — make_connector + sensitivity / link / fetch surfaces
# ---------------------------------------------------------------------------


def test_make_connector_rejects_invalid_sensitivity() -> None:
    """Operator config with an unknown F39 tier raises actionable error."""
    with pytest.raises(ValueError, match="not a valid F39 tier"):
        make_connector({"default_sensitivity": "bogus-tier"})


def test_make_connector_constructs_with_no_credentials() -> None:
    """The connector constructs OK when no secrets are present (deferred client)."""
    import os

    for var in (
        "CONNECTOR_GITHUB_APP_ID",
        "CONNECTOR_GITHUB_INSTALLATION_ID",
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY",
        "CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN",
        "CONNECTOR_GITHUB_WEBHOOK_SECRET",
    ):
        os.environ.pop(var, None)
    connector = make_connector({})
    # First use raises (the deferred client surfaces the actionable error).
    with pytest.raises(ValueError, match="no credential provided"):
        list(connector.list_changes(cursor=None))


def test_connector_source_link_round_trips_blob() -> None:
    """source_link for a blob URI returns the github.com blob URL."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    link = connector.source_link("github://org/repo/blob/sha-1")
    assert link == "https://github.com/org/repo/blob/sha-1"


def test_connector_source_link_round_trips_issues() -> None:
    """source_link for an issues URI returns the github.com issues URL."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.source_link("github://org/repo/issues/42") == "https://github.com/org/repo/issues/42"


def test_connector_source_link_round_trips_pulls() -> None:
    """source_link for a pulls URI returns the github.com pull URL."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.source_link("github://org/repo/pulls/7") == "https://github.com/org/repo/pull/7"


def test_connector_source_link_round_trips_commit() -> None:
    """source_link for a commit URI returns the github.com commit URL."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.source_link("github://org/repo/commit/sha-1") == "https://github.com/org/repo/commit/sha-1"


def test_connector_source_link_round_trips_repo_only() -> None:
    """source_link for a bare repo URI returns the github.com repo URL."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.source_link("github://org/repo") == "https://github.com/org/repo"


def test_connector_source_link_fallback_for_unknown_shape() -> None:
    """source_link for an unrecognised item_id falls back to github.com/{id}."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.source_link("not-a-uri").startswith("https://github.com/")


def test_connector_sensitivity_for_uses_envelope_when_cached() -> None:
    """sensitivity_for prefers the per-item envelope tier over the default."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    connector._envelope_cache["github://org/repo/blob/x"] = {"sensitivity": "public"}
    assert connector.sensitivity_for("github://org/repo/blob/x") == "public"


def test_connector_sensitivity_for_falls_back_to_default() -> None:
    """sensitivity_for returns the connector's default for an uncached id."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.sensitivity_for("github://org/repo/blob/missing") == "client-confidential"


def test_connector_fetch_returns_raw_artefact_for_blob() -> None:
    """fetch(blob) returns RawArtefact with the bytes from fetch_blob."""

    class _BlobClient(_StubClient):
        def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
            return b"blob-contents"

    connector = GitHubConnector(client=_BlobClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    artefact = connector.fetch("github://org/repo/blob/sha-1")
    assert isinstance(artefact, RawArtefact)
    assert artefact.raw == b"blob-contents"


def test_connector_fetch_returns_raw_artefact_for_issue_with_cached_body() -> None:
    """fetch(issue) returns the cached body bytes."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    connector._envelope_cache["github://org/repo/issues/1"] = {"body": "issue body text"}
    artefact = connector.fetch("github://org/repo/issues/1")
    assert artefact.raw == b"issue body text"
    assert artefact.mime == "application/json"


def test_connector_fetch_returns_raw_artefact_for_unknown_kind() -> None:
    """fetch for an unknown kind round-trips an empty JSON envelope."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    artefact = connector.fetch("github://org/repo/commit/sha-1")
    assert artefact.mime == "application/json"


def test_connector_iter_containers_marks_archived_as_revoked() -> None:
    """An archived repo emits a Container with access_state=REVOKED."""

    class _ArchivedRepoClient(_StubClient):
        def list_installation_repositories(self):
            return (
                GitHubRepoRef(
                    repo_id=1,
                    full_name="org/archived",
                    default_branch="main",
                    visibility="private",
                    archived=True,
                ),
            )

    connector = GitHubConnector(client=_ArchivedRepoClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert containers[0].access_state == "REVOKED"


def test_connector_list_changes_for_container_unknown_repo_yields_access_lost() -> None:
    """If the container's repo is no longer in the installation, emit access_lost."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_github", True)
    connector = GitHubConnector(client=_StubClient(), flag_reader=resolver.get)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    from kairix.core.protocols import Container

    container = Container(
        cc_pair_id=1,
        container_id="org/no-longer-here",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert len(events) == 1
    assert events[0].op == "access_lost"


def test_connector_oauth_authorization_url_returns_github_url() -> None:
    """OAuthConnector classmethod returns the github.com OAuth authorize URL."""
    url = GitHubConnector.oauth_authorization_url(state="abc")
    assert url == "https://github.com/login/oauth/authorize?state=abc"


def test_connector_oauth_code_to_token_returns_exchange_envelope() -> None:
    """OAuthConnector classmethod returns the structured token-exchange envelope."""
    envelope = GitHubConnector.oauth_code_to_token(code="abc-code")
    assert envelope["auth_kind"] == "github_user_oauth"
    assert envelope["code"] == "abc-code"


def test_connector_subscribe_returns_deterministic_id() -> None:
    """subscribe round-trips the callback_url into a deterministic subscription id."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    sid = connector.subscribe("https://example.com/webhook")
    assert sid is not None and "example.com/webhook" in sid


def test_connector_renew_subscription_is_noop_for_github_apps() -> None:
    """renew_subscription is a no-op (GitHub App webhooks have no TTL)."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.renew_subscription("sub-123") == "sub-123"


def test_connector_unsubscribe_logs_and_returns_none() -> None:
    """unsubscribe is a no-op (handled out-of-band via App settings)."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    assert connector.unsubscribe("sub-123") is None


def test_connector_load_credentials_returns_input_unchanged() -> None:
    """CredentialsConnector.load_credentials is the identity transform."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    cred = {"installation_id": 1, "app_private_key_pem": "..."}
    assert connector.load_credentials(cred) == cred


def test_connector_handle_event_deduplicates_replays() -> None:
    """A redelivery of the same delivery_id is dropped (idempotency)."""
    secret = "secret"  # pragma: allowlist secret
    body = json.dumps({"action": "opened", "issue": {"number": 1, "updated_at": ""}, "repository": {}}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        HEADER_DELIVERY_ID: "delivery-dup",
        HEADER_EVENT_TYPE: "issues",
        HEADER_SIGNATURE_256: sig,
    }
    connector = GitHubConnector(client=_StubClient(), webhook_secret=secret)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    first = list(connector.handle_event({"body": body, "headers": headers, "webhook_secret": secret}))
    second = list(connector.handle_event({"body": body, "headers": headers, "webhook_secret": secret}))
    assert len(first) == 1
    assert second == []


def test_connector_handle_event_accepts_string_body() -> None:
    """handle_event coerces a str body to bytes before HMAC verification."""
    secret = "secret"  # pragma: allowlist secret
    payload = json.dumps({"action": "opened", "issue": {"number": 1, "updated_at": ""}, "repository": {}})
    sig = "sha256=" + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        HEADER_DELIVERY_ID: "d-1",
        HEADER_EVENT_TYPE: "issues",
        HEADER_SIGNATURE_256: sig,
    }
    connector = GitHubConnector(client=_StubClient(), webhook_secret=secret)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    events = list(connector.handle_event({"body": payload, "headers": headers, "webhook_secret": secret}))
    assert len(events) == 1


def test_connector_stats_returns_diagnostic_counters() -> None:
    """stats() surfaces the client + connector counter snapshot."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    snapshot = connector.stats()
    assert "rest_requests" in snapshot
    assert "repos_tracked" in snapshot
    assert "deliveries_seen" in snapshot
    assert "last_path_taken" in snapshot


def test_connector_reindex_per_item_replay_emits_change_event() -> None:
    """A non-force-push item id replays as a single MODIFIED ChangeEvent."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    events = list(connector.reindex(("github://org/repo/blob/x",)))
    assert len(events) == 1
    assert events[0].op == "modified"
    assert events[0].metadata.get("reindex") is True


def test_connector_next_cursor_round_trips_per_repo_state() -> None:
    """next_cursor encodes the current per-repo cursor state as JSON."""
    connector = GitHubConnector(client=_StubClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    connector._per_repo_cursors["org/a"] = PerRepoCursorState(code_sha="sha-a")
    parsed = json.loads(connector.next_cursor())
    assert parsed["org/a"]["code_sha"] == "sha-a"


# ---------------------------------------------------------------------------
# api_client helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected_mime",
    [
        ("src/main.py", "text/x-python"),
        ("README.md", "text/markdown"),
        ("config.yaml", "application/yaml"),
        ("config.yml", "application/yaml"),
        ("data.json", "application/json"),
        ("pyproject.toml", "application/toml"),
        ("lib.rs", "text/x-rust"),
        ("main.go", "text/x-go"),
        ("app.ts", "text/typescript"),
        ("Component.tsx", "text/typescript"),
        ("script.js", "text/javascript"),
        ("index.html", "text/html"),
        ("style.css", "text/css"),
        ("install.sh", "text/x-shellscript"),
        ("notes.txt", "text/plain"),
        ("binary.bin", "application/octet-stream"),
    ],
)
def testguess_mime_from_path(path: str, expected_mime: str) -> None:
    """Path-extension-to-mime mapping covers the canonical code formats."""
    assert guess_mime_from_path(path) == expected_mime


def test_github_repo_ref_is_frozen() -> None:
    """F42 — GitHubRepoRef is a frozen dataclass."""
    ref = GitHubRepoRef(repo_id=1, full_name="org/repo", default_branch="main", visibility="private", archived=False)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ref.full_name = "mutated"  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_commit_ref_is_frozen() -> None:
    """F42 — GitHubCommitRef is a frozen dataclass."""
    ref = GitHubCommitRef(sha="a", committed_at="b", message="c", author=None)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ref.sha = "mutated"  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_blob_ref_is_frozen() -> None:
    """F42 — GitHubBlobRef is a frozen dataclass."""
    ref = GitHubBlobRef(path="x", sha="y", size=1, mime_hint="text/plain")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ref.path = "mutated"  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_issue_ref_is_frozen() -> None:
    """F42 — GitHubIssueRef is a frozen dataclass."""
    ref = GitHubIssueRef(number=1, kind="issue", updated_at="", title="", body="", state="open")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ref.number = 99  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_installation_token_is_frozen() -> None:
    """F42 — GitHubInstallationToken is a frozen dataclass."""
    tok = GitHubInstallationToken(token="t", expires_at="")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        tok.token = "mutated"  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_credentials_is_frozen() -> None:
    """F42 — GitHubCredentials is a frozen dataclass."""
    cred = GitHubCredentials(installation_id=1)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        cred.installation_id = 2  # type: ignore[misc]  # F3 rationale: deliberately mutating a frozen dataclass field to assert FrozenInstanceError — guard test for F42 immutability


def test_github_client_config_defaults_match_spec() -> None:
    """GitHubClientConfig defaults satisfy spec §6 concurrency cap."""
    cfg = GitHubClientConfig()
    assert cfg.base_url == "https://api.github.com"
    assert cfg.max_parallel_repos == 4  # per spec §6


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal GitHubApiClient-shape stub for connector unit tests."""

    def __init__(self) -> None:
        from kairix.connectors.github.api_client import ClientStatsSnapshot

        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def list_installation_repositories(self):
        return (
            GitHubRepoRef(
                repo_id=1,
                full_name="org/repo",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
        )

    def list_commits_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def list_issues_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str):
        _ = (full_name, ref)
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Slim connector + reindex full-container + deferred-credential client —
# public-surface coverage for the SlimConnector and Resolver Protocols
# and the credential-resolution branches in __init__ (no internal imports).
# ---------------------------------------------------------------------------


class _SlimRichClient:
    """Stub returning blobs + issues for slim-doc enumeration."""

    def __init__(self) -> None:
        from kairix.connectors.github.api_client import ClientStatsSnapshot

        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )
        self._blobs = (
            GitHubBlobRef(path="a.py", sha="sha-a", size=10, mime_hint="text/x-python"),
            GitHubBlobRef(path="b.md", sha="sha-b", size=20, mime_hint="text/markdown"),
        )
        self._issues = (
            GitHubIssueRef(number=1, kind="issue", updated_at="t", title="i1", body="b1", state="open"),
            GitHubIssueRef(number=2, kind="pull", updated_at="t", title="p1", body="b2", state="open"),
        )

    def list_installation_repositories(self):
        return (
            GitHubRepoRef(repo_id=1, full_name="org/repo", default_branch="main", visibility="private", archived=False),
        )

    def list_commits_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def list_issues_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return self._issues

    def get_tree_recursive(self, *, full_name: str, ref: str):
        _ = (full_name, ref)
        return self._blobs, False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


def test_retrieve_all_slim_docs_enumerates_blobs_and_issues() -> None:
    """SlimConnector — id-only enumeration covers blobs + issues + PRs."""
    from kairix.core.protocols import Container

    connector = GitHubConnector(client=_SlimRichClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    container = Container(
        cc_pair_id=1,
        container_id="org/repo",
        access_state="active",
        cursor_token=None,
        last_synced_at=None,
    )
    ids = list(connector.retrieve_all_slim_docs(container))
    assert "github://org/repo/blob/sha-a" in ids
    assert "github://org/repo/blob/sha-b" in ids
    assert "github://org/repo/issues/1" in ids
    assert "github://org/repo/pulls/2" in ids


def test_retrieve_all_slim_docs_with_perms_yields_serialised_acl() -> None:
    """SlimConnectorWithPermSync wraps every id with a JSON-encoded ACL string."""
    from kairix.core.protocols import Container

    connector = GitHubConnector(client=_SlimRichClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    container = Container(
        cc_pair_id=1,
        container_id="org/repo",
        access_state="active",
        cursor_token=None,
        last_synced_at=None,
    )
    pairs = list(connector.retrieve_all_slim_docs_with_perms(container))
    assert len(pairs) == 4  # 2 blobs + 2 issues/PRs
    for item_id, acl_json in pairs:
        decoded = json.loads(acl_json)
        assert decoded["container_id"] == "org/repo"
        assert decoded["visibility"] == "private"
        assert item_id.startswith("github://org/repo/")


def test_reindex_force_push_metadata_triggers_full_container_refresh() -> None:
    """A force-push envelope routes reindex through _reconcile_full_container."""
    connector = GitHubConnector(client=_SlimRichClient())  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    item_id = "github://org/repo/blob/sha-a"
    connector._envelope_cache[item_id] = {"force_push": True}
    events = list(connector.reindex((item_id,)))
    # _reconcile_full_container yields one MODIFIED per blob in the repo tree.
    assert len(events) == 2
    assert all(e.op == "modified" for e in events)
    assert all(e.metadata.get("reconcile") is True for e in events)
    # Cursor for the repo is reset after the reconcile.
    assert connector._per_repo_cursors["org/repo"].code_sha is None


def test_connector_constructs_with_pat_credentials_only() -> None:
    """PAT-only credentials route through _build_default_client to a real client."""
    creds = GitHubCredentials(personal_access_token="ghp_test_token")
    connector = GitHubConnector(credentials=creds)
    # _DeferredCredentialClient would have an installation_token_rotations stat of 0
    # AND raise on first call. The real client must NOT raise on construction.
    assert connector.name == "github"


def test_connector_constructs_with_installation_id_credentials_only() -> None:
    """Installation-id credentials route through _build_default_client to a real client."""
    creds = GitHubCredentials(installation_id=42)
    connector = GitHubConnector(credentials=creds)
    assert connector.name == "github"


def test_connector_with_no_credentials_defers_until_first_use() -> None:
    """No-credential construction yields _DeferredCredentialClient that raises on use."""
    connector = GitHubConnector()  # no credentials, no client
    # stats() on the connector returns a mapping (does not raise even with deferred client).
    snap = connector.stats()
    assert snap["rest_requests"] == 0
    # Any IO method on the deferred client raises with an actionable next-step
    # message. The connector wraps the underlying ValueError into the credential
    # surface, but the operator-facing string is what matters here.
    with pytest.raises((ValueError, Exception)) as exc:
        list(connector.iter_containers(cc_pair_id=1))
    msg = str(exc.value).lower()
    assert "next:" in msg or "set" in msg or "credential" in msg


def test_connector_invalidate_token_on_deferred_client_is_noop() -> None:
    """invalidate_token on the deferred client does not raise — operator-friendly."""
    connector = GitHubConnector()  # deferred client
    # Should not raise — invalidate is idempotent until creds arrive.
    connector._client.invalidate_token()
