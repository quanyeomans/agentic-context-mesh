"""Step definitions for ``extractor_markitdown.feature``.

Drives the real :class:`kairix.extractors.markitdown.MarkitdownExtractor`
with a fake ``converter_factory=`` that returns a scripted
``DocumentConverterResult``-shaped object — F1-clean (no monkeypatch),
F2-clean (no env mutation). The fake converter is the canonical seam
for behaviour tests; the real upstream library is exercised by the
contract / unit / integration tests instead.

Step phrasings carry the literal word "markitdown" so the global
pytest-bdd step registry doesn't collide with the passthrough feature's
analogous Given/When/Then phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the step.
  * "carries non-empty markdown" — flipping ``extract`` to return
    empty markdown in production fails the step.
  * "extractor's version string is non-empty" — clearing the
    module-level ``version`` constant in production fails the step.
  * "quality_ok false for the produced document" (escalation gate) —
    flipping ``quality_ok`` to return ``True`` unconditionally fails
    the @error scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.markitdown import MarkitdownExtractor, make_extractor, version

pytestmark = pytest.mark.bdd


# Step-phrase fragments lifted to constants because the same literal
# repeats >=3 times across this module (F17 — no >=10-char string
# duplicated >=3 times in a module).
_PHRASE_OFFICE_DOCUMENT_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PHRASE_OFFICE_PRESENTATION_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PHRASE_OFFICE_SPREADSHEET_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class _FakeConverterResult:
    """In-memory stand-in for ``markitdown.DocumentConverterResult``."""

    markdown: str
    title: str | None = None

    @property
    def text_content(self) -> str:
        return self.markdown


class _ScriptedConverter:
    """Fake :class:`_MarkitdownConverter` — returns a preconfigured result."""

    def __init__(self, markdown: str, title: str | None = None) -> None:
        self.markdown = markdown
        self.title = title
        self.calls: list[str] = []

    def convert(self, source: Any, **kwargs: Any) -> _FakeConverterResult:
        self.calls.append(str(source))
        return _FakeConverterResult(markdown=self.markdown, title=self.title)


@pytest.fixture
def markitdown_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
        "fake": None,
    }


def _build_extractor(state: dict[str, Any], *, scripted_markdown: str) -> MarkitdownExtractor:
    fake = _ScriptedConverter(markdown=scripted_markdown, title="fixture")
    state["fake"] = fake
    return MarkitdownExtractor(version=version, converter_factory=lambda: fake)


@given(parsers.parse('the markitdown extractor is registered under the name "{name}"'))
def _register_markitdown(markitdown_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, MarkitdownExtractor)
    assert real.name == name
    markitdown_state["extractor"] = _build_extractor(
        markitdown_state,
        scripted_markdown="# Fixture document\n\nText body recovered from PDF.\n" * 4,
    )


@given("the operator has raw bytes for a small PDF with text content")
def _pdf_bytes(markitdown_state: dict[str, Any]) -> None:
    markitdown_state["raw"] = b"%PDF-1.4\n" + (b"text-body " * 32)


@given('the operator has raw bytes whose first four bytes are "%PDF"')
def _pdf_magic_bytes(markitdown_state: dict[str, Any]) -> None:
    markitdown_state["raw"] = b"%PDF-1.4\n%magic-only"


@given("the upstream converter returns empty markdown for the supplied bytes")
def _scanned_pdf_bytes(markitdown_state: dict[str, Any]) -> None:
    markitdown_state["raw"] = b"%PDF-1.4\n" + (b"\x00" * 256)
    markitdown_state["extractor"] = _build_extractor(markitdown_state, scripted_markdown="")


@when(parsers.parse('the operator asks the markitdown extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(markitdown_state: dict[str, Any], mime: str) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    markitdown_state["claimed"] = extractor.can_extract(mime, markitdown_state["raw"][:8])


@when("the operator asks the markitdown extractor whether it can extract the office open xml document mime")
def _ask_office_doc(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    markitdown_state["claimed"] = extractor.can_extract(_PHRASE_OFFICE_DOCUMENT_MIME, b"PK\x03\x04")


@when("the operator asks the markitdown extractor whether it can extract the office open xml presentation mime")
def _ask_office_pres(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    markitdown_state["claimed"] = extractor.can_extract(_PHRASE_OFFICE_PRESENTATION_MIME, b"PK\x03\x04")


@when("the operator asks the markitdown extractor whether it can extract the office open xml spreadsheet mime")
def _ask_office_sheet(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    markitdown_state["claimed"] = extractor.can_extract(_PHRASE_OFFICE_SPREADSHEET_MIME, b"PK\x03\x04")


@when("the operator invokes the markitdown extractor's extract method on the bytes")
def _invoke_extract(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    markitdown_state["doc"] = extractor.extract(markitdown_state["raw"], "application/pdf")


@then("the markitdown extractor claims the mime type")
def _then_claims(markitdown_state: dict[str, Any]) -> None:
    assert markitdown_state["claimed"] is True


@then("the markitdown extractor does not claim the mime type")
def _then_does_not_claim(markitdown_state: dict[str, Any]) -> None:
    assert markitdown_state["claimed"] is False


@then("the markitdown document carries non-empty markdown")
def _then_non_empty(markitdown_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = markitdown_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@then("the markitdown extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    doc: ExtractedDocument = markitdown_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the markitdown extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    doc: ExtractedDocument = markitdown_state["doc"]
    assert extractor.quality_ok(doc) is False


@then("the markitdown extractor's version string is non-empty")
def _then_version_non_empty(markitdown_state: dict[str, Any]) -> None:
    extractor: MarkitdownExtractor = markitdown_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
