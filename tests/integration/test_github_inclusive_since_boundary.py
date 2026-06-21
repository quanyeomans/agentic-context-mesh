"""Integration tests for the GitHub connector's inclusive-``since`` boundary fix.

Background (resilience audit, HIGH severity): GitHub's
``GET /repos/{owner}/{repo}/commits?since=`` and
``GET /repos/{owner}/{repo}/issues?since=`` parameters are **inclusive**
on the commit ``committed_at`` / issue ``updated_at`` timestamp. The
connector persists the boundary commit's ``committed_at`` as the next
``?since=`` value, so on a quiet repo (no new commits) every tick
re-fetches, re-extracts, and re-emits the boundary commit (and boundary
issue) forever — a wasted-work loop that burns fetch/extract every tick.

These tests pin the fix:

1. **Quiet repo re-emits ZERO events on the next tick** — the boundary
   commit / issue seen on tick 1 must NOT re-emit on tick 2 when nothing
   new has landed.
2. **A genuinely-new commit at the SAME timestamp as the boundary is
   NOT dropped** — the fix must not blindly advance ``since`` by +1s (or
   otherwise exclude the boundary second), which would silently drop a
   same-second commit. The compound cursor (boundary timestamp + the set
   of SHAs already emitted at that timestamp) is robust to this.

Both tests drive the connector via :meth:`GitHubConnector.list_changes`
with a scripted GitHub client across two ticks, persisting the per-repo
cursor between ticks (the production path serialises the cursor between
ticks). The scripted client honours GitHub's inclusive ``since``
semantics so the test exercises the real wire contract.

F47 — every test reaches production code through the connector's
constructor; no direct ``*Pipeline(...)`` construction.

Sabotage proofs documented in the commit body — the cursor-advance fix
was reverted, the quiet-repo-re-emit test confirmed to fail, and the
code restored to its canonical shape.
"""

from __future__ import annotations

import pytest

from kairix.connectors.github import GitHubConnector
from kairix.connectors.github.api_client import (
    ClientStatsSnapshot,
    GitHubCommitRef,
    GitHubIssueRef,
    GitHubRepoRef,
)

pytestmark = pytest.mark.integration

_REPO = "agent-alpha-org/quiet-repo"
_BOUNDARY_TS = "2026-05-23T01:00:00Z"


class _InclusiveSinceClient:
    """Scripted client honouring GitHub's INCLUSIVE ``since`` semantics.

    ``list_commits_since`` / ``list_issues_since`` return every scripted
    commit / issue whose timestamp is ``>= since`` (mirroring GitHub's
    real boundary behaviour), oldest-first — exactly the wire contract
    that triggers the boundary re-emit bug when the connector persists
    the boundary timestamp as the next ``since``.
    """

    def __init__(
        self,
        *,
        commits: list[GitHubCommitRef] | None = None,
        issues: list[GitHubIssueRef] | None = None,
    ) -> None:
        self._commits = list(commits) if commits is not None else []
        self._issues = list(issues) if issues is not None else []
        self.commit_since_calls: list[str | None] = []
        self.issue_since_calls: list[str | None] = []
        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def add_commit(self, commit: GitHubCommitRef) -> None:
        self._commits.append(commit)

    def list_installation_repositories(self) -> tuple[GitHubRepoRef, ...]:
        return (
            GitHubRepoRef(
                repo_id=1,
                full_name=_REPO,
                default_branch="main",
                visibility="private",
                archived=False,
            ),
        )

    def list_commits_since(self, *, full_name: str, since: str | None) -> tuple[GitHubCommitRef, ...]:
        _ = full_name
        self.commit_since_calls.append(since)
        kept = [c for c in self._commits if since is None or c.committed_at >= since]
        kept.sort(key=lambda c: c.committed_at)  # oldest-first, like the api_client
        return tuple(kept)

    def list_issues_since(self, *, full_name: str, since: str | None) -> tuple[GitHubIssueRef, ...]:
        _ = full_name
        self.issue_since_calls.append(since)
        kept = [i for i in self._issues if since is None or i.updated_at >= since]
        kept.sort(key=lambda i: i.updated_at)
        return tuple(kept)

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple[tuple, bool]:
        _ = (full_name, ref)
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self) -> ClientStatsSnapshot:
        return self._stats

    def invalidate_token(self) -> None:
        return None


def _drain_two_ticks(connector: GitHubConnector) -> tuple[list, list]:
    """Run two ticks, persisting the cursor between them (production path).

    Tick 2 reconstructs the connector cursor from the serialised cursor
    string the connector produced after tick 1 — exactly how the
    orchestrator persists + restores the per-repo cursor between ticks.
    """
    first = list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    second = list(connector.list_changes(cursor=cursor))
    return first, second


def test_quiet_repo_reemits_zero_events_on_next_tick() -> None:
    """A repo with no new commits/issues re-emits ZERO events on tick 2.

    The boundary commit + boundary issue emitted on tick 1 must not be
    re-emitted on tick 2 when nothing new has landed, even though
    GitHub's inclusive ``since`` re-returns them from the wire.

    Sabotage-proof: reverting the cursor-advance fix (so the connector
    persists only the boundary timestamp and re-emits everything the
    inclusive ``since`` returns) flips this test to fail — tick 2
    re-emits the boundary commit + issue.
    """
    client = _InclusiveSinceClient(
        commits=[
            GitHubCommitRef(
                sha="boundary-commit",
                committed_at=_BOUNDARY_TS,
                message="boundary",
                author="agent-alpha",
            ),
        ],
        issues=[
            GitHubIssueRef(
                number=7,
                kind="issue",
                updated_at=_BOUNDARY_TS,
                title="boundary issue",
                body="b",
                state="open",
            ),
        ],
    )
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam

    first, second = _drain_two_ticks(connector)

    # Tick 1 emits the boundary commit + boundary issue.
    assert len(first) == 2, f"tick 1 should emit the boundary commit + issue; got {first!r}"
    # Tick 2 re-asks the wire (inclusive since re-returns the boundary
    # rows) but the connector must NOT re-emit them.
    assert second == [], f"quiet repo must re-emit ZERO events on tick 2; got {second!r}"


def test_genuinely_new_commit_at_same_timestamp_is_not_dropped() -> None:
    """A new commit sharing the boundary timestamp must still emit on tick 2.

    Guards against the naive +1s cursor-advance pitfall: if the connector
    advanced ``since`` to ``boundary + 1s`` it would silently drop a
    genuinely-new commit landing at the SAME second as the boundary. The
    compound cursor (boundary timestamp + already-seen SHA set) emits the
    new same-second commit while still skipping the already-seen one.

    Sabotage-proof: advancing the cursor by +1s instead of tracking the
    seen-SHA set flips this test to fail — the same-second new commit is
    excluded by the ``since = boundary + 1s`` filter.
    """
    boundary_commit = GitHubCommitRef(
        sha="boundary-commit",
        committed_at=_BOUNDARY_TS,
        message="boundary",
        author="agent-alpha",
    )
    client = _InclusiveSinceClient(commits=[boundary_commit])
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam

    first = list(connector.list_changes(cursor=None))
    assert len(first) == 1, f"tick 1 should emit the boundary commit; got {first!r}"

    # A genuinely-new commit lands at the SAME second as the boundary.
    new_same_second = GitHubCommitRef(
        sha="new-same-second-commit",
        committed_at=_BOUNDARY_TS,
        message="new same-second",
        author="agent-beta",
    )
    client.add_commit(new_same_second)

    cursor = connector.next_cursor()
    second = list(connector.list_changes(cursor=cursor))

    emitted_ids = [ev.item_id for ev in second]
    assert any("new-same-second-commit" in i for i in emitted_ids), (
        f"a genuinely-new same-second commit must NOT be dropped; got {emitted_ids!r}"
    )
    assert not any("boundary-commit" in i for i in emitted_ids), (
        f"the already-seen boundary commit must NOT re-emit; got {emitted_ids!r}"
    )
    assert len(second) == 1, f"exactly the one new commit should emit on tick 2; got {emitted_ids!r}"


def test_cursor_boundary_is_max_timestamp_with_only_that_seconds_shas_seen() -> None:
    """After a drain the cursor records the MAX timestamp + only its SHAs.

    Pins the exact boundary computation across multiple distinct commit
    timestamps:

      * the persisted boundary (``code_sha``) is the MAX ``committed_at``,
        not an earlier one;
      * the boundary seen-set (``seen_commit_shas``) holds ONLY the SHAs
        AT that max second — an earlier-second commit is excluded from the
        seen-set (else it would be wrongly suppressed when GitHub later
        re-returns the inclusive boundary), and a same-max-second sibling
        IS included (else it would re-emit forever).

    This is the observable contract behind the boundary helper: the
    next-tick inclusive ``?since=<max>`` re-returns exactly the max-second
    commits, and the connector must skip precisely those and nothing else.
    Tick 2 confirms the round-trip emits ZERO events on a quiet repo.
    """
    earlier = GitHubCommitRef(
        sha="earlier-sha",
        committed_at="2026-05-23T01:00:00Z",
        message="earlier",
        author="agent-alpha",
    )
    max_a = GitHubCommitRef(
        sha="max-sha-a",
        committed_at="2026-05-23T02:00:00Z",
        message="max-a",
        author="agent-alpha",
    )
    max_b = GitHubCommitRef(
        sha="max-sha-b",
        committed_at="2026-05-23T02:00:00Z",  # SAME max second as max_a
        message="max-b",
        author="agent-beta",
    )
    client = _InclusiveSinceClient(commits=[earlier, max_a, max_b])
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam

    first = list(connector.list_changes(cursor=None))
    assert len(first) == 3, f"tick 1 should emit all three commits; got {first!r}"

    state = connector._per_repo_cursors[_REPO]
    # Boundary advances to the MAX committed_at (kills `>` -> `>=` / and/or
    # mutations on the boundary scan).
    assert state.code_sha == "2026-05-23T02:00:00Z", f"boundary must be the max committed_at; got {state.code_sha!r}"
    # Seen-set holds ONLY the SHAs at the max second — both same-second
    # siblings, and NOT the earlier-second commit (kills `==` -> `!=` and
    # the and/or mutation on the seen-set comprehension).
    assert state.seen_commit_shas == frozenset({"max-sha-a", "max-sha-b"}), (
        f"seen-set must hold exactly the max-second SHAs; got {sorted(state.seen_commit_shas)!r}"
    )
    assert "earlier-sha" not in state.seen_commit_shas, "an earlier-second commit must NOT be in the boundary seen-set"

    # Round-trip: a quiet tick 2 (inclusive since re-returns the two
    # max-second commits) must re-emit nothing.
    cursor = connector.next_cursor()
    second = list(connector.list_changes(cursor=cursor))
    assert second == [], f"quiet repo must re-emit ZERO events on tick 2; got {second!r}"
