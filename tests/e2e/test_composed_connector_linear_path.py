"""End-to-end composed path test for the ``connector_linear`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-scope-topology/connector-design-specs/linear.md``
— a top-level capability spec.

Scenario: composes the full production path:

  build_connector_pipeline (factory)
    → FakeLinearConnector emits a ChangeEvent for an issue node
      with mime ``text/markdown``
    → ExtractorRegistry resolves the passthrough extractor (Markdown
      passes through unchanged)
    → DefaultSilverProcessor chunks the Markdown
    → _SqliteChunkWriter persists into documents + FTS5
    → BM25 query against the linear collection returns the chunk for
      a token from the issue description body

Flag dispatch path:

  flag-resolver pins connector_linear=True
    → dispatch_linear_sync routes to the production ON branch helper
    → branch helper wraps the pipeline run

The OFF path is covered by the integration tests at
``tests/integration/test_feature_flag_connector_linear.py``. F54's
E2E requirement is per-flag (one E2E composed-path file).

Sabotage proof (executed by the agent, restored on completion):
mutating _ISSUE_NODE's ``title`` to "Improve visibility" and
``description`` to "Unrelated content about something else entirely."
(removing all occurrences of "roadmap") makes the BM25 assertion fail
with ``AssertionError: 0 >= 1``. Restored, the "roadmap" token in both
title and description surfaces via BM25.

No optional extras required — the Linear connector renders issues as
Markdown, which routes through the passthrough extractor and avoids the
markitdown/pptx/docx skipif gates that gate the SharePoint E2E.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors import ExtractorRegistry
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.worker import (
    ConnectorSyncResult,
    dispatch_linear_sync,
)
from tests.fakes import FakeFeatureFlagResolver, FakeLinearConnector

pytestmark = pytest.mark.e2e

# Issue node seeded into the FakeLinearConnector. Uses synthetic content
# only (F32 — no real workspace data, no real names from Linear).
_ISSUE_NODE: dict[str, object] = {
    "id": "issue-fixture-0001",
    "identifier": "ENG-42",
    "title": "Improve roadmap visibility",
    "description": (
        "The roadmap needs a clearer breakdown of milestones and owners.\n"
        "Each initiative should link to its planning document."
    ),
    "url": "https://linear.app/your-team/issue/ENG-42",
    "createdAt": "2026-05-10T09:00:00.000Z",
    "updatedAt": "2026-05-22T14:00:00.000Z",
    "state": {"name": "In Progress", "type": "started"},
    "team": {"key": "ENG", "name": "Engineering"},
    "labels": {"nodes": []},
}

# BM25 token that must appear in the rendered Markdown output.
# ``render._render_issue`` includes the description verbatim after the
# field block — "roadmap" is a distinctive word not found in any fixture
# or framework prose, so a false-positive BM25 hit is structurally
# impossible in this in-memory test DB.
_QUERY_TOKEN = "roadmap"


def _build_db_with_schema(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db, dims=4)
    return db


def test_composed_linear_issue_path(tmp_path: Path) -> None:
    """Linear: composed path issue → connector → passthrough → silver → BM25.

    Sabotage proof (executed): mutated _ISSUE_NODE title to "Improve
    visibility" and description to "Unrelated content about something
    else entirely." (removing all "roadmap" occurrences) → BM25 asserts
    ``0 >= 1`` (FAILED). Restored both fields → 1 passed.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_linear", True)
    db_path = tmp_path / "index.sqlite"
    db = _build_db_with_schema(db_path)

    fake_connector = FakeLinearConnector(nodes={"issue": [_ISSUE_NODE]})
    registry = ExtractorRegistry()

    def _on_branch() -> ConnectorSyncResult:
        # Resolve the passthrough extractor for text/markdown — same mime
        # the FakeLinearConnector.fetch() emits via render().
        extractor = registry.resolve("text/markdown", b"# ENG-42")
        pipeline = build_connector_pipeline(
            db=db,
            collection="linear",
        )
        result = pipeline.run_batch(fake_connector, extractor)
        db.commit()
        return ConnectorSyncResult(
            synced=result.processed,
            failed=result.dead_lettered,
            dead_letter_added=result.dead_lettered,
        )

    sync_result = dispatch_linear_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
    )
    assert sync_result.synced >= 1, (
        f"composed path must index the fixture issue; got {sync_result}. "
        "Sabotage hint: check that FakeLinearConnector seeded the issue node "
        "and the pipeline's chunk_writer received processed chunks."
    )

    rows = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'linear'",
            (_QUERY_TOKEN,),
        )
    )
    db.close()
    assert len(rows) >= 1, (
        "composed Linear path must surface the issue description via BM25; got 0 matches. "
        f"Sabotage hint: check that the issue description contained {_QUERY_TOKEN!r} and "
        "the chunk-writer populated documents/content for the 'linear' collection."
    )
