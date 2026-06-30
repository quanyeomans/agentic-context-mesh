"""SQLite-backed ``FactStore`` implementation: ``SQLiteFactStore``.

Production sibling of the SearchPipeline's chunk store, scoped to
canonical entity-attribute-value records (``FactRecord``). Backs the
Plan B-parity ingest pipeline (#mem0-vs-kairix-uplift): conversation
turns → LLM-extracted facts → this store → federated retrieval.

Schema (created lazily on first ``add``):

* ``facts``                     — canonical record table
* ``idx_facts_entity_attribute`` — fast conflict-detection lookup
* ``idx_facts_namespace``       — engagement-scoped filtering
* ``facts_fts``                 — FTS5 virtual table over
                                   (entity, attribute, value); BM25
                                   recall surface for ``search``.

Connection management mirrors ``kairix/core/db/__init__.py``'s pattern:
no long-lived connection; every method opens, executes, closes. WAL
mode is set on first connect so concurrent ingest + query is safe.

Recall is BM25-only until a vector index is wired (PLA-262, #340).
When the constructor is given BOTH an ``embedder: EmbeddingService``
AND a ``vector_index: VectorRepository``, ``search`` embeds the query,
recalls semantically-related facts from the vector index, and
RRF-fuses that list with the BM25 list — reusing the production search
pipeline's :func:`kairix.core.search.rrf.rrf` helper, never a fresh RRF
implementation. With either seam absent (the default), ``search`` runs
FTS-only exactly as before, so existing deployments are unaffected.

``vector_index_path`` names the on-disk fact vector index; the boundary
(the factory) builds a ``VectorRepository`` over it and injects it as
``vector_index=``. Populating that index + wiring it through the factory
is the PLA-262 follow-up; this module implements + tests the fused path
behind the injection seam so it activates the moment a vector index is
present.

F26 clean: imports only from ``kairix.core.protocols`` and the sibling
``kairix.core.search`` package — no ``kairix.providers`` /
``kairix.transport`` reach.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from kairix.core.facts.records import StoredFactRecord
from kairix.core.protocols import EmbeddingService, FactHit, FactRecord, VectorRepository

logger = logging.getLogger(__name__)


# Error-message prefix for ``KeyError`` raised by ``supersede`` when the
# referenced id does not exist. Extracted to a module-level constant
# (F17 — no string literal of ≥10 chars duplicated ≥3 times) so the
# three raise-sites in ``supersede`` reference one source of truth.
_ERROR_NO_FACT_WITH_ID = "SQLiteFactStore: no fact with id"

# Column name for the Stream A Lever A temporal anchor (F17).
_COL_EVIDENCE_AT = "evidence_at"

# Synthetic ``collection`` value stamped on the fusion rows handed to
# ``rrf()`` (PLA-262). Facts carry no collection of their own; the key is
# only present because the shared RRF row contract requires it.
_FUSION_COLLECTION = "facts"


# ---------------------------------------------------------------------------
# Schema DDL — single source of truth for the SQLite layout.
# ---------------------------------------------------------------------------

_SCHEMA_DDL = (
    """
    CREATE TABLE IF NOT EXISTS facts (
        id TEXT PRIMARY KEY,
        entity TEXT NOT NULL,
        attribute TEXT NOT NULL,
        value TEXT NOT NULL,
        confidence REAL NOT NULL,
        source_turn_ids TEXT NOT NULL,
        extracted_at TEXT NOT NULL,
        superseded_by TEXT,
        namespace TEXT NOT NULL,
        evidence_at TEXT,
        FOREIGN KEY(superseded_by) REFERENCES facts(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_facts_entity_attribute
        ON facts(entity, attribute)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_facts_namespace
        ON facts(namespace)
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
        entity, attribute, value,
        content='facts',
        content_rowid='rowid'
    )
    """,
)


# Lightweight forward-migration DDL — applied after the IF NOT EXISTS
# create above so a pre-existing facts table (created before the
# evidence_at column shipped) gets the new column without losing rows.
# ``ALTER TABLE ... ADD COLUMN`` is idempotent only via the OperationalError
# we catch in ``_ensure_schema``; SQLite has no native ``IF NOT EXISTS``
# clause for ``ADD COLUMN`` before 3.35 (we target older versions too).
_MIGRATIONS = ((_COL_EVIDENCE_AT, f"ALTER TABLE facts ADD COLUMN {_COL_EVIDENCE_AT} TEXT"),)


class StoredFactHit:
    """Concrete ``FactHit`` Protocol satisfier: ``record`` + ``score`` pair.

    Carried by ``SQLiteFactStore.search`` results. ``score`` is the
    BM25 rank-fusion score normalised into ``[0.0, 1.0]`` so callers
    can fuse across retrievers without re-scaling.
    """

    def __init__(self, *, record: FactRecord, score: float) -> None:
        self._record = record
        self._score = score

    @property
    def record(self) -> FactRecord:
        return self._record

    @property
    def score(self) -> float:
        return self._score


class SQLiteFactStore:
    """SQLite + FTS5 ``FactStore`` implementation.

    Satisfies ``kairix.core.protocols.FactStore`` at runtime. Schema is
    initialised lazily on first ``add`` to keep construction cheap (a
    factory wiring up the production pipeline does not pay the schema
    cost until ingest actually runs).

    Args:
        db_path: SQLite database file. Parent dirs are created on open.
        vector_index_path: On-disk location of the companion fact vector
            index (a usearch index over ``(entity, attribute, value)``).
            Documented for the boundary that builds the
            ``VectorRepository`` over it; ``search`` consumes the repo via
            the injected ``vector_index`` seam, not the path directly.
        embedder: Optional ``EmbeddingService``. Used to embed the query
            for the fused (vector + BM25) recall path. Absent → FTS-only.
        vector_index: Optional ``VectorRepository`` (the fact vector
            index). Injected by the factory boundary (built over
            ``vector_index_path``) or by tests (a ``FakeVectorRepository``).
            Its ``search`` returns rows carrying a fact ``id`` (or ``path``)
            in best-first rank order. Absent → FTS-only. The fused path
            fires only when BOTH ``embedder`` and ``vector_index`` are
            present — otherwise ``search`` runs today's BM25-only recall.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        vector_index_path: Path | None = None,
        embedder: EmbeddingService | None = None,
        vector_index: VectorRepository | None = None,
    ) -> None:
        self._db_path = db_path
        self._vector_index_path = vector_index_path
        self._embedder = embedder
        self._vector_index = vector_index
        self._schema_initialised = False

    # -- Connection management ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh SQLite connection with WAL + foreign-keys.

        Matches the pattern in ``kairix/core/db/__init__.py``. No
        long-lived connection — callers must close.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # F77-allow: per-collection facts DB (separate file from worker DB); short-lived connection per call.
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create the schema if it does not yet exist.

        Idempotent — every statement uses ``IF NOT EXISTS``.
        Schema is created on the first ``add`` call rather than at
        construction time so empty-store deployments stay zero-cost.
        """
        if self._schema_initialised:
            return
        for ddl in _SCHEMA_DDL:
            conn.execute(ddl)
        self._apply_column_migrations(conn)
        conn.commit()
        self._schema_initialised = True

    @staticmethod
    def _apply_column_migrations(conn: sqlite3.Connection) -> None:
        """Apply idempotent forward migrations to the ``facts`` table.

        Each migration in ``_MIGRATIONS`` is (column_name, ALTER-DDL).
        We probe ``PRAGMA table_info(facts)`` once and only execute
        ALTERs for columns that are missing — keeps the call idempotent
        across SQLite versions that pre-date ``IF NOT EXISTS`` on
        ``ADD COLUMN`` (3.35+).
        """
        cursor = conn.execute("PRAGMA table_info(facts)")
        # F63-bounded: PRAGMA table_info returns one row per column (schema-bounded).
        existing_columns = {row[1] for row in cursor.fetchall()}
        for column_name, ddl in _MIGRATIONS:
            if column_name not in existing_columns:
                conn.execute(ddl)

    # -- FactStore Protocol ---------------------------------------------------

    def add(self, fact: FactRecord) -> None:
        """Persist a fact. Idempotent on the deterministic ``id``.

        Uses ``INSERT OR IGNORE`` so re-adding a fact with the same id
        leaves the existing row untouched (matches the Protocol's
        contract for safe re-ingest).
        """
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO facts (
                    id, entity, attribute, value, confidence,
                    source_turn_ids, extracted_at, superseded_by, namespace,
                    evidence_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id,
                    fact.entity,
                    fact.attribute,
                    fact.value,
                    float(fact.confidence),
                    json.dumps(list(fact.source_turn_ids)),
                    fact.extracted_at,
                    fact.superseded_by,
                    fact.namespace,
                    # Tolerate FactRecord-shaped duck types that pre-date
                    # the ``evidence_at`` field — they expose neither the
                    # property nor a ``None`` default. ``getattr`` keeps
                    # ``FactStore.add`` callable from older test fakes.
                    getattr(fact, _COL_EVIDENCE_AT, None),
                ),
            )
            # Keep the FTS index in sync. ``INSERT OR IGNORE`` above
            # short-circuits on duplicate id; mirror that here by only
            # inserting into FTS when the parent row actually landed.
            if conn.total_changes > 0:
                conn.execute(
                    """
                    INSERT INTO facts_fts (rowid, entity, attribute, value)
                    SELECT rowid, entity, attribute, value FROM facts WHERE id = ?
                    """,
                    (fact.id,),
                )
            conn.commit()
        finally:
            conn.close()

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        namespace: str | None = None,
    ) -> list[FactHit]:
        """Recall live (non-superseded) facts matching ``query``.

        Honours ``namespace`` filtering for engagement-scoped recall and
        excludes records carrying a ``superseded_by`` link by default.
        Returns ``[]`` if the store is empty, the schema has not yet been
        initialised, or the query produces no matches.

        Two recall modes (PLA-262, #340):

        * **BM25-only** (default) — ``facts_fts MATCH ?`` with BM25
          ranking. Runs when no ``embedder`` / ``vector_index`` is wired,
          or when the vector leg surfaces nothing. This is the original
          behaviour, preserved byte-for-byte for vault-only deployments.
        * **Fused (vector + BM25)** — when BOTH an ``embedder`` and a
          ``vector_index`` are present, the query is embedded, the vector
          index recalls semantically-related facts (including facts with
          no lexical overlap with the query), and the two ranked lists are
          combined with Reciprocal Rank Fusion via the shared
          :func:`kairix.core.search.rrf.rrf` helper. This lifts recall on
          conversational queries that BM25 alone misses.
        """
        if not query or not query.strip():
            return []

        conn = self._connect()
        try:
            if not self._table_exists(conn, "facts_fts"):
                return []
            bm25_hits: list[FactHit] = [
                StoredFactHit(record=self._row_to_record(row), score=self._normalise_bm25(row["bm25"]))
                for row in self._fts_query(conn, query, top_k, namespace)
            ]
            vec_records = self._vector_recall(conn, query, top_k, namespace)
            if not vec_records:
                return list(bm25_hits)
            return self._rrf_fuse(bm25_hits, vec_records, top_k)
        finally:
            conn.close()

    # -- Fused recall (vector + BM25, RRF) ------------------------------------

    def _vector_recall(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        namespace: str | None,
    ) -> list[FactRecord]:
        """Embed ``query`` and recall facts from the vector index.

        Returns the live (non-superseded, namespace-filtered) facts the
        vector index ranks as nearest neighbours, in best-first order.
        Returns ``[]`` — degrading to BM25-only — whenever the fused path
        is not wired (no embedder / no vector index) or any best-effort
        step yields nothing. Never raises: a misbehaving embedder or
        index must not break recall.
        """
        # Both seams are required for the fused path; either absent → the
        # caller falls back to BM25-only. Kept as two independent guards
        # (not a combined ``or``) so each early-return is observable on its
        # own — the downstream ``try/except`` would otherwise mask a missing
        # seam behind a swallowed attribute error.
        if self._embedder is None:
            return []
        if self._vector_index is None:
            return []
        try:
            query_vec = self._embedder.embed(query)
        except Exception as exc:
            logger.warning("SQLiteFactStore: query embed failed — vector leg skipped: %s", exc)
            return []
        if not query_vec:
            return []
        try:
            rows = self._vector_index.search(query_vec, top_k)
        except Exception as exc:
            logger.warning("SQLiteFactStore: vector index search failed — vector leg skipped: %s", exc)
            return []
        fact_ids = [fid for fid in (self._vec_row_id(row) for row in rows) if fid]
        if not fact_ids:
            return []
        return self._facts_by_ids(conn, fact_ids, namespace)

    def _facts_by_ids(
        self,
        conn: sqlite3.Connection,
        fact_ids: list[str],
        namespace: str | None,
    ) -> list[FactRecord]:
        """Fetch live facts for ``fact_ids``, preserving the input order.

        Drops superseded rows and (when ``namespace`` is set) out-of-scope
        rows so the vector leg obeys the same visibility contract as the
        BM25 leg. Order mirrors the vector index ranking.
        """
        # Only the bound-parameter COUNT is interpolated (one ``?`` per id);
        # no user data reaches the SQL text — the established IN-clause
        # pattern used across the repo (see core/db/repository.py).
        placeholders = ", ".join("?" for _ in fact_ids)
        sql = f"SELECT * FROM facts WHERE id IN ({placeholders}) AND superseded_by IS NULL"
        params: list[object] = list(fact_ids)
        if namespace is not None:
            sql += " AND namespace = ?"
            params.append(namespace)
        # F63-bounded: ``fact_ids`` is the vector index result list, capped at ``top_k``.
        rows_by_id = {row["id"]: row for row in conn.execute(sql, params).fetchall()}
        return [self._row_to_record(rows_by_id[fid]) for fid in fact_ids if fid in rows_by_id]

    def _rrf_fuse(
        self,
        bm25_hits: list[FactHit],
        vec_records: list[FactRecord],
        top_k: int,
    ) -> list[FactHit]:
        """RRF-fuse the BM25 and vector fact lists into one ranked list.

        Reuses the production search pipeline's
        :func:`kairix.core.search.rrf.rrf` — facts are adapted into its
        ``BM25Result`` / ``VecResult`` row contract (the fact ``id`` plays
        the document ``file`` / ``path``), and the fused rows map back to
        ``StoredFactHit``. No RRF maths is re-implemented here. A fact
        present in only the vector list (no lexical overlap with the query)
        earns its score from the vector rank alone — that is how
        semantically-related facts BM25 misses reach the result set.
        """
        from kairix.core.search.bm25 import BM25Result
        from kairix.core.search.rrf import RRF_K, rrf
        from kairix.core.search.vec_index import VecResult

        records_by_id: dict[str, FactRecord] = {}
        bm25_rows: list[BM25Result] = []
        for hit in bm25_hits:
            record = hit.record
            records_by_id[record.id] = record
            bm25_rows.append(
                BM25Result(
                    file=record.id,
                    title=record.entity,
                    snippet=record.value,
                    score=float(hit.score),
                    collection=_FUSION_COLLECTION,
                    source_page=None,
                )
            )
        vec_rows: list[VecResult] = []
        for record in vec_records:
            records_by_id[record.id] = record
            vec_rows.append(
                VecResult(
                    hash_seq=record.id,
                    distance=0.0,
                    path=record.id,
                    collection=_FUSION_COLLECTION,
                    title=record.entity,
                    snippet=record.value,
                    source_page=None,
                )
            )

        hits: list[FactHit] = []
        for row in rrf(bm25_rows, vec_rows, k=RRF_K)[:top_k]:
            fused_record = records_by_id.get(row.path)
            if fused_record is not None:
                hits.append(StoredFactHit(record=fused_record, score=row.rrf_score))
        return hits

    @staticmethod
    def _vec_row_id(row: object) -> str | None:
        """Extract the fact id from a vector-index result row.

        Accepts the canonical ``id`` key first, then ``path`` (so a
        usearch-backed ``VecResult`` row, keyed by ``path``, also works).
        Returns ``None`` for a row carrying neither.
        """
        if not isinstance(row, dict):
            return None
        # Prefer the canonical ``id`` key; fall back to ``path`` when ``id``
        # is absent/empty (a usearch ``VecResult`` row is keyed by ``path``).
        raw = row.get("id")
        if not raw:
            raw = row.get("path")
        return str(raw) if raw else None

    def find_conflicts(
        self,
        *,
        entity: str,
        attribute: str,
        namespace: str | None = None,
    ) -> list[FactRecord]:
        """Live (non-superseded) facts matching ``(entity, attribute)``.

        Used by the consolidation pass: every new fact looks up
        existing facts about the same ``(entity, attribute)`` key, then
        the contradict use case decides which (if any) to supersede.
        """
        conn = self._connect()
        try:
            if not self._table_exists(conn, "facts"):
                return []
            sql = "SELECT * FROM facts WHERE entity = ? AND attribute = ? AND superseded_by IS NULL"
            params: list[object] = [entity, attribute]
            if namespace is not None:
                sql += " AND namespace = ?"
                params.append(namespace)
            # F63-bounded: per-entity+attribute lookup (typically returns 1 active fact + few superseded).
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_record(row) for row in rows]

    def supersede(self, *, old_id: str, new_id: str) -> None:
        """Mark ``old_id`` as superseded by ``new_id``.

        Raises ``KeyError`` if either id is absent. After the link
        is established the old fact no longer appears in default
        ``search`` results but stays retrievable for audit (future
        ``include_superseded=True`` kwarg).
        """
        conn = self._connect()
        try:
            if not self._table_exists(conn, "facts"):
                raise KeyError(f"{_ERROR_NO_FACT_WITH_ID} {old_id!r}")
            if not self._row_exists(conn, old_id):
                raise KeyError(f"{_ERROR_NO_FACT_WITH_ID} {old_id!r}")
            if not self._row_exists(conn, new_id):
                raise KeyError(f"{_ERROR_NO_FACT_WITH_ID} {new_id!r}")
            conn.execute(
                "UPDATE facts SET superseded_by = ? WHERE id = ?",
                (new_id, old_id),
            )
            conn.commit()
        finally:
            conn.close()

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_exists(conn: sqlite3.Connection, fact_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = ? LIMIT 1",
            (fact_id,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _fts_query(
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        namespace: str | None,
    ) -> list[sqlite3.Row]:
        """Run the BM25 query against ``facts_fts`` and join the live row.

        Query handling:

        - **FTS5 specials are stripped, not escaped.** Punctuation
          characters that FTS5 treats as syntax (``?``, ``"``, ``(``,
          ``)``, ``*``, ``:``, ``-``, ``+``, ``^``) cause
          ``sqlite3.OperationalError: fts5: syntax error`` when passed
          raw. Sanitisation turns them into spaces before tokenisation.
        - **Tokens OR-joined, not AND-joined.** Default FTS5 MATCH on a
          multi-word query treats it as an implicit AND — every token
          must appear in the indexed row. Natural-language benchmark
          questions ("When did Caroline go to the LGBTQ support group?")
          tokenise into 9+ tokens; no single fact row contains all of
          them. OR-joining lets BM25 rank by partial overlap, which is
          the behaviour the caller actually wants.

        BM25 score is returned via ``bm25(facts_fts)`` (lower is better);
        the caller normalises into ``[0.0, 1.0]``.
        """
        fts_match = SQLiteFactStore._build_fts_match_expr(query)
        if not fts_match:
            # Query collapsed to no usable tokens — return empty rather
            # than letting FTS5 raise on an empty/invalid MATCH.
            return []

        sql = (
            "SELECT facts.*, bm25(facts_fts) AS bm25 "
            "FROM facts_fts "
            "JOIN facts ON facts.rowid = facts_fts.rowid "
            "WHERE facts_fts MATCH ? "
            "AND facts.superseded_by IS NULL"
        )
        params: list[object] = [fts_match]
        if namespace is not None:
            sql += " AND facts.namespace = ?"
            params.append(namespace)
        sql += " ORDER BY bm25 ASC LIMIT ?"
        params.append(top_k)
        return list(conn.execute(sql, params).fetchall())

    @staticmethod
    def _build_fts_match_expr(query: str) -> str:
        """Convert a free-text query into an FTS5-safe OR-joined MATCH expression.

        Strips FTS5 syntax characters, lowercases, tokenises on whitespace,
        drops empty tokens and tokens that are pure FTS5 reserved words,
        then joins survivors with ``OR``. Returns ``""`` when nothing
        survives — caller treats empty as "no facts match".
        """
        # FTS5 special characters that must be removed (NOT escaped — escaping
        # produces phrase queries which we don't want here). Includes both
        # FTS5 syntax chars and natural-language punctuation (apostrophe,
        # comma, period, semicolon) that FTS5's tokeniser leaves attached
        # to tokens and trips up bare-token MATCH expressions.
        specials = "?\"'()*:-+^!~,.;/"
        cleaned = query
        for ch in specials:
            cleaned = cleaned.replace(ch, " ")

        # Tokenise + drop FTS5 reserved words (NOT/AND/OR uppercased would
        # produce operator parses; lowercased they're fine, but safer to drop).
        reserved = {"and", "or", "not", "near"}
        tokens = [tok for tok in cleaned.lower().split() if tok and tok not in reserved]
        if not tokens:
            return ""

        # OR-join so BM25 can rank by partial overlap. Each token wrapped in
        # double-quotes would make it a phrase query — we want bare tokens
        # so FTS5's tokeniser applies its own stemming + prefix rules.
        return " OR ".join(tokens)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> StoredFactRecord:
        """Translate one SQLite row into a ``StoredFactRecord``."""
        raw_turns = json.loads(row["source_turn_ids"])
        # ``evidence_at`` was added in Stream A Lever A; rows from a
        # pre-migration database may not expose the column key. Probe
        # the row.keys() tuple before indexing so legacy SQLite files
        # don't raise ``IndexError`` here.
        evidence_at: str | None
        if _COL_EVIDENCE_AT in row.keys():
            evidence_at = row[_COL_EVIDENCE_AT]
        else:
            evidence_at = None
        return StoredFactRecord(
            id=row["id"],
            entity=row["entity"],
            attribute=row["attribute"],
            value=row["value"],
            confidence=float(row["confidence"]),
            source_turn_ids=tuple(raw_turns),
            extracted_at=row["extracted_at"],
            superseded_by=row["superseded_by"],
            namespace=row["namespace"],
            evidence_at=evidence_at,
        )

    @staticmethod
    def _normalise_bm25(raw: float) -> float:
        """Map BM25 (lower-better, can be negative) into ``[0.0, 1.0]``.

        Mirrors the chunk-side scoring in ``SQLiteDocumentRepository``:
        ``|raw| / (1 + |raw|)`` so a strong match (large magnitude)
        approaches 1.0 and a weak match approaches 0.0.
        """
        magnitude = abs(float(raw))
        return magnitude / (1.0 + magnitude)
