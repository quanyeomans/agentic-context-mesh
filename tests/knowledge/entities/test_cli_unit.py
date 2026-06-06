"""Additional unit tests for entities CLI lifting coverage above 90%.

The existing ``test_cli.py`` covers formatters and individual cmd_*
functions in isolation. This module fills the remaining gaps:

  - The ``cmd_seed`` happy path with candidates + Neo4j seeding.
  - The ``cmd_seed`` default db_path resolution branch.
  - The ``main()`` dispatcher's suggest / validate / get branches.

All tests construct fakes via the *Deps dataclasses in
``kairix.knowledge.entities.cli`` / ``kairix.use_cases.entity`` /
``kairix.use_cases.entity_get`` and pass them through ``deps=``. F1
forbids ``monkeypatch.setattr`` on kairix internals.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

import kairix.knowledge.entities.cli as entities_cli
from kairix.core.health import HealthDeps
from kairix.knowledge.entities.cli import EntitySeedDeps
from kairix.use_cases.entity import EntitySuggestDeps, EntityValidateDeps
from kairix.use_cases.entity_get import EntityGetDeps

pytestmark = pytest.mark.unit


def _capture(fn: Any) -> tuple[int, str, str]:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = int(fn())
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    return rc, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# cmd_seed — happy path + branches not covered by test_seed_cli.py
# ---------------------------------------------------------------------------


class _AvailableNeo:
    available = True


class _UnavailableNeo:
    available = False


def _populated_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE documents (id INTEGER, path TEXT, title TEXT, active INTEGER)")
    db.execute("INSERT INTO documents VALUES (1, 'a.md', 'A', 1)")
    db.commit()
    db.close()


def _fake_candidate(name: str = "Entity") -> Any:
    """Build a candidate-shaped object without depending on the prod dataclass."""
    from types import SimpleNamespace

    return SimpleNamespace(
        entity_type="ORG",
        name=name,
        confidence=0.9,
        source_docs=["a.md"],
    )


def test_seed_with_candidates_lists_and_seeds_neo4j(tmp_path: Path) -> None:
    """seed with non-empty candidate list + available Neo4j → seed_graph called."""
    db_path = tmp_path / "index.sqlite"
    _populated_db(db_path)

    fake_candidates = [_fake_candidate(f"Entity{i:02d}") for i in range(25)]
    seed_calls: list[int] = []

    def _fake_seed_graph(_neo: Any, candidates: list[Any]) -> int:
        seed_calls.append(len(candidates))
        return len(candidates)

    deps = EntitySeedDeps(
        scan_for_entities=lambda _db, _limit: fake_candidates,
        seed_graph=_fake_seed_graph,
    )

    rc = entities_cli.main(
        ["seed"],
        db_path=db_path,
        neo4j_client=_AvailableNeo(),
        seed_deps=deps,
    )
    assert rc == 0
    assert seed_calls == [25]


def test_seed_with_candidates_dry_run_does_not_call_seed_graph(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _populated_db(db_path)

    called: list[str] = []

    def _record(_neo: Any, _cands: list[Any]) -> int:
        called.append("yes")
        return 0

    deps = EntitySeedDeps(
        scan_for_entities=lambda _db, _limit: [_fake_candidate()],
        seed_graph=_record,
    )

    rc = entities_cli.main(
        ["seed", "--dry-run"],
        db_path=db_path,
        neo4j_client=_AvailableNeo(),
        seed_deps=deps,
    )
    assert rc == 0
    assert called == [], "seed_graph must not be called in --dry-run mode"


def test_seed_exits_1_when_neo4j_unavailable(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _populated_db(db_path)

    deps = EntitySeedDeps(
        scan_for_entities=lambda _db, _limit: [_fake_candidate()],
    )

    rc = entities_cli.main(
        ["seed"],
        db_path=db_path,
        neo4j_client=_UnavailableNeo(),
        seed_deps=deps,
    )
    assert rc == 1


def test_seed_default_db_path_resolves_via_deps(tmp_path: Path) -> None:
    """When db_path is None, cmd_seed calls deps.get_db_path() to resolve it."""
    real_db = tmp_path / "k.db"
    _populated_db(real_db)

    deps = EntitySeedDeps(
        scan_for_entities=lambda _db, _limit: [],
        get_db_path=lambda: str(real_db),
    )

    # neo4j_client unused because no candidates.
    rc = entities_cli.main(["seed"], seed_deps=deps)
    assert rc == 0


def test_seed_default_neo4j_client_resolves_via_deps(tmp_path: Path) -> None:
    """When neo4j_client is None, cmd_seed calls deps.get_neo4j_client()."""
    db_path = tmp_path / "index.sqlite"
    _populated_db(db_path)

    seed_calls: list[int] = []

    def _record(_neo: Any, cands: list[Any]) -> int:
        seed_calls.append(len(cands))
        return 1

    deps = EntitySeedDeps(
        scan_for_entities=lambda _db, _limit: [_fake_candidate()],
        seed_graph=_record,
        get_neo4j_client=lambda: _AvailableNeo(),
    )

    rc = entities_cli.main(["seed"], db_path=db_path, seed_deps=deps)
    assert rc == 0
    assert seed_calls == [1]


# ---------------------------------------------------------------------------
# Dispatcher branches — exercise cmd_suggest / cmd_validate / cmd_get
# directly with their canonical *Deps so the test does not bypass the
# production handler, but also avoids the dispatcher's ``deps=None`` shortcut.
# ---------------------------------------------------------------------------


def test_cmd_suggest_happy_path_uses_injected_deps() -> None:
    """cmd_suggest formats the EntitySuggestOutput produced via injected deps."""
    args = argparse.Namespace(text="acme is a client", file=None, format="table")

    class _FakeNeo:
        available = True

    deps = EntitySuggestDeps(
        suggest_fn=lambda _text, _neo: [],  # zero suggestions
        neo4j_client_fn=lambda: _FakeNeo(),
    )

    rc, stdout, _ = _capture(lambda: entities_cli.cmd_suggest(args, deps=deps))
    assert rc == 0
    assert "Total: 0 entities found" in stdout


def test_cmd_validate_no_matches_returns_1() -> None:
    """cmd_validate returns 1 when the use case returns zero matches."""
    args = argparse.Namespace(name="Acme", update=False, format="table")

    deps = EntityValidateDeps(
        validate_fn=lambda _name, _neo, *, update=False: {
            "name": "Acme",
            "neo4j_id": "acme",
            "matches": [],
            "updated": False,
            "error": "",
        },
        neo4j_client_fn=lambda: object(),
    )

    rc, _stdout, _ = _capture(lambda: entities_cli.cmd_validate(args, deps=deps))
    assert rc == 1


def test_cmd_get_renders_table_when_found() -> None:
    """cmd_get returns 0 and renders the entity name when the lookup succeeds."""
    args = argparse.Namespace(name="Acme", format="table")

    health_deps = HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )
    deps = EntityGetDeps(
        fetch_fn=lambda _name: {
            "id": "acme",
            "name": "Acme",
            "type": "Organisation",
            "summary": "supplier",
            "vault_path": "/Acme.md",
        },
        health_deps=health_deps,
    )

    rc, stdout, _ = _capture(lambda: entities_cli.cmd_get(args, deps=deps))
    assert rc == 0
    assert "Acme" in stdout


# ---------------------------------------------------------------------------
# EntitySeedDeps — exercise the production defaults so the dataclass's
# lazy-import shim functions are covered. Each default just dispatches to
# the live helper; the test pins that the dispatch returns the expected
# type / forwards args, not the underlying helper's contract.
# ---------------------------------------------------------------------------


def test_entity_seed_deps_default_scan_for_entities_returns_list(tmp_path: Path) -> None:
    """The default scan helper accepts (db, limit) and returns a list."""
    db_path = tmp_path / "k.db"
    _populated_db(db_path)
    from kairix.core.db import open_db

    deps = EntitySeedDeps()
    db = open_db(db_path)
    try:
        out = deps.scan_for_entities(db, 1)
    finally:
        db.close()
    assert isinstance(out, list)


def test_entity_seed_deps_default_get_db_path_returns_path_or_str() -> None:
    """The default db-path resolver returns a Path / str (real helper output)."""
    deps = EntitySeedDeps()
    out = deps.get_db_path()
    # get_db_path may return a Path; just confirm str(out) is non-empty
    assert str(out)


def test_entity_seed_deps_default_get_neo4j_client_returns_object() -> None:
    """The default Neo4j client resolver returns *something* (real client or null)."""
    deps = EntitySeedDeps()
    client = deps.get_neo4j_client()
    # client object exists; .available may be True or False — both are fine.
    assert client is not None
    assert hasattr(client, "available")


def test_entity_seed_deps_default_seed_graph_is_callable() -> None:
    """seed_graph default is the production helper (callable, not None)."""
    deps = EntitySeedDeps()
    assert callable(deps.seed_graph)
