"""Unit tests for :mod:`kairix.extractors.passthrough` (IM-4).

Drives the real :class:`PassthroughExtractor` via its factory. No
monkeypatch, no @patch — the production class has no upstream
dependency, so the only test seam needed is the version-string
constructor argument.

Sabotage-proof per test:

  * ``test_extract_round_trips_utf8`` — flipping the decode to ``""``
    in :meth:`PassthroughExtractor.extract` breaks the assertion.
  * ``test_quality_ok_false_for_whitespace_only`` — relaxing
    :meth:`quality_ok` to ``return True`` breaks the assertion.
  * ``test_can_extract_rejects_binary_mime`` — broadening
    :meth:`can_extract` (e.g. removing the ``text/`` prefix check)
    breaks the assertion.
  * ``test_extract_handles_invalid_utf8_bytes`` — switching the
    decode error mode from ``replace`` to ``strict`` breaks the
    assertion.
"""

from __future__ import annotations

import pytest

from kairix.extractors import ExtractedDocument
from kairix.extractors.passthrough import PassthroughExtractor, make_extractor, version

pytestmark = pytest.mark.unit


def test_factory_returns_passthrough_instance() -> None:
    extractor = make_extractor()
    assert isinstance(extractor, PassthroughExtractor)
    assert extractor.name == "passthrough"
    assert extractor.version == version


def test_version_module_level_non_empty() -> None:
    """F40 sanity — the module-level ``version`` is a non-empty string."""
    assert isinstance(version, str)
    assert version.strip() != ""


def test_can_extract_claims_text_markdown() -> None:
    extractor = make_extractor()
    assert extractor.can_extract("text/markdown", b"") is True


def test_can_extract_claims_text_plain() -> None:
    extractor = make_extractor()
    assert extractor.can_extract("text/plain", b"") is True


def test_can_extract_claims_text_subtypes() -> None:
    extractor = make_extractor()
    assert extractor.can_extract("text/x-markdown", b"") is True
    assert extractor.can_extract("text/rst", b"") is True


def test_can_extract_rejects_binary_mime() -> None:
    extractor = make_extractor()
    assert extractor.can_extract("application/pdf", b"%PDF") is False
    assert extractor.can_extract("application/octet-stream", b"\x00\x01") is False


def test_extract_round_trips_utf8() -> None:
    raw = b"# Heading\n\nA paragraph.\n"
    doc = make_extractor().extract(raw, "text/markdown")
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown == "# Heading\n\nA paragraph.\n"
    assert doc.pages == ()
    assert doc.images == ()
    assert doc.confidence == 1.0


def test_extract_handles_invalid_utf8_bytes() -> None:
    raw = b"valid prefix \xff\xfe and trailing \xc3text"
    doc = make_extractor().extract(raw, "text/plain")
    # ``errors='replace'`` substitutes U+FFFD for invalid sequences,
    # so the decoded string contains the prefix + suffix unchanged.
    assert "valid prefix" in doc.markdown
    assert "trailing" in doc.markdown


def test_quality_ok_true_for_non_empty_markdown() -> None:
    extractor = make_extractor()
    doc = extractor.extract(b"# hi\n", "text/markdown")
    assert extractor.quality_ok(doc) is True


def test_quality_ok_false_for_empty_markdown() -> None:
    extractor = make_extractor()
    doc = extractor.extract(b"", "text/markdown")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_for_whitespace_only() -> None:
    extractor = make_extractor()
    doc = extractor.extract(b"   \n\n  \t\n", "text/markdown")
    assert extractor.quality_ok(doc) is False


def test_metadata_fields_are_none_by_default() -> None:
    doc = make_extractor().extract(b"text", "text/plain")
    assert doc.metadata.title is None
    assert doc.metadata.author is None
    assert doc.metadata.created_date is None
    assert doc.metadata.language is None
    assert doc.metadata.page_count is None
