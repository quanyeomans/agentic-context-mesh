"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`BronzeStore`.

Three Protocol methods (``write`` / ``read`` / ``replay``). The shipped
:class:`kairix.core.connectors.streaming_bronze.StreamingBronzeStore`
exposes a documented failure surface — its ``read`` ALWAYS raises
:class:`BronzeNotPersistedError` because streaming bronze does not
retain raw bytes. ``write`` propagates SQLite errors when the schema is
absent; ``replay`` returns the empty iterator when no rows match the
``source_name`` filter.

We use the production class directly (it's already designed for the
failure-mode probes) plus an inline ``_RaisingStore`` for ``write``
exception propagation.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.streaming_bronze import BronzeNotPersistedError, StreamingBronzeStore
from kairix.core.db.schema import create_schema
from kairix.core.protocols import BronzeRef

pytestmark = pytest.mark.contract


def test_write_raises_when_schema_absent() -> None:
    """``write`` issues an INSERT on ``bronze_records``; if the table
    doesn't exist (schema not created) the SQLite layer raises
    :class:`sqlite3.OperationalError` — the Protocol surface must
    propagate, not swallow.

    Sabotage proof: wrap the ``store.write(...)`` call in
    ``try: ... except sqlite3.OperationalError: pass``. Re-run: the
    test fails because ``pytest.raises`` sees no exception. Restored.
    """
    db = sqlite3.connect(":memory:")
    # Deliberately skip create_schema — the table does not exist.
    store = StreamingBronzeStore(db=db)
    with pytest.raises(sqlite3.OperationalError, match="bronze_records"):
        store.write("src-alpha", "item-001", b"body", "text/plain")
    db.close()


def test_read_raises_bronze_not_persisted_on_streaming_ref() -> None:
    """:class:`StreamingBronzeStore.read` ALWAYS raises — it's the
    documented "streaming bronze doesn't retain bytes" failure shape.
    The re-extract path must route through ``connector.fetch`` instead.

    Sabotage proof: in :class:`StreamingBronzeStore.read` change
    ``raise BronzeNotPersistedError(...)`` to
    ``return (b"", ref.mime)``. Re-run: the test fails because no
    exception is raised. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    store = StreamingBronzeStore(db=db)
    ref = BronzeRef(
        source_name="src-alpha",
        item_id="item-001",
        raw_path=None,
        mime="text/plain",
        fetched_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(BronzeNotPersistedError, match="streaming bronze does not retain raw bytes"):
        store.read(ref)
    db.close()


def test_replay_returns_empty_when_no_records_for_source() -> None:
    """``replay`` yields zero items when no ``bronze_records`` row has
    ``source_name == requested``. Callers must distinguish "no fetch"
    (empty iterator) from "fetch failed" (raises). Empty is the
    observable proof.

    Sabotage proof: in :class:`StreamingBronzeStore.replay` change the
    SQL ``WHERE source_name = ?`` to ``WHERE 1=1``. Re-run: the test
    fails because the inserted ``src-other`` row leaks into the result.
    Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    store = StreamingBronzeStore(db=db)
    # Write one record for a DIFFERENT source so we can prove the filter
    # narrows correctly (sabotage-provable empty).
    store.write("src-other", "item-001", b"body", "text/plain")
    rows = list(store.replay("src-alpha"))
    assert rows == [], f"empty source must yield empty iterator; got {rows!r}"
    db.close()
