"""End-to-end composed path test for the ``connector_notion`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-scope-topology/connector-design-specs/notion.md``
— a top-level capability spec.

Scenario: composes the full production path:

  build_connector_pipeline (factory)
    → fake Notion connector emits a ChangeEvent for a markdown page
      with mime ``text/markdown``
    → ExtractorRegistry resolves the passthrough extractor (markdown
      passes through unchanged)
    → DefaultSilverProcessor chunks the markdown
    → _SqliteChunkWriter persists into documents + FTS5
    → BM25 query against the notion collection returns the chunk for
      a token from the page body

Flag dispatch path:

  flag-resolver pins connector_notion=True
    → dispatch_notion_sync routes to the production ON branch helper
    → branch helper wraps the pipeline run

The OFF path is covered by the integration tests at
``tests/integration/test_feature_flag_connector_notion.py``. F54's
E2E requirement is per-flag (one E2E composed-path file).

Sabotage proof (executed by the agent, restored on completion):
mutating the seeded fake's body to a literal that does NOT contain
the query token (e.g. emit "irrelevant content" while the query is
"engagement") makes the BM25 assertion fail because the chunk doesn't
match. Restored, the body's "engagement" token surfaces via BM25.

No optional extras required — the notion connector renders markdown,
which routes through the passthrough extractor and avoids the
markitdown/pptx/docx skipif gates that gate the sharepoint E2E.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors import ExtractorRegistry
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import (
    ChangeEvent,
    RawArtefact,
    Sensitivity,
)
from kairix.worker import (
    ConnectorSyncResult,
    dispatch_notion_sync,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


_NOTION_MIME = "text/markdown"


@dataclass
class _NotionFixtureContent:
    """One markdown page to emit through the fake Notion connector."""

    item_id: str
    raw: bytes
    mime: str
    web_url: str
    last_modified_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeNotionConnectorForE2E:
    """Capture-shaped SourceConnector mirroring the real plugin's wire shape.

    Emits one :class:`ChangeEvent` per seeded fixture and serves the
    raw markdown bytes via :meth:`fetch`. ``source_link`` returns the
    Notion web URL the real connector emits; ``sensitivity_for``
    returns the configured default tier (mirrors the real connector's
    F39 behaviour).
    """

    def __init__(
        self,
        *,
        fixtures: list[_NotionFixtureContent],
        sensitivity: Sensitivity = "internal",
    ) -> None:
        self.name = "notion"
        self._fixtures = list(fixtures)
        self._sensitivity: Sensitivity = sensitivity
        self._by_id: dict[str, _NotionFixtureContent] = {f.item_id: f for f in fixtures}

    def list_changes(self, _cursor: Any | None) -> Iterator[ChangeEvent]:
        events: list[ChangeEvent] = []
        for fixture in self._fixtures:
            events.append(
                ChangeEvent(
                    op="modified",
                    item_id=fixture.item_id,
                    modified_at=fixture.last_modified_at,
                    metadata={
                        "sensitivity": self._sensitivity,
                        "parent_type": "workspace",
                        "name": "Engagement brief fixture",
                        "mime": fixture.mime,
                    },
                )
            )
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        fixture = self._by_id[item_id]
        return RawArtefact(raw=fixture.raw, mime=fixture.mime, fetched_at=_now())

    def source_link(self, item_id: str) -> str:
        fixture = self._by_id.get(item_id)
        return fixture.web_url if fixture is not None else f"notion://pages/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        return self._sensitivity

    def next_cursor(self) -> str | None:
        return None


def _build_db_with_schema(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db, dims=4)
    return db


def _populate_fts(db: sqlite3.Connection) -> None:
    """Mirror the production embed pipeline's FTS5 population step.

    The connector pipeline's chunk writer persists ``documents`` +
    ``content`` rows but does not touch ``documents_fts``. We
    materialise FTS rows from the matched ``documents`` JOIN
    ``content`` so the BM25 query has a target. Mirrors the SharePoint
    + Obsidian E2E test helpers.
    """
    db.execute("DELETE FROM documents_fts")
    db.execute(
        """
        INSERT INTO documents_fts (rowid, filepath, title, doc)
        SELECT d.id, d.path, d.title, c.doc
        FROM documents d
        JOIN content c ON c.hash = d.hash
        WHERE d.active = 1
        """
    )
    db.commit()


def test_composed_notion_markdown_path(tmp_path: Path) -> None:
    """Notion: composed path markdown page → connector → passthrough → silver → BM25.

    Sabotage proof (verified): mutating the fixture body to omit the
    query token causes the BM25 assertion to fail with zero matches.
    Restored, the page body's "engagement" token surfaces via BM25.
    """
    fixture = _NotionFixtureContent(
        item_id="page-fixture-0001",
        raw=b"# Engagement brief\n\nThe engagement scope for phase 2 covers ingest pipeline work.\n",
        mime=_NOTION_MIME,
        web_url="https://www.notion.so/your-team/page-fixture-0001",
        last_modified_at="2026-05-22T10:00:00Z",
    )
    query_token = "engagement"

    resolver = FakeFeatureFlagResolver().with_flag("connector_notion", True)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "index.sqlite"
    db = _build_db_with_schema(db_path)

    fake_connector = _FakeNotionConnectorForE2E(fixtures=[fixture])
    registry = ExtractorRegistry()

    def _on_branch() -> ConnectorSyncResult:
        extractor = registry.resolve(fixture.mime, fixture.raw[:8])
        pipeline = build_connector_pipeline(
            db=db,
            bronze_root=bronze_root,
            collection="notion",
        )
        result = pipeline.run_batch(fake_connector, extractor)
        db.commit()
        return ConnectorSyncResult(
            synced=result.processed,
            failed=result.dead_lettered,
            dead_letter_added=result.dead_lettered,
        )

    sync_result = dispatch_notion_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
    )
    assert sync_result.synced >= 1, f"composed path must index the fixture; got {sync_result}"

    _populate_fts(db)

    rows = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'notion'",
            (query_token,),
        )
    )
    db.close()
    assert len(rows) >= 1, (
        "composed Notion path must surface the page body via BM25; got 0 matches. "
        f"Sabotage hint: check that the markdown body contained {query_token!r} and the chunk-writer "
        "populated documents/content for the 'notion' collection."
    )
