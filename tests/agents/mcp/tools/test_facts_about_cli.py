"""Unit tests for the ``kairix facts-about`` CLI (``facts_about_cli.main``).

Drives ``main`` in-process against a tmp SQLite index seeded with a fact
and an entity summary, asserting on the rendered output (JSON + human).
The CLI is a thin shim over ``tool_facts_about`` — the tool's behaviour is
pinned in ``test_facts_about_tool.py``; here we pin the binary surface:
argument parsing, path-override wiring, output rendering, and exit codes.

F2-clean: the ``--db-path`` / ``--document-root`` flags point the command
at a tmp store; no ``KAIRIX_*`` env vars are set.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

import kairix.agents.mcp.tools.facts_about_cli as facts_about_cli
from kairix.core.connectors.collection_router import legacy_chunk_writer
from kairix.core.db.schema import create_schema
from kairix.core.facts import SQLiteFactStore
from kairix.knowledge.entities.summary_projector import build_entity_summary_chunk, hash_summary
from tests.fakes import FakeFactRecord

pytestmark = pytest.mark.unit

_ENTITY_SUMMARIES = "entity-summaries"


def _seed(db_path: Path, *, with_fact: bool, with_summary: bool) -> None:
    """Lay down the documents schema and (optionally) seed a fact + summary."""
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        if with_summary:
            writer = legacy_chunk_writer(db, collection=_ENTITY_SUMMARIES)
            writer.upsert(
                [
                    build_entity_summary_chunk(
                        summary="Acme Corp is a fictional manufacturing company.",
                        qid="Q-acme",
                        name="Acme Corp",
                        tick_iso="2026-06-30T00:00:00Z",
                        content_hash=hash_summary("Acme Corp is a fictional manufacturing company."),
                    )
                ]
            )
        db.commit()
    finally:
        db.close()
    if with_fact:
        SQLiteFactStore(db_path=db_path).add(
            FakeFactRecord(id="f-acme", entity="Acme Corp", attribute="industry", value="manufacturing")
        )


def _run(db_path: Path, doc_root: Path, *args: str) -> tuple[int, str, str]:
    """Invoke ``facts_about_cli.main`` in-process; return (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    rc = facts_about_cli.main(
        ["--db-path", str(db_path), "--document-root", str(doc_root), *args],
        out=out,
        err=err,
    )
    return rc, out.getvalue(), err.getvalue()


def test_cli_json_mode_surfaces_fact_and_summary(tmp_path: Path) -> None:
    """``--json`` emits the envelope with both the fact AND the entity summary.

    Sabotage: drop the ``--db-path`` wiring in ``_resolve_paths`` (so the
    override is ignored) → the command reads the wrong store, the seeded
    fact/summary are absent, and these assertions fail. Mutate-confirmed by
    deleting the ``overrides["db_path"]`` line.
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, with_fact=True, with_summary=True)

    rc, stdout, _ = _run(db_path, tmp_path / "vault", "Acme Corp", "--json")

    assert rc == 0
    envelope = json.loads(stdout)
    assert envelope["error"] == ""
    assert [h["value"] for h in envelope["hits"]] == ["manufacturing"]
    summaries = [s["summary"] for s in envelope["entity_summaries"]]
    assert any("manufacturing company" in s for s in summaries)


def test_cli_text_mode_renders_human_summary(tmp_path: Path) -> None:
    """Default (non-JSON) mode renders a human-readable summary block.

    Sabotage: make ``_format_human`` return ``""`` → the value/summary
    tokens vanish from stdout and the assertions below fail.
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, with_fact=True, with_summary=True)

    rc, stdout, _ = _run(db_path, tmp_path / "vault", "Acme Corp")

    assert rc == 0
    assert "kairix facts-about: Acme Corp" in stdout
    assert "industry: manufacturing" in stdout
    assert "manufacturing company" in stdout
    # The seeded fact carries namespace "shared"; the human line renders it.
    # Sabotage: flip ``hit.get("namespace") or "—"`` to ``and`` in
    # _format_human → a truthy namespace collapses to "—" and this fails.
    assert "ns shared" in stdout


def test_cli_top_k_flag_is_forwarded(tmp_path: Path) -> None:
    """``--top-k`` is parsed and forwarded so the leg bounds are honoured.

    Sabotage: hard-code ``top_k=20`` in ``main`` instead of ``args.top_k``
    → an arbitrary value still works here, so we assert the parsed namespace
    value reaches the envelope echo (``top_k`` round-trips). Mutate-confirmed
    by replacing ``top_k=args.top_k`` with ``top_k=20``.
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, with_fact=True, with_summary=False)

    rc, stdout, _ = _run(db_path, tmp_path / "vault", "Acme Corp", "--top-k", "3", "--json")

    assert rc == 0
    assert json.loads(stdout)["top_k"] == 3


def test_cli_unknown_entity_succeeds_with_no_facts(tmp_path: Path) -> None:
    """A lookup for an unknown entity exits 0 with empty hits + summaries.

    Sabotage: make ``main`` return 1 when ``hits`` is empty → this exit-code
    assertion fails (the contract is "no facts" is not an error).
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, with_fact=True, with_summary=True)

    rc, stdout, _ = _run(db_path, tmp_path / "vault", "Nonexistent Co", "--json")

    assert rc == 0
    envelope = json.loads(stdout)
    assert envelope["error"] == ""
    assert envelope["hits"] == []
    assert envelope["entity_summaries"] == []


def test_cli_empty_entity_returns_error_exit_one(tmp_path: Path) -> None:
    """An empty entity is rejected with a non-zero exit and an error line.

    Drives the error-return + ``_format_human`` error branch + the stderr
    write.

    Sabotage: change ``main`` to always ``return 0`` → the rc assertion
    fails; remove the ``err_sink.write`` → the stderr assertion fails.
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, with_fact=False, with_summary=False)

    rc, stdout, stderr = _run(db_path, tmp_path / "vault", "")

    assert rc == 1
    assert "InvalidInput" in stdout
    assert "facts-about" in stderr
