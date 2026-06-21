"""Tests for IM-3 + Task 4 — ``run_connector_sync_pipeline`` enumerates
the canonical ``topology.connectors`` block and wires the production
``ConnectorPipeline`` against each cc_pair.

Task 4 of the connector canonical-collapse refactor (Phase 1) moves
ingest enumeration off the legacy top-level ``connectors:`` list onto
``topology.connectors`` read through the overlay-aware merged mapping
(``ConnectorSyncDeps.config_mapping_fn``). The overloaded legacy
``entry["name"]`` splits into three canonical values: ``kind`` (plugin
resolution), the cc_pair ``name`` (routing / chunk-writer key), and
``config`` (the connector_specific_config mapping).

Covers:
  - disabled short-circuit returns zero counters without touching the
    config / DB / bronze paths;
  - end-to-end run against a real Obsidian vault + passthrough extractor
    enumerated from ``topology.connectors`` + a cc_pair, indexes both
    markdown files and lands them in the cc_pair-named collection;
  - per-connector failure is logged and the loop continues — sibling
    connectors still report their own counters;
  - a connector with zero cc_pairs is skipped (no collection target);
  - the ``connector_enabled`` predicate is consulted per entry — a
    registered kind gated OFF is skipped, a flagless sibling still runs.

Sabotage-proof (executed by the agent, recorded for the reader): in
``run_connector_sync_pipeline`` comment out the ``pipeline.run_batch(...)``
line inside ``_run_one_connector_batch``; re-run
``test_runs_configured_obsidian_pipeline`` — the ``synced == 2``
assertion fails (the counters stay at zero because no item was
processed). Restore the call; the test passes again.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.worker import (
    ConnectorSyncDeps,
    ConnectorSyncResult,
    run_connector_sync_pipeline,
)

pytestmark = pytest.mark.unit


def _no_db_factory() -> sqlite3.Connection:
    """Sentinel db_factory that asserts when the short-circuit path is bypassed."""
    raise AssertionError("db_factory must not be invoked on the short-circuit path")


def _obsidian_topology(vault: Path, *, cc_pair_name: str = "obsidian-personal") -> dict[str, Any]:
    """Build a minimal merged mapping with one obsidian connector + cc_pair.

    Mirrors the canonical ``topology.connectors`` / ``topology.cc_pairs``
    shape the setup wizard writes — the connector carries ``kind`` +
    ``connector_specific_config``, and a single cc_pair binds it to a
    routing name (the chunk-writer collection key).
    """
    return {
        "topology_v2": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Obsidian Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            "cc_pairs": [
                {
                    "id": "obsidian-pair",
                    "connector": "obsidian-conn",
                    "credential": None,
                    "name": cc_pair_name,
                }
            ],
        }
    }


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
        config_mapping_fn=dict,
        db_factory=_no_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result == ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


@pytest.mark.integration
def test_runs_configured_obsidian_pipeline(tmp_path: Path) -> None:
    """A real vault with two markdown files + a ``topology.connectors``
    block (one obsidian connector + one cc_pair) drives the full
    ``ConnectorPipeline`` and indexes both items.

    Proves the canonical-topology ingest redirect (Task 4): ingest
    enumerates ``topology.connectors`` from the merged mapping, NOT the
    legacy top-level ``connectors:`` list. This is the wizard-onboarding
    fix — the wizard writes ``topology.connectors`` and ingest now reads
    it.

    Uses real :class:`StreamingBronzeStore`, :class:`DefaultSilverProcessor`,
    :class:`CursorStore`, :class:`DeadLetterStore`, the in-process
    SQLite chunk-writer / entity-graph sink, and the real Obsidian
    connector + passthrough extractor resolved through the entry-point
    registry. No fakes at this seam — F47-clean.

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

    db_path = tmp_path / "index.sqlite"

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _obsidian_topology(vault),
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

    db = sqlite3.connect(str(db_path))
    try:
        # #336 — wired-writer regression pin. Without
        # documents_media_writer=SqliteDocumentsMediaWriter(db) silver skips
        # the per-document row. Both notes should land one row each.
        (media_count,) = db.execute("SELECT COUNT(*) FROM documents_media").fetchone()
        # The chunk-writer routing key is the cc_pair name — chunks land in
        # the cc_pair-named collection, NOT the connector kind or the
        # connector name. Pins the D2 kind-vs-cc_pair-name split.
        collections = {row[0] for row in db.execute("SELECT DISTINCT collection FROM documents").fetchall()}
    finally:
        db.close()
    assert media_count == 2, (
        f"expected documents_media populated for both notes (#336 regression pin); got {media_count}. "
        "fix: confirm DefaultSilverProcessor is constructed with documents_media_writer=SqliteDocumentsMediaWriter(db) "
        "in kairix.worker._run_one_connector_batch and _build_reextract_components."
    )
    assert collections == {"obsidian-personal"}, (
        "chunks must land in the cc_pair-named collection (routing keys on cc_pair name, "
        f"not connector kind 'obsidian'); got {collections}."
    )


@pytest.mark.unit
def test_failing_connector_logged_and_loop_continues(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With two configured connectors, the first one raises during
    resolution; the second one resolves and runs. The aggregated result
    must reflect only the second connector's counters and the worker
    must log the failure rather than crash.

    Uses a fictional ``does-not-exist`` kind for the first connector
    (forces ``resolve_connector`` to raise ``KeyError``) plus a valid
    obsidian connector against an empty vault for the second (zero items
    but the entry resolves cleanly). Each connector has a cc_pair so both
    are enumerated.

    Sabotage proof: remove the ``except Exception`` block inside the
    ``for entry in entries`` loop; the ``KeyError`` from the unknown
    connector propagates out of ``run_connector_sync_pipeline`` and the
    test fails with an unhandled error. Restored, the test passes
    because the per-entry try/except absorbs the failure.
    """
    empty_vault = tmp_path / "vault"
    empty_vault.mkdir()

    mapping: dict[str, Any] = {
        "topology_v2": {
            "connectors": [
                {"id": "missing-conn", "kind": "does-not-exist", "name": "Missing"},
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Empty Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(empty_vault)},
                },
            ],
            "cc_pairs": [
                {"id": "missing-pair", "connector": "missing-conn", "credential": None, "name": "missing-cc"},
                {"id": "obsidian-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"},
            ],
        }
    }

    db_path = tmp_path / "index.sqlite"

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
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
        rec.getMessage() for rec in caplog.records if "missing-cc" in rec.getMessage() and "failed" in rec.getMessage()
    ]
    assert failure_logs, (
        f"expected a warning naming the failing cc_pair; got {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.unit
def test_no_topology_returns_zero_counter_no_op(tmp_path: Path) -> None:
    """No topology connectors → zero-counter result, no DB construction,
    no raise.

    Sabotage proof: change the early-return after the no-entries check
    to ``return ConnectorSyncResult(synced=9, ...)``; ``result.synced ==
    0`` fails. Restored, the test passes.
    """
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=dict,
        db_factory=_no_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result == ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


@pytest.mark.unit
def test_zero_cc_pair_connector_is_skipped(tmp_path: Path) -> None:
    """A topology connector referenced by zero cc_pairs is not ingestable
    (no collection target) → skipped, no DB construction, zero counters.

    A connector with no cc_pair yields no entry, so the no-entries guard
    short-circuits before the db_factory is touched.

    Sabotage proof: yield a synthetic entry for a cc_pair-less connector
    (drop the ``connector.id in cc_pairs_by_connector_id`` guard) → the
    _no_db_factory sentinel fires when the loop builds the DB, failing
    this test.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    mapping: dict[str, Any] = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "lonely-conn",
                    "kind": "obsidian",
                    "name": "No cc_pair",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            # No cc_pairs referencing lonely-conn.
            "cc_pairs": [],
        }
    }

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=_no_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    assert result == ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


@pytest.mark.integration
def test_connector_enabled_gates_per_entry_in_loop(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-entry loop consults ``connector_enabled(entry["kind"], ...)``
    — a registered kind whose flag reads OFF is skipped, while a flagless
    sibling still runs.

    Two connectors: ``sharepoint`` (registered ``connector_sharepoint``
    flag, pinned OFF) and ``obsidian`` (flagless, always-on) over a real
    two-note vault. With the flag OFF, the sharepoint plugin never
    resolves (it would otherwise need a Graph credential); obsidian still
    indexes both notes.

    Sabotage proof: drop the ``connector_enabled`` skip in the loop — the
    sharepoint entry resolves + runs, but the ``gated off`` INFO log no
    longer fires, so the assertion below fails. Restored, the predicate
    skips sharepoint before resolution and the log fires.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nFirst note.\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n\nSecond note.\n", encoding="utf-8")

    mapping: dict[str, Any] = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "sharepoint-conn",
                    "kind": "sharepoint",
                    "name": "Corp SharePoint",
                    "connector_specific_config": {},
                },
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                },
            ],
            "cc_pairs": [
                {"id": "sp-pair", "connector": "sharepoint-conn", "credential": None, "name": "sharepoint-cc"},
                {"id": "ob-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"},
            ],
        }
    }

    db_path = tmp_path / "index.sqlite"

    def _flag_reader(_name: str) -> bool:
        # connector_sharepoint OFF (and any other registered kind defaults OFF).
        return False

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        flag_reader=_flag_reader,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = run_connector_sync_pipeline(deps)

    # Obsidian (flagless) still ran and indexed both notes.
    assert result.synced == 2, f"flagless obsidian sibling must still sync; got {result}"
    assert result.failed == 0

    gated_logs = [
        rec.getMessage()
        for rec in caplog.records
        if "sharepoint" in rec.getMessage().lower() and "gated" in rec.getMessage().lower()
    ]
    assert gated_logs, (
        f"expected a 'connector sharepoint gated off' INFO log; got {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.unit
def test_multi_cc_pair_connector_yields_one_entry_per_pair(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A single topology connector referenced by TWO cc_pairs yields two
    ingest entries (D2: one entry per cc_pair) — each carries the cc_pair
    name as its routing key.

    Proven cursor-independently: the connector kind is unresolvable
    (``does-not-exist``), so every entry fails at ``resolve_connector``
    and the per-entry try/except logs a failure naming the cc_pair. Two
    cc_pairs → two distinct failure logs (one per cc_pair name). If the
    enumeration yielded one entry per CONNECTOR instead of per cc_pair,
    only one of the two cc_pair names would ever appear.

    Sabotage proof: yield one entry per CONNECTOR (iterate
    ``parsed.connectors`` and emit a single entry per connector) → only
    one cc_pair name is logged, so the ``{"pair-a-cc", "pair-b-cc"}``
    assertion fails. Restored, both cc_pair names appear.
    """
    mapping: dict[str, Any] = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "shared-conn",
                    "kind": "does-not-exist",
                    "name": "Shared Connector",
                    "connector_specific_config": {},
                }
            ],
            "cc_pairs": [
                {"id": "pair-a", "connector": "shared-conn", "credential": None, "name": "pair-a-cc"},
                {"id": "pair-b", "connector": "shared-conn", "credential": None, "name": "pair-b-cc"},
            ],
        }
    }

    db_path = tmp_path / "index.sqlite"

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.WARNING, logger="kairix.worker"):
        run_connector_sync_pipeline(deps)

    failed_pairs = {
        name
        for name in ("pair-a-cc", "pair-b-cc")
        if any(name in rec.getMessage() and "failed" in rec.getMessage() for rec in caplog.records)
    }
    assert failed_pairs == {"pair-a-cc", "pair-b-cc"}, (
        "each cc_pair must produce its own ingest entry (one entry per cc_pair, D2); "
        f"only these cc_pair names reached the per-entry loop: {failed_pairs}."
    )
