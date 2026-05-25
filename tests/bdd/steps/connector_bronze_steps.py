"""Step implementations for connector_bronze.feature.

Drives the real :class:`FilesystemBronzeStore` against a ``tmp_path``
bronze root. No monkey-patching; the store accepts a real
:class:`sqlite3.Connection` and the test owns the per-batch commit
exactly as the production orchestrator does.

The orphan-reaper scenario covers the post-fsync-pre-commit window the
module docstring anticipates ("harmless garbage that can be GC'd by a
sweeper") — the sweeper is the framework reaper this binding exercises.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.db.schema import create_schema
from kairix.core.protocols import BronzeRef


@dataclass
class _Ctx:
    bronze_root: Path
    db: sqlite3.Connection
    store: FilesystemBronzeStore
    source_payloads: dict[str, bytes] = field(default_factory=dict)
    written_ref: BronzeRef | None = None
    replay_refs: list[BronzeRef] = field(default_factory=list)
    orphan_path: Path | None = None
    reaped_count: int = 0


@pytest.fixture
def bronze_ctx(tmp_path: Path) -> _Ctx:
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    store = FilesystemBronzeStore(db, bronze_root)
    return _Ctx(bronze_root=bronze_root, db=db, store=store)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse('a configured connector source named "{source_name}"'))
def _configured_source(bronze_ctx: _Ctx, source_name: str) -> None:
    bronze_ctx.source_payloads.setdefault(source_name, b"")


@given(parsers.parse('the source returns one payload of raw bytes for identifier "{item_id}"'))
def _source_payload(bronze_ctx: _Ctx, item_id: str) -> None:
    bronze_ctx.source_payloads[item_id] = b"raw bytes for " + item_id.encode()


@given(parsers.parse('the bronze store holds three records for "{source_name}" written in order "{a}", "{b}", "{c}"'))
def _three_records(bronze_ctx: _Ctx, source_name: str, a: str, b: str, c: str) -> None:
    for item_id in (a, b, c):
        bronze_ctx.store.write(source_name, item_id, item_id.encode(), "text/plain")
    bronze_ctx.db.commit()


@given(parsers.parse('the bronze store has a registered blob for "{item_id}" under "{source_name}"'))
def _registered_blob(bronze_ctx: _Ctx, item_id: str, source_name: str) -> None:
    bronze_ctx.written_ref = bronze_ctx.store.write(source_name, item_id, b"tracked-payload", "text/plain")
    bronze_ctx.db.commit()


@given(parsers.parse('an orphan blob exists on disk under "{source_name}" with no registry row'))
def _orphan_on_disk(bronze_ctx: _Ctx, source_name: str) -> None:
    # Simulate the post-fsync-pre-commit crash: the temp file got
    # written + renamed, but the bronze_records INSERT never reached
    # commit() (process killed, OOM, container restart). The blob path
    # uses a synthetic hash prefix that no real write would collide with.
    orphan_hash = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    orphan_dir = bronze_ctx.bronze_root / source_name / orphan_hash[:2]
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan_path = orphan_dir / orphan_hash
    orphan_path.write_bytes(b"unreferenced bytes")
    bronze_ctx.orphan_path = orphan_path


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the pipeline writes the payload to the bronze store")
def _write_one(bronze_ctx: _Ctx) -> None:
    item_id, raw = next((i, p) for i, p in bronze_ctx.source_payloads.items() if p)
    bronze_ctx.written_ref = bronze_ctx.store.write("alpha-source", item_id, raw, "text/plain")
    bronze_ctx.db.commit()


@when(parsers.parse('the operator replays the bronze records for "{source_name}"'))
def _replay(bronze_ctx: _Ctx, source_name: str) -> None:
    bronze_ctx.replay_refs = list(bronze_ctx.store.replay(source_name))


@when(parsers.parse('the maintenance scheduler runs the bronze orphan reaper for "{source_name}"'))
def _run_reaper(bronze_ctx: _Ctx, source_name: str) -> None:
    bronze_ctx.reaped_count = bronze_ctx.store.reap_orphans(source_name)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the raw bytes are stored on the filesystem under the bronze root")
def _bytes_on_disk(bronze_ctx: _Ctx) -> None:
    assert bronze_ctx.written_ref is not None
    blob = bronze_ctx.bronze_root / bronze_ctx.written_ref.raw_path
    assert blob.is_file()
    assert blob.read_bytes().startswith(b"raw bytes for ")


@then(parsers.parse('a pointer record links "{item_id}" to its filesystem location'))
def _pointer_links(bronze_ctx: _Ctx, item_id: str) -> None:
    row = bronze_ctx.db.execute(
        "SELECT raw_path FROM bronze_records WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    assert row is not None
    assert bronze_ctx.written_ref is not None
    assert row[0] == bronze_ctx.written_ref.raw_path


@then("the pointer record carries the source name and the fetch timestamp")
def _pointer_metadata(bronze_ctx: _Ctx) -> None:
    row = bronze_ctx.db.execute("SELECT source_name, fetched_at FROM bronze_records").fetchone()
    assert row is not None
    assert row[0] == "alpha-source"
    assert row[1].endswith("Z")


@then(parsers.parse('the replay yields the records in the order "{a}", "{b}", "{c}"'))
def _replay_order(bronze_ctx: _Ctx, a: str, b: str, c: str) -> None:
    assert [r.item_id for r in bronze_ctx.replay_refs] == [a, b, c]


@then("the orphan blob is deleted from disk")
def _orphan_gone(bronze_ctx: _Ctx) -> None:
    assert bronze_ctx.orphan_path is not None
    assert not bronze_ctx.orphan_path.exists()
    assert bronze_ctx.reaped_count == 1


@then("the tracked-item blob is still present")
def _tracked_present(bronze_ctx: _Ctx) -> None:
    assert bronze_ctx.written_ref is not None
    blob = bronze_ctx.bronze_root / bronze_ctx.written_ref.raw_path
    assert blob.is_file()
    assert blob.read_bytes() == b"tracked-payload"


@then(parsers.parse('the bronze_records row for "{item_id}" is unchanged'))
def _registry_unchanged(bronze_ctx: _Ctx, item_id: str) -> None:
    row = bronze_ctx.db.execute(
        "SELECT raw_path FROM bronze_records WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    assert row is not None
    assert bronze_ctx.written_ref is not None
    assert row[0] == bronze_ctx.written_ref.raw_path
