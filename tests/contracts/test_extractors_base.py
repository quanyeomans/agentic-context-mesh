"""Contract tests: ``kairix.extractors`` re-exports the Extractor Protocol
and its value objects under stable names.

These tests pin the plugin-author-facing import surface so renames /
reshuffles in ``kairix/extractors/_base.py`` or (eventually)
``kairix/core/protocols.py`` get caught before downstream plugins
break. The full ``Extractor`` Protocol behaviour test lives with the
plugin contract tests under ``tests/contracts/test_<name>_protocol.py``
once Wave 2+ lands real extractors (F43).

The Wave 1 SC-3 scaffold defines ``Extractor`` and its value objects
locally in ``kairix.extractors._base`` (see the SC-1 TODO in that
module). When SC-1 swaps the placeholders for re-exports from
``kairix.core.protocols``, this contract test continues to pass
unchanged — that swap-out is exactly what the test protects.
"""

from __future__ import annotations

import dataclasses

import pytest

pytestmark = pytest.mark.contract


class TestExtractorReExports:
    """Plugin-author-facing import surface stays stable."""

    def test_extractor_protocol_importable_from_package_root(self) -> None:
        """`from kairix.extractors import Extractor` resolves."""
        from kairix.extractors import Extractor

        assert Extractor.__name__ == "Extractor"

    def test_value_objects_importable_from_package_root(self) -> None:
        """All extractor value objects re-export from the package root."""
        from kairix.extractors import (
            DocMetadata,
            ExtractedDocument,
            Image,
            MimeType,
            Page,
        )

        assert ExtractedDocument.__name__ == "ExtractedDocument"
        assert Page.__name__ == "Page"
        assert Image.__name__ == "Image"
        assert DocMetadata.__name__ == "DocMetadata"
        # MimeType is a str alias in the Wave 1 SC-3 scaffold; pin that
        # contract so downstream code can rely on `MimeType("text/plain")`
        # being a plain string.
        assert MimeType is str

    def test_extractor_protocol_has_required_members(self) -> None:
        """The Extractor Protocol exposes name, version, can_extract,
        extract, and quality_ok per
        ``docs/architecture/connector-ingestion-architecture.md`` § 2."""
        from kairix.extractors import Extractor

        # Methods land in ``dir()``; class-level attribute declarations
        # (``name: str``) land in ``__annotations__``. Both are part of
        # the Protocol contract — check the union.
        method_members = set(dir(Extractor))
        attribute_members = set(getattr(Extractor, "__annotations__", {}).keys())
        members = method_members | attribute_members

        required = {"name", "version", "can_extract", "extract", "quality_ok"}
        missing = required - members
        assert not missing, (
            f"Extractor Protocol missing required members: {sorted(missing)}. "
            f"fix: declare them in kairix/extractors/_base.py per the spec."
        )

    def test_extracted_document_is_frozen_dataclass(self) -> None:
        """``ExtractedDocument`` is a frozen dataclass — boundary value
        objects must be immutable per the spec § 2 'value object discipline'."""
        from kairix.extractors import ExtractedDocument

        assert dataclasses.is_dataclass(ExtractedDocument), (
            "ExtractedDocument must be a dataclass. fix: add @dataclass(frozen=True) in kairix/extractors/_base.py."
        )
        # ``__dataclass_params__.frozen`` is the canonical signal that
        # ``frozen=True`` was passed to the decorator. It is a real
        # runtime attribute set by the dataclass decorator.
        assert ExtractedDocument.__dataclass_params__.frozen, (
            "ExtractedDocument must be frozen. fix: pass frozen=True to @dataclass in kairix/extractors/_base.py."
        )
