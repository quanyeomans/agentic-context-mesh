"""
Kairix database schema creation, validation, and migration.

The schema includes
a ``kairix_meta`` table for schema versioning. Column names and types are
identical to ensure all existing queries work without modification.

Tables:
  - documents            — document registry (path, collection, hash, active flag,
                            connector provenance columns added in v2)
  - content              — document text keyed by content hash
  - content_vectors      — chunk metadata (hash, seq, pos, model, embedded_at, chunk_date)
  - documents_fts        — FTS5 full-text search index
  - documents_media      — per-source-media metadata (Wave 1 connector framework)
  - document_pages       — per-page extracted text + image descriptions
  - connector_cursors    — per-connector incremental sync cursors
  - connector_deadletter — per-item failure tracking with backoff
  - bronze_records       — bronze-tier raw blob registry (atomic with cursor advance)
  - entity_signals       — extracted entity / relationship signals queued for Neo4j
  - kairix_meta          — schema version tracking

Vector storage is handled by usearch (HNSW ANN index), not SQLite.
"""

import logging
import sqlite3

from . import EMBED_VECTOR_DIMS

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "4"

# F17 — table names appear in schema validation, migration, and conditional
# column-add logic; one constant per table keeps the SQL identifier in a single
# edit site.
_TABLE_CONTENT_VECTORS = "content_vectors"
_TABLE_DOCUMENTS_MEDIA = "documents_media"
_TABLE_BRONZE_RECORDS = "bronze_records"
# GH #334 — entity_signals constant referenced by the migration path
# (column-add + the integrity-required-tables list) to satisfy F17's
# "no string literal duplicated ≥3 times" cap.
_TABLE_ENTITY_SIGNALS = "entity_signals"
# GH #373 — topology_scope_entries referenced by the integrity-required-tables
# list + the default_in_scope ALTER TABLE migration (2 sites in migrate). One
# constant per table keeps the SQL identifier in a single edit site.
_TABLE_TOPOLOGY_SCOPE_ENTRIES = "topology_scope_entries"


def create_schema(db: sqlite3.Connection, *, dims: int = EMBED_VECTOR_DIMS) -> None:
    """
    Create all kairix tables if they do not exist.

    Idempotent — safe to call on every startup. Uses IF NOT EXISTS for all
    DDL statements.

    Args:
        db:   Open sqlite3.Connection.
        dims: Vector embedding dimensions (for metadata only — vectors stored in usearch).
    """
    # Tables only — indexes that depend on migrated columns (agent_owner,
    # chunk_date) come after migrate() runs, otherwise legacy DBs that don't
    # yet have those columns fail with "no such column" on CREATE INDEX.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            created_at TEXT,
            modified_at TEXT,
            active INTEGER DEFAULT 1,
            agent_owner TEXT,
            source_name TEXT,
            source_uri TEXT,
            source_modified_at TEXT,
            source_page INTEGER,
            sensitivity TEXT NOT NULL DEFAULT 'public',
            -- GH #409: indexed exact-match column for the search enrich phase.
            -- Derived from ``path`` so writers never set it explicitly.
            -- VIRTUAL (not STORED) because SQLite forbids STORED generated
            -- columns in ALTER TABLE on legacy DBs; VIRTUAL columns ARE
            -- indexable (the index materialises the value) so the planner
            -- still gets O(log N) lookup via ``idx_documents_path_canonical``.
            path_canonical TEXT GENERATED ALWAYS AS (path) VIRTUAL,
            UNIQUE(collection, path)
        );

        CREATE TABLE IF NOT EXISTS content (
            hash TEXT PRIMARY KEY,
            doc TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS content_vectors (
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL,
            pos INTEGER NOT NULL,
            model TEXT,
            embedded_at TEXT,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        );

        CREATE TABLE IF NOT EXISTS documents_media (
            hash TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            format TEXT NOT NULL,
            size_bytes INTEGER,
            page_count INTEGER,
            title TEXT,
            author TEXT,
            created_date TEXT,
            language TEXT,
            extraction_status TEXT DEFAULT 'pending',
            extraction_timestamp INTEGER,
            extractor_name TEXT,
            extractor_version TEXT
        );

        CREATE TABLE IF NOT EXISTS document_pages (
            hash TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            extracted_text TEXT,
            has_images INTEGER DEFAULT 0,
            image_descriptions TEXT,
            PRIMARY KEY (hash, page_number),
            FOREIGN KEY (hash) REFERENCES documents_media(hash)
        );

        CREATE TABLE IF NOT EXISTS connector_cursors (
            source_name TEXT PRIMARY KEY,
            cursor_token TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS connector_deadletter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            failure_count INTEGER NOT NULL,
            last_error TEXT,
            last_attempt TEXT NOT NULL,
            UNIQUE(source_name, item_id)
        );

        CREATE TABLE IF NOT EXISTS bronze_records (
            source_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            raw_path TEXT NOT NULL,
            mime TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            content_hash TEXT,
            PRIMARY KEY (source_name, item_id)
        );

        CREATE TABLE IF NOT EXISTS entity_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            confidence REAL NOT NULL,
            sensitivity TEXT NOT NULL,
            pushed_to_neo4j INTEGER DEFAULT 0,
            pushed_at TEXT,
            last_push_error TEXT,
            push_attempt_count INTEGER DEFAULT 0
        );

        -- table-is-derived: schema bookkeeping; writes owned by this module
        CREATE TABLE IF NOT EXISTS kairix_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS content_vectors_pruned (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL,
            pos INTEGER NOT NULL,
            model TEXT,
            embedded_at TEXT,
            chunk_date TEXT,
            pruned_at TEXT NOT NULL,
            UNIQUE(hash, seq)
        );

        -- ADR-025 §8: append-only per-item per-stage status timeline.
        -- Updates are forbidden (see kairix/core/observability/status_emit.py).
        -- Maintenance retention prune writes a PRUNED_RETENTION row before DELETE.
        CREATE TABLE IF NOT EXISTS pipeline_item_status (
            source_name      TEXT NOT NULL,
            item_id          TEXT NOT NULL,
            stage            TEXT NOT NULL,
            status_code      TEXT NOT NULL,
            severity         TEXT NOT NULL CHECK (severity IN ('ok','warn','error')),
            detail_json      TEXT,
            occurred_at      TEXT NOT NULL,
            chunker_version  TEXT,
            extractor_version TEXT,
            PRIMARY KEY (source_name, item_id, stage, occurred_at)
        );

        -- ADR-029 G.1: agent-facing query queue + carry-along delivery.
        -- INSERT site: kairix/core/queue/dispatch.py (dispatch_or_queue).
        -- UPDATE site: kairix/core/queue/carry_along.py (mark 'delivered').
        CREATE TABLE IF NOT EXISTS pending_queries (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            args_json TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            delivered_at TEXT,
            result_json TEXT,
            error_message TEXT,
            UNIQUE(agent_id, args_hash, submitted_at)
        );

        -- Issue #398 (Workstream D): per-MCP-tool-call observability log.
        -- INSERT site: kairix/agents/mcp/errors.py (_record_mcp_call, fire-and-forget
        -- from async_tool_handler's finally block; failures swallowed so observability
        -- never breaks a tool call). Query site: kairix/quality/probe/mcp_calls_cli.py
        -- (kairix probe mcp-calls). No UPDATE/DELETE in production — the table is
        -- append-only; operators retention-prune via DELETE WHERE timestamp < ...
        -- (see docs/operations/runbooks/how-to-read-mcp-call-log.md).
        CREATE TABLE IF NOT EXISTS mcp_call_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            tool            TEXT NOT NULL,
            agent           TEXT,
            latency_ms      INTEGER NOT NULL,
            success         INTEGER NOT NULL,
            error_class     TEXT,
            payload_hash    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mcp_call_log_tool_time
            ON mcp_call_log(tool, timestamp);
        CREATE INDEX IF NOT EXISTS idx_mcp_call_log_time
            ON mcp_call_log(timestamp);

        CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(hash);
        CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
        CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active);
        CREATE INDEX IF NOT EXISTS idx_content_vectors_pruned_at ON content_vectors_pruned(pruned_at);
        CREATE INDEX IF NOT EXISTS idx_pipeline_status_lookup
            ON pipeline_item_status (source_name, item_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pipeline_status_by_code
            ON pipeline_item_status (status_code, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pending_queries_agent_pending
            ON pending_queries (agent_id, status)
            WHERE status IN ('completed', 'failed');
    """)

    # Run migrations to bring legacy schemas up to current (adds agent_owner,
    # chunk_date columns to existing tables). On a fresh DB this is a no-op
    # because the CREATE TABLE above already declared the columns.
    migrate(db)

    # Indexes that depend on migrated columns — must come after migrate() so
    # the columns exist on legacy DBs.
    db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_documents_agent_owner ON documents(agent_owner);
        CREATE INDEX IF NOT EXISTS idx_content_vectors_chunk_date ON content_vectors(chunk_date);
        CREATE INDEX IF NOT EXISTS idx_documents_source_uri ON documents(source_uri);
        -- GH #409: indexed exact-match lookup for the search enrich phase.
        -- Replaces ``WHERE path LIKE '%suffix'`` (full table scan on 1.1M
        -- rows, 14s p50 in production) with ``WHERE path_canonical IN (?)``
        -- (O(log N) index probe). On a legacy DB the migrate() call above
        -- has already added the ``path_canonical`` virtual generated column
        -- before this CREATE INDEX runs.
        CREATE INDEX IF NOT EXISTS idx_documents_path_canonical ON documents(path_canonical);
    """)

    # FTS5 — external content mode is not needed; we populate directly.
    # Check if it already exists before creating (FTS5 doesn't support IF NOT EXISTS).
    fts_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    if not fts_exists:
        db.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(filepath, title, doc, tokenize='porter unicode61')")

    # Schema version — REPLACE so the row tracks the current code's version
    # after an in-place migration of a legacy DB.
    db.execute(
        "INSERT OR REPLACE INTO kairix_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    db.execute(
        "INSERT OR IGNORE INTO kairix_meta (key, value) VALUES ('created_by', 'kairix')",
    )

    db.commit()
    logger.info(
        "db.schema: kairix schema initialised (version=%s, dims=%d)",
        SCHEMA_VERSION,
        dims,
    )


def validate_schema(db: sqlite3.Connection) -> list[str]:
    """
    Validate the database schema against expectations.

    Returns a list of error strings. Empty list means schema is valid.
    """
    errors: list[str] = []

    # Check required tables
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    required_tables = (
        "documents",
        "content",
        _TABLE_CONTENT_VECTORS,
        # KFEAT-021 Phase 1 — soft-delete staging table for orphan content_vectors.
        "content_vectors_pruned",
        # Connector-framework Wave 1 (SC-4)
        _TABLE_DOCUMENTS_MEDIA,
        "document_pages",
        "connector_cursors",
        "connector_deadletter",
        _TABLE_BRONZE_RECORDS,
        _TABLE_ENTITY_SIGNALS,
        # Topology v2 Wave A — 12 net-new tables. Existence is unconditional;
        # population is gated by the `topology_v2_schema` feature flag.
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
        _TABLE_TOPOLOGY_SCOPE_ENTRIES,
        "topology_skills",
        # ADR-029 G.1 — agent-facing query queue
        "pending_queries",
        # Issue #398 (Workstream D) — per-MCP-tool-call observability log
        "mcp_call_log",
    )
    for required in required_tables:
        if required not in tables:
            errors.append(f"missing table: {required}")

    if errors:
        return errors  # Can't check columns if tables are missing

    # Check critical columns
    expected_cols = {
        "documents": {"id", "collection", "path", "hash", "active", "sensitivity"},
        "content": {"hash", "doc"},
        _TABLE_CONTENT_VECTORS: {"hash", "seq", "pos"},
    }
    for table, expected in expected_cols.items():
        # safe: table name from expected_cols keys (hardcoded)
        actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        missing = expected - actual
        if missing:
            errors.append(f"{table} missing columns: {missing}")

    return errors


def _add_column_if_missing(
    db: sqlite3.Connection,
    table: str,
    column: str,
    column_def: str,
) -> bool:
    """
    Add ``column`` to ``table`` with ``column_def`` (e.g. "TEXT" or
    "TEXT NOT NULL DEFAULT 'public'") if not already present.

    Returns True if the column was added, False if it already existed.

    ``table`` and ``column`` are caller-supplied identifiers (not user input)
    so the f-string interpolation is safe — sqlite3 has no parameter binding
    for DDL identifiers.
    """
    # safe: table/column come from hardcoded callers in this module
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
    db.commit()
    logger.info("db.schema: migration — added %s column to %s", column, table)
    return True


# Connector-framework Wave 1 columns added to the existing documents table.
# Each tuple is (column_name, column_def). column_def is appended verbatim to
# `ALTER TABLE documents ADD COLUMN <name> <def>`.
_DOCUMENTS_CONNECTOR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_name", "TEXT"),
    ("source_uri", "TEXT"),
    ("source_modified_at", "TEXT"),
    ("source_page", "INTEGER"),
    ("sensitivity", "TEXT NOT NULL DEFAULT 'public'"),
)

# Connector-framework Wave 1 tables — created idempotently on legacy DBs.
# Each entry is the full `CREATE TABLE IF NOT EXISTS …` statement.
_CONNECTOR_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS documents_media (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER,
    page_count INTEGER,
    title TEXT,
    author TEXT,
    created_date TEXT,
    language TEXT,
    extraction_status TEXT DEFAULT 'pending',
    extraction_timestamp INTEGER,
    extractor_name TEXT,
    extractor_version TEXT
);

CREATE TABLE IF NOT EXISTS document_pages (
    hash TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    extracted_text TEXT,
    has_images INTEGER DEFAULT 0,
    image_descriptions TEXT,
    PRIMARY KEY (hash, page_number),
    FOREIGN KEY (hash) REFERENCES documents_media(hash)
);

CREATE TABLE IF NOT EXISTS connector_cursors (
    source_name TEXT PRIMARY KEY,
    cursor_token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_deadletter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    failure_count INTEGER NOT NULL,
    last_error TEXT,
    last_attempt TEXT NOT NULL,
    UNIQUE(source_name, item_id)
);

CREATE TABLE IF NOT EXISTS bronze_records (
    source_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    mime TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content_hash TEXT,
    PRIMARY KEY (source_name, item_id)
);

CREATE TABLE IF NOT EXISTS entity_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    pushed_to_neo4j INTEGER DEFAULT 0,
    pushed_at TEXT,
    last_push_error TEXT,
    push_attempt_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS content_vectors_pruned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    seq INTEGER NOT NULL,
    pos INTEGER NOT NULL,
    model TEXT,
    embedded_at TEXT,
    chunk_date TEXT,
    pruned_at TEXT NOT NULL,
    UNIQUE(hash, seq)
);

CREATE INDEX IF NOT EXISTS idx_content_vectors_pruned_at ON content_vectors_pruned(pruned_at);

-- ADR-029 G.1: agent-facing query queue. See kairix/core/queue/.
-- INSERT site: dispatch.py; UPDATE site: carry_along.py (status->'delivered').
CREATE TABLE IF NOT EXISTS pending_queries (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    delivered_at TEXT,
    result_json TEXT,
    error_message TEXT,
    UNIQUE(agent_id, args_hash, submitted_at)
);

CREATE INDEX IF NOT EXISTS idx_pending_queries_agent_pending
    ON pending_queries (agent_id, status)
    WHERE status IN ('completed', 'failed');

-- Issue #398 (Workstream D) — per-MCP-tool-call observability log.
-- INSERT site: kairix/agents/mcp/errors.py (_record_mcp_call from
-- async_tool_handler's finally block, fire-and-forget). Query site:
-- kairix/quality/probe/mcp_calls_cli.py (kairix probe mcp-calls).
-- Append-only; retention-prune via DELETE WHERE timestamp < ...
-- (see docs/operations/runbooks/how-to-read-mcp-call-log.md).
CREATE TABLE IF NOT EXISTS mcp_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    tool            TEXT NOT NULL,
    agent           TEXT,
    latency_ms      INTEGER NOT NULL,
    success         INTEGER NOT NULL,
    error_class     TEXT,
    payload_hash    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mcp_call_log_tool_time
    ON mcp_call_log(tool, timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_call_log_time
    ON mcp_call_log(timestamp);
"""


# Topology v2 (Wave A) — 12 new tables for the connector/collection/scope
# topology evolution. Tables exist unconditionally (CREATE IF NOT EXISTS);
# the `topology_v2_schema` feature flag controls whether they get populated.
# See docs/architecture/connector-scope-topology/ADR.md.
_TOPOLOGY_V2_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS topology_connectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    connector_specific_config TEXT NOT NULL,
    refresh_freq_seconds INTEGER,
    prune_freq_seconds INTEGER,
    perm_sync_freq_seconds INTEGER,
    default_sensitivity TEXT NOT NULL DEFAULT 'internal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    credential_ref TEXT NOT NULL,
    user_id TEXT,
    admin_public INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_cc_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id INTEGER NOT NULL,
    credential_id INTEGER,
    name TEXT NOT NULL UNIQUE,
    access_type TEXT NOT NULL DEFAULT 'PRIVATE',
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    last_successful_index_time TEXT,
    last_time_perm_sync TEXT,
    last_time_external_group_sync TEXT,
    last_time_hierarchy_fetch TEXT,
    in_repeated_error_state INTEGER NOT NULL DEFAULT 0,
    total_docs_indexed INTEGER NOT NULL DEFAULT 0,
    refresh_freq_override_seconds INTEGER,
    prune_freq_override_seconds INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (connector_id) REFERENCES topology_connectors(id),
    FOREIGN KEY (credential_id) REFERENCES topology_credentials(id)
);

CREATE TABLE IF NOT EXISTS topology_containers (
    cc_pair_id INTEGER NOT NULL,
    container_id TEXT NOT NULL,
    access_state TEXT NOT NULL DEFAULT 'ACCESSIBLE',
    cursor_token TEXT,
    last_synced_at TEXT,
    PRIMARY KEY (cc_pair_id, container_id),
    FOREIGN KEY (cc_pair_id) REFERENCES topology_cc_pairs(id)
);

CREATE TABLE IF NOT EXISTS topology_hierarchy_nodes (
    cc_pair_id INTEGER NOT NULL,
    raw_node_id TEXT NOT NULL,
    raw_parent_id TEXT,
    display_name TEXT NOT NULL,
    link TEXT,
    node_type TEXT NOT NULL,
    external_access_json TEXT,
    sensitivity_hint TEXT,
    PRIMARY KEY (cc_pair_id, raw_node_id),
    FOREIGN KEY (cc_pair_id) REFERENCES topology_cc_pairs(id)
);

CREATE TABLE IF NOT EXISTS topology_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    default_sensitivity TEXT NOT NULL DEFAULT 'internal',
    on_unmapped_item TEXT NOT NULL DEFAULT 'land_in_default_collection',
    visibility TEXT NOT NULL DEFAULT 'engagement',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_collection_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    cc_pair_id INTEGER NOT NULL,
    source_path_filter TEXT NOT NULL,
    sensitivity_override TEXT,
    FOREIGN KEY (collection_id) REFERENCES topology_collections(id),
    FOREIGN KEY (cc_pair_id) REFERENCES topology_cc_pairs(id)
);

CREATE TABLE IF NOT EXISTS topology_federated_connectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    query_strategy TEXT NOT NULL,
    FOREIGN KEY (collection_id) REFERENCES topology_collections(id)
);

CREATE TABLE IF NOT EXISTS topology_group_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    can_read INTEGER NOT NULL DEFAULT 1,
    can_write INTEGER NOT NULL DEFAULT 0,
    max_sensitivity TEXT NOT NULL DEFAULT 'internal',
    UNIQUE(collection_id, group_id),
    FOREIGN KEY (collection_id) REFERENCES topology_collections(id)
);

CREATE TABLE IF NOT EXISTS topology_scope_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL UNIQUE,
    actor_kind TEXT NOT NULL,
    inherits_from_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_scope_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_profile_id INTEGER NOT NULL,
    collection_name TEXT NOT NULL,
    can_read INTEGER NOT NULL DEFAULT 1,
    can_write INTEGER NOT NULL DEFAULT 0,
    max_sensitivity TEXT NOT NULL DEFAULT 'internal',
    -- GH #373: per-entry "is this collection in the default-search superset"
    -- flag. NOT NULL DEFAULT 1 = back-compat (pre-#373 rows surface in
    -- default search). Operators flip individual entries to 0 to mark them
    -- opt-in (e.g. reflib) — only reachable via explicit `collections=[...]`.
    default_in_scope INTEGER NOT NULL DEFAULT 1,
    UNIQUE(scope_profile_id, collection_name),
    FOREIGN KEY (scope_profile_id) REFERENCES topology_scope_profiles(id)
);

CREATE TABLE IF NOT EXISTS topology_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    ranking TEXT NOT NULL DEFAULT 'fuse_then_rerank',
    iteration TEXT NOT NULL DEFAULT 'one_shot',
    task_collections_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Topology v2 — additional columns on existing tables.
_DOCUMENTS_TOPOLOGY_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("archived", "INTEGER NOT NULL DEFAULT 0"),
    ("access_lost", "INTEGER NOT NULL DEFAULT 0"),
    ("chunker_version", "TEXT"),
)

_DOCUMENTS_MEDIA_TOPOLOGY_V2_COLUMNS: tuple[tuple[str, str], ...] = (("chunker_version", "TEXT"),)

# GH #373 — additional columns on existing topology_v2 tables. Each tuple is
# (column_name, column_def). The default of 1 (in-scope) is the back-compat
# invariant — pre-#373 rows continue to surface in default search after the
# ALTER TABLE migration runs, so the cutover does not silently drop every
# collection from default search.
_TOPOLOGY_SCOPE_ENTRIES_TOPOLOGY_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("default_in_scope", "INTEGER NOT NULL DEFAULT 1"),
)


def _migrate_documents_connector_columns(db: sqlite3.Connection, tables: set[str]) -> None:
    """Add connector-framework Wave 1 columns to legacy documents tables."""
    if "documents" not in tables:
        return
    for column, column_def in _DOCUMENTS_CONNECTOR_COLUMNS:
        _add_column_if_missing(db, "documents", column, column_def)


def _migrate_documents_path_canonical(db: sqlite3.Connection, tables: set[str]) -> None:
    """GH #409 — add ``path_canonical`` virtual generated column.

    On legacy DBs the column is added via ALTER TABLE with a VIRTUAL
    generated expression (``GENERATED ALWAYS AS (path) VIRTUAL``). SQLite
    forbids STORED generated columns in ALTER TABLE but permits VIRTUAL
    ones; VIRTUAL columns are still indexable, which is what makes the
    enrich-phase query rewrite (``WHERE path_canonical IN (?)``) usable
    via an index probe instead of a full table scan.

    Idempotent — uses ``PRAGMA table_xinfo`` (rather than ``table_info``)
    because the latter omits generated columns, which would cause a
    re-run on a fresh DB to attempt a duplicate ALTER TABLE.

    On a 1.1M-row production DB the bare ALTER TABLE is metadata-only
    (no row copy); the subsequent ``CREATE INDEX`` materialises one
    index entry per row (~1.1M rows in single-digit minutes on the
    target VM). The index is created separately in :func:`create_schema`
    after this migration runs so it sees the new column.
    """
    if "documents" not in tables:
        return
    # safe: hardcoded table name; table_xinfo is the variant of table_info
    # that includes generated columns (table_info omits them, which would
    # make this migration not idempotent on a fresh DB).
    existing = {row[1] for row in db.execute("PRAGMA table_xinfo(documents)")}
    if "path_canonical" in existing:
        return
    db.execute("ALTER TABLE documents ADD COLUMN path_canonical TEXT GENERATED ALWAYS AS (path) VIRTUAL")
    db.commit()
    logger.info("db.schema: migration — added path_canonical (virtual generated) column to documents")


def _migrate_topology_v2_columns(db: sqlite3.Connection, tables: set[str]) -> None:
    """Add topology v2 (Wave A) columns to existing tables.

    Pure-additive — `archived` + `access_lost` default to 0 (false),
    `chunker_version` defaults to NULL. No behavioural change until
    `topology_v2_schema` flag flips and write paths start populating.

    GH #373 — also adds `default_in_scope INTEGER NOT NULL DEFAULT 1` to
    `topology_scope_entries`. Existing rows get `default_in_scope=1` so
    they continue to surface in default search after the migration runs.
    """
    if "documents" in tables:
        for column, column_def in _DOCUMENTS_TOPOLOGY_V2_COLUMNS:
            _add_column_if_missing(db, "documents", column, column_def)
    if _TABLE_DOCUMENTS_MEDIA in tables:
        for column, column_def in _DOCUMENTS_MEDIA_TOPOLOGY_V2_COLUMNS:
            _add_column_if_missing(db, _TABLE_DOCUMENTS_MEDIA, column, column_def)
    if _TABLE_TOPOLOGY_SCOPE_ENTRIES in tables:
        for column, column_def in _TOPOLOGY_SCOPE_ENTRIES_TOPOLOGY_V2_COLUMNS:
            _add_column_if_missing(db, _TABLE_TOPOLOGY_SCOPE_ENTRIES, column, column_def)


def migrate(db: sqlite3.Connection) -> None:
    """
    Run all pending migrations. Idempotent — safe to call on every startup.

    Currently handles:
      - Creating kairix_meta table (if missing)
      - Adding chunk_date column to content_vectors (if missing)
      - Adding agent_owner column to documents (if missing, #114)
      - Adding connector-framework Wave 1 columns to documents (SC-4):
        source_name, source_uri, source_modified_at, source_page, sensitivity
      - Creating connector-framework Wave 1 tables (SC-4):
        documents_media, document_pages, connector_cursors,
        connector_deadletter, bronze_records, entity_signals
    """
    # Ensure kairix_meta exists
    db.execute("""
        -- table-is-derived: schema bookkeeping; writes owned by this module
        CREATE TABLE IF NOT EXISTS kairix_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # chunk_date migration
    if _TABLE_CONTENT_VECTORS in tables:
        _add_column_if_missing(db, _TABLE_CONTENT_VECTORS, "chunk_date", "TEXT")

    # agent_owner migration — per-document agent provenance for #114.
    # Existing rows get NULL (treated as shared / not agent-owned) until a
    # `kairix embed --backfill-agent-owner` pass re-applies the path → agent
    # mapping from the configured AgentRegistry.
    if "documents" in tables:
        _add_column_if_missing(db, "documents", "agent_owner", "TEXT")

    # Connector-framework Wave 1 (SC-4): new columns + new tables.
    _migrate_documents_connector_columns(db, tables)
    db.executescript(_CONNECTOR_TABLES_DDL)

    # GH #409 — path_canonical virtual column for indexed exact-match
    # enrich. Must run before the idx_documents_path_canonical CREATE
    # INDEX in create_schema(), which is the case because create_schema
    # invokes migrate() first.
    _migrate_documents_path_canonical(db, tables)

    # Streaming-bronze Phase 2: bronze_records.content_hash. SHA-256 of
    # raw bytes computed at write time on both BronzeStore impls; used
    # by Phase 3+ for re-fetch verification + dedupe detection.
    # Existing rows get NULL until they're re-written.
    if _TABLE_BRONZE_RECORDS in tables:
        _add_column_if_missing(db, _TABLE_BRONZE_RECORDS, "content_hash", "TEXT")

    # GH #334 — Neo4j entity-graph drain. Legacy entity_signals tables
    # gain two columns that the drain tick writes: ``last_push_error``
    # (per-row failure message) + ``push_attempt_count`` (bounded retry
    # counter; the drain stops re-trying past 3 attempts). Both default
    # to NULL / 0 so unpushed rows on a legacy DB look "never attempted"
    # to the drain on first encounter — which is the correct semantic.
    if _TABLE_ENTITY_SIGNALS in tables:
        _add_column_if_missing(db, _TABLE_ENTITY_SIGNALS, "last_push_error", "TEXT")
        _add_column_if_missing(db, _TABLE_ENTITY_SIGNALS, "push_attempt_count", "INTEGER DEFAULT 0")

    # Topology v2 (Wave A): additional tables + columns. Pure-additive;
    # write paths are gated by the `topology_v2_schema` feature flag.
    _migrate_topology_v2_columns(db, tables)
    db.executescript(_TOPOLOGY_V2_TABLES_DDL)
    db.commit()

    # Ensure indexes exist (idempotent) — only if the tables exist
    if "documents" in tables:
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(hash);
            CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
            CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active);
            CREATE INDEX IF NOT EXISTS idx_documents_agent_owner ON documents(agent_owner);
            CREATE INDEX IF NOT EXISTS idx_documents_source_uri ON documents(source_uri);
            -- GH #409 — exact-match enrich-phase index (see _migrate_documents_path_canonical).
            CREATE INDEX IF NOT EXISTS idx_documents_path_canonical ON documents(path_canonical);
        """)
    if _TABLE_CONTENT_VECTORS in tables:
        db.execute("CREATE INDEX IF NOT EXISTS idx_content_vectors_chunk_date ON content_vectors(chunk_date)")
