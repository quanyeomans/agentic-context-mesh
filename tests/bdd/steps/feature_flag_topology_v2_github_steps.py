"""Step definitions for feature_flag_topology_v2_github.feature.

Drives the GitHub connector's Wave E
:meth:`GitHubConnector.list_changes_for_container` +
:meth:`GitHubConnector.load_hierarchy` branches with the flag value
pinned through the canonical :class:`FakeFeatureFlagResolver` from
``tests/fakes.py``.

Per F46: every step reaches a sanctioned entry point in its call
graph (depth ≤ 2). Construction uses :class:`GitHubConnector`
directly with a :class:`FakeGitHubApiClient` injected via the
``client`` DI seam — no factory layer needed because the connector
*is* the unit under test.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` provides
the flag-reader; no @patch / monkeypatch of kairix internals.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by agent, restored on completion): inverting
the if-branch in :meth:`GitHubConnector.load_hierarchy` so OFF emits
the Wave E tree and ON emits the root-only node — confirmed both BDD
scenarios fail. Restoring the original branch direction returns the
suite to green.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.github import GitHubConnector
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd


# Test-internal scripted client that satisfies the GitHubApiClient
# surface the connector calls. Living here (not in tests/fakes.py)
# because it's a step-internal scripted detail, not a Protocol fake.
class _ScriptedClient:
    def __init__(self, repos: list[tuple[str, str]]) -> None:
        from kairix.connectors.github.api_client import (
            ClientStatsSnapshot,
            GitHubRepoRef,
        )

        self._repos = tuple(
            GitHubRepoRef(
                repo_id=i + 1,
                full_name=full_name,
                default_branch="main",
                visibility=visibility,
                archived=False,
            )
            for i, (full_name, visibility) in enumerate(repos)
        )
        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def list_installation_repositories(self) -> tuple:  # type: ignore[type-arg]  # F3 rationale: bounded test surface — concrete tuple shape is fine; ScriptedClient mirrors api_client.py methods one-to-one
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None) -> tuple:  # type: ignore[type-arg]  # F3 rationale: bounded test surface — concrete tuple shape is fine; ScriptedClient mirrors api_client.py methods one-to-one
        from kairix.connectors.github.api_client import GitHubCommitRef

        _ = since
        return (
            GitHubCommitRef(
                sha=f"sha-{full_name.replace('/', '-')}",
                committed_at="2026-05-23T00:00:00Z",
                message="seed",
                author="agent-alpha",
            ),
        )

    def list_issues_since(self, *, full_name: str, since: str | None) -> tuple:  # type: ignore[type-arg]  # F3 rationale: bounded test surface — concrete tuple shape is fine; ScriptedClient mirrors api_client.py methods one-to-one
        _ = (full_name, since)
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple:  # type: ignore[type-arg]  # F3 rationale: bounded test surface — concrete tuple shape is fine; ScriptedClient mirrors api_client.py methods one-to-one

        _ = (full_name, ref)
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


@dataclass
class _Ctx:
    """Per-scenario context."""

    flag_value: bool = False
    connector: GitHubConnector | None = None
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    repos: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
def topology_github_ctx() -> _Ctx:
    return _Ctx()


@given(parsers.parse("the operator has the topology v2 github flag set to {value}"))
def _operator_sets_flag(topology_github_ctx: _Ctx, value: str) -> None:
    topology_github_ctx.flag_value = value.strip().lower() == "true"


@given("a github connector with two seeded repositories")
def _seed_two_repos(topology_github_ctx: _Ctx) -> None:
    topology_github_ctx.repos = [
        ("agent-alpha-org/repo-one", "private"),
        ("agent-alpha-org/repo-two", "public"),
    ]
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_github", topology_github_ctx.flag_value)
    topology_github_ctx.connector = GitHubConnector(
        client=_ScriptedClient(topology_github_ctx.repos),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent
        flag_reader=resolver.get,
    )


@when("the operator calls list_changes_for_container for one repository")
def _call_list_changes_for_container(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=topology_github_ctx.repos[0][0],
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))


@when("load_hierarchy is invoked")
def _invoke_load_hierarchy(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    topology_github_ctx.hierarchy_nodes = list(connector.load_hierarchy(cc_pair_id=42))


@then("the connector reports it took the legacy code path")
def _then_legacy_path(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    assert connector._last_path_taken == "legacy"


@then("the connector reports it took the scoped code path")
def _then_scoped_path(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    assert connector._last_path_taken == "scoped"


@then("load_hierarchy emits one root ORG node")
def _then_root_org_only(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    nodes = list(connector.load_hierarchy(cc_pair_id=42))
    assert len(nodes) == 1, f"expected one root node when flag OFF; got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].raw_node_id == "github"


@then("load_hierarchy emits parent before child for org then repo")
def _then_parent_before_child(topology_github_ctx: _Ctx) -> None:
    connector = topology_github_ctx.connector
    assert connector is not None
    nodes = list(connector.load_hierarchy(cc_pair_id=42))
    emitted: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in emitted, (
                f"F58 violation: {node.raw_node_id!r} parent {node.raw_parent_id!r} not previously emitted"
            )
        emitted.add(node.raw_node_id)
    # At minimum: one org + two repos (the seeded count).
    org_nodes = [n for n in nodes if n.raw_parent_id is None]
    repo_nodes = [n for n in nodes if n.raw_parent_id is not None and "/" in n.display_name]
    assert len(org_nodes) >= 1, f"expected ≥1 org node; got {len(org_nodes)}"
    assert len(repo_nodes) >= 2, f"expected ≥2 repo nodes (one per seeded repo); got {len(repo_nodes)}"
