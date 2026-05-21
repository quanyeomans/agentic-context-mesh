"""Unit tests for :class:`kairix.core.facts.SQLiteFactStore` migration paths.

Targets branches that the round-trip tests in
``test_sqlite_fact_store.py`` cannot reach via the standard
``add → search/find_conflicts`` path:

* ``_apply_column_migrations`` idempotency — second call on an
  already-migrated DB is a no-op (probe ``PRAGMA table_info`` first).
* ``_row_to_record`` legacy projection — a pre-Lever-A SQLite file
  whose rows lack the ``evidence_at`` column reaches ``_row_to_record``
  via ``find_conflicts`` (which does NOT call ``_ensure_schema``).
  The projection must take the ``else: evidence_at = None`` branch
  rather than raising ``IndexError`` on ``row[_COL_EVIDENCE_AT]``.

Marker rationale (``unit``): same as ``test_sqlite_fact_store.py`` —
one component (the SQLite fact-store) against stdlib SQLite via
``tmp_path``, no external service, no usearch, no cross-component
wiring. F1 / F5 / F6 clean.

Every test was sabotage-proven during authoring (mutate → run → confirm
fail → restore). The proof transcripts are in the commit body.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.facts import SQLiteFactStore, StoredFactRecord

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — build a pre-Lever-A schema (no ``evidence_at`` column).
# ---------------------------------------------------------------------------


def _build_legacy_schema(db_path: Path) -> None:
    """Create a pre-migration SQLite file at ``db_path``.

    Mirrors the schema that shipped before Stream A Lever A added the
    ``evidence_at`` column — same columns, same FTS5 virtual table,
    but no ``evidence_at``. Used to exercise the migration paths.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE facts (
                id TEXT PRIMARY KEY,
                entity TEXT NOT NULL,
                attribute TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_turn_ids TEXT NOT NULL,
                extracted_at TEXT NOT NULL,
                superseded_by TEXT,
                namespace TEXT NOT NULL,
                FOREIGN KEY(superseded_by) REFERENCES facts(id)
            );
            CREATE INDEX idx_facts_entity_attribute ON facts(entity, attribute);
            CREATE INDEX idx_facts_namespace ON facts(namespace);
            CREATE VIRTUAL TABLE facts_fts USING fts5(
                entity, attribute, value,
                content='facts',
                content_rowid='rowid'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_legacy_row(
    db_path: Path,
    *,
    fact_id: str,
    entity: str,
    attribute: str,
    value: str,
    namespace: str = "shared",
) -> None:
    """Insert a row into the legacy schema (no ``evidence_at``) and its FTS shadow."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO facts (id, entity, attribute, value, confidence, source_turn_ids, "
            "extracted_at, superseded_by, namespace) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id,
                entity,
                attribute,
                value,
                0.9,
                '["t1"]',
                "2026-01-01T00:00:00Z",
                None,
                namespace,
            ),
        )
        conn.execute(
            "INSERT INTO facts_fts (rowid, entity, attribute, value) "
            "SELECT rowid, entity, attribute, value FROM facts WHERE id = ?",
            (fact_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Idempotent column migration — already-migrated DBs are no-ops.
# ---------------------------------------------------------------------------


def test_column_migration_is_idempotent_across_store_instances(tmp_path: Path) -> None:
    """Re-opening a store on a migrated DB must not re-run the ALTER.

    A fresh ``SQLiteFactStore`` resets ``_schema_initialised`` so the
    first ``add`` call on the re-opened instance calls
    ``_apply_column_migrations`` again. SQLite raises
    ``OperationalError: duplicate column name: evidence_at`` if the
    ALTER fires a second time. The PRAGMA table_info probe must guard
    against that.

    Sabotage-proof (executed): remove the
    ``if column_name not in existing_columns:`` guard in
    ``_apply_column_migrations`` — the second store's ``add`` raises
    ``sqlite3.OperationalError: duplicate column name: evidence_at``.
    Sabotage confirmed → branch restored → assertion passes.
    """
    db = tmp_path / "facts.sqlite"

    store1 = SQLiteFactStore(db_path=db)
    store1.add(
        StoredFactRecord(
            id="f1",
            entity="e",
            attribute="a",
            value="v1",
            confidence=0.9,
            source_turn_ids=("t1",),
            extracted_at="2026-01-01T00:00:00Z",
            superseded_by=None,
            namespace="shared",
        )
    )

    # Re-open the same DB through a fresh instance — schema_initialised
    # is False again, so _ensure_schema runs and _apply_column_migrations
    # is invoked a second time. Must not raise.
    store2 = SQLiteFactStore(db_path=db)
    store2.add(
        StoredFactRecord(
            id="f2",
            entity="e",
            attribute="a",
            value="v2",
            confidence=0.9,
            source_turn_ids=("t2",),
            extracted_at="2026-01-02T00:00:00Z",
            superseded_by=None,
            namespace="shared",
        )
    )

    # Both rows must be present — confirms the second add() actually committed.
    hits = store2.find_conflicts(entity="e", attribute="a")
    assert {h.id for h in hits} == {"f1", "f2"}, (
        f"expected both rows after idempotent migration; got {[h.id for h in hits]}"
    )


# ---------------------------------------------------------------------------
# Legacy row projection — ``_row_to_record`` else branch via public API.
# ---------------------------------------------------------------------------


def test_find_conflicts_on_unmigrated_legacy_db_projects_evidence_at_as_none(
    tmp_path: Path,
) -> None:
    """``find_conflicts`` on a legacy DB returns records with ``evidence_at=None``.

    ``find_conflicts`` does NOT call ``_ensure_schema`` — it short-circuits
    on ``_table_exists`` then runs the SELECT directly. A legacy SQLite
    file has rows whose ``sqlite3.Row`` does not expose an
    ``evidence_at`` key. The ``_row_to_record`` projection must take the
    ``else: evidence_at = None`` branch rather than raise IndexError.

    Sabotage-proof (executed): change ``evidence_at = None`` (line 445)
    to ``evidence_at = "SAB"`` — the assertion below catches the sentinel
    leak. Sabotage confirmed → branch restored → assertion passes.
    """
    db = tmp_path / "legacy.sqlite"
    _build_legacy_schema(db)
    _insert_legacy_row(db, fact_id="leg1", entity="Caroline", attribute="status", value="single")

    # Construct a store but never call add — schema migration does not run.
    store = SQLiteFactStore(db_path=db)

    records = store.find_conflicts(entity="Caroline", attribute="status")
    assert len(records) == 1, f"expected 1 record from legacy DB; got {len(records)}"
    record = records[0]
    assert record.id == "leg1"
    assert record.evidence_at is None, (
        f"legacy row must project evidence_at as None; got {record.evidence_at!r}. "
        "If non-None, the else-branch of _row_to_record was bypassed."
    )


def test_search_on_unmigrated_legacy_db_projects_evidence_at_as_none(
    tmp_path: Path,
) -> None:
    """``search`` on a legacy DB returns hits with ``record.evidence_at=None``.

    Same legacy scenario as the ``find_conflicts`` test, but exercised
    through the FTS search path. Both public methods route through
    ``_row_to_record`` and both must take the ``else`` branch when the
    row lacks the ``evidence_at`` column.

    Sabotage-proof (executed): change ``evidence_at = None`` (line 445)
    to ``evidence_at = "SAB"`` — this test catches the leak too, proving
    the search code path also relies on the legacy else-branch. Sabotage
    confirmed → branch restored → assertion passes.
    """
    db = tmp_path / "legacy.sqlite"
    _build_legacy_schema(db)
    _insert_legacy_row(
        db,
        fact_id="leg-search",
        entity="Caroline",
        attribute="role",
        value="distinctvaluetoken",
    )

    store = SQLiteFactStore(db_path=db)
    hits = store.search("distinctvaluetoken")

    assert hits, "expected legacy FTS row to surface in search results"
    assert hits[0].record.id == "leg-search"
    assert hits[0].record.evidence_at is None, (
        f"legacy search hit must project evidence_at as None; got {hits[0].record.evidence_at!r}"
    )
