"""
Tests for mnemosyne.entities.resolver — resolve_canonical().

Covers:
- Canonical entity lookup (already canonical, returns itself)
- Alias lookup (canonical_id set, returns canonical row)
- Case-insensitive matching
- Unknown entity → None
- All 7 alias groups from seed_aliases.py
"""

from __future__ import annotations

import sqlite3

import pytest

from mnemosyne.entities.resolver import resolve_canonical
from mnemosyne.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh entities DB backed by a temp file, pre-seeded with test data."""
    db_path = str(tmp_path / "test_resolver.db")
    monkeypatch.setenv("MNEMOSYNE_TEST_DB", db_path)
    conn = open_entities_db()
    conn.row_factory = sqlite3.Row
    _seed_test_db(conn)
    yield conn
    conn.close()


def _seed_test_db(db: sqlite3.Connection) -> None:
    """Insert canonical entities and alias rows for testing."""
    now = "2026-01-01T00:00:00Z"

    canonicals = [
        ("bridgewater-engineering", "concept", "Bridgewater Engineering"),
        ("softcorp", "organisation", "Softcorp"),
        ("dynamics-365", "concept", "Dynamics 365"),
        ("burger-palace-canonical", "organisation", "Burger Palace"),
        ("genesys-cloud", "concept", "Genesys Cloud"),
        ("copilot-studio", "concept", "Copilot Studio"),
        ("triad-consulting", "organisation", "Triad Consulting"),
    ]
    for cid, ctype, cname in canonicals:
        db.execute(
            """
            INSERT OR IGNORE INTO entities
                (id, type, name, status, markdown_path, agent_scope, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, 'shared', ?, ?)
            """,
            (cid, ctype, cname, f"06-Entities/{ctype}/{cid}.md", now, now),
        )

    aliases = [
        ("bwe-c", "concept", "BWE-C", "bridgewater-engineering"),
        ("bwe-amp-c", "concept", "BWE&C", "bridgewater-engineering"),
        ("scft", "organisation", "SCFT", "softcorp"),
        ("softcorp-corp", "organisation", "Softcorp Corporation", "softcorp"),
        ("d365", "concept", "D365", "dynamics-365"),
        ("dynamics365", "concept", "Dynamics365", "dynamics-365"),
        ("softcorp-dynamics", "concept", "Softcorp Dynamics", "dynamics-365"),
        ("burger-palace", "organisation", "Burger Palace", "burger-palace-canonical"),
        ("burgerpalace", "organisation", "BurgerPalace", "burger-palace-canonical"),
        ("genesys", "concept", "Genesys", "genesys-cloud"),
        ("pva", "concept", "Power Virtual Agents", "copilot-studio"),
        ("3-cubes", "organisation", "3 Cubes", "triad-consulting"),
    ]
    for aid, atype, aname, cid in aliases:
        db.execute(
            """
            INSERT OR IGNORE INTO entities
                (id, type, name, status, markdown_path, agent_scope, created_at, updated_at, canonical_id)
            VALUES (?, ?, ?, 'active', ?, 'shared', ?, ?, ?)
            """,
            (aid, atype, aname, f"06-Entities/{atype}/{aid}.md", now, now, cid),
        )

    db.commit()


# ---------------------------------------------------------------------------
# Core acceptance criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_sme_amp_c_returns_smec(db):
    """resolve_canonical('BWE&C', db) → canonical row with id='bridgewater-engineering'."""
    row = resolve_canonical("BWE&C", db)
    assert row is not None, "Expected canonical row for 'BWE&C'"
    assert row["id"] == "bridgewater-engineering"
    assert row["name"] == "Bridgewater Engineering"


@pytest.mark.unit
def test_resolve_msft_returns_microsoft(db):
    """resolve_canonical('SCFT', db) → canonical row with id='softcorp'."""
    row = resolve_canonical("SCFT", db)
    assert row is not None, "Expected canonical row for 'SCFT'"
    assert row["id"] == "softcorp"


@pytest.mark.unit
def test_resolve_d365_returns_dynamics_365(db):
    """resolve_canonical('D365', db) → canonical row with id='dynamics-365'."""
    row = resolve_canonical("D365", db)
    assert row is not None, "Expected canonical row for 'D365'"
    assert row["id"] == "dynamics-365"
    assert row["name"] == "Dynamics 365"


@pytest.mark.unit
def test_resolve_canonical_entity_returns_itself(db):
    """resolve_canonical('Bridgewater Engineering', db) → canonical row with id='bridgewater-engineering'."""
    row = resolve_canonical("Bridgewater Engineering", db)
    assert row is not None
    assert row["id"] == "bridgewater-engineering"
    assert row["canonical_id"] is None


@pytest.mark.unit
def test_resolve_microsoft_returns_microsoft(db):
    """resolve_canonical('Softcorp', db) → canonical row with id='softcorp'."""
    row = resolve_canonical("Softcorp", db)
    assert row is not None
    assert row["id"] == "softcorp"
    assert row["canonical_id"] is None


@pytest.mark.unit
def test_resolve_unknown_returns_none(db):
    """resolve_canonical('UnknownXYZ', db) → None."""
    row = resolve_canonical("UnknownXYZ", db)
    assert row is None


# ---------------------------------------------------------------------------
# Case-insensitive matching
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_case_insensitive_canonical(db):
    """resolve_canonical('msft', db) → canonical row for 'softcorp' (case-insensitive alias)."""
    row = resolve_canonical("scft", db)
    assert row is not None, "Expected canonical row for lowercase 'msft'"
    assert row["id"] == "softcorp"


@pytest.mark.unit
def test_resolve_case_insensitive_canonical_name(db):
    """resolve_canonical('triad consulting', db) → canonical row (case-insensitive name match)."""
    row = resolve_canonical("triad consulting", db)
    assert row is not None
    assert row["id"] == "triad-consulting"


@pytest.mark.unit
def test_resolve_case_insensitive_alias(db):
    """resolve_canonical('bwe-c', db) → canonical row for 'bridgewater-engineering'."""
    row = resolve_canonical("bwe-c", db)
    assert row is not None
    assert row["id"] == "bridgewater-engineering"


# ---------------------------------------------------------------------------
# All 7 alias groups covered
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_sme_c_alias(db):
    """BWE-C → smec."""
    row = resolve_canonical("BWE-C", db)
    assert row is not None and row["id"] == "bridgewater-engineering"


@pytest.mark.unit
def test_resolve_microsoft_corp_alias(db):
    """Softcorp Corporation → microsoft."""
    row = resolve_canonical("Softcorp Corporation", db)
    assert row is not None and row["id"] == "softcorp"


@pytest.mark.unit
def test_resolve_dynamics365_alias(db):
    """Dynamics365 → dynamics-365."""
    row = resolve_canonical("Dynamics365", db)
    assert row is not None and row["id"] == "dynamics-365"


@pytest.mark.unit
def test_resolve_microsoft_dynamics_alias(db):
    """Softcorp Dynamics → dynamics-365."""
    row = resolve_canonical("Softcorp Dynamics", db)
    assert row is not None and row["id"] == "dynamics-365"


@pytest.mark.unit
def test_resolve_hungry_jacks_alias(db):
    """Burger Palace → burger-palace-canonical."""
    row = resolve_canonical("Burger Palace", db)
    assert row is not None and row["id"] == "burger-palace-canonical"
    assert row["name"] == "Burger Palace"


@pytest.mark.unit
def test_resolve_burgerpalace_alias(db):
    """BurgerPalace → burger-palace-canonical."""
    row = resolve_canonical("BurgerPalace", db)
    assert row is not None and row["id"] == "burger-palace-canonical"


@pytest.mark.unit
def test_resolve_genesys_alias(db):
    """Genesys → genesys-cloud."""
    row = resolve_canonical("Genesys", db)
    assert row is not None and row["id"] == "genesys-cloud"
    assert row["name"] == "Genesys Cloud"


@pytest.mark.unit
def test_resolve_pva_alias(db):
    """Power Virtual Agents → copilot-studio."""
    row = resolve_canonical("Power Virtual Agents", db)
    assert row is not None and row["id"] == "copilot-studio"
    assert row["name"] == "Copilot Studio"


@pytest.mark.unit
def test_resolve_3_cubes_alias(db):
    """3 Cubes → triad-consulting."""
    row = resolve_canonical("3 Cubes", db)
    assert row is not None and row["id"] == "triad-consulting"
    assert row["name"] == "Triad Consulting"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_empty_string_returns_none(db):
    """Empty string should return None."""
    row = resolve_canonical("", db)
    assert row is None


@pytest.mark.unit
def test_resolve_empty_db(tmp_path, monkeypatch):
    """resolve_canonical on an empty DB returns None."""
    db_path = str(tmp_path / "empty.db")
    monkeypatch.setenv("MNEMOSYNE_TEST_DB", db_path)
    conn = open_entities_db()
    row = resolve_canonical("Anything", conn)
    assert row is None
    conn.close()


@pytest.mark.unit
def test_resolve_returns_dict(db):
    """resolve_canonical should return a dict (not sqlite3.Row)."""
    row = resolve_canonical("Bridgewater Engineering", db)
    assert isinstance(row, dict)
    assert "id" in row
    assert "name" in row


@pytest.mark.unit
def test_resolve_canonical_row_has_no_canonical_id(db):
    """Returned canonical row should always have canonical_id=None."""
    for alias in ["BWE&C", "BWE-C", "SCFT", "D365"]:
        row = resolve_canonical(alias, db)
        assert row is not None, f"Expected row for alias '{alias}'"
        assert row["canonical_id"] is None, (
            f"Canonical row for '{alias}' should have canonical_id=None, got {row['canonical_id']}"
        )
