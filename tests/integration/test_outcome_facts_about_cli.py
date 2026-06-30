"""F30 outcome test — ``kairix facts-about`` subprocess surface (PLA-263).

Proves the binary surface operators (and agents shelling out) script
against: spawn ``python -m kairix.cli facts-about <entity> --json`` over a
seeded tmp index, then assert the emitted envelope carries the entity's
fact AND its indexed entity summary. This is the composed production path
(subprocess → kairix.cli → facts_about_cli → tool_facts_about → SQLite
fact store + entity-summaries collection), not an internal call-count.

F2-clean: no ``KAIRIX_*`` env vars in the subprocess invocation — the
``--db-path`` / ``--document-root`` flags are the hermetic seam.

Sabotage-proof anchor: replacing the ``out_sink.write(json.dumps(...))``
render in facts_about_cli.main with ``pass`` makes the json.loads of empty
stdout fail. Re-adding ``@warm_gate`` to the shared tool path does not
affect the CLI (the CLI never gates) — the cold-serve guarantee is pinned
in test_mcp_cold_start.py.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.connectors.collection_router import legacy_chunk_writer
from kairix.core.db.schema import create_schema
from kairix.core.facts import SQLiteFactStore
from kairix.knowledge.entities.summary_projector import build_entity_summary_chunk, hash_summary
from tests.fakes import FakeFactRecord

pytestmark = pytest.mark.integration

_SUMMARY = "Acme Corp is a fictional manufacturing company."


def _seed(db_path: Path) -> None:
    """Seed a tmp index with one fact + one entity-summary chunk."""
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        writer = legacy_chunk_writer(db, collection="entity-summaries")
        writer.upsert(
            [
                build_entity_summary_chunk(
                    summary=_SUMMARY,
                    qid="Q-acme",
                    name="Acme Corp",
                    tick_iso="2026-06-30T00:00:00Z",
                    content_hash=hash_summary(_SUMMARY),
                )
            ]
        )
        db.commit()
    finally:
        db.close()
    SQLiteFactStore(db_path=db_path).add(
        FakeFactRecord(id="f-acme", entity="Acme Corp", attribute="industry", value="manufacturing")
    )


def test_facts_about_cli_subprocess_surfaces_fact_and_summary(tmp_path: Path) -> None:
    """Drive ``kairix facts-about 'Acme Corp' --json`` over a seeded index;
    assert the envelope on stdout carries the fact value AND the summary.

    This is the F30 contract: subprocess + stdout-envelope assertion (not
    returncode alone, not internal call-counts).
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "facts-about",
            "Acme Corp",
            "--json",
            "--db-path",
            str(db_path),
            "--document-root",
            str(tmp_path / "vault"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.stdout, f"empty stdout — subprocess crashed before render. stderr={proc.stderr!r}"
    envelope = json.loads(proc.stdout)
    assert envelope["error"] == "", f"unexpected error envelope: {envelope!r}"
    assert envelope["entity"] == "Acme Corp"
    assert [h["value"] for h in envelope["hits"]] == ["manufacturing"]
    summaries = [s["summary"] for s in envelope["entity_summaries"]]
    assert any("manufacturing company" in s for s in summaries), f"entity summary missing: {summaries!r}"
    assert proc.returncode == 0


def test_facts_about_cli_subprocess_unknown_entity_emits_clean_envelope(tmp_path: Path) -> None:
    """An unknown entity still emits a parseable, error-free envelope on stdout.

    Pins the operator-facing "no facts is not a failure" contract through
    the binary surface.
    """
    db_path = tmp_path / "index.sqlite"
    _seed(db_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "facts-about",
            "Nobody Knows This Entity",
            "--json",
            "--db-path",
            str(db_path),
            "--document-root",
            str(tmp_path / "vault"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.stdout, f"empty stdout. stderr={proc.stderr!r}"
    envelope = json.loads(proc.stdout)
    assert envelope["error"] == ""
    assert envelope["hits"] == []
    assert proc.returncode == 0
