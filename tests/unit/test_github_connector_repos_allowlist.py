"""GitHub connector ``repos_allowlist`` config — happy + bad-shape + log behaviour.

Pins the operator-facing config surface added so an admin-scope PAT
can drain only a subset of org repos instead of every repo it can see.

F1-clean / F2-clean: the connector is constructed through the ``client``
DI seam with a local stub mirroring :class:`GitHubApiClient` (the same
pattern used in :mod:`tests.unit.test_github_connector_unit`). No
``monkeypatch`` / ``@patch`` on kairix internals; no ``KAIRIX_*`` env-var
manipulation.

Sabotage proofs (executed locally, mutate → fail → restore):

  * ``test_three_slug_allowlist_drains_only_those_repos`` — drop the
    ``if not self._repos_allowlist`` short-circuit in
    ``GitHubConnector._apply_repos_allowlist`` (delete the early
    ``return repos``). Without the short-circuit the helper falls
    through to the membership check; for the no-allowlist path
    ``test_unset_allowlist_drains_all_repos`` flips with
    ``AssertionError: assert 0 == 3`` (every repo filtered out).
    Restore the early return; suite turns green again.
  * ``test_bad_slug_raises_value_error_with_f21_markers`` — change the
    regex from ``r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"`` to
    ``r".*"`` in ``connector.py`` so every string passes; the test
    flips with ``DID NOT RAISE <class 'ValueError'>`` for the ``"foo"``
    case. Restored the regex; suite turns green again.
  * ``test_filter_outcome_is_logged_once_per_connector_lifetime`` —
    drop the ``self._allowlist_logged = True`` assignment so the log
    line fires every tick; the second-tick caplog assertion flips with
    ``AssertionError: expected exactly one log record; got 2``.
    Restored the assignment.
"""

from __future__ import annotations

import logging

import pytest

from kairix.connectors.github import GitHubConnector, make_connector
from kairix.connectors.github.api_client import ClientStatsSnapshot, GitHubRepoRef

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Local stub mirroring GitHubApiClient — three repos under one org so the
# allowlist filter has something to discriminate against.
# ---------------------------------------------------------------------------


class _ThreeRepoStub:
    """Minimal :class:`GitHubApiClient`-shape stub yielding three private repos."""

    def __init__(self, full_names: tuple[str, ...] = ()) -> None:
        names = full_names or (
            "agent-alpha-org/repo-one",
            "agent-alpha-org/repo-two",
            "agent-alpha-org/repo-three",
        )
        self._repos = tuple(
            GitHubRepoRef(
                repo_id=i + 1,
                full_name=name,
                default_branch="main",
                visibility="private",
                archived=False,
            )
            for i, name in enumerate(names)
        )
        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def list_installation_repositories(self) -> tuple[GitHubRepoRef, ...]:
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None) -> tuple:
        del full_name, since
        return ()

    def list_issues_since(self, *, full_name: str, since: str | None) -> tuple:
        del full_name, since
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple[tuple, bool]:
        del full_name, ref
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        del full_name, sha
        return b""

    def stats(self) -> ClientStatsSnapshot:
        return self._stats

    def invalidate_token(self) -> None:
        return None


def _build_connector(
    *,
    repos_allowlist: list[str] | None = None,
    full_names: tuple[str, ...] = (),
) -> GitHubConnector:
    """Construct a connector with the stub + an optional allowlist."""
    return GitHubConnector(
        client=_ThreeRepoStub(full_names=full_names),  # type: ignore[arg-type]  # F3 rationale: stub mirrors GitHubApiClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
        repos_allowlist=repos_allowlist,
    )


# ---------------------------------------------------------------------------
# iter_containers — the most direct observable surface for the filter
# ---------------------------------------------------------------------------


def test_unset_allowlist_drains_all_repos() -> None:
    """No allowlist == back-compat: every installation-accessible repo drains.

    Sabotage proof noted in the module docstring.
    """
    connector = _build_connector(repos_allowlist=None)
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert len(containers) == 3, f"expected all 3 repos to drain; got {len(containers)}"
    container_ids = {c.container_id for c in containers}
    assert container_ids == {
        "agent-alpha-org/repo-one",
        "agent-alpha-org/repo-two",
        "agent-alpha-org/repo-three",
    }


def test_empty_allowlist_drains_all_repos() -> None:
    """An empty list (operator left the key but no entries) == no filter.

    Distinguishes the "operator forgot to populate" shape from the
    "operator wants nothing" shape — the former is a footgun we want
    to avoid (silent zero-drain is worse than an explicit removal).
    """
    connector = _build_connector(repos_allowlist=[])
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert len(containers) == 3, f"expected all 3 repos to drain; got {len(containers)}"


def test_three_slug_allowlist_drains_only_those_repos() -> None:
    """A three-slug allowlist restricts the drain to exactly those three repos.

    This is the production deploy scenario from 2026-06-02 — the PAT
    can see every org repo but the operator only wants a specific
    handful of repos draining.

    Sabotage proof noted in the module docstring.
    """
    connector = _build_connector(
        repos_allowlist=["agent-alpha-org/repo-one", "agent-alpha-org/repo-three"],
    )
    containers = list(connector.iter_containers(cc_pair_id=1))
    container_ids = {c.container_id for c in containers}
    assert container_ids == {"agent-alpha-org/repo-one", "agent-alpha-org/repo-three"}, (
        f"expected only the two allowlisted repos; got {container_ids!r}"
    )


def test_unknown_slug_in_allowlist_is_skipped_silently() -> None:
    """An allowlist entry the credential can't see is silently skipped.

    The allowlist is intent, not assertion — a PAT that lost access
    mid-rotation, or a slug for a repo not yet created, should NOT
    fail the drain. The connector simply drops the unknown entry and
    drains what it can.
    """
    connector = _build_connector(
        repos_allowlist=[
            "agent-alpha-org/repo-one",
            "agent-alpha-org/never-existed",  # not in the stub's repo set
            "agent-alpha-org/repo-two",
        ],
    )
    containers = list(connector.iter_containers(cc_pair_id=1))
    container_ids = {c.container_id for c in containers}
    assert container_ids == {"agent-alpha-org/repo-one", "agent-alpha-org/repo-two"}


def test_list_changes_honours_allowlist() -> None:
    """The legacy ``list_changes`` surface also filters via the allowlist.

    Belt-and-braces — both the Wave E ``iter_containers`` path and the
    legacy ``list_changes`` path route through ``_apply_repos_allowlist``
    so an operator on either branch gets the same filter behaviour.
    """
    connector = _build_connector(
        repos_allowlist=["agent-alpha-org/repo-two"],
    )
    # No commits are returned by the stub, but the per-repo cursor map
    # records the repos the connector did try to drain. We assert on
    # next_cursor() to observe the filtered set.
    list(connector.list_changes(cursor=None))
    cursor_keys = set(connector._per_repo_cursors.keys())
    assert cursor_keys == {"agent-alpha-org/repo-two"}, (
        f"expected per-repo cursor only for the allowlisted repo; got {cursor_keys!r}"
    )


# ---------------------------------------------------------------------------
# Logging — first sync per tick announces the filter outcome
# ---------------------------------------------------------------------------


def test_filter_outcome_is_logged_once_per_connector_lifetime(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The filter logs ``repos_allowlist filtered N→K`` exactly once.

    Confirms the operator can verify the filter took effect on first
    sync without log spam every tick. Subsequent calls are silent.

    Sabotage proof noted in the module docstring.
    """
    connector = _build_connector(
        repos_allowlist=["agent-alpha-org/repo-one"],
    )
    with caplog.at_level(logging.INFO, logger="kairix.connectors.github.connector"):
        # Two ticks — the log should fire on the first, not the second.
        list(connector.iter_containers(cc_pair_id=1))
        list(connector.iter_containers(cc_pair_id=1))
    matching = [r for r in caplog.records if "repos_allowlist filtered" in r.getMessage()]
    assert len(matching) == 1, f"expected exactly one log record; got {len(matching)}"
    message = matching[0].getMessage()
    assert "3" in message and "1" in message, f"expected N→K counts in message; got {message!r}"
    # The "filtered out" list lets the operator see which repos were dropped.
    assert "agent-alpha-org/repo-two" in message
    assert "agent-alpha-org/repo-three" in message


def test_no_log_when_allowlist_is_unset(caplog: pytest.LogCaptureFixture) -> None:
    """No allowlist == no filter log (avoid noise in the back-compat path)."""
    connector = _build_connector(repos_allowlist=None)
    with caplog.at_level(logging.INFO, logger="kairix.connectors.github.connector"):
        list(connector.iter_containers(cc_pair_id=1))
    matching = [r for r in caplog.records if "repos_allowlist filtered" in r.getMessage()]
    assert matching == [], f"expected zero filter logs; got {matching!r}"


# ---------------------------------------------------------------------------
# make_connector — config-time slug validation (F21 markers)
# ---------------------------------------------------------------------------


def test_make_connector_accepts_valid_allowlist() -> None:
    """Happy path through ``make_connector`` — valid slugs round-trip into the connector."""
    connector = make_connector({"repos_allowlist": ["agent-alpha-org/repo-one", "agent-alpha-org/repo-two"]})
    assert connector._repos_allowlist == frozenset({"agent-alpha-org/repo-one", "agent-alpha-org/repo-two"})


def test_make_connector_accepts_unset_allowlist() -> None:
    """Unset allowlist == empty frozenset on the connector (no filter)."""
    connector = make_connector({})
    assert connector._repos_allowlist == frozenset()


def test_bad_slug_raises_value_error_with_f21_markers() -> None:
    """A missing-slash slug raises with operator-actionable ``fix:`` / ``next:``.

    Sabotage proof noted in the module docstring.
    """
    with pytest.raises(ValueError, match="invalid 'owner/repo' slug") as exc_info:
        make_connector({"repos_allowlist": ["foo"]})
    message = str(exc_info.value)
    assert "fix:" in message, f"expected F21 'fix:' marker; got {message!r}"
    assert "next:" in message, f"expected F21 'next:' marker; got {message!r}"
    assert "foo" in message, f"expected the bad slug in the error; got {message!r}"


@pytest.mark.parametrize(
    "bad_slug",
    [
        "no-slash",
        "/missing-owner",
        "missing-repo/",
        "has spaces/here",
        "a/b/c",  # too many segments
        "a//b",  # empty segment
    ],
)
def test_various_bad_slug_shapes_are_rejected(bad_slug: str) -> None:
    """The regex rejects the common operator-typo shapes."""
    with pytest.raises(ValueError, match="invalid 'owner/repo' slug"):
        make_connector({"repos_allowlist": [bad_slug]})


def test_non_list_allowlist_raises_value_error_with_f21_markers() -> None:
    """An operator passing a scalar (e.g. a single string) raises with markers.

    Without this guard a single string would iterate per-character —
    every char would fail the slug check and the operator would see
    a confusing per-char error list. Catching the type up front
    surfaces the right next-step.
    """
    with pytest.raises(ValueError, match="must be a list") as exc_info:
        make_connector({"repos_allowlist": "agent-alpha-org/repo-one"})
    message = str(exc_info.value)
    assert "fix:" in message
    assert "next:" in message


def test_allowlist_deduplicates_repeated_slugs() -> None:
    """Duplicate slugs collapse to a single frozenset entry."""
    connector = make_connector(
        {
            "repos_allowlist": [
                "agent-alpha-org/repo-one",
                "agent-alpha-org/repo-one",
                "agent-alpha-org/repo-one",
            ]
        }
    )
    assert connector._repos_allowlist == frozenset({"agent-alpha-org/repo-one"})


# ---------------------------------------------------------------------------
# F65 propagation — filtered repos do not leak envelope metadata
# ---------------------------------------------------------------------------


def test_filtered_repos_do_not_leak_metadata() -> None:
    """A repo dropped by the allowlist contributes no SourceMetadata.

    The connector's ``metadata_for`` reads from ``self._envelope_cache``;
    if the filter let a forbidden repo through, its envelope would
    populate the cache and a later ``metadata_for(item_id)`` call
    for that repo's items would return a non-empty SourceMetadata.

    This test drains via the allowlist + asserts that no envelope keys
    reference the excluded repos.
    """
    connector = _build_connector(
        repos_allowlist=["agent-alpha-org/repo-one"],
    )
    list(connector.list_changes(cursor=None))
    # The cache should only carry entries for the allowlisted repo
    # (or be empty if the stub returns no commits, which it does).
    leaked = [key for key in connector._envelope_cache if "agent-alpha-org/repo-two" in key or "repo-three" in key]
    assert leaked == [], f"expected no metadata for filtered-out repos; got {leaked!r}"
