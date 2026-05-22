"""Step definitions for ``extractor_passthrough.feature``.

Drives the real :class:`kairix.extractors.passthrough.PassthroughExtractor`
constructed via the :func:`kairix.extractors.passthrough.make_extractor`
factory — no monkeypatch, no @patch, no env mutation. The feature is the
canonical reference for the Extractor Protocol shape; these steps assert
that the real factory returns an instance that satisfies the spec.

Step phrasings carry the literal word "passthrough" so the global
pytest-bdd step registry doesn't collide with the markitdown feature's
analogous Given/When/Then phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to ``return False``
    in production fails the step.
  * "carries the decoded markdown" — flipping the decode to ``""`` in
    production fails the step.
  * "quality_ok true for the produced document" — flipping
    ``quality_ok`` to ``return False`` in production fails the step.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.passthrough import PassthroughExtractor, make_extractor

pytestmark = pytest.mark.bdd


@pytest.fixture
def passthrough_state() -> dict[str, Any]:
    """Per-scenario state container — extractor + raw bytes + last result."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
    }


@given(parsers.parse('the passthrough extractor is registered under the name "{name}"'))
def _register_passthrough(passthrough_state: dict[str, Any], name: str) -> None:
    extractor = make_extractor()
    assert isinstance(extractor, PassthroughExtractor)
    assert extractor.name == name
    passthrough_state["extractor"] = extractor


@given("the operator has raw bytes for a markdown note that decode to non-empty UTF-8")
def _markdown_bytes(passthrough_state: dict[str, Any]) -> None:
    passthrough_state["raw"] = b"# Heading\n\nBody paragraph with a [link](https://example.test).\n"


@given("the operator has plain text bytes that decode to non-empty UTF-8")
def _plaintext_bytes(passthrough_state: dict[str, Any]) -> None:
    passthrough_state["raw"] = b"hello passthrough\nplain second line\n"


@given("the operator has empty raw bytes")
def _empty_bytes(passthrough_state: dict[str, Any]) -> None:
    passthrough_state["raw"] = b""


@when(parsers.parse('the operator asks the passthrough extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(passthrough_state: dict[str, Any], mime: str) -> None:
    extractor: PassthroughExtractor = passthrough_state["extractor"]
    passthrough_state["claimed"] = extractor.can_extract(mime, passthrough_state["raw"][:8])


@when("the operator invokes the passthrough extractor's extract method on the bytes")
def _invoke_extract(passthrough_state: dict[str, Any]) -> None:
    extractor: PassthroughExtractor = passthrough_state["extractor"]
    passthrough_state["doc"] = extractor.extract(passthrough_state["raw"], "text/markdown")


@then("the passthrough extractor claims the mime type")
def _then_claims(passthrough_state: dict[str, Any]) -> None:
    assert passthrough_state["claimed"] is True


@then("the passthrough extractor does not claim the mime type")
def _then_does_not_claim(passthrough_state: dict[str, Any]) -> None:
    assert passthrough_state["claimed"] is False


@then("the passthrough document carries the decoded markdown")
def _then_carries_decoded_markdown(passthrough_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = passthrough_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown == passthrough_state["raw"].decode("utf-8", errors="replace")
    assert "Heading" in doc.markdown


@then("the passthrough document carries the decoded text")
def _then_carries_decoded_text(passthrough_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = passthrough_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown == passthrough_state["raw"].decode("utf-8", errors="replace")


@then("the passthrough document has an empty pages tuple")
def _then_empty_pages(passthrough_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = passthrough_state["doc"]
    assert doc.pages == ()


@then("the passthrough document has an empty images tuple")
def _then_empty_images(passthrough_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = passthrough_state["doc"]
    assert doc.images == ()


@then("the passthrough extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(passthrough_state: dict[str, Any]) -> None:
    extractor: PassthroughExtractor = passthrough_state["extractor"]
    doc: ExtractedDocument = passthrough_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the passthrough extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(passthrough_state: dict[str, Any]) -> None:
    extractor: PassthroughExtractor = passthrough_state["extractor"]
    doc: ExtractedDocument = passthrough_state["doc"]
    assert extractor.quality_ok(doc) is False
