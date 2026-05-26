"""Contract tests for the connector-framework Protocol surface (SC-1 Wave 1).

Pins the SC-1 seam-and-shape commit before Wave 2 implementations
land:

  * every connector-surface Protocol on
    :mod:`kairix.core.protocols` is importable;
  * every value object across the boundary is a frozen dataclass
    (F42 discipline — refactors stay refactor-safe);
  * a synthetic implementation that satisfies the Protocol's method
    set passes the ``isinstance`` runtime check (Wave 2 real impls
    will pass the same gate against the real production classes).

Wave 2 extends this file (or adds siblings) with behavioural
assertions once :class:`FilesystemBronzeStore` / :class:`DefaultSilverProcessor`
have real bodies.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest

from kairix.core.protocols import (
    BronzeRef,
    BronzeStore,
    ChangeEvent,
    Chunk,
    Cursor,
    DocMetadata,
    EntityGraphSink,
    EntitySignal,
    ExtractedDocument,
    Extractor,
    Image,
    MimeType,
    Page,
    RawArtefact,
    Sensitivity,
    SilverOutput,
    SilverProcessor,
    SourceConnector,
)

# ---------------------------------------------------------------------------
# Value objects — frozen dataclass discipline (F42 boundary contract).
# ---------------------------------------------------------------------------


@pytest.mark.contract
class TestConnectorValueObjectsAreFrozen:
    """Every value object that crosses the connector boundary must be a frozen dataclass."""

    @pytest.mark.contract
    @pytest.mark.parametrize(
        "value_object",
        [
            ChangeEvent,
            RawArtefact,
            Page,
            Image,
            DocMetadata,
            ExtractedDocument,
            BronzeRef,
            Chunk,
            EntitySignal,
            SilverOutput,
        ],
    )
    def test_value_object_is_frozen_dataclass(self, value_object: type) -> None:
        """F42 + spec §3 — value objects are ``@dataclass(frozen=True)``."""
        assert dataclasses.is_dataclass(value_object), f"{value_object.__name__} is not a dataclass"
        # ``dataclasses.fields`` won't help us inspect frozen — check params.
        params = getattr(value_object, "__dataclass_params__", None)
        assert params is not None, f"{value_object.__name__} has no __dataclass_params__"
        assert params.frozen is True, f"{value_object.__name__} is not frozen — F42 violation"


# ---------------------------------------------------------------------------
# Protocol conformance — synthetic implementations satisfy isinstance().
# ---------------------------------------------------------------------------


class _SyntheticSourceConnector:
    """Minimal :class:`SourceConnector` impl for Wave 1 shape verification."""

    name: str = "synthetic"

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        raise NotImplementedError

    def fetch(self, item_id: str) -> RawArtefact:
        raise NotImplementedError

    def source_link(self, item_id: str) -> str:
        raise NotImplementedError

    def sensitivity_for(self, item_id: str) -> Sensitivity:
        raise NotImplementedError


class _SyntheticExtractor:
    """Minimal :class:`Extractor` impl for Wave 1 shape verification."""

    name: str = "synthetic"
    version: str = "0.0.0"

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        raise NotImplementedError

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        raise NotImplementedError

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        raise NotImplementedError


class _SyntheticBronzeStore:
    """Minimal :class:`BronzeStore` impl for Wave 1 shape verification."""

    def write(self, source_name: str, item_id: str, raw: bytes, mime: MimeType) -> BronzeRef:
        raise NotImplementedError

    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        raise NotImplementedError

    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        raise NotImplementedError


class _SyntheticSilverProcessor:
    """Minimal :class:`SilverProcessor` impl for Wave 1 shape verification."""

    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
    ) -> SilverOutput:
        raise NotImplementedError


class _SyntheticEntityGraphSink:
    """Minimal :class:`EntityGraphSink` impl for Wave 1 shape verification."""

    def stage(self, signals: Sequence[EntitySignal]) -> int:
        raise NotImplementedError


@pytest.mark.contract
class TestConnectorProtocolConformance:
    """Synthetic implementations must satisfy the runtime-checkable Protocols."""

    @pytest.mark.contract
    def test_source_connector_protocol_conformance(self) -> None:
        assert isinstance(_SyntheticSourceConnector(), SourceConnector)

    @pytest.mark.contract
    def test_extractor_protocol_conformance(self) -> None:
        assert isinstance(_SyntheticExtractor(), Extractor)

    @pytest.mark.contract
    def test_bronze_store_protocol_conformance(self) -> None:
        assert isinstance(_SyntheticBronzeStore(), BronzeStore)

    @pytest.mark.contract
    def test_silver_processor_protocol_conformance(self) -> None:
        assert isinstance(_SyntheticSilverProcessor(), SilverProcessor)

    @pytest.mark.contract
    def test_entity_graph_sink_protocol_conformance(self) -> None:
        assert isinstance(_SyntheticEntityGraphSink(), EntityGraphSink)


# ---------------------------------------------------------------------------
# Skeleton implementations — Wave 1 stubs raise NotImplementedError on call.
# ---------------------------------------------------------------------------


@pytest.mark.contract
class TestConnectorImplementationsExist:
    """IM-2 (Connector-Framework Wave 2): Bronze / Silver / Pipeline / Registry
    have real implementations; the canonical-shape contract tests live in
    ``tests/integration/test_connector_pipeline.py``. This block proves the
    classes import and construct so a regression that replaces a real impl
    with a NotImplementedError stub fails here at the contract gate.
    """

    @pytest.mark.contract
    def test_filesystem_bronze_store_constructs(self, tmp_path: Path) -> None:
        import sqlite3

        from kairix.core.connectors.bronze import FilesystemBronzeStore
        from kairix.core.db.schema import create_schema

        db = sqlite3.connect(":memory:")
        try:
            create_schema(db)
            store = FilesystemBronzeStore(db, tmp_path)
            ref = store.write("src", "item-1", b"hello", "text/plain")
            assert ref.source_name == "src"
            assert ref.item_id == "item-1"
            assert ref.mime == "text/plain"
        finally:
            db.close()

    @pytest.mark.contract
    def test_streaming_bronze_store_satisfies_protocol(self) -> None:
        """Phase 1 of streaming-bronze (#27): StreamingBronzeStore must
        satisfy the same BronzeStore Protocol as FilesystemBronzeStore
        so callers can swap impls without API change. Sabotage proof:
        drop the StreamingBronzeStore.replay method → isinstance(...,
        BronzeStore) returns False.
        """
        import sqlite3

        from kairix.core.connectors.streaming_bronze import StreamingBronzeStore
        from kairix.core.db.schema import create_schema

        db = sqlite3.connect(":memory:")
        try:
            create_schema(db)
            store = StreamingBronzeStore(db)
            assert isinstance(store, BronzeStore)
            ref = store.write("src", "item-1", b"hello", "text/plain")
            assert ref.source_name == "src"
            assert ref.item_id == "item-1"
            assert ref.mime == "text/plain"
            # Streaming-specific: raw_path is the empty sentinel
            assert ref.raw_path == ""
        finally:
            db.close()

    @pytest.mark.contract
    def test_both_bronze_stores_yield_identical_replay_shape(self, tmp_path: Path) -> None:
        """Caller-facing equivalence — replay() yields BronzeRefs that
        carry the same (source_name, item_id, mime, fetched_at) fields
        regardless of which impl wrote them. Only ``raw_path`` differs
        (sentinel vs real path). This is the contract that lets the
        Bug D re-extract path handle both impls with one code path
        (Phase 5 of streaming-bronze).

        Sabotage proof: change StreamingBronzeStore.replay to yield
        BronzeRefs with item_id=None → field-equality check fails.
        """
        import sqlite3

        from kairix.core.connectors.bronze import FilesystemBronzeStore
        from kairix.core.connectors.streaming_bronze import StreamingBronzeStore
        from kairix.core.db.schema import create_schema

        db_a = sqlite3.connect(":memory:")
        db_b = sqlite3.connect(":memory:")
        try:
            create_schema(db_a)
            create_schema(db_b)
            fs_store = FilesystemBronzeStore(db_a, tmp_path)
            st_store = StreamingBronzeStore(db_b)
            fs_store.write("src", "item-1", b"hello", "text/plain")
            st_store.write("src", "item-1", b"hello", "text/plain")
            fs_refs = list(fs_store.replay("src"))
            st_refs = list(st_store.replay("src"))
            assert len(fs_refs) == len(st_refs) == 1
            assert fs_refs[0].source_name == st_refs[0].source_name
            assert fs_refs[0].item_id == st_refs[0].item_id
            assert fs_refs[0].mime == st_refs[0].mime
            # raw_path intentionally differs: filesystem has a path,
            # streaming has the empty sentinel
            assert fs_refs[0].raw_path != ""
            assert st_refs[0].raw_path == ""
        finally:
            db_a.close()
            db_b.close()

    @pytest.mark.contract
    def test_default_silver_processor_returns_chunks(self) -> None:
        from kairix.core.connectors.silver import DefaultSilverProcessor

        ref = BronzeRef(
            source_name="src",
            item_id="item",
            raw_path="src/abc",
            mime="text/plain",
            fetched_at="1970-01-01T00:00:00Z",
        )
        markdown = "Hello world.\n\n" + ("This is a body paragraph. " * 40)
        doc = ExtractedDocument(
            markdown=markdown,
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=1.0,
        )
        out = DefaultSilverProcessor().process(ref, doc, "src://x", "1970-01-01T00:00:00Z", "public")
        assert len(out.chunks) >= 1
        for chunk in out.chunks:
            # F39 guard — every chunk carries the three metadata fields.
            assert chunk.source_uri == "src://x"
            assert chunk.source_modified_at == "1970-01-01T00:00:00Z"
            assert chunk.sensitivity == "public"

    @pytest.mark.contract
    def test_connector_registry_resolve_raises_keyerror_for_unknown(self) -> None:
        from kairix.core.connectors.registry import ConnectorRegistry

        with pytest.raises(KeyError):
            ConnectorRegistry().resolve("does-not-exist-connector")

    @pytest.mark.contract
    def test_extractor_registry_resolve_raises_keyerror_for_unknown(self) -> None:
        from kairix.core.connectors.registry import ExtractorRegistry

        # Bytes shape no real extractor claims — the registry must raise.
        with pytest.raises(KeyError):
            ExtractorRegistry().resolve("application/x-no-such-format-9000", b"\x00\x00\x00\x00")
