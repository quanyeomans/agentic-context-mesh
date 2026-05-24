"""End-to-end composed path test for the ``topology_v2_github`` flag.

F48 sibling for the Wave-E per-container pilot of the github
connector. Per F54, the flag has both branches covered by
integration tests; this E2E composes the ON-branch path against
the real :class:`GitHubConnector` + real factory shape.

Composed path under test:

  flag pinned via :class:`FakeFeatureFlagResolver`
    → real :class:`GitHubConnector` with a recording
      :class:`httpx.MockTransport` so no real HTTP egress happens
    → :meth:`iter_containers` emits one Container per repo
    → :meth:`list_changes_for_container` scopes the drain to that repo
    → :meth:`load_hierarchy` walks Org → repo → top-level-dir
      parent-before-child per F58.

Sabotage proof (executed by agent, restored on completion):
flipping the ``if not self._flag_reader(...)`` branch in
:meth:`GitHubConnector.list_changes_for_container` so the OFF branch
takes the scoped path makes ``_last_path_taken == "scoped"`` regardless
of flag state — this test fails with the OFF assertion. Restoring the
original branch direction returns the test to green.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.github import GitHubConnector
from kairix.connectors.github.api_client import GitHubApiClient, GitHubClientConfig
from kairix.core.protocols import Container
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


_TWO_REPOS = [
    {
        "id": 1,
        "full_name": "agent-alpha-org/repo-one",
        "default_branch": "main",
        "visibility": "private",
        "archived": False,
    },
    {
        "id": 2,
        "full_name": "agent-alpha-org/repo-two",
        "default_branch": "main",
        "visibility": "public",
        "archived": False,
    },
]


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/installation/repositories":
        return httpx.Response(200, json={"total_count": 2, "repositories": _TWO_REPOS})
    if path.endswith("/commits"):
        return httpx.Response(
            200,
            json=[
                {
                    "sha": f"sha-{request.url.host}",
                    "commit": {
                        "message": "seed",
                        "author": {"name": "agent-alpha", "date": "2026-05-23T01:00:00Z"},
                    },
                }
            ],
        )
    if path.endswith("/issues"):
        return httpx.Response(200, json=[])
    if "/git/trees/" in path:
        return httpx.Response(
            200,
            json={
                "sha": "tree-sha",
                "truncated": False,
                "tree": [
                    {"path": "src/main.py", "sha": "blob-1", "size": 100, "type": "blob"},
                    {"path": "docs/index.md", "sha": "blob-2", "size": 50, "type": "blob"},
                ],
            },
        )
    if path.startswith("/app/installations/"):
        return httpx.Response(200, json={"token": "tok", "expires_at": "2026-05-23T02:00:00Z"})
    return httpx.Response(404, json={"message": "unknown path " + path})


def _composed_connector(flag_value: bool) -> GitHubConnector:
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_github", flag_value)
    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport, base_url="https://api.github.com")
    api = GitHubApiClient(
        installation_id=999,
        http_client=http,
        config=GitHubClientConfig(base_url="https://api.github.com"),
    )
    return GitHubConnector(client=api, flag_reader=resolver.get)


@pytest.mark.e2e
def test_composed_topology_v2_github_flag_on_emits_per_repo_containers() -> None:
    """Flag ON: iter_containers + list_changes_for_container + load_hierarchy."""
    connector = _composed_connector(True)
    containers = list(connector.iter_containers(cc_pair_id=11))
    assert len(containers) == 2
    container_ids = {c.container_id for c in containers}
    assert container_ids == {"agent-alpha-org/repo-one", "agent-alpha-org/repo-two"}

    # Scoped drain.
    container = containers[0]
    events = list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "scoped"
    assert len(events) == 1
    assert events[0].metadata.get("repo") == container.container_id

    # Hierarchy: org + 2 repos + per-repo top-level dirs.
    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    assert len(nodes) >= 3
    emitted: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in emitted, (
                f"F58 violation: parent {node.raw_parent_id!r} of node {node.raw_node_id!r} "
                f"not previously emitted (emitted={sorted(emitted)!r})"
            )
        emitted.add(node.raw_node_id)


@pytest.mark.e2e
def test_composed_topology_v2_github_flag_off_legacy_shape() -> None:
    """Flag OFF: legacy delegation + single root ORG node."""
    connector = _composed_connector(False)
    container = Container(
        cc_pair_id=11,
        container_id="agent-alpha-org/repo-one",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "legacy"

    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    assert len(nodes) == 1
    assert nodes[0].raw_parent_id is None
    assert nodes[0].raw_node_id == "github"
