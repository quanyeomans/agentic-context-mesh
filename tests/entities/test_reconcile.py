"""
Tests for mnemosyne.entities.reconcile — ontology reconciler.

Covers:
- find_canonical(): exact match, alias match, no match
- reconcile_extracted(): creates new entity, merges above threshold
- update_mentions(): INSERT OR IGNORE idempotency
"""

from __future__ import annotations

import pytest

from mnemosyne.entities.extract import ExtractedEntity
from mnemosyne.entities.reconcile import (
    _string_similarity,
    find_canonical,
    reconcile_extracted,
    update_mentions,
)
from mnemosyne.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh entities DB backed by a temp file."""
    db_path = str(tmp_path / "test_reconcile.db")
    monkeypatch.setenv("MNEMOSYNE_TEST_DB", db_path)
    conn = open_entities_db()
    yield conn
    conn.close()


@pytest.fixture()
def db_with_entities(db):
    """DB pre-populated with two entities and one alias."""
    now = "2026-01-01T00:00:00Z"

    # Main entity: Triad Consulting
    db.execute(
        """
        INSERT INTO entities (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
        VALUES ('triad-consulting', 'organisation', 'Triad Consulting', 'active',
                '06-Entities/organisation/triad-consulting.md', 'shared', ?, ?)
        """,
        (now, now),
    )

    # Main entity: Alex Jordan
    db.execute(
        """
        INSERT INTO entities (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
        VALUES ('alex-jordan', 'person', 'Alex Jordan', 'active',
                '06-Entities/person/alex-jordan.md', 'shared', ?, ?)
        """,
        (now, now),
    )

    # Alias entity: "3 Cubes" → points to triad-consulting
    db.execute(
        """
        INSERT INTO entities (id, type, name, status, markdown_path, agent_scope,
                              created_at, updated_at, canonical_id)
        VALUES ('3-cubes', 'organisation', '3 Cubes', 'active',
                '06-Entities/organisation/3-cubes.md', 'shared', ?, ?, 'triad-consulting')
        """,
        (now, now),
    )

    db.commit()
    return db


# ---------------------------------------------------------------------------
# _string_similarity utility
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_string_similarity_identical():
    """Identical strings should have similarity 1.0."""
    assert _string_similarity("Triad Consulting", "Triad Consulting") == 1.0


@pytest.mark.unit
def test_string_similarity_dissimilar():
    """Completely different strings should have low similarity."""
    score = _string_similarity("Triad Consulting", "XYZ123")
    assert score < 0.4


@pytest.mark.unit
def test_string_similarity_partial():
    """Partially similar strings should have intermediate scores."""
    score = _string_similarity("Triad Consulting", "Triad Consultin")
    assert 0.5 < score < 1.0


# ---------------------------------------------------------------------------
# find_canonical: exact match
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_canonical_exact_match(db_with_entities):
    """Exact name match should return the correct entity_id."""
    entity_id = find_canonical("Triad Consulting", db_with_entities)
    assert entity_id == "triad-consulting"


@pytest.mark.unit
def test_find_canonical_exact_match_case_insensitive(db_with_entities):
    """Exact match should be case-insensitive."""
    entity_id = find_canonical("triad consulting", db_with_entities)
    assert entity_id == "triad-consulting"


@pytest.mark.unit
def test_find_canonical_person_exact_match(db_with_entities):
    """Person entity exact match should work."""
    entity_id = find_canonical("Alex Jordan", db_with_entities)
    assert entity_id == "alex-jordan"


# ---------------------------------------------------------------------------
# find_canonical: alias match
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_canonical_alias_match(db_with_entities):
    """Alias entity match should return the canonical entity_id, not the alias id."""
    # "3 Cubes" is an alias for "Triad Consulting" (canonical_id = 'triad-consulting')
    entity_id = find_canonical("3 Cubes", db_with_entities)
    assert entity_id == "triad-consulting", (
        f"Expected canonical 'triad-consulting' for alias '3 Cubes', got: {entity_id}"
    )


# ---------------------------------------------------------------------------
# find_canonical: no match
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_canonical_no_match_returns_none(db_with_entities):
    """A completely unknown name should return None."""
    entity_id = find_canonical("UnknownEntity12345", db_with_entities)
    assert entity_id is None


@pytest.mark.unit
def test_find_canonical_no_match_empty_db(db):
    """find_canonical on an empty DB should return None."""
    entity_id = find_canonical("Anything", db)
    assert entity_id is None


@pytest.mark.unit
def test_find_canonical_strict_threshold_no_match(db_with_entities):
    """With similarity_threshold=1.0 (exact only), slightly different names should return None."""
    # "Triad Consultin" is similar but not identical
    entity_id = find_canonical("Triad Consultin", db_with_entities, similarity_threshold=1.0)
    assert entity_id is None


# ---------------------------------------------------------------------------
# reconcile_extracted: creates new entity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconcile_creates_new_entity(db):
    """reconcile_extracted should create a new entity record when no match found."""
    extracted = [
        ExtractedEntity(
            name="Acme Corporation",
            entity_type="org",
            confidence=0.9,
            context="We partnered with Acme Corporation.",
            source_path="/vault/notes.md",
        )
    ]

    result = reconcile_extracted(extracted, db)

    assert "Acme Corporation" in result
    new_id = result["Acme Corporation"]

    # Verify it's in the DB
    row = db.execute("SELECT name, type FROM entities WHERE id = ?", (new_id,)).fetchone()
    assert row is not None
    assert row["name"] == "Acme Corporation"


@pytest.mark.unit
def test_reconcile_multiple_new_entities(db):
    """Multiple new entities should all be created."""
    extracted = [
        ExtractedEntity("Alpha Corp", "org", 0.9, "Alpha Corp is great.", "/vault/a.md"),
        ExtractedEntity("Beta Ltd", "org", 0.8, "Beta Ltd joined us.", "/vault/a.md"),
    ]
    result = reconcile_extracted(extracted, db)
    assert "Alpha Corp" in result
    assert "Beta Ltd" in result
    assert result["Alpha Corp"] != result["Beta Ltd"]


# ---------------------------------------------------------------------------
# reconcile_extracted: merges when above threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconcile_merges_exact_match(db_with_entities):
    """reconcile_extracted should merge an exact name match without creating duplicate."""
    before_count = db_with_entities.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    extracted = [
        ExtractedEntity(
            name="Triad Consulting",
            entity_type="org",
            confidence=0.95,
            context="Triad Consulting is the company.",
            source_path="/vault/notes.md",
        )
    ]
    result = reconcile_extracted(extracted, db_with_entities)

    after_count = db_with_entities.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    assert result["Triad Consulting"] == "triad-consulting"
    assert after_count == before_count, "No new entity should be created for an exact match"


@pytest.mark.unit
def test_reconcile_returns_entity_id_mapping(db_with_entities):
    """reconcile_extracted should return a dict mapping names to entity IDs."""
    extracted = [
        ExtractedEntity("Alex Jordan", "person", 0.95, "Alex Jordan spoke.", "/vault/n.md"),
        ExtractedEntity("NewEntity99", "org", 0.8, "NewEntity99 is new.", "/vault/n.md"),
    ]
    result = reconcile_extracted(extracted, db_with_entities)

    assert isinstance(result, dict)
    assert "Alex Jordan" in result
    assert result["Alex Jordan"] == "alex-jordan"
    assert "NewEntity99" in result


# ---------------------------------------------------------------------------
# update_mentions: idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_update_mentions_creates_row(db_with_entities):
    """update_mentions should create a row in entity_mentions."""
    update_mentions(
        entity_ids=["triad-consulting"],
        doc_hash="abc123",
        doc_path="/vault/notes.md",
        db=db_with_entities,
    )

    row = db_with_entities.execute(
        "SELECT mention_count FROM entity_mentions WHERE entity_id = ? AND doc_uri = ?",
        ("triad-consulting", "/vault/notes.md"),
    ).fetchone()

    assert row is not None
    assert row["mention_count"] == 1


@pytest.mark.unit
def test_update_mentions_is_idempotent(db_with_entities):
    """Calling update_mentions twice for the same entity+doc should not duplicate rows."""
    for _ in range(3):
        update_mentions(
            entity_ids=["triad-consulting"],
            doc_hash="abc123",
            doc_path="/vault/notes.md",
            db=db_with_entities,
        )

    rows = db_with_entities.execute(
        "SELECT mention_count FROM entity_mentions WHERE entity_id = ? AND doc_uri = ?",
        ("triad-consulting", "/vault/notes.md"),
    ).fetchall()

    # Should be exactly ONE row (idempotent insert), with accumulated mention_count
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"


@pytest.mark.unit
def test_update_mentions_increments_count(db_with_entities):
    """Repeated update_mentions calls should increment mention_count."""
    for _ in range(3):
        update_mentions(
            entity_ids=["alex-jordan"],
            doc_hash="hash456",
            doc_path="/vault/meeting.md",
            db=db_with_entities,
        )

    row = db_with_entities.execute(
        "SELECT mention_count FROM entity_mentions WHERE entity_id = ? AND doc_uri = ?",
        ("alex-jordan", "/vault/meeting.md"),
    ).fetchone()
    assert row is not None
    assert row["mention_count"] == 3


@pytest.mark.unit
def test_update_mentions_multiple_entities(db_with_entities):
    """update_mentions should handle multiple entity IDs in one call."""
    update_mentions(
        entity_ids=["triad-consulting", "alex-jordan"],
        doc_hash="multi123",
        doc_path="/vault/multi.md",
        db=db_with_entities,
    )

    rows = db_with_entities.execute(
        "SELECT entity_id FROM entity_mentions WHERE doc_uri = ?",
        ("/vault/multi.md",),
    ).fetchall()
    entity_ids_in_db = {r["entity_id"] for r in rows}
    assert "triad-consulting" in entity_ids_in_db
    assert "alex-jordan" in entity_ids_in_db


@pytest.mark.unit
def test_update_mentions_empty_list(db_with_entities):
    """update_mentions with empty entity_ids list should not error or write rows."""
    update_mentions(
        entity_ids=[],
        doc_hash="empty",
        doc_path="/vault/empty.md",
        db=db_with_entities,
    )
    rows = db_with_entities.execute(
        "SELECT * FROM entity_mentions WHERE doc_uri = ?",
        ("/vault/empty.md",),
    ).fetchall()
    assert rows == []
