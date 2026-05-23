"""End-to-end composed path test for the ``obsidian_connector_primary`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a top-level
capability spec.

Exercises the composed production path with the flag ON:

  paths setup (FakePaths over tmp_path)
    → real SQLite schema + FTS5 index
    → seeded markdown vault (mirrors test_composed_production_path.py)
    → real ConnectorPipeline via the production
      ``run_connector_sync_pipeline`` against a real
      ``kairix.config.yaml`` declaring an obsidian connector
    → real factory.build_search_pipeline(paths=...) for the query side
    → real SearchPipeline.search() through composed production code
    → assertion that the connector-pipeline-ingested chunk is
      retrievable through the composed search surface

The OFF path is covered by ``tests/e2e/test_composed_production_path.py``
(the canonical E2E test runs against the default-off registry state)
plus the integration tests in
``tests/integration/test_feature_flag_obsidian_connector_primary.py``.
F54's E2E requirement is per-flag (one E2E composed-path file); both
branches don't both need an E2E entry.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from kairix.worker import (
    ConnectorSyncDeps,
    run_via_connector_pipeline,
    run_via_legacy_document_scanner,
    dispatch_connector_sync,
)
from tests.fakes import FakeFeatureFlagResolver, FakePaths, FakeProvider, FakeProviderRegistry


def _seed_vault(document_root: Path) -> str:
    """Seed the vault with one canonical markdown note + return its body.

    Returns the body so the test can assert on a substring that's
    distinctive enough to survive chunking + FTS5 indexing.
    """
    document_root.mkdir(parents=True, exist_ok=True)
    body = (
        "# Obsidian connector cutover trial\n\n"
        "The obsidian connector cutover trial captures the dogfood VM's first "
        "UAT pass with the obsidian-connector-primary flag flipped on. "
        "Retrieval through the connector-pipeline path must surface this note.\n"
    )
    (document_root / "cutover_trial.md").write_text(body, encoding="utf-8")
    return body


def _populate_fts(db_path: Path) -> None:
    """Mirror the production embed pipeline's FTS5 population step.

    The connector pipeline's :class:`_SqliteChunkWriter` writes
    ``documents`` + ``content`` rows but does NOT touch ``documents_fts``
    (Wave-2 explicit scope — Wave 3+ swaps in a writer that updates the
    FTS index). For the E2E search assertion to find the note, we
    populate the FTS index from the materialised rows the same way
    ``test_composed_production_path.py`` does for the legacy path.
    """
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
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
    finally:
        db.close()


def _extract_path(row: object) -> str:
    """Same shape as test_composed_production_path.py — pull doc path from BudgetedResult."""
    inner = getattr(row, "result", None)
    return str(getattr(inner, "path", "") or "")


@pytest.mark.e2e
def test_composed_obsidian_connector_primary_on_path(tmp_path: Path) -> None:
    """Flag ON, composed path: connector-pipeline ingest → factory.build → search.

    Sabotage proof (verified): commenting out the
    ``connector_pipeline_runner`` invocation in the test's branch
    composition makes the ``documents`` row never land and the search
    assertion fails. Restored, the composed path returns the seeded
    note.
    """
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    _seed_vault(paths.document_root)

    # Real schema in the FakePaths-rooted DB — same shape as e2e_db fixture
    db = sqlite3.connect(str(paths.db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db)
    db.close()

    # Real connector config declaring obsidian over the seeded vault
    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "extractor": "passthrough",
                        "config": {"vault_root": str(paths.document_root)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # Pin the flag ON via the canonical fake resolver and route through
    # the production dispatch surface. The branches stay wired to the
    # real production helpers — the ON branch passes through a
    # tmp_path-rooted ConnectorSyncDeps so the pipeline runs against
    # the FakePaths-rooted DB.
    resolver = FakeFeatureFlagResolver().with_flag("obsidian_connector_primary", True)
    connector_deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(paths.db_path), timeout=10.0),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    legacy_calls = {"n": 0}

    def _never_legacy() -> object:
        legacy_calls["n"] += 1
        return run_via_legacy_document_scanner()

    sync_result = dispatch_connector_sync(
        read_flag=resolver.get,
        on_branch=lambda: run_via_connector_pipeline(connector_deps),
        off_branch=_never_legacy,
    )

    assert legacy_calls["n"] == 0, "flag ON must NOT run the legacy DocumentScanner branch"
    assert sync_result.synced >= 1, f"connector pipeline must have indexed the seeded note; got {sync_result}"

    _populate_fts(paths.db_path)

    # Composed search surface — production factory + production pipeline
    reset_search_pipeline_cache()
    cfg = RetrievalConfig(provider="fake")
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    pipeline = build_search_pipeline(config=cfg, registry=registry, paths=paths)

    result = pipeline.search(query="obsidian connector cutover trial", budget=3000)

    assert result.results, (
        f"connector-pipeline-ingested note must be retrievable through composed search; "
        f"error={result.error!r} bm25={result.bm25_count} vec={result.vec_count}"
    )
    returned_paths = [_extract_path(row) for row in result.results]
    assert any("cutover_trial" in p for p in returned_paths if p), (
        f"search returned results but not the connector-pipeline-ingested note: {returned_paths}"
    )
