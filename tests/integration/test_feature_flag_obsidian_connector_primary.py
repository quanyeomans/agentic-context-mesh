"""Integration tests for the ``obsidian_connector_primary`` flag (PR-6).

Exercises both branches of :func:`kairix.worker.dispatch_connector_sync`
through the production composition surface:

  * **Flag OFF** — the legacy ``DocumentScanner`` branch reads a real
    markdown file from a tmp_path-rooted document_root and writes a
    real row to the ``documents`` table via a real SQLite connection.
  * **Flag ON** — the connector-pipeline branch is driven via the
    production :func:`kairix.worker.run_connector_sync_pipeline`
    against a real ``kairix.config.yaml`` declaring an obsidian
    connector and the same tmp_path-rooted vault.

F47 — the multi-component pipeline is constructed via real factory
surfaces (``run_connector_sync_pipeline`` + ``LegacyScannerDeps``); no
direct ``*Pipeline(...)`` construction in this file.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through the production ``dispatch_connector_sync(read_flag=…)``
DI seam — no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
inverting the if/else in :func:`dispatch_connector_sync` so OFF runs
the connector pipeline and ON runs the legacy scanner — confirmed
that BOTH :func:`test_flag_off_uses_legacy_scanner` AND
:func:`test_flag_on_uses_connector_pipeline` fail. Restoring the
original branch direction returns both tests to green.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
import yaml

from kairix.core.db.schema import create_schema
from kairix.worker import (
    ConnectorSyncDeps,
    LegacyScannerDeps,
    dispatch_connector_sync,
    run_connector_sync_pipeline,
    run_via_connector_pipeline,
    run_via_legacy_document_scanner,
)
from tests.fakes import FakeFeatureFlagResolver, FakePaths

pytestmark = pytest.mark.integration

_LEGACY_MARKER = "routing via legacy document scanner"
_CONNECTOR_MARKER = "routing via obsidian connector pipeline"


def _seeded_paths(tmp_path: Path) -> FakePaths:
    """FakePaths rooted at tmp_path with a single seeded markdown note + schema.

    The legacy DocumentScanner needs a real document_root + a real
    SQLite schema; the connector pipeline needs the same DB so its
    chunk writer can land rows. Sharing one fixture keeps both branches
    apples-to-apples on the same DB shape.
    """
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    paths.document_root.mkdir(parents=True, exist_ok=True)
    (paths.document_root / "alpha.md").write_text(
        "# Alpha\n\nFirst note ingested via the active branch.\n",
        encoding="utf-8",
    )
    db = sqlite3.connect(str(paths.db_path), timeout=10.0)
    create_schema(db)
    db.close()
    return paths


def _open_db(paths: FakePaths) -> sqlite3.Connection:
    """Open the seeded SQLite connection — shared helper between branches."""
    return sqlite3.connect(str(paths.db_path), timeout=10.0)


def test_flag_off_uses_legacy_scanner(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """OFF branch — DocumentScanner runs and the ConnectorPipeline does not.

    The fake resolver pins ``obsidian_connector_primary`` to False, the
    production :func:`dispatch_connector_sync` routes through the legacy
    branch, and the seeded markdown file lands in the ``documents``
    table proving the scanner did real work.
    """
    paths = _seeded_paths(tmp_path)
    resolver = FakeFeatureFlagResolver().with_flag("obsidian_connector_primary", False)

    legacy_deps = LegacyScannerDeps(
        document_root_resolver=lambda: paths.document_root,
        db_factory=lambda: _open_db(paths),
    )
    # Construct a never-called connector-pipeline runner — if the
    # dispatcher routes here instead, the assertion failure will name
    # the wrong branch explicitly. F47-friendly: the production
    # ``run_connector_sync_pipeline`` is what the test *would* invoke
    # in the ON case; we wrap it so a misroute fails loud.
    connector_pipeline_calls = {"n": 0}

    def _connector_pipeline_runner() -> object:
        connector_pipeline_calls["n"] += 1
        return run_connector_sync_pipeline()

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        dispatch_connector_sync(
            read_flag=resolver.get,
            on_branch=_connector_pipeline_runner,
            off_branch=lambda: run_via_legacy_document_scanner(legacy_deps),
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_LEGACY_MARKER in m for m in messages), (
        f"flag OFF must route through the legacy DocumentScanner branch; logs={messages!r}"
    )
    assert not any(_CONNECTOR_MARKER in m for m in messages), (
        f"flag OFF must NOT route through the connector pipeline branch; logs={messages!r}"
    )
    assert connector_pipeline_calls["n"] == 0, "connector pipeline must not run when flag is OFF"

    # The real scanner wrote the seeded note into ``documents``.
    db = _open_db(paths)
    try:
        cursor = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1")
        (count,) = cursor.fetchone()
    finally:
        db.close()
    assert count >= 1, "legacy DocumentScanner must have indexed the seeded note"


def test_flag_on_uses_connector_pipeline(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """ON branch — ConnectorPipeline runs and the DocumentScanner does not.

    The fake resolver pins ``obsidian_connector_primary`` to True, the
    production :func:`dispatch_connector_sync` routes through the
    connector-pipeline branch, and a real ``kairix.config.yaml``
    declaring an obsidian connector drives the
    :class:`ConnectorPipeline` over the same seeded vault.
    """
    paths = _seeded_paths(tmp_path)
    resolver = FakeFeatureFlagResolver().with_flag("obsidian_connector_primary", True)

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

    connector_deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: _open_db(paths),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    legacy_branch_calls = {"n": 0}

    def _legacy_runner() -> object:
        legacy_branch_calls["n"] += 1
        return run_via_legacy_document_scanner(
            LegacyScannerDeps(
                document_root_resolver=lambda: paths.document_root,
                db_factory=lambda: _open_db(paths),
            )
        )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_connector_sync(
            read_flag=resolver.get,
            on_branch=lambda: run_via_connector_pipeline(connector_deps),
            off_branch=_legacy_runner,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_CONNECTOR_MARKER in m for m in messages), (
        f"flag ON must route through the connector pipeline branch; logs={messages!r}"
    )
    assert not any(_LEGACY_MARKER in m for m in messages), (
        f"flag ON must NOT route through the legacy DocumentScanner branch; logs={messages!r}"
    )
    assert legacy_branch_calls["n"] == 0, "legacy DocumentScanner must not run when flag is ON"

    # Connector pipeline drove the configured obsidian connector against
    # the seeded vault; at least one note went through the pipeline.
    assert result.synced >= 1, f"connector pipeline must have processed the seeded note; got {result}"
