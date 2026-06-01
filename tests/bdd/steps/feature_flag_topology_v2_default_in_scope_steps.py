"""Step definitions for feature_flag_topology_v2_default_in_scope.feature (GH #373).

Scaffolding ahead of impl: the @when steps xfail their scenarios with
``pytest.xfail(...)`` carrying a concrete reason. The implementation
agent removes the xfail calls inline when the production wiring is in
place.

F1-clean: no monkeypatch of internals.
F2-clean: no env-var manipulation.
F46-compliant: composition runs through
:func:`kairix.core.factory.build_collection_resolver` with the
:class:`FakeFeatureFlagResolver` injected via the ``flag_reader=`` DI
seam — no direct construction of underscored internals.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_collection_resolver
from kairix.core.search.scope import Scope
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_XFAIL_REASON = "impl pending — #373 / feature-flag topology_v2_default_in_scope"


@pytest.fixture
def _ff_v2_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "tmp_path": tmp_path,
        "db_path": None,
        "flag_value": False,
        "result": None,
    }


def _seed(tmp_path: Path) -> Path:
    """Seed agent 'shape' with 7 in-default + 1 opt-in scope entries."""
    db_path = tmp_path / "ff_index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("shape", now, now),
    )
    profile_id = cur.lastrowid
    cols = {row[1] for row in db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
    has_default_col = "default_in_scope" in cols
    entries = [
        ("sharepoint", 1, 0, "internal", 1),
        ("obsidian", 1, 0, "internal", 1),
        ("slack", 1, 0, "personal", 1),
        ("email", 1, 0, "personal", 1),
        ("calendar", 1, 0, "personal", 1),
        ("github", 1, 0, "confidential", 1),
        ("shape-memory", 1, 1, "personal", 1),
        ("reflib", 1, 0, "public", 0),
    ]
    for name, can_read, can_write, max_sens, default_in_scope in entries:
        if has_default_col:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, "
                "max_sensitivity, default_in_scope) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens, default_in_scope),
            )
        else:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens),
            )
    db.commit()
    db.close()
    return db_path


@given(parsers.parse('a scope profile with 7 in-default and 1 opt-in scope entries for agent "{agent}"'))
def _seed_scope(_ff_v2_state: dict[str, Any], agent: str) -> None:
    del agent
    _ff_v2_state["db_path"] = _seed(_ff_v2_state["tmp_path"])


@given(parsers.parse("the operator has the topology-v2-default-in-scope flag set to {value}"))
def _flag_value(_ff_v2_state: dict[str, Any], value: str) -> None:
    _ff_v2_state["flag_value"] = value.lower() == "true"


@when(parsers.parse('the operator resolves the default collection list for agent "{agent}"'))
def _resolve_default(_ff_v2_state: dict[str, Any], agent: str) -> None:
    """Drive the resolver via build_collection_resolver (F46 / F47).

    The factory wires the FakeFeatureFlagResolver's .get method through
    ``flag_reader=`` — production-shape composition path.

    Scaffold: until the production wiring honours the flag the assertions
    will degrade, so xfail the scenario.
    """
    flags = (
        FakeFeatureFlagResolver()
        .with_flag("topology_v2_collection_resolver", True)
        .with_flag("topology_v2_default_in_scope", _ff_v2_state["flag_value"])
    )
    resolver = build_collection_resolver(db_path=_ff_v2_state["db_path"], flag_reader=flags.get)
    try:
        _ff_v2_state["result"] = resolver.resolve(agent=agent, scope=Scope.SHARED_AGENT)
    except Exception:
        pytest.xfail(_XFAIL_REASON)
    # Until the flag actually filters, the on-branch return won't match expected.
    if _ff_v2_state["flag_value"]:
        pytest.xfail(_XFAIL_REASON)


@then("every scope-eligible collection name is returned, including the opt-in collection")
def _every_name_returned(_ff_v2_state: dict[str, Any]) -> None:
    result = _ff_v2_state["result"]
    assert result is not None
    expected = {
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
        "reflib",
    }
    assert set(result) == expected, f"flag OFF must surface every read-eligible name; got {set(result)!r}"


@then("only the 7 in-default collection names are returned")
def _seven_in_default_returned(_ff_v2_state: dict[str, Any]) -> None:
    result = _ff_v2_state["result"]
    assert result is not None
    expected = {
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
    }
    assert set(result) == expected, f"flag ON must filter to the 7 in-default names; got {set(result)!r}"


@then("the opt-in collection name is not in the result")
def _opt_in_excluded(_ff_v2_state: dict[str, Any]) -> None:
    result = _ff_v2_state["result"] or []
    assert "reflib" not in result, f"opt-in 'reflib' leaked into default search: {result!r}"
