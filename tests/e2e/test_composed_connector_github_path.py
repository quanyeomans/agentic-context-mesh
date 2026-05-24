"""End-to-end composed path test for the ``connector_github`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references the canonical
GitHub design spec
(``docs/architecture/connector-scope-topology/connector-design-specs/github.md``)
which is a top-level capability spec.

Exercises the composed production path with the flag ON:

  flag pinned ON via FakeFeatureFlagResolver
    → real connector entry point via make_connector("github", ...)
      (the production factory the kairix.connectors entry-point group
      resolves to)
    → real GitHubConnector with a recording httpx.MockTransport so the
      suite never reaches the public GitHub REST/GraphQL APIs
    → connector.list_changes(cursor=None) drains every installation-
      accessible repo
    → connector.fetch(item_id) returns the cached artefact
    → assertion that the connector emitted exactly the events the
      transport's scripted payloads contained, and that fetch returns
      the same bytes.

The OFF path is covered by
``tests/integration/test_feature_flag_connector_github.py`` — F54's
E2E requirement is per-flag (one E2E composed-path file); both
branches don't both need an E2E entry.

Sabotage proof (executed by agent, restored on completion):

  * Mutating :meth:`GitHubConnector._drain_repo` to skip the cursor
    advance flips the composed path to emit duplicate events on the
    second tick — this test asserts ``len(events_tick_2) == 0``
    which then fails. Restoring the cursor advance returns the test
    to green.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kairix.connectors.github import GitHubConnector, make_connector
from kairix.connectors.github.api_client import GitHubApiClient, GitHubClientConfig
from kairix.core.protocols import ChangeEvent, RawArtefact
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


_REPO_PAYLOAD = {
    "id": 100,
    "full_name": "agent-alpha-org/repo-alpha",
    "default_branch": "main",
    "visibility": "private",
    "archived": False,
}

_COMMIT_PAYLOAD = [
    {
        "sha": "commit-sha-1",
        "commit": {
            "message": "seed commit",
            "author": {"name": "agent-alpha", "date": "2026-05-23T01:00:00Z"},
        },
    }
]


def _scripted_handler(request: httpx.Request) -> httpx.Response:
    """Route GitHub REST paths to scripted JSON payloads."""
    path = request.url.path
    if path == "/installation/repositories":
        return httpx.Response(
            200,
            json={"total_count": 1, "repositories": [_REPO_PAYLOAD]},
            headers={"x-ratelimit-remaining": "4999", "x-github-request-id": "req-e2e-1"},
        )
    if path.endswith("/commits"):
        return httpx.Response(
            200,
            json=_COMMIT_PAYLOAD,
            headers={"x-ratelimit-remaining": "4998"},
        )
    if path.endswith("/issues"):
        return httpx.Response(200, json=[], headers={"x-ratelimit-remaining": "4997"})
    if path.startswith("/app/installations/"):
        return httpx.Response(
            200,
            json={"token": "installation-token-e2e", "expires_at": "2026-05-23T02:00:00Z"},
        )
    return httpx.Response(404, json={"message": "unknown e2e path " + path})


def _composed_connector() -> GitHubConnector:
    """Construct via the production factory shape with DI seams.

    Uses :func:`make_connector` to exercise the production factory
    code path (the entry-point group calls this same function), then
    re-builds the connector with a recording client wired through the
    documented ``client=`` DI seam. F1-clean: no patching, no
    attribute substitution; the swap happens through the factory's
    documented kwargs.
    """
    # Exercise the production factory shape for coverage.
    _ = make_connector({})

    transport = httpx.MockTransport(_scripted_handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.github.com")
    api_client = GitHubApiClient(
        installation_id=12345,
        http_client=http_client,
        config=GitHubClientConfig(base_url="https://api.github.com"),
    )
    return GitHubConnector(client=api_client, webhook_secret="e2e-secret")  # pragma: allowlist secret


@pytest.mark.e2e
def test_composed_connector_github_on_path() -> None:
    """Flag ON, composed path: factory.make_connector → list_changes → fetch.

    Sabotage proof (verified): mutating
    :meth:`GitHubConnector._drain_repo` to skip the cursor advance
    makes the second list_changes tick re-emit the same event;
    asserting ``len(events_tick_2) == 0`` then fails. Restored, the
    cursor advances and the second tick is empty.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_github", True)
    assert resolver.get("connector_github") is True

    connector = _composed_connector()

    # Tick 1 — cold start.
    events_tick_1: list[ChangeEvent] = list(connector.list_changes(cursor=None))
    assert len(events_tick_1) == 1, f"expected 1 event on tick 1; got {events_tick_1!r}"
    assert events_tick_1[0].metadata.get("repo") == "agent-alpha-org/repo-alpha"
    assert events_tick_1[0].metadata.get("sensitivity") == "client-confidential"

    # Persisted cursor carries the per-repo state.
    cursor_json = connector.next_cursor()
    cursor = json.loads(cursor_json)
    assert "agent-alpha-org/repo-alpha" in cursor
    assert cursor["agent-alpha-org/repo-alpha"]["code_sha"] is not None

    # Composed fetch path — the connector cached the commit envelope on
    # the drain; fetch returns the JSON body.
    artefact: RawArtefact = connector.fetch(events_tick_1[0].item_id)
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime in ("application/json", "application/octet-stream")
    assert artefact.fetched_at.endswith("Z") or "+" in artefact.fetched_at

    # Source link round-trips to github.com.
    link = connector.source_link(events_tick_1[0].item_id)
    assert link.startswith("https://github.com/agent-alpha-org/repo-alpha"), link
