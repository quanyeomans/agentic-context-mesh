"""GitHub envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.github.GitHubConnector` lifts per-commit
envelope (``author`` + ``committed_at`` + ``repo``) onto the
:class:`SourceMetadata` payload; silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``author`` on the scripted GitHubCommitRef;
assert ``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from kairix.connectors.github import GitHubConnector
from kairix.connectors.github.api_client import (
    GitHubBlobRef,
    GitHubCommitRef,
    GitHubInstallationToken,
    GitHubIssueRef,
    GitHubRepoRef,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


class _ScriptedClient:
    """Minimal stand-in for :class:`GitHubApiClient` carrying one repo + one commit."""

    def __init__(self) -> None:
        self._repos = (
            GitHubRepoRef(
                repo_id=42,
                full_name="org/metadata-repo",
                default_branch="main",
                visibility="private",
                archived=False,
            ),
        )

    def list_installation_repositories(self) -> tuple[GitHubRepoRef, ...]:
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None) -> Iterator[GitHubCommitRef]:
        del since
        if full_name != "org/metadata-repo":
            return
        yield GitHubCommitRef(
            sha="metadata-sha",
            committed_at="2026-05-28T10:30:00Z",
            message="commit body with enough content to chunk through silver",
            author="agent-alpha",
        )

    def list_issues_since(self, *, full_name: str, since: str | None) -> Iterator[GitHubIssueRef]:
        del full_name, since
        return iter(())

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple[tuple[GitHubBlobRef, ...], bool]:
        del full_name, ref
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        del full_name, sha
        return b""

    def stats(self) -> object:
        class _Stats:
            rest_requests = 0
            rest_rate_remaining = 5000
            rest_rate_reset_epoch = 0
            rest_403_secondary_total = 0
            installation_token_rotations = 0

        return _Stats()

    def invalidate_token(self) -> None:
        return None

    def bearer_header(self) -> str:
        return "Bearer test"

    def rotate_token(self) -> GitHubInstallationToken:
        return GitHubInstallationToken(token="test-token", expires_at="2099-01-01T00:00:00Z")


def test_github_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """GitHubConnector.metadata_for surfaces commit author + committed_at + repo tag."""
    client = _ScriptedClient()
    connector = GitHubConnector(client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client mirrors GitHubApiClient shape for the test seam only.
    db_path = tmp_path / "github_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="github-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "GitHubConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, f"expected commit author 'agent-alpha' on chunk.author; got {authors!r}"
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T10:30:00Z" in chunk_dates, f"expected commit committed_at on chunk_date; got {chunk_dates!r}"
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "org/metadata-repo" in all_tags, f"expected 'org/metadata-repo' in chunk.tags; got {sorted(all_tags)!r}"
