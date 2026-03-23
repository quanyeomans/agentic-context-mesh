"""
Tests for mnemosyne.entities.graph — entity CRUD and mentions.
"""

import pytest

from mnemosyne.entities.graph import (
    entity_lookup,
    entity_write,
    get_mentions,
    slug,
)
from mnemosyne.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Open a fresh entities DB backed by a temp file."""
    db_path = str(tmp_path / "test_entities.db")
    monkeypatch.setenv("MNEMOSYNE_TEST_DB", db_path)
    conn = open_entities_db()
    yield conn
    conn.close()


@pytest.fixture()
def vault_root(tmp_path, monkeypatch):
    """Patch VAULT_ROOT to a temp directory so tests don't write to /data/obsidian-vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    import mnemosyne.entities.graph as graph_module

    monkeypatch.setattr(graph_module, "VAULT_ROOT", vault)
    return vault


# ---------------------------------------------------------------------------
# slug()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_slug_dan_mcmahon():
    assert slug("Alex Jordan") == "alex-jordan"


@pytest.mark.unit
def test_slug_three_cubes():
    assert slug("Triad Consulting") == "triad-consulting"


@pytest.mark.unit
def test_slug_special_chars():
    assert slug("OpenClaw/QMD") == "openclaw-qmd"


@pytest.mark.unit
def test_slug_leading_trailing_hyphens():
    # No leading/trailing hyphens
    result = slug("  Hello World  ")
    assert not result.startswith("-")
    assert not result.endswith("-")
    assert result == "hello-world"


@pytest.mark.unit
def test_slug_multiple_spaces():
    assert slug("a  b") == "a-b"


# ---------------------------------------------------------------------------
# entity_write()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_entity_write_creates_db_record(db, vault_root):
    """entity_write() should create a row in entities table."""
    entity_id = entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )
    assert entity_id == "alex-jordan"

    row = db.execute("SELECT id, type, name FROM entities WHERE id = ?", (entity_id,)).fetchone()
    assert row is not None
    assert row[0] == "alex-jordan"
    assert row[1] == "person"
    assert row[2] == "Alex Jordan"


@pytest.mark.unit
def test_entity_write_twice_does_not_duplicate(db, vault_root):
    """Calling entity_write() twice should update, not duplicate."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
        summary="Updated summary",
    )
    count = db.execute("SELECT COUNT(*) FROM entities WHERE id = 'alex-jordan'").fetchone()[0]
    assert count == 1

    row = db.execute("SELECT summary FROM entities WHERE id = 'alex-jordan'").fetchone()
    assert row[0] == "Updated summary"


@pytest.mark.unit
def test_entity_write_with_facts(db, vault_root):
    """entity_write() with facts should insert rows into entity_facts."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
        facts=["CEO, 2024", "Founder of Triad Consulting"],
    )
    rows = db.execute("SELECT fact_text FROM entity_facts WHERE entity_id = 'alex-jordan' ORDER BY id").fetchall()
    fact_texts = [r[0] for r in rows]
    assert "CEO, 2024" in fact_texts
    assert "Founder of Triad Consulting" in fact_texts


@pytest.mark.unit
def test_entity_write_creates_vault_file(db, vault_root):
    """entity_write() should create the vault Markdown file if it doesn't exist."""
    entity_write(
        name="Triad Consulting",
        entity_type="organisation",
        markdown_path="06-Entities/organisation/triad-consulting.md",
        db=db,
        summary="A consulting company",
    )
    vault_file = vault_root / "06-Entities/organisation/triad-consulting.md"
    assert vault_file.exists()
    content = vault_file.read_text()
    assert "entity-id: triad-consulting" in content
    assert "# Triad Consulting" in content
    assert "A consulting company" in content


@pytest.mark.unit
def test_entity_write_does_not_overwrite_existing_vault_file(db, vault_root):
    """entity_write() should NOT overwrite a vault file that already exists."""
    vault_file = vault_root / "06-Entities/person/existing.md"
    vault_file.parent.mkdir(parents=True, exist_ok=True)
    vault_file.write_text("# Custom content\nDo not overwrite me.")

    entity_write(
        name="Existing",
        entity_type="person",
        markdown_path="06-Entities/person/existing.md",
        db=db,
    )
    content = vault_file.read_text()
    assert "Do not overwrite me." in content


# ---------------------------------------------------------------------------
# entity_lookup()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_entity_lookup_exact_match(db, vault_root):
    """entity_lookup() should find an entity by exact name."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
        summary="CEO",
    )
    result = entity_lookup("Alex Jordan", db)
    assert result is not None
    assert result.id == "alex-jordan"
    assert result.name == "Alex Jordan"
    assert result.type == "person"
    assert result.summary == "CEO"


@pytest.mark.unit
def test_entity_lookup_fuzzy_match(db, vault_root):
    """entity_lookup() should fall back to fuzzy LIKE match on partial name."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )
    result = entity_lookup("Jordan", db)
    assert result is not None
    assert result.id == "alex-jordan"


@pytest.mark.unit
def test_entity_lookup_not_found_returns_none(db):
    """entity_lookup() should return None when nothing matches."""
    result = entity_lookup("Completely Unknown Person", db)
    assert result is None


@pytest.mark.unit
def test_entity_lookup_returns_facts(db, vault_root):
    """entity_lookup() result should include associated facts."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
        facts=["CEO, 2024"],
    )
    result = entity_lookup("Alex Jordan", db)
    assert result is not None
    assert len(result.facts) >= 1
    fact_texts = [f["fact_text"] for f in result.facts]
    assert "CEO, 2024" in fact_texts


@pytest.mark.unit
def test_entity_lookup_returns_relationships(db, vault_root):
    """entity_lookup() result should include relationships."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )
    entity_write(
        name="Triad Consulting",
        entity_type="organisation",
        markdown_path="06-Entities/organisation/triad-consulting.md",
        db=db,
    )
    now = "2026-01-01T00:00:00Z"
    db.execute(
        """
        INSERT INTO relationships (from_entity, to_entity, rel_type, created_at, updated_at)
        VALUES ('alex-jordan', 'triad-consulting', 'member_of', ?, ?)
        """,
        (now, now),
    )
    db.commit()

    result = entity_lookup("Alex Jordan", db)
    assert result is not None
    assert len(result.relationships) == 1
    assert result.relationships[0]["rel_type"] == "member_of"


# ---------------------------------------------------------------------------
# get_mentions()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_mentions_ordering(db, vault_root):
    """get_mentions() should return doc_uris ordered by mention_count DESC, last_seen DESC."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )

    mentions_data = [
        ("vault://notes/note-a.md", 5, "2026-01-01", "2026-03-01"),
        ("vault://notes/note-b.md", 10, "2026-01-01", "2026-03-20"),
        ("vault://notes/note-c.md", 3, "2026-01-01", "2026-03-15"),
        ("vault://notes/note-d.md", 10, "2026-01-01", "2026-03-10"),
    ]
    for doc_uri, count, first, last in mentions_data:
        db.execute(
            """
            INSERT INTO entity_mentions (entity_id, doc_uri, mention_count, first_seen, last_seen)
            VALUES ('alex-jordan', ?, ?, ?, ?)
            """,
            (doc_uri, count, first, last),
        )
    db.commit()

    mentions = get_mentions("alex-jordan", db, limit=10)

    # note-b (10, 2026-03-20) should come first
    assert mentions[0] == "vault://notes/note-b.md"
    # note-d (10, 2026-03-10) should be second (same count, older last_seen)
    assert mentions[1] == "vault://notes/note-d.md"
    # note-a (5) before note-c (3)
    assert mentions[2] == "vault://notes/note-a.md"
    assert mentions[3] == "vault://notes/note-c.md"


@pytest.mark.unit
def test_get_mentions_limit(db, vault_root):
    """get_mentions() should respect the limit parameter."""
    entity_write(
        name="Alex Jordan",
        entity_type="person",
        markdown_path="06-Entities/person/alex-jordan.md",
        db=db,
    )
    for i in range(15):
        db.execute(
            """
            INSERT INTO entity_mentions (entity_id, doc_uri, mention_count, first_seen, last_seen)
            VALUES ('alex-jordan', ?, ?, '2026-01-01', '2026-03-01')
            """,
            (f"vault://doc-{i}.md", i + 1),
        )
    db.commit()

    mentions = get_mentions("alex-jordan", db, limit=5)
    assert len(mentions) == 5


@pytest.mark.unit
def test_get_mentions_no_results(db):
    """get_mentions() should return empty list for unknown entity."""
    result = get_mentions("nonexistent-entity", db, limit=10)
    assert result == []
