"""Integration tests for the ``topology_v2_github`` flag.

Both branches of :meth:`GitHubConnector.list_changes_for_container` +
:meth:`GitHubConnector.load_hierarchy` exercised end-to-end:

  * **Flag OFF** — the Wave B shim path: list_changes_for_container
    delegates to legacy ``list_changes``; load_hierarchy emits one
    root ORG node only.
  * **Flag ON** — the Wave E per-container path: scoped drain;
    load_hierarchy emits Org → repo → dirs parent-before-child (F58).

F47 — both branches reach the production code via the connector's
constructor (the unit under test); no direct ``*Pipeline(...)``
construction in this file.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through the connector's ``flag_reader`` DI seam — no @patch
/ monkeypatch.

Sabotage proof (executed by agent, restored on completion):
inverting the if/else in :meth:`load_hierarchy` so OFF emits the full
tree and ON emits the root only flips both
:func:`test_topology_v2_github_flag_off_legacy` and
:func:`test_topology_v2_github_flag_on_per_repo` to red. Restoring
the canonical branch direction returns both to green.
"""

from __future__ import annotations

import pytest

from kairix.connectors.github import GitHubConnector
from kairix.core.protocols import Container
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


class _ScriptedClient:
    """Scripted client emitting two repos under one org."""

    def __init__(self) -> None:
        from kairix.connectors.github.api_client import (
            ClientStatsSnapshot,
            GitHubRepoRef,
        )

        self._repos = (
            GitHubRepoRef(
                repo_id=1,
                full_name="agent-alpha-org/repo-one",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
            GitHubRepoRef(
                repo_id=2,
                full_name="agent-alpha-org/repo-two",
                default_branch="main",
                visibility="public",
                archived=False,
            ),
        )
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

        _ = since
        return (
            GitHubCommitRef(
                sha=f"sha-{full_name}",
                committed_at="2026-05-23T01:00:00Z",
                message="seed",
                author="agent-alpha",
            ),
        )

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


def test_topology_v2_github_flag_off_legacy() -> None:
    """OFF — list_changes_for_container delegates to the legacy path."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_github", False)
    connector = GitHubConnector(
        client=_ScriptedClient(),  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
        flag_reader=resolver.get,
    )
    container = Container(
        cc_pair_id=1,
        container_id="agent-alpha-org/repo-one",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "legacy"
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) == 1, f"OFF must emit one root node only; got {len(nodes)}"
    assert nodes[0].raw_node_id == "github"


def test_topology_v2_github_flag_on_per_repo() -> None:
    """ON — list_changes_for_container scopes to one container; hierarchy is Org → repo."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_github", True)
    connector = GitHubConnector(
        client=_ScriptedClient(),  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
        flag_reader=resolver.get,
    )
    container = Container(
        cc_pair_id=1,
        container_id="agent-alpha-org/repo-one",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "scoped"
    assert len(events) == 1
    assert events[0].metadata.get("repo") == "agent-alpha-org/repo-one"

    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    # 1 org + 2 repos (no dirs from the empty tree) ≥ 3.
    assert len(nodes) >= 3, f"ON must emit org + per-repo nodes; got {len(nodes)}"
    # F58 — every non-root parent must reference a previously-emitted node.
    emitted: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in emitted, (
                f"F58 violation: parent {node.raw_parent_id!r} of node {node.raw_node_id!r} "
                f"not previously emitted (so far: {sorted(emitted)!r})"
            )
        emitted.add(node.raw_node_id)
