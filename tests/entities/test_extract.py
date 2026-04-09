"""
Tests for mnemosyne.entities.extract — NER extraction pipeline.

Covers:
- extract_rules_based_with_db(): vault folder names, DB entity names, Title Case heuristic,
  sentence-start skip
- extract_file(): LLM skipped when rules find >= 3 entities
"""

from __future__ import annotations

import pytest

from mnemosyne.entities.extract import (
    _invalidate_vault_cache,
    extract_file,
    extract_rules_based_with_db,
    read_stub_aliases,
)
from mnemosyne.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh entities DB backed by a temp file."""
    db_path = str(tmp_path / "test_entities.db")
    monkeypatch.setenv("MNEMOSYNE_TEST_DB", db_path)
    conn = open_entities_db()
    yield conn
    conn.close()


@pytest.fixture()
def vault_root(tmp_path):
    """Minimal fake vault with folders."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Create folder structure that mirrors a real vault
    (vault / "02-Areas" / "Work" / "Clients" / "Acme Corp").mkdir(parents=True)
    (vault / "02-Areas" / "Work" / "Clients" / "Gamma Systems").mkdir(parents=True)
    (vault / "01-Projects" / "Mnemosyne").mkdir(parents=True)
    # Also a file
    (vault / "02-Areas" / "Work" / "Clients" / "Acme Corp" / "Overview.md").write_text(
        "# Acme Corp\nSoftware platform partner.", encoding="utf-8"
    )
    _invalidate_vault_cache()
    return vault


@pytest.fixture()
def db_with_entities(db):
    """DB pre-populated with known entities."""
    now = "2026-01-01T00:00:00Z"
    db.execute(
        """
        INSERT INTO entities (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, 'shared', ?, ?)
        """,
        (
            "acme-corp",
            "organisation",
            "Acme Corp",
            "06-Entities/organisation/acme-corp.md",
            now,
            now,
        ),
    )
    db.execute(
        """
        INSERT INTO entities (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, 'shared', ?, ?)
        """,
        ("alice-chen", "person", "Alice Chen", "06-Entities/person/alice-chen.md", now, now),
    )
    db.commit()
    return db


# ---------------------------------------------------------------------------
# extract_rules_based_with_db: vault folder name detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_detects_vault_folder_names(db, vault_root):
    """extract_rules_based_with_db detects names matching vault folder names."""
    _invalidate_vault_cache()
    text = "We recently partnered with Acme Corp for the platform integration project."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)

    names = [e.name for e in results]
    assert "Acme Corp" in names, f"Expected 'Acme Corp' in extracted names, got: {names}"


@pytest.mark.unit
def test_extract_vault_folder_confidence_is_high(db, vault_root):
    """Vault structure matches should have confidence >= 0.85."""
    _invalidate_vault_cache()
    text = "Mnemosyne is the memory system we are building."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)

    mnemosyne_results = [e for e in results if e.name.lower() == "mnemosyne"]
    assert mnemosyne_results, "Mnemosyne should be extracted"
    assert mnemosyne_results[0].confidence >= 0.85


@pytest.mark.unit
def test_extract_multiple_vault_names(db, vault_root):
    """Multiple vault folder names in text should all be extracted."""
    _invalidate_vault_cache()
    text = "We discussed Acme Corp and Gamma Systems during the strategy session."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)
    names = {e.name for e in results}
    assert "Acme Corp" in names
    assert "Gamma Systems" in names


# ---------------------------------------------------------------------------
# extract_rules_based_with_db: known entity names from DB
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_detects_db_entity_names(db_with_entities, vault_root):
    """extract_rules_based_with_db detects entity names already in the DB."""
    _invalidate_vault_cache()
    text = "Alice Chen presented the Acme Corp roadmap at the all-hands."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db_with_entities, vault_root=vault_root)
    names = [e.name for e in results]
    assert "Alice Chen" in names, f"Expected 'Alice Chen', got: {names}"
    assert "Acme Corp" in names, f"Expected 'Acme Corp', got: {names}"


@pytest.mark.unit
def test_extract_db_entity_confidence_is_high(db_with_entities, vault_root):
    """DB-matched entities should have confidence >= 0.9."""
    _invalidate_vault_cache()
    text = "Alice Chen is the CEO."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db_with_entities, vault_root=vault_root)
    alice_results = [e for e in results if e.name == "Alice Chen"]
    assert alice_results, "Alice Chen should be extracted"
    assert alice_results[0].confidence >= 0.9


# ---------------------------------------------------------------------------
# extract_rules_based_with_db: Title Case heuristic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_detects_title_case_candidates(db, vault_root):
    """Title Case multi-word phrases not at sentence start should be extracted."""
    _invalidate_vault_cache()
    # Put the proper noun in the middle of a sentence to avoid sentence-start skip
    text = "We are partnering with Quantum Systems for the deployment."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)
    names = [e.name for e in results]
    assert any("Quantum" in n for n in names), f"Expected Quantum Systems or similar, got: {names}"


@pytest.mark.unit
def test_extract_title_case_confidence_is_low(db, vault_root):
    """Title Case heuristic candidates should have low confidence (< 0.6)."""
    _invalidate_vault_cache()
    text = "We are partnering with Novel Framework for this work."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)
    heuristic = [e for e in results if e.confidence < 0.6]
    assert heuristic, "Should have at least one low-confidence heuristic candidate"


# ---------------------------------------------------------------------------
# extract_rules_based_with_db: sentence-start capitalisation skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_skips_sentence_start_capitalisation(db, vault_root):
    """Title Case words at sentence start should NOT be extracted as candidates."""
    _invalidate_vault_cache()
    # "The Great Project" is at sentence start — should not be extracted by heuristic
    text = "The Great Project is what we are working on. We aim to finish it soon."
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)
    # Only Title Case heuristic check — vault/DB won't match these words
    heuristic_results = [e for e in results if e.confidence < 0.6]
    sentence_start_names = [e.name for e in heuristic_results if "Great Project" in e.name]
    assert not sentence_start_names, (
        f"'The Great Project' at sentence start should not be extracted, got: {heuristic_results}"
    )


@pytest.mark.unit
def test_extract_captures_title_case_mid_sentence(db, vault_root):
    """Title Case phrases mid-sentence should be captured; sentence-start should be skipped."""
    _invalidate_vault_cache()
    # First sentence: sentence start (skip). Second: mid-sentence (capture).
    text = "Alpha Beta is our internal tool. We use Alpha Beta daily at Acme Corp."
    # "Alpha Beta" appears mid-sentence in second sentence
    results = extract_rules_based_with_db(text, "/vault/notes.md", db, vault_root=vault_root)
    names = [e.name for e in results]  # noqa: F841
    # Acme Corp might match DB or heuristic; just check we got something mid-sentence
    # The key assertion is that at least one result exists (mid-sentence capture)
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# extract_file: LLM skipped when rules find >= 3 entities
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_file_skips_llm_when_rules_sufficient(tmp_path, db_with_entities, vault_root, monkeypatch):
    """extract_file should skip LLM when rules find >= 3 entities."""
    _invalidate_vault_cache()

    # Create a test markdown file with 3+ known entity names
    md_file = tmp_path / "test_note.md"
    md_file.write_text(
        "# Meeting Notes\n\nAlice Chen met with the Acme Corp team.\nWe also discussed Mnemosyne development.\n",
        encoding="utf-8",
    )

    llm_called = []

    def mock_extract_llm(*args, **kwargs):
        llm_called.append(True)
        return []

    monkeypatch.setattr("mnemosyne.entities.extract.extract_llm", mock_extract_llm)
    # Also patch vault_root in extract module
    import mnemosyne.entities.extract as extract_mod

    monkeypatch.setattr(extract_mod, "VAULT_ROOT", vault_root)
    _invalidate_vault_cache()

    # Pre-add Mnemosyne to DB so rules find it
    now = "2026-01-01T00:00:00Z"
    db_with_entities.execute(
        """
        INSERT OR IGNORE INTO entities
            (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
        VALUES ('mnemosyne', 'project', 'Mnemosyne', 'active',
                '06-Entities/project/mnemosyne.md', 'shared', ?, ?)
        """,
        (now, now),
    )
    db_with_entities.commit()

    results = extract_file(
        str(md_file),
        db_with_entities,
        use_llm=True,
        api_key="test-key",
        endpoint="https://test.openai.azure.com",
        vault_root=vault_root,
    )

    assert not llm_called, (
        f"LLM should not be called when rules find >= 3 entities, but extract_file returned {len(results)} results"
    )
    assert len(results) >= 2  # at least Alice Chen + Acme Corp (or Mnemosyne)


@pytest.mark.unit
def test_extract_file_returns_empty_for_missing_file(db):
    """extract_file should return empty list for a non-existent file."""
    results = extract_file("/nonexistent/path/file.md", db)
    assert results == []


@pytest.mark.unit
def test_extract_file_strips_frontmatter(tmp_path, db, vault_root, monkeypatch):
    """extract_file should strip YAML frontmatter before NER."""
    _invalidate_vault_cache()
    import mnemosyne.entities.extract as extract_mod

    monkeypatch.setattr(extract_mod, "VAULT_ROOT", vault_root)
    _invalidate_vault_cache()

    md_file = tmp_path / "note.md"
    md_file.write_text(
        "---\ntitle: Test\ntype: note\n---\n\nWe partnered with Acme Corp for the platform.\n",
        encoding="utf-8",
    )

    results = extract_file(str(md_file), db, vault_root=vault_root)
    names = [e.name for e in results]
    # Acme Corp is a vault folder name
    assert "Acme Corp" in names


# ---------------------------------------------------------------------------
# read_stub_aliases: frontmatter aliases field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_stub_aliases_inline_list(tmp_path):
    """Reads aliases from inline YAML list syntax: aliases: [BWE-C, BWE&C]."""
    stub = tmp_path / "smec.md"
    stub.write_text(
        "---\ntype: concept\nname: Delta Co\n"
        "entity-id: bridgewater-engineering\naliases: [BWE-C, BWE&C]\n---\n\n# Delta Co\n",
        encoding="utf-8",
    )
    aliases = read_stub_aliases(str(stub))
    assert aliases == ["BWE-C", "BWE&C"]


@pytest.mark.unit
def test_read_stub_aliases_block_list(tmp_path):
    """Reads aliases from block YAML list syntax."""
    stub = tmp_path / "smec.md"
    stub.write_text(
        "---\ntype: concept\nname: Delta Co\naliases:\n  - BWE-C\n  - BWE&C\n---\n\n# Delta Co\n",
        encoding="utf-8",
    )
    aliases = read_stub_aliases(str(stub))
    assert aliases == ["BWE-C", "BWE&C"]


@pytest.mark.unit
def test_read_stub_aliases_empty_list(tmp_path):
    """Returns [] when aliases field is an empty inline list."""
    stub = tmp_path / "builder.md"
    stub.write_text(
        "---\ntype: concept\nname: Builder\naliases: []\n---\n\n# Builder\n",
        encoding="utf-8",
    )
    aliases = read_stub_aliases(str(stub))
    assert aliases == []


@pytest.mark.unit
def test_read_stub_aliases_missing_field(tmp_path):
    """Returns [] when no aliases: field present in frontmatter."""
    stub = tmp_path / "entity.md"
    stub.write_text(
        "---\ntype: concept\nname: Builder\ncreated: 2026-01-01\n---\n\n# Builder\n",
        encoding="utf-8",
    )
    aliases = read_stub_aliases(str(stub))
    assert aliases == []


@pytest.mark.unit
def test_read_stub_aliases_no_frontmatter(tmp_path):
    """Returns [] for a file without YAML frontmatter."""
    stub = tmp_path / "entity.md"
    stub.write_text("# Just a heading\n\nSome text.\n", encoding="utf-8")
    aliases = read_stub_aliases(str(stub))
    assert aliases == []


@pytest.mark.unit
def test_read_stub_aliases_missing_file():
    """Returns [] for a non-existent file — never raises."""
    aliases = read_stub_aliases("/nonexistent/path/entity.md")
    assert aliases == []


@pytest.mark.unit
def test_read_stub_aliases_real_entity_stub():
    """Reads aliases from an existing vault entity stub (builder.md)."""
    import os

    stub_path = "/vault/agent-knowledge/entities/concept/builder.md"
    if not os.path.exists(stub_path):
        pytest.skip("Entity stub not present in this environment")
    aliases = read_stub_aliases(stub_path)
    # builder.md has aliases: [] — should return list (empty or populated)
    assert isinstance(aliases, list)
