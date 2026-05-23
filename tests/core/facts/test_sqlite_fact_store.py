"""Unit tests for :class:`kairix.core.facts.SQLiteFactStore`.

Each test touches a real on-disk SQLite database in a ``tmp_path``-scoped
file and drives behaviour through the public ``kairix.core.facts``
surface only. No internal imports (F5). No monkeypatching (F1). No
test-only kwargs in production (F6).

Marker rationale (``unit``): these tests exercise ONE component (the
SQLite fact-store) against stdlib SQLite via ``tmp_path``. There is no
external service, no usearch index, and no cross-component wiring —
the kairix taxonomy reserves ``integration`` for "multi-component with
real database and usearch index". A single component against stdlib
SQLite is unit-shaped (the SQLite file is a deterministic local fixture,
analogous to a Fake). Sibling tests in ``tests/core/facts/``
(``test_consolidation``, ``test_llm_fact_extractor``) follow the same
``unit`` convention. Re-marking is what brings these sabotage-proven
behaviours into the F7 per-file coverage floor's view.

Every test below was sabotage-proven during authoring: a concrete
mutation in production was identified, the test was confirmed to fail
under that mutation, and production was restored. The proof transcript
is in the commit body.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.facts import SQLiteFactStore, StoredFactHit, StoredFactRecord
from kairix.core.protocols import FactHit, FactRecord, FactStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — keep individual tests focused on the behaviour they probe.
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> SQLiteFactStore:
    """Construct a fresh store backed by ``tmp_path/facts.sqlite``."""
    return SQLiteFactStore(db_path=tmp_path / "facts.sqlite")


def _make_record(
    *,
    fact_id: str = "f1",
    entity: str = "agent-alpha",
    attribute: str = "status",
    value: str = "single",
    source_turn_ids: tuple[str, ...] = ("t1",),
    namespace: str = "shared",
    superseded_by: str | None = None,
) -> StoredFactRecord:
    """Construct a record with sensible defaults."""
    return StoredFactRecord(
        id=fact_id,
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=0.9,
        source_turn_ids=source_turn_ids,
        extracted_at="2026-01-01T00:00:00Z",
        superseded_by=superseded_by,
        namespace=namespace,
    )


# ---------------------------------------------------------------------------
# Protocol compliance — isinstance probes through the public surface
# ---------------------------------------------------------------------------


def test_store_satisfies_fact_store_protocol(tmp_path: Path) -> None:
    """``SQLiteFactStore`` instances satisfy the runtime-checkable
    ``FactStore`` Protocol.

    Sabotage-proof: rename ``SQLiteFactStore.supersede`` and the
    runtime ``isinstance`` check loses a Protocol method → assertion
    fails.
    """
    store = _make_store(tmp_path)
    assert isinstance(store, FactStore)


def test_record_satisfies_fact_record_protocol_after_round_trip(
    tmp_path: Path,
) -> None:
    """Records returned by ``find_conflicts`` satisfy ``FactRecord``.

    Sabotage-proof: drop the ``namespace`` field from
    ``StoredFactRecord`` and ``isinstance(..., FactRecord)`` fails.
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f1"))
    conflicts = store.find_conflicts(entity="agent-alpha", attribute="status")
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], FactRecord)


def test_hit_satisfies_fact_hit_protocol(tmp_path: Path) -> None:
    """Search hits expose the ``FactHit`` Protocol (``record`` + ``score``).

    Sabotage-proof: drop ``score`` from ``StoredFactHit`` and the
    runtime probe fails.
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f1", value="distinctword"))
    hits = store.search("distinctword")
    assert hits, "expected at least one hit for a word that is in the value"
    hit = hits[0]
    assert isinstance(hit, FactHit)
    assert isinstance(hit, StoredFactHit)
    assert hit.record.id == "f1"
    assert hit.score >= 0.0


# ---------------------------------------------------------------------------
# add → search round-trip
# ---------------------------------------------------------------------------


def test_add_then_search_recovers_the_added_fact(tmp_path: Path) -> None:
    """A just-added fact is recovered by an FTS query on its value.

    Sabotage-proof: change ``SQLiteFactStore.add`` to skip the FTS
    insert (delete the second INSERT block) and ``search`` returns
    nothing → the ``matched`` assertion fails.
    """
    store = _make_store(tmp_path)
    target = _make_record(fact_id="f-target", value="bowling")
    distractor = _make_record(
        fact_id="f-distractor",
        entity="John",
        attribute="hobby",
        value="chess",
    )
    store.add(target)
    store.add(distractor)

    hits = store.search("bowling", top_k=5)
    matched = [h for h in hits if h.record.id == "f-target"]
    assert matched, f"target id missing from results; got {[h.record.id for h in hits]}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_add_is_idempotent_on_id(tmp_path: Path) -> None:
    """Adding the same fact twice produces exactly one row.

    Sabotage-proof: drop the ``OR IGNORE`` clause from the production
    ``INSERT`` and the second ``add`` raises
    ``sqlite3.IntegrityError`` on the primary-key duplicate → test
    fails.
    """
    store = _make_store(tmp_path)
    fact = _make_record(fact_id="f-same", value="uniqword")
    store.add(fact)
    store.add(fact)

    conn = sqlite3.connect(str(tmp_path / "facts.sqlite"))
    try:
        (row_count,) = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        (fts_count,) = conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()
    finally:
        conn.close()
    assert row_count == 1, f"expected 1 row in facts; got {row_count}"
    assert fts_count == 1, f"expected 1 row in facts_fts; got {fts_count}"


# ---------------------------------------------------------------------------
# Deterministic-id minting
# ---------------------------------------------------------------------------


def test_mint_id_is_deterministic_across_invocations() -> None:
    """The same triple produces the same id every call.

    Sabotage-proof: replace ``hashlib.sha256`` with a random-prefixed
    digest and the two ids diverge.
    """
    args = {"entity": "agent-alpha", "attribute": "status", "source_turn_ids": ("t1", "t2")}
    assert StoredFactRecord.mint_id(**args) == StoredFactRecord.mint_id(**args)


def test_mint_id_is_order_independent_on_source_turn_ids() -> None:
    """Re-ordering ``source_turn_ids`` does not change the id.

    Sabotage-proof: remove the ``sorted()`` call in ``mint_id`` and
    the two id strings stop matching.
    """
    id_a = StoredFactRecord.mint_id(entity="X", attribute="y", source_turn_ids=("t1", "t2", "t3"))
    id_b = StoredFactRecord.mint_id(entity="X", attribute="y", source_turn_ids=("t3", "t1", "t2"))
    assert id_a == id_b


def test_mint_id_changes_when_entity_changes() -> None:
    """Distinct entities produce distinct ids — basic collision resistance."""
    id_a = StoredFactRecord.mint_id(entity="X", attribute="y", source_turn_ids=("t1",))
    id_b = StoredFactRecord.mint_id(entity="Z", attribute="y", source_turn_ids=("t1",))
    assert id_a != id_b


# ---------------------------------------------------------------------------
# find_conflicts
# ---------------------------------------------------------------------------


def test_find_conflicts_returns_only_entity_attribute_matches(tmp_path: Path) -> None:
    """``find_conflicts`` is keyed exactly on ``(entity, attribute)``.

    Sabotage-proof: remove the ``attribute = ?`` clause from the
    ``find_conflicts`` SQL and ``f3`` leaks into the result set.
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f1", entity="agent-alpha", attribute="status"))
    store.add(_make_record(fact_id="f2", entity="agent-alpha", attribute="status", value="dating"))
    store.add(_make_record(fact_id="f3", entity="agent-alpha", attribute="job"))
    store.add(_make_record(fact_id="f4", entity="John", attribute="status"))

    conflicts = store.find_conflicts(entity="agent-alpha", attribute="status")
    ids = {f.id for f in conflicts}
    assert ids == {"f1", "f2"}, f"unexpected ids; got {ids!r}"


def test_find_conflicts_excludes_superseded(tmp_path: Path) -> None:
    """Superseded facts do not appear in ``find_conflicts``."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-old"))
    store.add(_make_record(fact_id="f-new", value="married"))
    store.supersede(old_id="f-old", new_id="f-new")

    live = store.find_conflicts(entity="agent-alpha", attribute="status")
    assert {f.id for f in live} == {"f-new"}


def test_find_conflicts_honours_namespace(tmp_path: Path) -> None:
    """Engagement isolation — namespace filter restricts results."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-a", namespace="eng-a"))
    store.add(_make_record(fact_id="f-b", namespace="eng-b"))

    conflicts_a = store.find_conflicts(entity="agent-alpha", attribute="status", namespace="eng-a")
    assert {f.id for f in conflicts_a} == {"f-a"}

    conflicts_all = store.find_conflicts(entity="agent-alpha", attribute="status")
    assert {f.id for f in conflicts_all} == {"f-a", "f-b"}


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


def test_supersede_links_old_to_new_and_masks_old_in_search(tmp_path: Path) -> None:
    """After ``supersede``, the old fact is gone from default search.

    Sabotage-proof: change the supersede UPDATE to no-op (e.g.
    ``WHERE id = 'nothing'``) and the old id reappears in search.
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-old", value="married"))
    store.add(_make_record(fact_id="f-new", value="single"))
    store.supersede(old_id="f-old", new_id="f-new")

    hits = store.search("agent-alpha", top_k=10)
    hit_ids = {h.record.id for h in hits}
    assert "f-old" not in hit_ids
    assert "f-new" in hit_ids


def test_supersede_missing_old_id_raises_key_error(tmp_path: Path) -> None:
    """Superseding from a non-existent id raises ``KeyError``."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-new"))
    with pytest.raises(KeyError, match="no fact with id"):
        store.supersede(old_id="f-does-not-exist", new_id="f-new")


def test_supersede_missing_new_id_raises_key_error(tmp_path: Path) -> None:
    """Superseding to a non-existent id raises ``KeyError``."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-old"))
    with pytest.raises(KeyError, match="no fact with id"):
        store.supersede(old_id="f-old", new_id="f-does-not-exist")


def test_supersede_raises_when_schema_uninitialised(tmp_path: Path) -> None:
    """Calling supersede before any ``add`` raises ``KeyError`` (no schema)."""
    store = _make_store(tmp_path)
    with pytest.raises(KeyError, match="no fact with id"):
        store.supersede(old_id="f-old", new_id="f-new")


# ---------------------------------------------------------------------------
# Namespace filtering on search
# ---------------------------------------------------------------------------


def test_search_honours_namespace_filter(tmp_path: Path) -> None:
    """``search(namespace=...)`` restricts results to the named namespace."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-a", value="bowling", namespace="eng-a"))
    store.add(_make_record(fact_id="f-b", value="bowling", namespace="eng-b"))

    hits_a = store.search("bowling", top_k=10, namespace="eng-a")
    assert {h.record.id for h in hits_a} == {"f-a"}

    hits_all = store.search("bowling", top_k=10)
    assert {h.record.id for h in hits_all} == {"f-a", "f-b"}


# ---------------------------------------------------------------------------
# Empty-store / empty-query short-circuits
# ---------------------------------------------------------------------------


def test_search_on_empty_store_returns_empty_list(tmp_path: Path) -> None:
    """Search before any add returns ``[]`` and does not raise."""
    store = _make_store(tmp_path)
    assert store.search("anything") == []


def test_find_conflicts_on_empty_store_returns_empty_list(tmp_path: Path) -> None:
    """find_conflicts before any add returns ``[]`` (no schema created yet)."""
    store = _make_store(tmp_path)
    assert store.find_conflicts(entity="X", attribute="y") == []


def test_search_with_blank_query_returns_empty_list(tmp_path: Path) -> None:
    """Whitespace-only query short-circuits to ``[]`` before touching the DB."""
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f1", value="present"))
    assert store.search("   ") == []
    assert store.search("") == []


def test_search_top_k_caps_result_count(tmp_path: Path) -> None:
    """``top_k`` caps the number of hits returned by search."""
    store = _make_store(tmp_path)
    for i in range(5):
        store.add(_make_record(fact_id=f"f-{i}", value="common"))
    hits = store.search("common", top_k=2)
    assert len(hits) == 2


# ---------------------------------------------------------------------------
# Persistence + WAL mode
# ---------------------------------------------------------------------------


def test_wal_mode_is_enabled_after_first_connect(tmp_path: Path) -> None:
    """First ``add`` enables WAL mode on the SQLite file.

    Sabotage-proof: remove ``PRAGMA journal_mode=WAL`` from
    ``_connect`` and the assertion drops to ``delete`` (the default).
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f1"))

    conn = sqlite3.connect(str(tmp_path / "facts.sqlite"))
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert mode.lower() == "wal", f"expected WAL; got {mode!r}"


def test_round_trip_preserves_all_record_fields(tmp_path: Path) -> None:
    """Every field on the input record survives the SQL round-trip.

    Sabotage-proof: change the ``find_conflicts`` SQL to omit
    ``confidence`` and the equality check fails.
    """
    store = _make_store(tmp_path)
    original = StoredFactRecord(
        id="f-round",
        entity="agent-alpha",
        attribute="status",
        value="single",
        confidence=0.77,
        source_turn_ids=("t1", "t2", "t3"),
        extracted_at="2026-05-19T12:00:00Z",
        superseded_by=None,
        namespace="eng-x",
    )
    store.add(original)
    recovered = store.find_conflicts(
        entity="agent-alpha",
        attribute="status",
        namespace="eng-x",
    )
    assert len(recovered) == 1
    rec = recovered[0]
    assert rec.id == original.id
    assert rec.entity == original.entity
    assert rec.attribute == original.attribute
    assert rec.value == original.value
    assert rec.confidence == pytest.approx(original.confidence)
    assert rec.source_turn_ids == original.source_turn_ids
    assert rec.extracted_at == original.extracted_at
    assert rec.superseded_by == original.superseded_by
    assert rec.namespace == original.namespace


def test_add_persists_across_store_instances(tmp_path: Path) -> None:
    """Reopening a store recovers previously-persisted facts."""
    db = tmp_path / "facts.sqlite"
    SQLiteFactStore(db_path=db).add(_make_record(fact_id="f1", value="persistent"))

    store2 = SQLiteFactStore(db_path=db)
    hits = store2.search("persistent")
    assert {h.record.id for h in hits} == {"f1"}


# ---------------------------------------------------------------------------
# FTS5 query sanitisation — caught by the LoCoMo benchmark, not the
# original capability tests. Realistic natural-language queries with
# punctuation + multi-word tokens must NOT raise + must NOT silently
# return zero hits when the fact store has matching content.
# ---------------------------------------------------------------------------


def test_search_question_mark_does_not_raise(tmp_path: Path) -> None:
    """Questions ending in ``?`` must not trigger fts5 syntax error.

    Sabotage-proof: remove ``?`` from the specials list in
    ``_build_fts_match_expr`` and this test fails because FTS5
    raises ``sqlite3.OperationalError: fts5: syntax error near "?"``.
    """
    db = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db)
    store.add(_make_record(fact_id="f1", entity="agent-alpha", attribute="role", value="VP"))
    # Must not raise. Empty result is fine (no fact contains "What" etc.)
    hits = store.search("What is agent-alpha's role?")
    # Should actually find the f1 fact via OR-join on "agent-alpha" / "role"
    assert any(h.record.id == "f1" for h in hits), (
        "OR-joined query 'What/is/agent-alpha/role' must surface the matching fact"
    )


def test_search_or_joins_multi_word_query_not_and(tmp_path: Path) -> None:
    """Multi-word queries OR-join tokens; one token match is sufficient.

    Sabotage-proof: change ``OR`` to ``AND`` in ``_build_fts_match_expr``
    and this assertion fails because no fact contains every benchmark-style
    question's tokens.
    """
    db = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db)
    store.add(_make_record(fact_id="f-lgbtq", entity="agent-alpha", attribute="event", value="LGBTQ support group"))
    store.add(_make_record(fact_id="f-other", entity="john", attribute="hobby", value="cooking"))

    hits = store.search("When did agent-alpha go to the LGBTQ support group")
    ids = {h.record.id for h in hits}
    assert "f-lgbtq" in ids, (
        f"OR-joined token search must surface the matching fact; got {ids!r}. "
        f"Sabotage check: if AND-joined, no row contains every query token → ids would be empty."
    )


def test_search_strips_fts5_specials(tmp_path: Path) -> None:
    """FTS5 special chars (``"`` ``(`` ``)`` ``*`` etc.) get stripped not escaped.

    Sabotage-proof: strip the for-loop in ``_build_fts_match_expr`` so
    specials reach FTS5 raw and this test fails on OperationalError.
    """
    db = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db)
    store.add(_make_record(fact_id="f1", entity="alice", attribute="book", value="The Lean Startup"))

    # All these contain FTS5 specials that would raise without sanitisation
    for query in [
        'When did Alice read "The Lean Startup"?',
        "What does Alice (the founder) read?",
        "Alice's *favourite* book?",
    ]:
        hits = store.search(query)
        assert any(h.record.id == "f1" for h in hits), f"sanitised query {query!r} should find the f1 fact"


def test_search_all_specials_no_real_tokens_returns_empty(tmp_path: Path) -> None:
    """A query that collapses to no usable tokens after sanitisation
    returns ``[]`` rather than raising or matching everything.

    Sabotage-proof: make ``_fts_query`` skip the empty-token guard and
    pass an empty string to FTS5 — operational error or matches all rows.
    """
    db = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db)
    store.add(_make_record(fact_id="f1", entity="x", attribute="y", value="z"))

    # Query is just punctuation + reserved words; nothing usable
    assert store.search("?? AND OR NOT") == []
    assert store.search("?") == []


def test_search_reserved_fts5_words_dropped(tmp_path: Path) -> None:
    """``and`` / ``or`` / ``not`` / ``near`` as bare query words are dropped,
    not passed through (they'd be parsed as FTS5 operators).

    Sabotage-proof: remove the ``reserved`` filter and the query
    ``and agent-alpha`` passes ``and OR agent-alpha`` to FTS5 which raises.
    """
    db = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db)
    store.add(_make_record(fact_id="f1", entity="agent-alpha", attribute="role", value="VP"))

    # 'and' is a reserved FTS5 operator; if passed through unfiltered FTS5 raises
    hits = store.search("and agent-alpha")
    assert any(h.record.id == "f1" for h in hits)


# ---------------------------------------------------------------------------
# Stream A Lever A — evidence_at round-trip + legacy migration
# ---------------------------------------------------------------------------


def test_evidence_at_persists_and_recalls_on_round_trip(tmp_path: Path) -> None:
    """``evidence_at`` round-trips through SQLite without truncation.

    Sabotage-proof: drop ``evidence_at`` from the INSERT in ``add`` and
    this test fails because the recalled record has ``evidence_at=None``
    rather than the ISO date stored.
    """
    store = _make_store(tmp_path)
    record = StoredFactRecord(
        id="f-evidence",
        entity="agent-alpha",
        attribute="moved_from",
        value="Sweden",
        confidence=0.85,
        source_turn_ids=("t1",),
        extracted_at="2026-01-01T00:00:00Z",
        superseded_by=None,
        namespace="shared",
        evidence_at="2019-04-15",
    )
    store.add(record)

    hits = store.search("Sweden")
    assert hits, "expected to recall the just-added record by FTS value"
    assert hits[0].record.evidence_at == "2019-04-15"


def test_evidence_at_none_round_trip(tmp_path: Path) -> None:
    """A record with ``evidence_at=None`` round-trips as ``None``.

    Sabotage-proof: change ``_row_to_record`` to always populate
    ``evidence_at`` from a sentinel string and this test fails because
    the legacy path leaks a non-None value.
    """
    store = _make_store(tmp_path)
    store.add(_make_record(fact_id="f-legacy"))

    hits = store.search("single")
    assert hits
    assert hits[0].record.evidence_at is None


def test_legacy_schema_without_evidence_at_column_migrates_on_add(tmp_path: Path) -> None:
    """A pre-existing facts table without ``evidence_at`` gets the column added.

    Simulates upgrading from a pre-Lever-A SQLite file. The schema
    initialiser must ALTER TABLE to add the column; we then verify
    that a new add() succeeds and the record round-trips.

    Sabotage-proof: drop the ``_apply_column_migrations`` call from
    ``_ensure_schema`` and this test fails because the second add
    raises ``sqlite3.OperationalError: table facts has no column
    named evidence_at``.
    """
    db_path = tmp_path / "legacy-facts.sqlite"
    # Build a pre-Lever-A schema by hand (no evidence_at column).
    legacy_conn = sqlite3.connect(str(db_path))
    legacy_conn.execute(
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
        )
        """
    )
    legacy_conn.execute(
        """
        CREATE VIRTUAL TABLE facts_fts USING fts5(
            entity, attribute, value,
            content='facts',
            content_rowid='rowid'
        )
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    # Now open the same path through the production store — should
    # auto-migrate on first add().
    store = SQLiteFactStore(db_path=db_path)
    record = StoredFactRecord(
        id="f-migrated",
        entity="X",
        attribute="y",
        value="z-distinct",
        confidence=0.9,
        source_turn_ids=("t1",),
        extracted_at="2026-01-01T00:00:00Z",
        superseded_by=None,
        namespace="shared",
        evidence_at="2024-12-01",
    )
    store.add(record)

    hits = store.search("z-distinct")
    assert hits
    assert hits[0].record.evidence_at == "2024-12-01"
