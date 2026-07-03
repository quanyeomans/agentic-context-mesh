"""Tests for ``run_reextract_dead_letter`` — operator-driven recovery of
dead-lettered items after a fixed extractor or new converter library ships.

Drives the function in-process with a tmp_path-rooted DB + bronze + real
Obsidian connector + passthrough extractor. Each test sabotage-proves by
mutating production code (recorded inline) so the assertion has teeth.

Task 5 (connector canonical-collapse): re-extract now reads the canonical
``topology`` block (parsed from the overlay-aware MERGED mapping, injected
via the ``config_mapping`` seam), NOT the legacy top-level
``connectors:`` list. The connector entry is matched by **cc_pair name**
(the dead_letter ``source_name`` routing key), the plugin resolves via the
connector **kind**, and the extractor flows from the connector's extractor
fields — the same kind/name/config/extractor split Task 4 established for
the live sync path. ``_obsidian_topology_mapping`` builds that shape.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors import DeadLetterStore, StreamingBronzeStore
from kairix.core.db.schema import create_schema
from kairix.worker import ReextractResult, run_reextract_dead_letter
from kairix.worker_cli import main as worker_main
from kairix.worker_cli import reextract

pytestmark = pytest.mark.unit


def _seed_bronze_and_dead_letter(
    *,
    db: sqlite3.Connection,
    bronze_root: Path,
    source_name: str,
    item_id: str,
    raw_path: Path,
    mime: str = "text/markdown",
) -> None:
    """Mirror the on-disk shape FilesystemBronzeStore.write produces: a
    bronze_records row pointing at ``raw_path`` + a dead_letter row that
    re-extract should later clear."""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    bronze = StreamingBronzeStore(db)
    raw_bytes = raw_path.read_bytes()
    bronze.write(source_name, item_id, raw_bytes, mime)
    dead_letter = DeadLetterStore(db)
    dead_letter.record(source_name, item_id, "boot — extractor failed before library hotfix")
    db.commit()


def _obsidian_topology_mapping(
    *,
    vault_root: Path,
    cc_pair_name: str = "obsidian",
    extractor: str = "passthrough",
) -> dict[str, Any]:
    """Build a merged mapping with one obsidian connector + cc_pair.

    Mirrors the canonical ``topology.connectors`` / ``topology.cc_pairs``
    shape the setup wizard writes. The cc_pair ``name`` is the routing key
    re-extract matches against ``source_name`` (and the dead_letter rows are
    keyed on); the connector ``kind`` resolves the plugin; the connector's
    ``extractor`` flows through to the re-extract extractor.
    """
    return {
        "topology": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Obsidian Vault",
                    "extractor": extractor,
                    "connector_specific_config": {"vault_root": str(vault_root)},
                }
            ],
            "cc_pairs": [
                {
                    "id": "obsidian-pair",
                    "connector": "obsidian-conn",
                    "credential": None,
                    "name": cc_pair_name,
                }
            ],
        }
    }


def test_recovers_dead_lettered_item_clears_row_and_writes_chunks(tmp_path: Path) -> None:
    """Happy path — a dead-lettered item with valid bronze + a configured
    connector + a working extractor recovers cleanly.

    Sabotage proof (executed): change ``dead_letter.clear(...)`` in
    ``_reextract_rows`` to a no-op (``pass``) and re-run; the
    ``dead_letter row gone`` assertion fails because the row stays.
    Restored, the test passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nBody content.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"
    _seed_bronze_and_dead_letter(
        db=db,
        bronze_root=bronze_root,
        source_name="obsidian",
        item_id="alpha.md",
        raw_path=note,
    )

    mapping = _obsidian_topology_mapping(vault_root=vault)

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
    )

    assert isinstance(result, ReextractResult)
    assert result.recovered == 1, f"expected recovered=1, got {result}"
    assert result.still_failing == 0
    assert result.skipped_no_bronze == 0
    assert result.skipped_no_connector == 0

    remaining = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        ("obsidian",),
    ).fetchone()[0]
    assert remaining == 0, "dead_letter row should be cleared after recovery"

    doc_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count >= 1, "recovery must have written a document row"

    db.close()


def test_reextract_matches_cc_pair_name_and_resolves_plugin_via_kind(tmp_path: Path) -> None:
    """Task 5 — re-extract reads the canonical topology entry split: it
    matches the connector entry by **cc_pair name** (the dead_letter
    routing key) while resolving the plugin via the connector **kind**.

    The topology here gives the cc_pair a name (``vault-personal``) that is
    DISTINCT from the connector kind (``obsidian``). Recovery succeeds only
    if (1) the entry is matched on the cc_pair name passed as
    ``source_name`` AND (2) the obsidian plugin is resolved via the
    connector's ``kind`` — not via ``source_name`` (which would try to
    resolve a non-existent ``vault-personal`` plugin). The recovered
    documents land in the ``vault-personal`` collection (the cc_pair name),
    proving the routing key flows through.

    Sabotage proof (executed): in ``_build_reextract_components``, change
    ``resolve_connector(entry["kind"])`` back to
    ``resolve_connector(source_name)``; this test fails because
    ``resolve_connector("vault-personal")`` raises (no such plugin) so
    nothing recovers. Restored, it passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nBody content for the split test.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"
    # dead_letter + bronze rows are keyed on the cc_pair name (source_name).
    _seed_bronze_and_dead_letter(
        db=db,
        bronze_root=bronze_root,
        source_name="vault-personal",
        item_id="alpha.md",
        raw_path=note,
    )

    # Connector kind=obsidian, cc_pair name=vault-personal (kind != name).
    mapping = _obsidian_topology_mapping(vault_root=vault, cc_pair_name="vault-personal")

    result = run_reextract_dead_letter(
        source_name="vault-personal",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
    )

    assert result.recovered == 1, f"recovery must match the cc_pair name and resolve the plugin via kind; got {result}"
    assert result.skipped_no_connector == 0

    # Routing key flows through: recovered docs carry the cc_pair-name
    # collection, NOT the connector kind 'obsidian'.
    collections = {
        row[0] for row in db.execute("SELECT DISTINCT collection FROM documents WHERE active = 1").fetchall()
    }
    assert collections == {"vault-personal"}, (
        f"recovered docs must land in the cc_pair-name collection 'vault-personal' "
        f"(the routing key), not the connector kind; got {collections}."
    )

    db.close()


def test_reextract_threads_topology_extractor_through(tmp_path: Path) -> None:
    """Task 5 — the connector's ``extractor`` field flows from the topology
    entry into the re-extract extractor resolution.

    The bronze row holds bytes that are NOT a valid PDF; the topology
    connector declares ``extractor: pdf_fallback``. Re-extract must resolve
    pdf_fallback (from the entry's extractor field) and fail to parse the
    junk bytes → ``still_failing``. A passthrough extractor would have
    "recovered" the junk bytes verbatim, so a green ``still_failing == 1``
    proves the topology extractor field threaded through (not a default).

    Sabotage proof (executed): in ``_load_connector_entry`` / the shared
    builder, drop the ``"extractor"`` key from the entry dict; re-extract
    falls back to passthrough, the junk bytes "recover", and this test
    fails with ``recovered == 1``. Restored, it lands in still_failing.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    junk = b"NOT A REAL PDF - a lure for the pdf_fallback extractor"
    (vault / "broken.pdf").write_bytes(junk)

    db = sqlite3.connect(":memory:")
    create_schema(db)
    StreamingBronzeStore(db).write("obsidian", "broken.pdf", junk, "application/pdf")
    DeadLetterStore(db).record("obsidian", "broken.pdf", "first-pass failed")
    db.commit()

    mapping = _obsidian_topology_mapping(vault_root=vault, extractor="pdf_fallback")

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_mapping=mapping,
    )

    assert result.still_failing == 1, (
        f"the topology extractor (pdf_fallback) must thread through and fail on junk PDF bytes; got {result}"
    )
    assert result.recovered == 0, (
        f"a recovered==1 would mean passthrough ran — the topology extractor field did not thread through; got {result}"
    )

    db.close()


def test_skipped_no_connector_when_source_not_in_topology(tmp_path: Path) -> None:
    """A dead_letter row whose ``source_name`` matches no topology cc_pair
    counts as ``skipped_no_connector`` and the row is preserved.

    Pins the Task-5 ``_load_connector_entry(...) is None`` path through the
    public re-extract surface: the topology declares an ``obsidian`` cc_pair
    only, so a ``ghost`` source has no matching entry.

    Sabotage proof: in ``_load_connector_entry``, replace
    ``e.get("name") == source_name`` with ``True`` (match the first entry);
    the test fails because re-extract then resolves the obsidian connector
    for the ghost rows instead of skipping. Restored, it passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    # Seed a dead_letter row for "ghost" — no matching topology cc_pair.
    dead_letter = DeadLetterStore(db)
    dead_letter.record("ghost", "item-1", "stale row from removed connector")
    db.commit()

    # Topology only declares an obsidian cc_pair, not ghost.
    mapping = _obsidian_topology_mapping(vault_root=vault)

    result = run_reextract_dead_letter(
        source_name="ghost",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
    )

    assert result.skipped_no_connector == 1
    assert result.recovered == 0
    assert result.still_failing == 0
    assert result.skipped_no_bronze == 0

    # Row preserved — operator can re-add the connector and retry.
    remaining = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        ("ghost",),
    ).fetchone()[0]
    assert remaining == 1

    db.close()


def test_skipped_no_connector_when_mapping_has_no_topology(tmp_path: Path) -> None:
    """When the merged mapping carries no topology connectors at all, every
    dead_letter row is skipped_no_connector. Pre-deploy boundary — operator
    runs reextract against a fresh data dir before the wizard has written a
    ``topology`` block.

    Injects an empty mapping through the ``config_mapping`` seam so the
    skip is deterministic (no dependence on the host's resolved config).

    Sabotage proof: change the ``if entry is None`` early-return body in
    ``run_reextract_dead_letter`` to fall through; the function raises
    resolving a connector from an empty entry. Restored, it skips.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)

    dead_letter = DeadLetterStore(db)
    dead_letter.record("orphan", "alpha", "no config yet")
    dead_letter.record("orphan", "beta", "no config yet")
    db.commit()

    result = run_reextract_dead_letter(
        source_name="orphan",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_mapping={},  # empty merged mapping (no topology connectors)
    )
    assert isinstance(result, ReextractResult)
    assert result.skipped_no_connector == 2, (
        f"both orphan rows should skip when no topology connector matches; got {result}"
    )
    assert result.recovered == 0
    assert result.still_failing == 0
    assert result.skipped_no_bronze == 0

    db.close()


def test_skipped_no_bronze_when_bronze_row_missing(tmp_path: Path) -> None:
    """A dead_letter row whose bronze_records row was pruned (e.g. the
    2026-05-25 orphan-prune recovery) counts as ``skipped_no_bronze``.

    Sabotage proof: remove the ``if row is None: skipped_no_bronze += 1;
    continue`` block; the function raises an AttributeError trying to
    index ``None``. Restored, it skips gracefully.
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)

    # Dead_letter row but no bronze_records — operator pruned bronze.
    dead_letter = DeadLetterStore(db)
    dead_letter.record("obsidian", "lost.md", "bronze pruned")
    db.commit()

    mapping = _obsidian_topology_mapping(vault_root=vault)

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_mapping=mapping,
    )

    assert result.skipped_no_bronze == 1, f"expected skipped_no_bronze=1, got {result}"
    assert result.recovered == 0
    assert result.still_failing == 0
    assert result.skipped_no_connector == 0

    db.close()


def test_dry_run_does_not_commit_or_clear(tmp_path: Path) -> None:
    """``dry_run=True`` walks the same logic but rolls back per-item, so
    the dead_letter row is NOT cleared and the chunks are NOT persisted.

    Sabotage proof: change ``db.rollback()`` in the dry-run branch to
    ``db.commit()``; the ``dead_letter row preserved`` assertion fails
    because the clear() inside the same transaction lands. Restored, it
    passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nDry-run check.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"
    _seed_bronze_and_dead_letter(
        db=db,
        bronze_root=bronze_root,
        source_name="obsidian",
        item_id="alpha.md",
        raw_path=note,
    )

    mapping = _obsidian_topology_mapping(vault_root=vault)

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
        dry_run=True,
    )

    assert result.recovered == 1  # Counter still bumps — extract succeeded.
    remaining = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        ("obsidian",),
    ).fetchone()[0]
    assert remaining == 1, "dry-run must preserve dead_letter rows"

    docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert docs == 0, "dry-run must not persist documents"

    db.close()


def test_limit_caps_processed_rows(tmp_path: Path) -> None:
    """``limit=N`` processes only the first N dead_letter rows even when
    more exist. Operator pre-flights a recovery on a small slice first.

    Sabotage proof: remove the ``if limit is not None: rows = rows[:limit]``
    slice; both rows recover and the assertion ``recovered == 1`` fails.
    Restored, it passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note_a = vault / "alpha.md"
    note_a.write_text("# Alpha\n\nFirst.\n", encoding="utf-8")
    note_b = vault / "beta.md"
    note_b.write_text("# Beta\n\nSecond.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    _seed_bronze_and_dead_letter(
        db=db, bronze_root=bronze_root, source_name="obsidian", item_id="alpha.md", raw_path=note_a
    )
    _seed_bronze_and_dead_letter(
        db=db, bronze_root=bronze_root, source_name="obsidian", item_id="beta.md", raw_path=note_b
    )

    mapping = _obsidian_topology_mapping(vault_root=vault)

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
        limit=1,
    )

    assert result.recovered == 1, f"limit=1 should have processed exactly 1 row, got {result}"
    remaining = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        ("obsidian",),
    ).fetchone()[0]
    assert remaining == 1, "one dead_letter row should still be present after limit=1"

    db.close()


# ---------------------------------------------------------------------------
# In-process CLI seam — exercises kairix/worker_cli.py reextract() + main()
# dispatch surface. Coverage tracked under unit-tests because subprocess
# outcome tests in tests/integration/test_outcome_worker_cli.py run in a
# fresh Python process where pytest-cov instrumentation is absent.
# ---------------------------------------------------------------------------


def test_reextract_cli_json_envelope_on_empty_db(tmp_path: Path) -> None:
    """``reextract()`` with as_json=True writes the JSON envelope and
    returns exit code 0 on an empty DB.

    Sabotage proof: change ``return 0`` to ``return 1``; the assertion
    on ``rc == 0`` fails. Restored, the test passes.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()

    out = io.StringIO()
    err = io.StringIO()
    rc = reextract(
        source_name="no-such-connector",
        db_path=db_path,
        out=out,
        err=err,
        as_json=True,
    )
    assert rc == 0
    envelope = json.loads(out.getvalue())
    assert envelope["source_name"] == "no-such-connector"
    assert envelope["recovered"] == 0
    assert envelope["dry_run"] is False
    assert err.getvalue() == ""


def test_reextract_cli_human_output_dry_run_prefix(tmp_path: Path) -> None:
    """``reextract()`` with as_json=False writes the human-readable line
    prefixed with ``[dry-run]`` when dry_run is True.

    Sabotage proof: drop the ``[dry-run] `` prefix conditional; the assertion
    fails. Restored, it passes.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()

    out = io.StringIO()
    err = io.StringIO()
    rc = reextract(
        source_name="no-such-connector",
        db_path=db_path,
        out=out,
        err=err,
        as_json=False,
        dry_run=True,
    )
    assert rc == 0
    output = out.getvalue()
    assert output.startswith("[dry-run] reextract source=no-such-connector")
    assert "recovered=0" in output
    assert err.getvalue() == ""


def test_main_dispatches_reextract_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``worker_main(['reextract', '--source-name', X, '--db-path', Y, '--json'])``
    routes through main()'s dispatch, lands the JSON envelope on stdout.

    Sabotage proof: comment out the ``if args.cmd == "reextract":`` branch
    in main(); the assertion ``envelope["source_name"] == ...`` fails
    because main falls through to the worker-loop path. Restored, it passes.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()

    rc = worker_main(
        ["reextract", "--source-name", "obsidian", "--db-path", str(db_path), "--json"],
    )
    assert rc == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["source_name"] == "obsidian"
    assert envelope["dry_run"] is False


def test_main_reextract_limit_flag_threads_through(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--limit 5`` from argv reaches ``run_reextract_dead_letter`` via main()
    dispatch — exercises the limit arg-binding line.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()

    rc = worker_main(
        ["reextract", "--source-name", "obsidian", "--db-path", str(db_path), "--limit", "5", "--json"],
    )
    assert rc == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["recovered"] == 0  # Empty DB → nothing to recover, but the flag threaded through cleanly.
