"""Tests for ``build_bronze_from_entry`` — streaming-bronze Phase 7 shape.

The helper always returns a StreamingBronzeStore. Operators who keep
the legacy ``bronze_mode`` config field get a fix-pointer error so the
removal is obvious at deploy time rather than at first sync.

F1-clean.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.registry import build_bronze_from_entry
from kairix.core.connectors.streaming_bronze import StreamingBronzeStore

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> sqlite3.Connection:
    from kairix.core.db.schema import create_schema

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def test_empty_entry_returns_streaming_store(db: sqlite3.Connection) -> None:
    """No config fields → streaming bronze (the only persistence model).

    Sabotage proof: change the return to None; the isinstance check fails.
    """
    store = build_bronze_from_entry({}, db=db)
    assert isinstance(store, StreamingBronzeStore)


def test_entry_with_only_extractor_field_returns_streaming_store(db: sqlite3.Connection) -> None:
    """An entry with unrelated fields (extractor, config, etc.) still
    yields the streaming bronze — the helper ignores everything except
    the obsolete ``bronze_mode`` field.
    """
    entry = {
        "name": "obsidian",
        "extractor": "passthrough",
        "config": {"vault_root": "/some/path"},
    }
    store = build_bronze_from_entry(entry, db=db)
    assert isinstance(store, StreamingBronzeStore)


def test_legacy_bronze_mode_field_raises_with_fix_pointer(db: sqlite3.Connection) -> None:
    """Operators with ``bronze_mode: filesystem`` or ``bronze_mode: streaming``
    in their pre-Phase-7 config get an error at deploy time directing them
    to remove the field. The fix-pointer message references the plan doc.

    Sabotage proof: replace ``raise ValueError`` with a silent return;
    the test fails because no exception fires.
    """
    with pytest.raises(ValueError, match="'bronze_mode' config field is no longer accepted"):
        build_bronze_from_entry({"bronze_mode": "filesystem"}, db=db)
    with pytest.raises(ValueError, match="streaming-bronze-plan"):
        build_bronze_from_entry({"bronze_mode": "streaming"}, db=db)


def test_streaming_store_writes_no_disk_blobs(db: sqlite3.Connection) -> None:
    """End-to-end sanity: the returned store satisfies the BronzeStore
    Protocol and writes a metadata row without touching disk.
    """
    store = build_bronze_from_entry({}, db=db)
    ref = store.write("src", "item-1", b"raw bytes", "text/plain")
    assert ref.source_name == "src"
    assert ref.item_id == "item-1"
    assert ref.raw_path is None  # streaming sentinel
    assert ref.content_hash and len(ref.content_hash) == 64
