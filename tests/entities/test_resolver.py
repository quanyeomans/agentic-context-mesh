"""
Tests for kairix.entities.resolver — resolve_canonical().

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

from kairix.entities.resolver import resolve_canonical
from kairix.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh entities DB backed by a temp file, pre-seeded with test data."""
    db_path = str(tmp_path / "test_resolver.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)
    conn = open_entities_db()
    conn.row_factory = sqlite3.Row
    _seed_test_db(conn)
    yield conn
    conn.close()


def _seed_test_db(db: sqlite3.Connection) -> None:
    """Insert canonical entities and alias rows for testing."""
    now = "2026-01-01T00:00:00Z"

    canonicals = [
        ("delta-co", "concept", "Delta Co"),
        ("softcorp", "organisation", "Softcorp"),
        ("delta-suite", "concept", "Delta Suite"),
        ("gamma-systems-canonical", "organisation", "Gamma Systems"),
        ("genesys-cloud", "concept", "Genesys Cloud"),
        ("studio-pro", "concept", "Studio Pro"),
        ("acme-corp", "organisation", "Acme Corp"),
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
        ("bwe-c", "concept", "BWE-C", "delta-co"),
        ("bwe-amp-c", "concept", "BWE&C", "delta-co"),
        ("scft", "organisation", "SCFT", "softcorp"),
        ("softcorp-corp", "organisation", "Softcorp Corporation", "softcorp"),
        ("d365", "concept", "D365", "delta-suite"),
        ("deltasuite", "concept", "DeltaSuite", "delta-suite"),
        ("delta-suite-alt", "concept", "Delta Suite Alt", "delta-suite"),
        ("gamma-systems", "organisation", "Gamma Systems", "gamma-systems-canonical"),
        ("burgerpalace", "organisation", "BurgerPalace", "gamma-systems-canonical"),
        ("genesys", "concept", "Genesys", "genesys-cloud"),
        ("pva", "concept", "Power Virtual Agents", "studio-pro"),
        ("3-cubes", "organisation", "3 Cubes", "acme-corp"),
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
    """resolve_canonical('BWE&C', db) → canonical row with id='delta-co'."""
    row = resolve_canonical("BWE&C", db)
    assert row is not None, "Expected canonical row for 'BWE&C'"
    assert row["id"] == "delta-co"
    assert row["name"] == "Delta Co"


@pytest.mark.unit
def test_resolve_msft_returns_microsoft(db):
    """resolve_canonical('SCFT', db) → canonical row with id='softcorp'."""
    row = resolve_canonical("SCFT", db)
    assert row is not None, "Expected canonical row for 'SCFT'"
    assert row["id"] == "softcorp"


@pytest.mark.unit
def test_resolve_d365_returns_dynamics_365(db):
    """resolve_canonical('D365', db) → canonical row with id='delta-suite'."""
    row = resolve_canonical("D365", db)
    assert row is not None, "Expected canonical row for 'D365'"
    assert row["id"] == "delta-suite"
    assert row["name"] == "Delta Suite"


@pytest.mark.unit
def test_resolve_canonical_entity_returns_itself(db):
    """resolve_canonical('Delta Co', db) → canonical row with id='delta-co'."""
    row = resolve_canonical("Delta Co", db)
    assert row is not None
    assert row["id"] == "delta-co"
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
    """resolve_canonical('3 cubes', db) → canonical row (case-insensitive alias match)."""
    row = resolve_canonical("3 cubes", db)
    assert row is not None
    assert row["id"] == "acme-corp"


@pytest.mark.unit
def test_resolve_case_insensitive_alias(db):
    """resolve_canonical('bwe-c', db) → canonical row for 'delta-co'."""
    row = resolve_canonical("bwe-c", db)
    assert row is not None
    assert row["id"] == "delta-co"


# ---------------------------------------------------------------------------
# All 7 alias groups covered
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_sme_c_alias(db):
    """BWE-C → smec."""
    row = resolve_canonical("BWE-C", db)
    assert row is not None and row["id"] == "delta-co"


@pytest.mark.unit
def test_resolve_microsoft_corp_alias(db):
    """Softcorp Corporation → microsoft."""
    row = resolve_canonical("Softcorp Corporation", db)
    assert row is not None and row["id"] == "softcorp"


@pytest.mark.unit
def test_resolve_delta_suite_alias(db):
    """DeltaSuite → delta-suite."""
    row = resolve_canonical("DeltaSuite", db)
    assert row is not None and row["id"] == "delta-suite"


@pytest.mark.unit
def test_resolve_microsoft_dynamics_alias(db):
    """Delta Suite Alt → delta-suite."""
    row = resolve_canonical("Delta Suite Alt", db)
    assert row is not None and row["id"] == "delta-suite"


@pytest.mark.unit
def test_resolve_hungry_jacks_alias(db):
    """Gamma Systems → gamma-systems-canonical."""
    row = resolve_canonical("Gamma Systems", db)
    assert row is not None and row["id"] == "gamma-systems-canonical"
    assert row["name"] == "Gamma Systems"


@pytest.mark.unit
def test_resolve_burgerpalace_alias(db):
    """BurgerPalace → gamma-systems-canonical."""
    row = resolve_canonical("BurgerPalace", db)
    assert row is not None and row["id"] == "gamma-systems-canonical"


@pytest.mark.unit
def test_resolve_genesys_alias(db):
    """Genesys → genesys-cloud."""
    row = resolve_canonical("Genesys", db)
    assert row is not None and row["id"] == "genesys-cloud"
    assert row["name"] == "Genesys Cloud"


@pytest.mark.unit
def test_resolve_pva_alias(db):
    """Power Virtual Agents → studio-pro."""
    row = resolve_canonical("Power Virtual Agents", db)
    assert row is not None and row["id"] == "studio-pro"
    assert row["name"] == "Studio Pro"


@pytest.mark.unit
def test_resolve_3_cubes_alias(db):
    """3 Cubes → acme-corp."""
    row = resolve_canonical("3 Cubes", db)
    assert row is not None and row["id"] == "acme-corp"
    assert row["name"] == "Acme Corp"


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
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)
    conn = open_entities_db()
    row = resolve_canonical("Anything", conn)
    assert row is None
    conn.close()


@pytest.mark.unit
def test_resolve_returns_dict(db):
    """resolve_canonical should return a dict (not sqlite3.Row)."""
    row = resolve_canonical("Delta Co", db)
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
