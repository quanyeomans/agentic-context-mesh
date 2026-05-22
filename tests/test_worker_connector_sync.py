"""Tests for IM-3 — ``run_connector_sync_pipeline`` wires the production
``ConnectorPipeline`` against operator-configured connectors.

Covers:
  - disabled short-circuit returns zero counters without touching the
    config / DB / bronze paths;
  - end-to-end run against a real Obsidian vault + passthrough extractor
    indexes the configured markdown files;
  - per-connector failure is logged and the loop continues — sibling
    connectors still report their own counters.

Sabotage-proof (executed by the agent, recorded for the reader): in
``run_connector_sync_pipeline`` comment out the ``pipeline.run_batch(...)``
line inside ``_run_one_connector_batch``; re-run
``test_runs_configured_obsidian_pipeline`` — the ``synced == 2``
assertion fails (the counters stay at zero because no item was
processed). Restore the call; the test passes again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from kairix.worker import (
    ConnectorSyncDeps,
    ConnectorSyncResult,
    run_connector_sync_pipeline,
)

pytestmark = pytest.mark.unit


def _no_db_factory() -> sqlite3.Connection:
    """Sentinel db_factory that asserts when the short-circuit path is bypassed."""
    raise AssertionError("db_factory must not be invoked on the short-circuit path")


@pytest.mark.unit
def test_disabled_short_circuits(tmp_path: Path) -> None:
    """When ``deps.disabled_fn`` returns True, ``run_connector_sync_pipeline``
    returns a zero-counter :class:`ConnectorSyncResult` and never touches
    the DB / config path.

    Sabotage proof: change the early-return body to
    ``return ConnectorSyncResult(synced=1, ...)`` and the
    ``result.synced == 0`` assertion fails. Restored, the test passes.
    """
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: True,
        config_path_resolver=lambda: tmp_path / "missing.yaml",
        db_factory=_no_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result == ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


@pytest.mark.integration
def test_runs_configured_obsidian_pipeline(tmp_path: Path) -> None:
    """A real vault with two markdown files + a minimal
    ``kairix.config.yaml`` declaring an obsidian connector drives the
    full ``ConnectorPipeline`` and indexes both items.

    Uses real :class:`FilesystemBronzeStore`, :class:`DefaultSilverProcessor`,
    :class:`CursorStore`, :class:`DeadLetterStore`, the in-process
    SQLite chunk-writer / entity-graph sink wired by IM-3, and the
    real Obsidian connector + passthrough extractor resolved through
    the entry-point registry. No fakes at this seam — F47-clean.

    Sabotage proof (the brief's mandated one): comment out
    ``pipeline.run_batch(connector, extractor)`` inside
    ``_run_one_connector_batch``; the assertion ``result.synced == 2``
    fails because nothing flows through the pipeline. Restored, both
    notes are indexed and the test passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nFirst note body content.\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n\nSecond note body content.\n", encoding="utf-8")

    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "extractor": "passthrough",
                        "config": {"vault_root": str(vault)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite"

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result.synced == 2, (
        f"expected both notes synced through the pipeline; got {result}. "
        "fix: confirm ConnectorPipeline.run_batch processed each ChangeEvent and "
        "the writer.upsert call landed the chunks."
    )
    assert result.failed == 0
    assert result.dead_letter_added == 0


@pytest.mark.unit
def test_failing_connector_logged_and_loop_continues(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With two configured connectors, the first one raises during
    resolution; the second one resolves and runs. The aggregated result
    must reflect only the second connector's counters and the worker
    must log the failure rather than crash.

    Uses a fictional ``does-not-exist`` connector name for the first
    entry (forces ``resolve_connector`` to raise ``KeyError``) plus a
    valid obsidian entry against an empty vault for the second (zero
    items but the entry resolves cleanly).

    Sabotage proof: remove the ``except Exception`` block inside the
    ``for entry in entries`` loop; the ``KeyError`` from the unknown
    connector propagates out of ``run_connector_sync_pipeline`` and the
    test fails with an unhandled error. Restored, the test passes
    because the per-entry try/except absorbs the failure.
    """
    import logging

    empty_vault = tmp_path / "vault"
    empty_vault.mkdir()

    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {"name": "does-not-exist", "config": {}},
                    {
                        "name": "obsidian",
                        "extractor": "passthrough",
                        "config": {"vault_root": str(empty_vault)},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite"

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.WARNING, logger="kairix.worker"):
        result = run_connector_sync_pipeline(deps)

    # First connector failed during resolve; second is a valid but empty
    # vault → zero items but no propagated exception. The aggregated
    # result reflects only the second connector's (zero) counters.
    assert result.synced == 0
    assert result.failed == 0
    assert result.dead_letter_added == 0

    failure_logs: list[str] = [
        rec.getMessage() for rec in caplog.records if "connector does-not-exist failed" in rec.getMessage()
    ]
    assert failure_logs, (
        f"expected a warning naming the failing connector; got {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.unit
def test_no_config_file_returns_zero_counter_no_op(tmp_path: Path) -> None:
    """No config file → zero-counter result, no DB construction, no raise.

    Sabotage proof: change the early-return after the no-entries check
    to ``return ConnectorSyncResult(synced=9, ...)``; ``result.synced ==
    0`` fails. Restored, the test passes.
    """
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: tmp_path / "missing.yaml",
        db_factory=_no_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result == ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)
