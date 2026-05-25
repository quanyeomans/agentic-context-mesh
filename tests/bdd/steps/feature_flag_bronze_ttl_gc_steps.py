"""Step definitions for feature_flag_bronze_ttl_gc.feature.

#316 — bronze TTL GC behind the ``bronze_ttl_gc`` flag. Both-branch
coverage per F54: OFF and ON scenarios drive the real
:class:`FilesystemBronzeStore` through a scheduler-shaped closure
that reads the flag value off a :class:`FakeFeatureFlagResolver`.

F1 / F2 clean — no monkey-patching, no env-var manipulation; the flag
state is pinned through the canonical resolver fake.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "bronze_ttl_gc"
_BACKDATED_AT = "2020-01-01T00:00:00Z"


@dataclass
class _Ctx:
    bronze_root: Path
    db: sqlite3.Connection
    store: FilesystemBronzeStore
    resolver: FakeFeatureFlagResolver | None = None
    backdated_raw_path: str | None = None
    deleted_count: int = 0


@pytest.fixture
def bronze_ttl_ctx(tmp_path: Path) -> _Ctx:
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    store = FilesystemBronzeStore(db, bronze_root)
    return _Ctx(bronze_root=bronze_root, db=db, store=store)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the bronze-ttl-gc flag set to {state:l}"))
def _flag_state(bronze_ttl_ctx: _Ctx, state: str) -> None:
    truthy = state.lower() == "true"
    bronze_ttl_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, truthy)


@given(parsers.parse('the bronze store contains a backdated registered blob for "{source_name}"'))
def _backdated_blob(bronze_ttl_ctx: _Ctx, source_name: str) -> None:
    ref = bronze_ttl_ctx.store.write(source_name, "backdated-item", b"backdated", "text/plain")
    # Backdate fetched_at so any reasonable TTL catches it.
    bronze_ttl_ctx.db.execute(
        "UPDATE bronze_records SET fetched_at = ? WHERE source_name = ? AND item_id = ?",
        (_BACKDATED_AT, source_name, "backdated-item"),
    )
    bronze_ttl_ctx.db.commit()
    bronze_ttl_ctx.backdated_raw_path = ref.raw_path


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the maintenance scheduler runs its bronze TTL GC stage")
def _run_ttl_gc(bronze_ttl_ctx: _Ctx) -> None:
    # Mirror the production closure shape: short-circuit on the flag,
    # otherwise walk every source dir under the bronze root.
    assert bronze_ttl_ctx.resolver is not None
    if not bronze_ttl_ctx.resolver.get(_FLAG_NAME):
        bronze_ttl_ctx.deleted_count = 0
        return
    total = 0
    for source_dir in bronze_ttl_ctx.bronze_root.iterdir():
        if source_dir.is_dir():
            total += bronze_ttl_ctx.store.gc_aged(source_dir.name, older_than_days=7)
    bronze_ttl_ctx.db.commit()
    bronze_ttl_ctx.deleted_count = total


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("no bronze_records rows are deleted")
def _no_rows_deleted(bronze_ttl_ctx: _Ctx) -> None:
    assert bronze_ttl_ctx.deleted_count == 0
    row_count = bronze_ttl_ctx.db.execute(
        "SELECT count(*) FROM bronze_records WHERE item_id = 'backdated-item'"
    ).fetchone()[0]
    assert row_count == 1


@then("the backdated blob is still on disk")
def _blob_still_on_disk(bronze_ttl_ctx: _Ctx) -> None:
    assert bronze_ttl_ctx.backdated_raw_path is not None
    assert (bronze_ttl_ctx.bronze_root / bronze_ttl_ctx.backdated_raw_path).is_file()


@then("the backdated bronze_records row is deleted")
def _row_deleted(bronze_ttl_ctx: _Ctx) -> None:
    assert bronze_ttl_ctx.deleted_count == 1
    row_count = bronze_ttl_ctx.db.execute(
        "SELECT count(*) FROM bronze_records WHERE item_id = 'backdated-item'"
    ).fetchone()[0]
    assert row_count == 0


@then("the backdated blob is removed from disk")
def _blob_removed(bronze_ttl_ctx: _Ctx) -> None:
    assert bronze_ttl_ctx.backdated_raw_path is not None
    assert not (bronze_ttl_ctx.bronze_root / bronze_ttl_ctx.backdated_raw_path).exists()
