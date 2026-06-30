"""Step definitions for cli_facts_about.feature.

Drives the ``kairix facts-about`` CLI in-process via
:func:`kairix.agents.mcp.tools.facts_about_cli.main` (F46 — composes
through the CLI surface, not a direct pipeline). The fact + entity-summary
are seeded into a tmp SQLite index; ``--db-path`` / ``--document-root``
point the command at it (F2-clean — no process-env mutation).
"""

from __future__ import annotations

import io
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.tools import facts_about_cli
from kairix.core.connectors.collection_router import legacy_chunk_writer
from kairix.core.db.schema import create_schema
from kairix.core.facts import SQLiteFactStore
from kairix.knowledge.entities.summary_projector import build_entity_summary_chunk, hash_summary
from tests.fakes import FakeFactRecord

pytestmark = pytest.mark.bdd

_ENTITY_SUMMARIES = "entity-summaries"


@dataclass
class _CliState:
    db_path: Path
    document_root: Path
    envelope: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0


@pytest.fixture
def _cli_state(tmp_path: Path) -> _CliState:
    """Per-scenario tmp knowledge store with the documents schema laid down.

    The documents schema (incl. ``documents_fts``) is created up front so
    the entity-summaries leg reads cleanly; the fact schema is created on
    the first ``SQLiteFactStore.add``.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        db.commit()
    finally:
        db.close()
    return _CliState(db_path=db_path, document_root=tmp_path / "vault")


@given(parsers.parse('the knowledge store holds a fact that "{entity}" has industry "{value}"'))
def _given_fact(_cli_state: _CliState, entity: str, value: str) -> None:
    store = SQLiteFactStore(db_path=_cli_state.db_path)
    store.add(FakeFactRecord(id=f"f-{entity}", entity=entity, attribute="industry", value=value))


@given(parsers.parse('the knowledge store holds an entity summary for "{entity}" that reads "{summary}"'))
def _given_summary(_cli_state: _CliState, entity: str, summary: str) -> None:
    db = sqlite3.connect(str(_cli_state.db_path), timeout=10.0)
    try:
        writer = legacy_chunk_writer(db, collection=_ENTITY_SUMMARIES)
        writer.upsert(
            [
                build_entity_summary_chunk(
                    summary=summary,
                    qid=f"Q-{entity}",
                    name=entity,
                    tick_iso="2026-06-30T00:00:00Z",
                    content_hash=hash_summary(summary),
                )
            ]
        )
        db.commit()
    finally:
        db.close()


@when(parsers.parse('the operator runs facts-about for "{entity}"'))
def _when_run(_cli_state: _CliState, entity: str) -> None:
    out = io.StringIO()
    _cli_state.exit_code = facts_about_cli.main(
        [
            entity,
            "--json",
            "--db-path",
            str(_cli_state.db_path),
            "--document-root",
            str(_cli_state.document_root),
        ],
        out=out,
        err=io.StringIO(),
    )
    _cli_state.envelope = json.loads(out.getvalue())


@then("the facts-about command succeeds")
def _then_success(_cli_state: _CliState) -> None:
    assert _cli_state.exit_code == 0
    assert _cli_state.envelope["error"] == ""


@then(parsers.parse('the facts-about output reports "{needle}"'))
def _then_reports(_cli_state: _CliState, needle: str) -> None:
    blob = json.dumps(_cli_state.envelope)
    assert needle in blob, f"expected {needle!r} in the facts-about output; got {blob!r}"


@then("the facts-about output reports no facts")
def _then_no_facts(_cli_state: _CliState) -> None:
    assert _cli_state.envelope["hits"] == []
