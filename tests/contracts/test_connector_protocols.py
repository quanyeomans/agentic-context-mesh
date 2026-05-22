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
class TestConnectorSkeletonsRaiseNotImplemented:
    """Wave 1 ships seams only; calling any skeleton method must raise NotImplementedError."""

    @pytest.mark.contract
    def test_filesystem_bronze_store_write_raises(self) -> None:
        from kairix.core.connectors.bronze import FilesystemBronzeStore

        with pytest.raises(NotImplementedError):
            FilesystemBronzeStore().write("src", "item", b"", "text/plain")

    @pytest.mark.contract
    def test_default_silver_processor_process_raises(self) -> None:
        from kairix.core.connectors.silver import DefaultSilverProcessor

        # We can't construct the real BronzeRef / ExtractedDocument here
        # without going through the same not-yet-implemented surfaces,
        # but the call-site simply has to raise before touching its
        # arguments. Pass placeholders that satisfy the type shape.
        ref = BronzeRef(
            source_name="src",
            item_id="item",
            raw_path="src/abc",
            mime="text/plain",
            fetched_at="1970-01-01T00:00:00Z",
        )
        doc = ExtractedDocument(
            markdown="",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.0,
        )
        with pytest.raises(NotImplementedError):
            DefaultSilverProcessor().process(ref, doc, "src://x", "1970-01-01T00:00:00Z", "public")

    @pytest.mark.contract
    def test_connector_pipeline_run_batch_raises(self) -> None:
        from kairix.core.connectors.pipeline import ConnectorPipeline

        # The pipeline raises before consuming connector / extractor,
        # so synthetic stand-ins are sufficient for the seam test.
        with pytest.raises(NotImplementedError):
            ConnectorPipeline().run_batch(_SyntheticSourceConnector(), _SyntheticExtractor())

    # CursorStore + DeadLetterStore skeletons were replaced by real impls in
    # IM-1 (Connector-Framework Wave 2). The atomic per-batch contract is
    # exercised by tests/integration/test_connector_cursor_store.py and
    # tests/integration/test_connector_deadletter_store.py; no more
    # NotImplementedError surface on those two stores.

    @pytest.mark.contract
    def test_connector_registry_resolve_raises(self) -> None:
        from kairix.core.connectors.registry import ConnectorRegistry

        with pytest.raises(NotImplementedError):
            ConnectorRegistry().resolve("obsidian")

    @pytest.mark.contract
    def test_extractor_registry_resolve_raises(self) -> None:
        from kairix.core.connectors.registry import ExtractorRegistry

        with pytest.raises(NotImplementedError):
            ExtractorRegistry().resolve("application/pdf", b"%PDF")
