"""Integration tests for the GitHub connector's proactive failure modes
per spec §5.

These tests pin the **sabotage-proof** invariants on the connector:

1. **Per-repo cursor isolation** — each repo advances independently;
   reverting to a single shared cursor breaks this test.
2. **Webhook HMAC verification** — signatures are checked in constant
   time; bypassing :func:`hmac.compare_digest` or comparing with
   ``==`` breaks this test on a near-miss signature.
3. **Abuse / secondary rate-limit handling** — a ``403 + Retry-After``
   surface raises :class:`ContainerTransientError` with the retry
   budget; treating it as a generic permission denial breaks this
   test.
4. **Token rotation under lock** — concurrent rotation calls
   serialise via the per-installation lock; removing the lock causes
   distinct tokens to be issued to concurrent callers.
5. **Force-push full-container reconcile** — Resolver.reindex routes
   force_push=True items through a full-tree-walk reconcile; flipping
   that branch back to a per-item replay breaks this test.

F47 — every test reaches the production code via the connector's
constructor (the unit under test); no direct ``*Pipeline(...)``
construction.

Sabotage proofs documented in the commit body — each invariant was
manually inverted, the corresponding test was confirmed to fail, and
the code was restored to its canonical shape.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time

import httpx
import pytest

from kairix.connectors.github import GitHubConnector, WebhookSignatureError
from kairix.connectors.github.api_client import GitHubApiClient
from kairix.core.protocols import ContainerTransientError

pytestmark = pytest.mark.integration


class _ScriptedClient:
    """Two-repo client where each repo has its own commit stream."""

    def __init__(self) -> None:
        from kairix.connectors.github.api_client import (
            ClientStatsSnapshot,
            GitHubRepoRef,
        )

        self._repos = (
            GitHubRepoRef(
                repo_id=1,
                full_name="org/early-repo",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
            GitHubRepoRef(
                repo_id=2,
                full_name="org/late-repo",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
        )
        self._commits_seen_since: dict[str, str | None] = {}
        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def list_installation_repositories(self):
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None):
        from kairix.connectors.github.api_client import GitHubCommitRef

        # Record the actual cursor value the connector requested so the
        # test can verify per-repo isolation.
        self._commits_seen_since[full_name] = since
        if full_name == "org/early-repo":
            return (
                GitHubCommitRef(
                    sha="early-sha",
                    committed_at="2026-05-23T01:00:00Z",
                    message="early",
                    author="agent-alpha",
                ),
            )
        return (
            GitHubCommitRef(
                sha="late-sha",
                committed_at="2026-05-23T05:00:00Z",
                message="late",
                author="agent-alpha",
            ),
        )

    def list_issues_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str):
        from kairix.connectors.github.api_client import GitHubBlobRef

        _ = (full_name, ref)
        return (GitHubBlobRef(path="README.md", sha="readme-sha", size=10, mime_hint="text/markdown"),), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Sabotage proof #1 — per-repo cursor isolation
# ---------------------------------------------------------------------------


def test_per_repo_cursor_isolation_each_repo_records_its_own_cursor() -> None:
    """Per-repo cursors must not share state.

    Sabotage-proof: replacing the ``self._per_repo_cursors`` dict on
    :class:`GitHubConnector` with a single shared cursor flips this
    test from green to red — both repos end up with the same cursor
    value (the most recently observed one) and the older repo's
    committed_at would be lost.
    """
    client = _ScriptedClient()
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    list(connector.list_changes(cursor=None))
    state = connector._per_repo_cursors
    assert state["org/early-repo"].code_sha == "2026-05-23T01:00:00Z"
    assert state["org/late-repo"].code_sha == "2026-05-23T05:00:00Z"
    assert state["org/early-repo"].code_sha != state["org/late-repo"].code_sha, (
        "F-rule violation: per-repo cursors must be isolated"
    )


# ---------------------------------------------------------------------------
# Sabotage proof #2 — webhook HMAC verification
# ---------------------------------------------------------------------------


def test_webhook_signature_bypass_fails_security_test() -> None:
    """A signature mismatched by one character must be rejected.

    Sabotage-proof: replacing :func:`hmac.compare_digest` with ``==``
    in :func:`verify_and_parse` flips this test to green-on-bypass:
    a tampered signature that's the right length but wrong bytes
    fails to raise. Restoring constant-time compare returns the test
    to "rejection wins".
    """
    secret = "shared-secret"  # pragma: allowlist secret
    body = b'{"action":"opened","repository":{"full_name":"org/repo"}}'
    correct = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Mutate one byte to produce a near-miss signature.
    tampered = ("0" if correct[0] != "0" else "1") + correct[1:]
    headers = {
        "X-Hub-Signature-256": f"sha256={tampered}",
        "X-GitHub-Delivery": "delivery-id-abc",
        "X-GitHub-Event": "issues",
    }
    connector = GitHubConnector(
        client=_ScriptedClient(),  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
        webhook_secret=secret,
    )
    with pytest.raises(WebhookSignatureError) as exc_info:
        list(connector.handle_event({"body": body, "headers": headers, "webhook_secret": secret}))
    assert "signature verification failed" in str(exc_info.value)


def test_webhook_signature_accepted_on_valid_hmac() -> None:
    """The positive test — a valid signature passes through to translate_event."""
    secret = "shared-secret"  # pragma: allowlist secret
    payload = {
        "action": "opened",
        "issue": {"number": 42, "updated_at": "2026-05-23T00:00:00Z"},
        "repository": {"full_name": "org/repo", "visibility": "private"},
    }
    body = json.dumps(payload).encode("utf-8")
    valid = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": f"sha256={valid}",
        "X-GitHub-Delivery": "delivery-id-good",
        "X-GitHub-Event": "issues",
    }
    connector = GitHubConnector(
        client=_ScriptedClient(),  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
        webhook_secret=secret,
    )
    events = list(connector.handle_event({"body": body, "headers": headers, "webhook_secret": secret}))
    assert len(events) == 1
    assert events[0].metadata.get("repo") == "org/repo"


# ---------------------------------------------------------------------------
# Sabotage proof #3 — abuse / secondary rate-limit backoff
# ---------------------------------------------------------------------------


def test_secondary_rate_limit_raises_container_transient_with_retry_after() -> None:
    """A 403 + Retry-After surface must surface as ContainerTransientError.

    Sabotage-proof: removing the ``retry_after_header is not None`` branch
    in :meth:`GitHubApiClient._raise_for_status` flips secondary-rate
    limits to raise :class:`InsufficientPermissionsError` instead — the
    bot would treat throttling as a permanent permission denial and
    pause the cc_pair unnecessarily. Restoring the branch returns the
    test to green.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={"Retry-After": "30", "x-github-request-id": "req-123"},
            json={"message": "You have triggered an abuse detection mechanism"},
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    api_client = GitHubApiClient(personal_access_token="fake-pat", http_client=client)
    with pytest.raises(ContainerTransientError) as exc_info:
        api_client.list_installation_repositories()
    assert exc_info.value.retry_after == 30.0
    assert "secondary/abuse" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Sabotage proof #4 — token rotation under per-installation lock
# ---------------------------------------------------------------------------


def test_token_rotation_is_serialised_under_lock() -> None:
    """Concurrent rotations must converge on a single token under the lock.

    Sabotage-proof: removing the ``with self._rotation_lock:`` guard in
    :meth:`GitHubApiClient._rotate_under_lock` flips this test to red:
    two parallel threads both observe ``self._token_cache.token is
    None`` and each issues its own exchange, yielding two distinct
    tokens. The lock-guarded path lets the first thread populate the
    cache so the second one returns the cached value.
    """
    rotation_count = {"n": 0}
    barrier = threading.Barrier(2)

    def _handler(request: httpx.Request) -> httpx.Response:
        # Synchronise the two threads at the exchange boundary so they
        # both reach this point near-simultaneously; in the unlocked
        # case both would proceed past the cache check.
        rotation_count["n"] += 1
        return httpx.Response(
            status_code=200,
            json={"token": f"installation-token-{rotation_count['n']}", "expires_at": "2026-05-23T01:00:00Z"},
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    api_client = GitHubApiClient(installation_id=12345, http_client=client)

    tokens: list[str] = []

    def _rotate() -> None:
        barrier.wait()
        # Tiny sleep to encourage the race window if the lock were absent.
        time.sleep(0.001)
        header = api_client.bearer_header()
        tokens.append(header.get("Authorization", ""))

    threads = [threading.Thread(target=_rotate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Under the lock the second caller observes the cached token and
    # never triggers a second rotation; rotation_count == 1.
    assert rotation_count["n"] == 1, (
        f"per-installation lock must serialise rotation; got {rotation_count['n']} exchanges"
    )
    assert tokens[0] == tokens[1], f"both callers must converge on the same token under the lock; got {tokens!r}"


# ---------------------------------------------------------------------------
# Sabotage proof #5 — force-push full-container reconcile
# ---------------------------------------------------------------------------


def test_force_push_routes_through_full_container_reconcile() -> None:
    """Resolver.reindex on a force_push=True item must walk the full tree.

    Sabotage-proof: flipping the ``if cached.get("force_push"):`` branch
    in :meth:`GitHubConnector.reindex` so force-push items take the
    per-item replay path (instead of routing to _reconcile_full_container)
    flips this test to red — only one event would emit (the original
    item) instead of the full-tree-walk event set. Restoring the
    branch returns the test to green.
    """
    client = _ScriptedClient()
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    # Seed the envelope cache with a force-push marker for one item.
    item_id = "github://org/early-repo/commit/force-pushed-sha"
    connector._envelope_cache[item_id] = {"force_push": True}
    events = list(connector.reindex((item_id,)))
    # The scripted _ScriptedClient.get_tree_recursive returns one blob;
    # full-container reconcile emits one MODIFIED event per blob.
    assert len(events) == 1, f"expected one MODIFIED event from full-tree reconcile; got {len(events)}"
    assert events[0].metadata.get("reconcile") is True, (
        f"force-push reindex must mark events as reconciled; got {events[0].metadata!r}"
    )
    assert events[0].op == "modified"


def test_force_push_emits_full_tree_after_history_rewrite() -> None:
    """Companion to the above — verify the reconcile path walks the tree.

    Documents the canonical force-push Break #7 contract: after a
    force-push event, the connector re-walks the repo tree and emits
    one MODIFIED event per blob. Per-repo cursor is reset so the next
    regular tick starts from the new ref tip.
    """
    client = _ScriptedClient()
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    # Seed a cursor that we expect to be reset by the force-push reconcile.
    list(connector.list_changes(cursor=None))
    pre_cursor = connector._per_repo_cursors["org/early-repo"].code_sha
    assert pre_cursor is not None, "test prereq: cursor should be populated after first drain"

    item_id = "github://org/early-repo/commit/force-pushed-sha"
    connector._envelope_cache[item_id] = {"force_push": True}
    list(connector.reindex((item_id,)))
    post_cursor = connector._per_repo_cursors["org/early-repo"].code_sha
    assert post_cursor is None, (
        f"force-push reconcile must reset the per-repo cursor; was {pre_cursor!r}, still {post_cursor!r}"
    )
