"""Unit tests for :mod:`kairix.extractors.markitdown` (IM-4).

Two seams are exercised:

  1. The **scripted-converter** seam — a fake
     ``converter_factory`` returning a stub result. Used for shape /
     branch tests that don't need the upstream library.
  2. The **real-library** seam — invokes the actual
     ``markitdown.MarkItDown`` library against a recorded PDF fixture
     under ``tests/fixtures/extractors/sample.pdf``. Skipped when the
     optional ``markitdown[pdf]`` extra is not installed (the
     library + its PDF stack are heavy and not part of the core
     wheel).

Sabotage-proof per test:

  * ``test_extract_invokes_converter_factory`` — flipping
    :meth:`extract` to bypass the factory returns the raw bytes as
    markdown and breaks the assertion that the scripted-converter
    output is surfaced verbatim.
  * ``test_quality_ok_false_for_short_recovery`` — relaxing
    :meth:`quality_ok` to ``return True`` breaks the assertion.
  * ``test_can_extract_rejects_text_mime`` — broadening
    :meth:`can_extract` (e.g. removing the mime allow-list) breaks
    the assertion.
  * ``test_real_pdf_fixture_round_trips`` — flipping the temp-file
    write to drop the bytes returns near-empty markdown and breaks
    the ``len(...) > 50`` check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kairix.extractors import ExtractedDocument
from kairix.extractors.markitdown import MarkitdownExtractor, make_extractor, version

pytestmark = pytest.mark.unit

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "extractors" / "sample.pdf"


@dataclass
class _StubResult:
    markdown: str
    title: str | None = None

    @property
    def text_content(self) -> str:
        return self.markdown


class _StubConverter:
    """Records every ``convert`` call and returns a fixed result."""

    def __init__(self, markdown: str, title: str | None = None) -> None:
        self.markdown = markdown
        self.title = title
        self.calls: list[str] = []

    def convert(self, source: Any, **kwargs: Any) -> _StubResult:
        self.calls.append(str(source))
        return _StubResult(markdown=self.markdown, title=self.title)


def _make_extractor(*, markdown: str, title: str | None = None) -> tuple[MarkitdownExtractor, _StubConverter]:
    """Build a real :class:`MarkitdownExtractor` wired to a stub converter."""
    stub = _StubConverter(markdown=markdown, title=title)
    extractor = MarkitdownExtractor(version=version, converter_factory=lambda: stub)
    return extractor, stub


def test_factory_returns_markitdown_instance() -> None:
    extractor = make_extractor()
    assert isinstance(extractor, MarkitdownExtractor)
    assert extractor.name == "markitdown"
    assert extractor.version == version


def test_version_module_level_non_empty() -> None:
    """F40 sanity — module-level ``version`` is a non-empty string."""
    assert isinstance(version, str)
    assert version.strip() != ""


def test_can_extract_claims_pdf_by_mime() -> None:
    extractor, _ = _make_extractor(markdown="x")
    assert extractor.can_extract("application/pdf", b"") is True


def test_can_extract_claims_pdf_by_magic_bytes() -> None:
    extractor, _ = _make_extractor(markdown="x")
    assert extractor.can_extract("application/octet-stream", b"%PDF-1.7") is True


def test_can_extract_claims_office_mimes() -> None:
    extractor, _ = _make_extractor(markdown="x")
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    pptx = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert extractor.can_extract(docx, b"PK\x03\x04") is True
    assert extractor.can_extract(pptx, b"PK\x03\x04") is True
    assert extractor.can_extract(xlsx, b"PK\x03\x04") is True


def test_can_extract_claims_html() -> None:
    extractor, _ = _make_extractor(markdown="x")
    assert extractor.can_extract("text/html", b"<html>") is True


def test_can_extract_rejects_text_mime() -> None:
    extractor, _ = _make_extractor(markdown="x")
    assert extractor.can_extract("text/plain", b"hello") is False
    assert extractor.can_extract("text/markdown", b"# hi") is False


def test_can_extract_rejects_bare_zip_without_mime_hint() -> None:
    """ZIP magic alone is ambiguous (could be a .zip backup, JAR, etc.)."""
    extractor, _ = _make_extractor(markdown="x")
    assert extractor.can_extract("application/octet-stream", b"PK\x03\x04") is False


def test_extract_invokes_converter_factory() -> None:
    extractor, stub = _make_extractor(markdown="# Recovered\n" + "body line\n" * 12)
    raw = b"%PDF-1.4\n" + (b"payload-bytes " * 32)
    doc = extractor.extract(raw, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert "Recovered" in doc.markdown
    assert "body line" in doc.markdown
    assert len(stub.calls) == 1
    # Confirm the temp-file path was passed (str path, not raw bytes).
    assert stub.calls[0].endswith(".pdf")


def test_extract_propagates_title_into_metadata() -> None:
    extractor, _ = _make_extractor(markdown="# Title body\n" + "x" * 80, title="Sample Title")
    doc = extractor.extract(b"%PDF-1.4\n" + b"z" * 32, "application/pdf")
    assert doc.metadata.title == "Sample Title"


def test_extract_uses_pdf_extension_for_pdf_mime() -> None:
    extractor, stub = _make_extractor(markdown="x" * 80)
    extractor.extract(b"%PDF-1.4\n" + b"a" * 16, "application/pdf")
    assert stub.calls[0].endswith(".pdf")


def test_extract_returns_zero_confidence_for_empty_bytes() -> None:
    extractor, _ = _make_extractor(markdown="anything")
    doc = extractor.extract(b"", "application/pdf")
    assert doc.confidence == 0.0


def test_quality_ok_true_for_substantive_recovery() -> None:
    extractor, _ = _make_extractor(markdown="# Recovered\n" + "good line of text\n" * 8)
    raw = b"%PDF-1.4\n" + (b"x" * 64)
    doc = extractor.extract(raw, "application/pdf")
    assert extractor.quality_ok(doc) is True


def test_quality_ok_false_for_short_recovery() -> None:
    extractor, _ = _make_extractor(markdown="x")
    raw = b"%PDF-1.4\n" + (b"x" * 4096)
    doc = extractor.extract(raw, "application/pdf")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_when_byte_ratio_too_low() -> None:
    # Recovered markdown is over the 50-char floor, but recovery is
    # well below the 10% byte-ratio threshold — escalation gate.
    markdown_blob = "x" * 60
    extractor, _ = _make_extractor(markdown=markdown_blob)
    raw = b"%PDF-1.4\n" + (b"x" * 8192)
    doc = extractor.extract(raw, "application/pdf")
    # 60 / 8201 ≈ 0.007 << 0.10
    assert extractor.quality_ok(doc) is False


# ---------------------------------------------------------------------------
# Real-library tests — exercise the actual markitdown package against a
# recorded PDF fixture. Skipped when the optional extra is not present.
# ---------------------------------------------------------------------------


def _markitdown_available() -> bool:
    try:
        import markitdown  # noqa: F401 — probe-only import; resolved at runtime
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    not _markitdown_available(),
    reason="markitdown extra not installed; install via 'pip install Kairix-agentic-knowledge-mgt[markitdown]'",
)
def test_real_pdf_fixture_round_trips() -> None:
    raw = FIXTURE_PDF.read_bytes()
    assert raw.startswith(b"%PDF")
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert len(doc.markdown) > 50
    # The fixture content carries known phrases — assert one survives the round-trip.
    assert "Hello PDF text" in doc.markdown
    assert extractor.quality_ok(doc) is True


@pytest.mark.skipif(
    not _markitdown_available(),
    reason="markitdown extra not installed; install via 'pip install Kairix-agentic-knowledge-mgt[markitdown]'",
)
def test_real_pdf_fixture_byte_recovery_above_threshold() -> None:
    raw = FIXTURE_PDF.read_bytes()
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    # ``confidence`` is the byte-recovery heuristic per the impl —
    # for a tiny PDF with a text content stream the recovery ratio
    # sits comfortably above the 10% escalation floor.
    assert doc.confidence >= 0.10
