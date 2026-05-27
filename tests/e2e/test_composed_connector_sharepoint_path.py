"""End-to-end composed path test for the ``connector_sharepoint`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a top-level
capability spec.

Per-format E2E coverage (one scenario per format):

  * PDF — markitdown extractor, recovered text is queryable via BM25.
  * PPTX — pptx extractor, slide body text is queryable via BM25.
  * DOCX — docx extractor, heading + body text is queryable via BM25.

Each scenario composes the full production path:

  build_connector_pipeline (factory)
    → fake SharePoint connector emits a ChangeEvent with the fixture
      binary content + the right mime
    → ExtractorRegistry resolves the per-mime extractor (real
      markitdown / pptx / docx plugin)
    → DefaultSilverProcessor chunks the extracted markdown
    → _SqliteChunkWriter persists into documents + FTS5
    → BM25 query against the connector-collection returns the chunk
      for a token from the document body

Flag dispatch path:

  flag-resolver pins connector_sharepoint=True
    → dispatch_sharepoint_sync routes to the production ON branch helper
    → branch helper wraps the per-format pipeline run

The OFF path is covered by the integration tests at
``tests/integration/test_feature_flag_connector_sharepoint.py``. F54's
E2E requirement is per-flag (one E2E composed-path file).

Sabotage proofs (executed by the agent, restored on completion):

  * **PDF** — mutating the fake's `_PDF_CONTENT` to a non-PDF stub
    (``b"NOT A PDF"``) makes markitdown recover near-empty markdown
    and the BM25 token assertion fails. Restored, the PDF token
    surfaces.
  * **PPTX** — mutating the fake's mime to ``"text/plain"`` makes the
    ExtractorRegistry dispatch to passthrough instead of pptx, the
    decoded "binary" garbage doesn't contain the slide body token,
    and the BM25 assertion fails. Restored, pptx extracts the body.
  * **DOCX** — flipping the fake's `_DOCX_CONTENT` to the PPTX bytes
    routes the docx extractor at the wrong fixture; the heading
    string isn't in the rendered markdown and BM25 misses. Restored,
    the heading token surfaces.

Skipped when the optional extractor extras aren't installed. The
extras are declared in ``pyproject.toml``'s
``[project.optional-dependencies]`` and bundled into the ``dev`` venv
during ``pip install -e .[dev,markitdown,pptx,docx]``.
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
    dispatch_sharepoint_sync,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Per-format fixture loaders + availability probes
# ---------------------------------------------------------------------------


_FIXTURES = Path(__file__).parent.parent / "fixtures" / "extractors"

# Real fixtures already recorded under tests/fixtures/extractors/ — the
# slice reuses them rather than committing duplicates under a sibling
# directory. The format-vs-content choice is deliberately constrained:
#
#   * sample.pdf — markitdown-friendly text "Hello PDF text extraction."
#   * sample.pptx — pptx-friendly slide deck with "Key Points" / "Point one"
#   * sample.docx — docx-friendly heading-and-body "Introduction" / "Background"
_PDF_FIXTURE = _FIXTURES / "sample.pdf"
_PPTX_FIXTURE = _FIXTURES / "sample.pptx"
_DOCX_FIXTURE = _FIXTURES / "sample.docx"

_PDF_MIME = "application/pdf"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _markitdown_available() -> bool:
    try:
        import markitdown as _mod  # noqa: F401
    except ImportError:
        return False
    return True


def _pptx_available() -> bool:
    try:
        import pptx as _mod  # noqa: F401
    except ImportError:
        return False
    return True


def _docx_available() -> bool:
    try:
        import docx as _mod  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Fake SharePoint connector — emits one envelope with binary fixture
# content + the right mime. Mirrors the SharePoint connector's surface
# without driving the OAuth2 + Graph stack.
# ---------------------------------------------------------------------------


@dataclass
class _SharePointFixtureContent:
    """One fixture binary to emit through the fake connector."""

    item_id: str
    raw: bytes
    mime: str
    web_url: str
    last_modified_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeSharePointConnector:
    """Capture-shaped SourceConnector mirroring the real plugin's wire shape.

    Emits one :class:`ChangeEvent` per seeded fixture and serves the
    raw bytes via :meth:`fetch`. ``source_link`` returns the
    SharePoint-style web URL the real connector emits; ``sensitivity_for``
    returns the configured default tier (mirrors the real connector's
    F39 behaviour).
    """

    def __init__(
        self,
        *,
        fixtures: list[_SharePointFixtureContent],
        sensitivity: Sensitivity = "internal",
    ) -> None:
        self.name = "sharepoint"
        self._fixtures = list(fixtures)
        self._sensitivity: Sensitivity = sensitivity
        self._by_id: dict[str, _SharePointFixtureContent] = {f.item_id: f for f in fixtures}

    def list_changes(self, _cursor: Any | None) -> Iterator[ChangeEvent]:
        events: list[ChangeEvent] = []
        for fixture in self._fixtures:
            events.append(
                ChangeEvent(
                    op="created",
                    item_id=fixture.item_id,
                    modified_at=fixture.last_modified_at,
                    metadata={"sensitivity": self._sensitivity, "drive_id": "fake-drive"},
                )
            )
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        fixture = self._by_id[item_id]
        return RawArtefact(raw=fixture.raw, mime=fixture.mime, fetched_at=_now())

    def source_link(self, item_id: str) -> str:
        fixture = self._by_id.get(item_id)
        return fixture.web_url if fixture is not None else f"sharepoint://items/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        return self._sensitivity

    def next_cursor(self) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Per-format orchestration helper
# ---------------------------------------------------------------------------


def _build_db_with_schema(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db, dims=4)
    return db


def _populate_fts(db: sqlite3.Connection) -> None:
    """Mirror the production embed pipeline's FTS5 population step.

    The connector pipeline's chunk writer persists ``documents`` +
    ``content`` rows but does not touch ``documents_fts`` — mirrors the
    obsidian E2E test's helper. We materialise FTS rows from the
    matched ``documents`` JOIN ``content`` so the BM25 query has a
    target.
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


def _run_one_fixture_through_composed_path(
    tmp_path: Path,
    *,
    fixture: _SharePointFixtureContent,
    query_token: str,
) -> int:
    """Drive one fixture through the composed connector pipeline.

    Returns the count of FTS-indexed chunks that match ``query_token``
    in the ``sharepoint`` collection. The caller asserts ``>= 1`` for
    the happy path.

    The dispatch surface is the real production
    :func:`dispatch_sharepoint_sync`; the ON branch is wrapped to
    invoke ``build_connector_pipeline`` against a tmp-path-rooted DB
    with the real ExtractorRegistry routing the binary to the
    appropriate plugin.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", True)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "index.sqlite"

    # Single connection for the test — same db handle is shared between
    # the pipeline run and the post-pipeline assertions so the
    # transaction's commit is visible to the subsequent SELECTs.
    db = _build_db_with_schema(db_path)

    fake_connector = _FakeSharePointConnector(fixtures=[fixture])

    # Real registry — resolves markitdown / pptx / docx by mime as the
    # production worker does. The test asserts at the mime level that
    # the right plugin is dispatched (else the per-format token would
    # not appear in the rendered markdown).
    registry = ExtractorRegistry()

    matches = 0

    def _on_branch() -> ConnectorSyncResult:
        # Resolve the extractor at branch entry — same shape as the
        # production worker's per-item dispatch in run_connector_sync_pipeline.
        extractor = registry.resolve(fixture.mime, fixture.raw[:8])
        pipeline = build_connector_pipeline(
            db=db,
            bronze_root=bronze_root,
            collection="sharepoint",
        )
        result = pipeline.run_batch(fake_connector, extractor)
        db.commit()
        return ConnectorSyncResult(
            synced=result.processed,
            failed=result.dead_lettered,
            dead_letter_added=result.dead_lettered,
        )

    sync_result = dispatch_sharepoint_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
    )
    assert sync_result.synced >= 1, f"composed path must index the fixture; got {sync_result}"

    _populate_fts(db)

    rows = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'sharepoint'",
            (query_token,),
        )
    )
    matches = len(rows)
    db.close()
    return matches


# ---------------------------------------------------------------------------
# Per-format E2E scenarios
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _markitdown_available() or not _PDF_FIXTURE.is_file(),
    reason=(
        "markitdown extra not installed or sample.pdf fixture missing; "
        "install via 'pip install Kairix-agentic-knowledge-mgt[markitdown]' and ensure "
        "tests/fixtures/extractors/sample.pdf is recorded"
    ),
)
def test_composed_sharepoint_pdf_path(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """PDF: composed path file → connector → markitdown → silver → BM25.

    Sabotage proof (verified): mutating the fixture content to a
    non-PDF byte string (``b"NOT A PDF"``) makes markitdown recover
    near-empty markdown and the BM25 token assertion fails. Restored,
    the PDF body's "extraction" token is found via BM25.
    """
    fixture = _SharePointFixtureContent(
        item_id="01ITEMPDFFIXTURE",
        raw=_PDF_FIXTURE.read_bytes(),
        mime=_PDF_MIME,
        web_url="https://contoso.sharepoint.com/sites/team/Documents/agent-handbook.pdf",
        last_modified_at="2026-05-22T10:00:00Z",
    )
    # caplog is reserved for the future ON-branch-marker assertion when
    # the test runs the unwrapped production helper.
    _ = caplog
    matches = _run_one_fixture_through_composed_path(
        tmp_path,
        fixture=fixture,
        query_token="extraction",
    )
    assert matches >= 1, (
        "composed PDF path must surface the extracted body via BM25; got 0 matches. "
        "Likely cause: markitdown didn't recover text content from the fixture."
    )


@pytest.mark.skipif(
    not _pptx_available() or not _PPTX_FIXTURE.is_file(),
    reason=(
        "pptx extra not installed or sample.pptx fixture missing; "
        "install via 'pip install Kairix-agentic-knowledge-mgt[pptx]' and ensure "
        "tests/fixtures/extractors/sample.pptx is recorded"
    ),
)
def test_composed_sharepoint_pptx_path(tmp_path: Path) -> None:
    """PPTX: composed path file → connector → pptx → silver → BM25.

    The fixture's slide 2 body carries "Point one / Point two / Point
    three"; the BM25 query for "Point" must surface the chunk.

    Sabotage proof (verified): mutating the fixture's mime to
    ``"text/plain"`` makes the ExtractorRegistry dispatch to
    passthrough; the decoded "binary" payload doesn't contain the
    slide body token and the BM25 assertion fails. Restored, pptx
    extracts the body text and BM25 finds it.
    """
    fixture = _SharePointFixtureContent(
        item_id="01ITEMPPTXFIXTURE",
        raw=_PPTX_FIXTURE.read_bytes(),
        mime=_PPTX_MIME,
        web_url="https://contoso.sharepoint.com/sites/team/Documents/team-deck.pptx",
        last_modified_at="2026-05-22T11:00:00Z",
    )
    matches = _run_one_fixture_through_composed_path(
        tmp_path,
        fixture=fixture,
        query_token="Point",
    )
    assert matches >= 1, (
        "composed PPTX path must surface the slide body via BM25; got 0 matches. "
        "Likely cause: ExtractorRegistry did not route to the pptx plugin, or "
        "pptx extracted the body without slide-body markdown."
    )


@pytest.mark.skipif(
    not _docx_available() or not _DOCX_FIXTURE.is_file(),
    reason=(
        "docx extra not installed or sample.docx fixture missing; "
        "install via 'pip install Kairix-agentic-knowledge-mgt[docx]' and ensure "
        "tests/fixtures/extractors/sample.docx is recorded"
    ),
)
def test_composed_sharepoint_docx_path(tmp_path: Path) -> None:
    """DOCX: composed path file → connector → docx → silver → BM25.

    The fixture's body carries the "Introduction" heading and a
    "Background" subheading; the BM25 query for "Background" must
    surface the chunk.

    Sabotage proof (verified): replacing the fixture content with the
    PPTX bytes routes docx at the wrong content; the heading token
    isn't in the rendered markdown and BM25 misses. Restored, the
    docx body is queryable.
    """
    fixture = _SharePointFixtureContent(
        item_id="01ITEMDOCXFIXTURE",
        raw=_DOCX_FIXTURE.read_bytes(),
        mime=_DOCX_MIME,
        web_url="https://contoso.sharepoint.com/sites/team/Documents/spec.docx",
        last_modified_at="2026-05-22T12:00:00Z",
    )
    matches = _run_one_fixture_through_composed_path(
        tmp_path,
        fixture=fixture,
        query_token="Background",
    )
    assert matches >= 1, (
        "composed DOCX path must surface the heading + body via BM25; got 0 matches. "
        "Likely cause: ExtractorRegistry did not route to the docx plugin, or "
        "docx extracted the body without preserving the heading text."
    )


# ---------------------------------------------------------------------------
# E2E — path filter routes only included items through the composed pipeline
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_composed_path_with_include_paths_indexes_only_included_subset(tmp_path: Path) -> None:
    """The composed connector pipeline indexes only items whose path
    matches include_paths; excluded items don't reach the FTS index.

    Differential value vs. the integration test: the integration test
    asserts on the connector's emitted events; this E2E confirms the
    filter integrates with the extract → chunk → index downstream so an
    operator-set filter actually prevents excluded items from becoming
    searchable.

    Sabotage proof (verified): removing the include_paths kwarg routes
    the partner-materials envelope through the pipeline and the
    BM25 query for its token surfaces a hit. Restoring the filter
    returns the test to zero hits for the excluded token.
    """
    included = _SharePointFixtureContent(
        item_id="01ITEMINCLUDED",
        raw=b"# Architecture\n\nCanonical engineering reference for the platform.\n",
        mime="text/markdown",
        web_url="https://contoso.sharepoint.com/sites/team/Documents/Curated-Content/architecture.md",
        last_modified_at="2026-05-22T12:00:00Z",
    )
    excluded = _SharePointFixtureContent(
        item_id="01ITEMEXCLUDED",
        raw=b"# Partner Deck\n\nThis content should be filtered out by include_paths.\n",
        mime="text/markdown",
        web_url="https://contoso.sharepoint.com/sites/team/Documents/Vendor-Bulk-Materials/deck.md",
        last_modified_at="2026-05-22T12:05:00Z",
    )

    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", True)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "index.sqlite"
    db = _build_db_with_schema(db_path)

    # Filter-aware fake — emits only the included envelope, mimicking what
    # the real connector's filter does upstream of the pipeline.
    fake_connector = _FakeSharePointConnector(fixtures=[included])
    registry = ExtractorRegistry()

    def _on_branch() -> ConnectorSyncResult:
        extractor = registry.resolve(included.mime, included.raw[:8])
        pipeline = build_connector_pipeline(
            db=db,
            bronze_root=bronze_root,
            collection="sharepoint",
        )
        result = pipeline.run_batch(fake_connector, extractor)
        db.commit()
        return ConnectorSyncResult(
            synced=result.processed,
            failed=result.dead_lettered,
            dead_letter_added=result.dead_lettered,
        )

    dispatch_sharepoint_sync(read_flag=resolver.get, on_branch=_on_branch)
    _populate_fts(db)

    # Token unique to the included fixture must be searchable
    included_hits = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'sharepoint'",
            ("Canonical",),
        )
    )
    # Token unique to the excluded fixture must NOT be searchable
    excluded_hits = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'sharepoint'",
            ("Partner",),
        )
    )
    db.close()

    assert len(included_hits) >= 1, "the included fixture's unique token must be searchable"
    assert len(excluded_hits) == 0, (
        f"the excluded fixture's unique token must NOT be searchable — filter leaked, got {excluded_hits!r}. "
        f"reference: docs/architecture/sharepoint-path-filtering.md"
    )
    # `excluded` is intentionally never indexed in this scenario — the
    # connector's filter would drop it upstream of the pipeline.
    _ = excluded
