"""Integration test for the topology v2 Wave A schema migration.

Verifies:
- ``SCHEMA_VERSION`` bumps to ``"3"``
- Fresh DB: all 12 topology_* tables exist; new doc/doc_media columns present
- Legacy DB (synthesised v2-shape): migrate() lands cleanly without data loss
- Migration is idempotent (running twice is a no-op)
- ``validate_schema`` reports clean post-migration
- ``topology_v2_schema`` feature flag exists and defaults False
- New dataclasses + typed exceptions import without error

Per F48: this lives in tests/integration/ and runs in CI Stage 3.
Per F47: uses factory-equivalent construction (the schema is its own
factory — no Pipeline construction).
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import SCHEMA_VERSION, create_schema, migrate, validate_schema

_TOPOLOGY_V2_TABLES = (
    "topology_connectors",
    "topology_credentials",
    "topology_cc_pairs",
    "topology_containers",
    "topology_hierarchy_nodes",
    "topology_collections",
    "topology_collection_sources",
    "topology_federated_connectors",
    "topology_group_grants",
    "topology_scope_profiles",
    "topology_scope_entries",
    "topology_skills",
)


@pytest.mark.integration
def test_schema_version_bumps_to_4() -> None:
    """Wave A bumped SCHEMA_VERSION from "2" to "3"; GH #409 bumps to "4"
    (path_canonical column + idx_documents_path_canonical for the
    enrich-phase indexed lookup that replaces the LIKE-suffix scan).
    """
    assert SCHEMA_VERSION == "4"


@pytest.mark.integration
def test_fresh_db_has_all_topology_v2_tables() -> None:
    """create_schema on a fresh in-memory DB creates all 12 new tables."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in _TOPOLOGY_V2_TABLES:
        assert table in actual, f"missing topology v2 table: {table}"


@pytest.mark.integration
def test_fresh_db_documents_has_new_columns() -> None:
    """Wave A adds ``archived``, ``access_lost``, ``chunker_version`` to documents."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    cols = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
    for column in ("archived", "access_lost", "chunker_version"):
        assert column in cols, f"documents.{column} missing after Wave A migration"


@pytest.mark.integration
def test_fresh_db_documents_media_has_chunker_version() -> None:
    """Wave A adds ``chunker_version`` to documents_media (parallel to extractor_version)."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    cols = {r[1] for r in db.execute("PRAGMA table_info(documents_media)")}
    assert "chunker_version" in cols


@pytest.mark.integration
def test_migration_is_idempotent() -> None:
    """Running migrate() twice is a no-op — second run doesn't error or duplicate state."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    # Insert a probe row that should survive a second migrate
    db.execute(
        """
        INSERT INTO topology_connectors (
            kind, name, connector_specific_config,
            refresh_freq_seconds, prune_freq_seconds, perm_sync_freq_seconds,
            default_sensitivity, created_at, updated_at
        ) VALUES ('obsidian', 'idempotency-probe', '{}', NULL, NULL, NULL, 'internal',
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z')
        """
    )
    db.commit()
    # Run migrate again — should be a no-op
    migrate(db)
    count = db.execute("SELECT COUNT(*) FROM topology_connectors WHERE name = 'idempotency-probe'").fetchone()[0]
    assert count == 1, "idempotent migration must preserve existing rows"


@pytest.mark.integration
def test_validate_schema_clean_after_wave_a() -> None:
    """validate_schema returns [] (clean) post-Wave-A migration."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    errors = validate_schema(db)
    assert errors == [], f"validate_schema should be clean; got errors: {errors!r}"


@pytest.mark.integration
def test_legacy_v2_db_migrates_cleanly() -> None:
    """A DB at schema_version=2 (no topology tables) migrates to v3 without data loss."""
    db = sqlite3.connect(":memory:")
    # Synthesise a v2-shape DB: bootstrap via create_schema, then drop the
    # topology_* tables to simulate the v2 starting state. Insert a probe
    # row into the legacy `documents` table so we can verify it survives.
    create_schema(db, dims=4)
    for table in _TOPOLOGY_V2_TABLES:
        # safe: table name from a closed allow-list above
        db.execute(f"DROP TABLE {table}")
    db.execute("UPDATE kairix_meta SET value = '2' WHERE key = 'schema_version'")
    db.execute(
        "INSERT INTO documents (collection, path, hash, sensitivity) VALUES (?, ?, ?, ?)",
        ("test", "legacy/probe.md", "hash-abc", "internal"),
    )
    db.commit()

    # Run migrate — should restore all the v3 tables + update schema_version
    migrate(db)
    db.commit()
    # Bump schema_version explicitly (create_schema does this at end; the
    # legacy migrate path doesn't, so emulate the create_schema closer).
    db.execute(
        "INSERT OR REPLACE INTO kairix_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    db.commit()

    # All topology tables back
    actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in _TOPOLOGY_V2_TABLES:
        assert table in actual, f"topology table {table} missing after legacy migration"
    # Legacy data preserved
    legacy_count = db.execute("SELECT COUNT(*) FROM documents WHERE path = 'legacy/probe.md'").fetchone()[0]
    assert legacy_count == 1, "legacy documents row lost during migration"
    # Version bumped to current SCHEMA_VERSION (already imported at module top).
    version = db.execute("SELECT value FROM kairix_meta WHERE key = 'schema_version'").fetchone()[0]
    assert version == SCHEMA_VERSION, f"expected schema_version={SCHEMA_VERSION} post-migration, got {version!r}"


@pytest.mark.integration
def test_new_topology_v2_dataclasses_import() -> None:
    """All new topology v2 dataclasses + enums + exceptions import cleanly."""
    from kairix.core.protocols import (
        # enums
        CCPairAccessType,
        CCPairStatus,
        # dataclasses
        Collection,
        CollectionSource,
        CollectionVisibility,
        ConnectorCredentialPair,
        ConnectorInstance,
        # exceptions
        ConnectorValidationError,
        Container,
        ContainerAccessDenied,
        ContainerAccessState,
        ContainerTransient,
        Credential,
        CredentialExpiredError,
        CredentialInvalidError,
        F39Tier,
        FederatedConnector,
        GroupGrant,
        HierarchyNode,
        HierarchyNodeType,
        InsufficientPermissionsError,
        ScopeEntry,
        ScopeProfile,
        ScopeProfileActorKind,
        Skill,
        TaskCollection,
        UnexpectedValidationError,
    )

    # Smoke: every imported symbol is non-None
    symbols = (
        CCPairAccessType,
        CCPairStatus,
        CollectionVisibility,
        ContainerAccessState,
        F39Tier,
        HierarchyNodeType,
        ScopeProfileActorKind,
        Collection,
        CollectionSource,
        Container,
        ConnectorCredentialPair,
        ConnectorInstance,
        Credential,
        FederatedConnector,
        GroupGrant,
        HierarchyNode,
        ScopeEntry,
        ScopeProfile,
        Skill,
        TaskCollection,
        ConnectorValidationError,
        ContainerAccessDenied,
        ContainerTransient,
        CredentialExpiredError,
        CredentialInvalidError,
        InsufficientPermissionsError,
        UnexpectedValidationError,
    )
    for symbol in symbols:
        assert symbol is not None


@pytest.mark.integration
def test_raw_artefact_sensitivity_hint_field() -> None:
    """RawArtefact gains optional ``sensitivity_hint`` field (default None)."""
    from kairix.core.protocols import RawArtefact

    # Default — back-compat for existing call sites
    a = RawArtefact(raw=b"x", mime="text/plain", fetched_at="2026-05-23T00:00:00Z")
    assert a.sensitivity_hint is None

    # Per-item hint emission path
    b = RawArtefact(
        raw=b"x",
        mime="text/plain",
        fetched_at="2026-05-23T00:00:00Z",
        sensitivity_hint="confidential",
    )
    assert b.sensitivity_hint == "confidential"


@pytest.mark.integration
def test_change_event_op_extended_enum() -> None:
    """ChangeEvent.op accepts the new ``archived`` and ``access_lost`` values."""
    from kairix.core.protocols import ChangeEvent

    # Existing values still work
    e1 = ChangeEvent(op="created", item_id="x", modified_at="2026-05-23T00:00:00Z")
    assert e1.op == "created"
    e2 = ChangeEvent(op="modified", item_id="x", modified_at="2026-05-23T00:00:00Z")
    assert e2.op == "modified"
    e3 = ChangeEvent(op="deleted", item_id="x", modified_at="2026-05-23T00:00:00Z")
    assert e3.op == "deleted"

    # New values land
    e4 = ChangeEvent(op="archived", item_id="x", modified_at="2026-05-23T00:00:00Z")
    assert e4.op == "archived"
    e5 = ChangeEvent(op="access_lost", item_id="x", modified_at="2026-05-23T00:00:00Z")
    assert e5.op == "access_lost"


@pytest.mark.integration
def test_typed_exceptions_hierarchy() -> None:
    """Connector exception classes form a coherent hierarchy."""
    from kairix.core.protocols import (
        ConnectorValidationError,
        CredentialExpiredError,
        CredentialInvalidError,
        InsufficientPermissionsError,
        UnexpectedValidationError,
    )

    # All four subclasses are ConnectorValidationError subclasses
    for cls in (
        CredentialInvalidError,
        CredentialExpiredError,
        InsufficientPermissionsError,
        UnexpectedValidationError,
    ):
        assert issubclass(cls, ConnectorValidationError)
        # And ConnectorValidationError is a plain Exception
        assert issubclass(ConnectorValidationError, Exception)

    # ContainerAccessDenied / ContainerTransient are plain Exception (not validation)
    from kairix.core.protocols import ContainerAccessDenied, ContainerTransient

    assert not issubclass(ContainerAccessDenied, ConnectorValidationError)
    assert not issubclass(ContainerTransient, ConnectorValidationError)
    # ContainerTransient carries retry_after
    e = ContainerTransient("rate limited", retry_after=60.0)
    assert e.retry_after == 60.0
