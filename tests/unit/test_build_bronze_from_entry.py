"""Tests for ``build_bronze_from_entry`` — Phase 4 of streaming-bronze (#27).

Mirror of ``test_build_extractor_from_entry.py`` for the bronze layer.
F1-clean (no monkeypatch) and exercises the production helper directly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.connectors.registry import build_bronze_from_entry
from kairix.core.connectors.streaming_bronze import StreamingBronzeStore

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    from kairix.core.db.schema import create_schema

    create_schema(conn)
    return conn


def test_default_returns_filesystem_bronze_store(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Entry with no ``bronze_mode`` field defaults to FilesystemBronzeStore.

    Sabotage proof: change the default ``"filesystem"`` to ``"streaming"``;
    the isinstance check fails because StreamingBronzeStore returns instead.
    """
    store = build_bronze_from_entry({}, db=db, bronze_root=tmp_path / "bronze")
    assert isinstance(store, FilesystemBronzeStore)


def test_explicit_filesystem_returns_filesystem_store(db: sqlite3.Connection, tmp_path: Path) -> None:
    store = build_bronze_from_entry({"bronze_mode": "filesystem"}, db=db, bronze_root=tmp_path / "bronze")
    assert isinstance(store, FilesystemBronzeStore)


def test_streaming_returns_streaming_store(db: sqlite3.Connection, tmp_path: Path) -> None:
    """``bronze_mode: streaming`` opts into metadata-only persistence.

    Sabotage proof: change the ``mode == "streaming"`` branch to return
    FilesystemBronzeStore; this test fails because the isinstance check
    on StreamingBronzeStore fails.
    """
    store = build_bronze_from_entry({"bronze_mode": "streaming"}, db=db, bronze_root=tmp_path / "bronze")
    assert isinstance(store, StreamingBronzeStore)


def test_unknown_mode_raises_with_fix_pointer(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Operator typo fails fast with a fix-pointer error per F21.

    Sabotage proof: change ``raise ValueError`` to ``return FilesystemBronzeStore(...)``;
    the test fails because no exception fires.
    """
    with pytest.raises(ValueError, match="bronze_mode must be 'streaming' or 'filesystem'"):
        build_bronze_from_entry({"bronze_mode": "potato"}, db=db, bronze_root=tmp_path / "bronze")


def test_streaming_store_ignores_bronze_root(db: sqlite3.Connection) -> None:
    """Streaming mode doesn't need a bronze_root since it writes no files.
    The helper still accepts it (uniform call surface) but doesn't pass it
    to StreamingBronzeStore's constructor.

    Sabotage proof: change StreamingBronzeStore branch to pass bronze_root
    as a positional arg; the test fails with TypeError (no such param).
    """
    store = build_bronze_from_entry({"bronze_mode": "streaming"}, db=db, bronze_root=Path("/nonexistent/path"))
    # Streaming store works fine — proves bronze_root wasn't used
    ref = store.write("src", "item-1", b"raw", "text/plain")
    assert ref.raw_path is None
