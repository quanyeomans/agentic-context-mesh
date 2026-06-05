"""F58 contract test for :class:`GitHubConnector.load_hierarchy`.

Filename matches the F58 detector's `test_*hierarchy*parent_before_child*`
glob — see ``scripts/checks/check_f58_hierarchy.py``.

Mechanically pins the org-before-repo-before-dir emission ordering per
spec §1 + F58. Sabotage-proof: swapping the
``_emit_orgs`` / ``_emit_repos`` calls in
:meth:`GitHubConnector.load_hierarchy` so repos emit before orgs flips
this test from green to red (the first repo's parent_id is missing
from the emitted set). Restoring the canonical order returns the
test to green.

Documented in the commit body: agent verified by manually flipping
the call order and confirming this test fails with
``F58 violation: node ... parent_id ... was not previously emitted``.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connectors.github import GitHubConnector
from kairix.core.protocols import HierarchyConnector, HierarchyNode

pytestmark = pytest.mark.contract


class _ScriptedClient:
    """Minimal scripted client emitting two repos under two orgs."""

    def __init__(self) -> None:
        from kairix.connectors.github.api_client import (
            ClientStatsSnapshot,
            GitHubRepoRef,
        )

        self._repos = (
            GitHubRepoRef(
                repo_id=1,
                full_name="org-alpha/repo-one",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
            GitHubRepoRef(
                repo_id=2,
                full_name="org-alpha/repo-two",
                default_branch="main",
                visibility="public",
                archived=False,
            ),
            GitHubRepoRef(
                repo_id=3,
                full_name="org-beta/repo-three",
                default_branch="main",
                visibility="internal",
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
        _ = (full_name, since)
        return ()

    def list_issues_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str):
        from kairix.connectors.github.api_client import GitHubBlobRef

        _ = (full_name, ref)
        return (
            GitHubBlobRef(path="src/main.py", sha="sha-main", size=100, mime_hint="text/x-python"),
            GitHubBlobRef(path="docs/README.md", sha="sha-readme", size=50, mime_hint="text/markdown"),
        ), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


def test_github_hierarchy_parent_before_child_orgs_then_repos_then_dirs() -> None:
    """Every emitted HierarchyNode's parent must be in the prior-emitted set.

    Post-cutover (task #132 — ``topology_v2_github`` retired) the
    connector always emits the full Org → repo → top-level-dir tree.
    The contract under test:

      * Every ``raw_parent_id`` that is not None must reference a
        previously-yielded ``raw_node_id`` within the same call.
      * Orgs emit before any of their repos; repos emit before any of
        their dirs.
    """
    connector = GitHubConnector(
        client=_ScriptedClient(),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
    )
    assert isinstance(connector, HierarchyConnector)
    nodes: list[HierarchyNode] = list(connector.load_hierarchy(cc_pair_id=7))
    assert nodes, "expected non-empty hierarchy"
    emitted: set[str] = set()
    by_id: dict[str, HierarchyNode] = {}
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in emitted, (
                f"F58 violation: node {node.raw_node_id!r} parent {node.raw_parent_id!r} "
                f"not previously emitted. emitted_so_far={sorted(emitted)!r}"
            )
        emitted.add(node.raw_node_id)
        by_id[node.raw_node_id] = node
    # Org nodes emit with raw_parent_id None.
    org_nodes = [n for n in nodes if n.raw_parent_id is None]
    assert len(org_nodes) >= 2, f"expected ≥2 distinct orgs; got {len(org_nodes)}"
    # Repo nodes emit with parent = org.
    repo_nodes = [n for n in nodes if "/" in n.display_name]
    assert len(repo_nodes) >= 3, f"expected ≥3 repo nodes; got {len(repo_nodes)}"
    for repo_node in repo_nodes:
        assert repo_node.raw_parent_id is not None
        parent = by_id[repo_node.raw_parent_id]
        assert "/" not in parent.display_name, f"repo {repo_node.raw_node_id!r} parent should be an org, not a repo"


def _bind_for_f58_detector(_: Any) -> HierarchyConnector:
    """Reference :class:`HierarchyConnector` in the same file as the contract
    tests so F58's detector picks up the binding.
    """
    return _
