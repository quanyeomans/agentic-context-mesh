"""Step definitions for ``extractor_docx.feature`` (OF-2, Wave 4).

Drives the real :class:`kairix.extractors.docx.DocxExtractor` against
the recorded docx fixtures under ``tests/fixtures/extractors/``
(``sample.docx`` + ``tracked_changes_sample.docx``). Production code
is exercised end-to-end against real python-docx — F1-clean (no
monkeypatch), F2-clean (no env mutation).

Step phrasings carry the literal phrase "docx" so the global
pytest-bdd step registry doesn't collide with the markitdown /
passthrough / pdf_fallback / ocr features' analogous Given/When/Then
phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the step.
  * "carries non-empty markdown" — flipping ``extract`` to return
    empty markdown in production fails the step.
  * "contains a heading 1 line" — removing the ``Heading 1`` branch
    of :func:`_paragraph_to_markdown` in production fails the step.
  * "contains the inserted accepted text" — flipping the track-
    changes walker to skip ``<w:ins>`` content fails the step.
  * "does not contain the deleted text" — flipping the track-
    changes walker to keep ``<w:del>`` content fails the step.
  * "flags that tracked changes were present" — clearing the
    ``last_extract_had_tracked_changes`` boolean fails the step.
  * "quality_ok false for the produced document" (escalation gate) —
    flipping ``quality_ok`` to return ``True`` unconditionally fails
    the @error scenario.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.docx import (
    DocxExtractor,
    make_extractor,
    version,
)

pytestmark = pytest.mark.bdd

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "extractors"
_SAMPLE_DOCX = _FIXTURE_DIR / "sample.docx"
_TRACKED_CHANGES_DOCX = _FIXTURE_DIR / "tracked_changes_sample.docx"


@pytest.fixture
def docx_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
    }


@given(parsers.parse('the docx extractor is registered under the name "{name}"'))
def _register_docx(docx_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, DocxExtractor)
    assert real.name == name
    docx_state["extractor"] = real


@given("the operator has raw bytes for a small docx with three heading levels and one table")
def _docx_sample_bytes(docx_state: dict[str, Any]) -> None:
    docx_state["raw"] = _SAMPLE_DOCX.read_bytes()


@given("the operator has raw bytes whose first four bytes are PK zip magic")
def _docx_zip_magic_bytes(docx_state: dict[str, Any]) -> None:
    docx_state["raw"] = b"PK\x03\x04" + b"trailing-bytes"


@given("the operator has raw bytes for a docx with inline tracked changes")
def _docx_tracked_bytes(docx_state: dict[str, Any]) -> None:
    docx_state["raw"] = _TRACKED_CHANGES_DOCX.read_bytes()


@given("the operator hands docx an essentially empty document body")
def _docx_empty_body(docx_state: dict[str, Any]) -> None:
    # Build an in-memory docx with no headings and almost no body so
    # the quality gate (>=100 chars AND >=1 heading) returns False.
    import docx as _docx  # local import — only this step needs the lib

    document = _docx.Document()
    document.add_paragraph("x")
    tmp = _FIXTURE_DIR / "_empty_body_scratch.docx"
    document.save(str(tmp))
    docx_state["raw"] = tmp.read_bytes()
    tmp.unlink()


@when("the operator asks the docx extractor whether it can extract the docx mime")
def _ask_can_extract_docx_mime(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    docx_state["claimed"] = extractor.can_extract(_DOCX_MIME, docx_state["raw"][:8])


@when(parsers.parse('the operator asks the docx extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(docx_state: dict[str, Any], mime: str) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    docx_state["claimed"] = extractor.can_extract(mime, docx_state["raw"][:8])


@when("the operator invokes the docx extractor's extract method on the bytes")
def _invoke_extract(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    docx_state["doc"] = extractor.extract(docx_state["raw"], _DOCX_MIME)


@then("the docx extractor claims the mime type")
def _then_claims(docx_state: dict[str, Any]) -> None:
    assert docx_state["claimed"] is True


@then("the docx extractor does not claim the mime type")
def _then_does_not_claim(docx_state: dict[str, Any]) -> None:
    assert docx_state["claimed"] is False


@then("the docx document carries non-empty markdown")
def _then_non_empty(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@then("the docx markdown contains a heading 1 line")
def _then_h1(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("# ") for line in doc.markdown.splitlines())


@then("the docx markdown contains a heading 2 line")
def _then_h2(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("## ") for line in doc.markdown.splitlines())


@then("the docx markdown contains a heading 3 line")
def _then_h3(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("### ") for line in doc.markdown.splitlines())


@then("the docx markdown contains a bullet list item")
def _then_bullet(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("- ") for line in doc.markdown.splitlines())


@then("the docx markdown contains a numbered list item")
def _then_numbered(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("1. ") for line in doc.markdown.splitlines())


@then("the docx markdown contains a pipe-syntax table row")
def _then_table(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert any(line.startswith("| ") and line.endswith(" |") for line in doc.markdown.splitlines())


@then("the docx markdown contains the inserted accepted text")
def _then_accepted_text(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert "INSERTED-CONTENT" in doc.markdown


@then("the docx markdown does not contain the deleted text")
def _then_rejected_text(docx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = docx_state["doc"]
    assert "DELETED-CONTENT" not in doc.markdown


@then("the docx extractor flags that tracked changes were present")
def _then_flags_tracked(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    assert extractor.last_extract_had_tracked_changes is True


@then("the docx extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    doc: ExtractedDocument = docx_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the docx extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    doc: ExtractedDocument = docx_state["doc"]
    assert extractor.quality_ok(doc) is False


@then("the docx extractor's version string is non-empty")
def _then_version_non_empty(docx_state: dict[str, Any]) -> None:
    extractor: DocxExtractor = docx_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
    # Pin to the canonical module-level value (F40).
    assert extractor.version == version
