"""Step definitions for ``extractor_gotenberg.feature`` (PR-3).

Drives the real :class:`kairix.extractors.gotenberg.GotenbergExtractor`
with two injected seams so neither the gotenberg HTTP service nor the
real ``pdf_fallback`` library is reached:

  * ``http_client`` — an :class:`httpx.Client` wired to
    :class:`httpx.MockTransport`. The "returns a converted PDF" Given
    serves ``%PDF`` bytes; the "unreachable" Given raises
    :class:`httpx.ConnectError`.
  * ``pdf_extractor`` — the canonical :class:`FakePdfFallbackExtractor`
    the converted PDF re-enters.

Production code is exercised end-to-end (the real convert → re-enter
chain) — F1-clean (no monkeypatch), F2-clean (no env mutation).

Step phrasings carry the literal phrase "gotenberg" so the global
pytest-bdd step registry doesn't collide with the docx / markitdown /
passthrough / pdf_fallback / ocr features' analogous Given/When/Then
phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the @happy_path scenario.
  * "does not claim the mime type" — broadening ``can_extract`` to
    claim PDF / text / octet-stream fails the @error scenarios.
  * "carries non-empty markdown" — dropping the convert→re-enter call
    in production returns empty markdown and fails the step.
  * "raises so the orchestrator escalates" — softening the unreachable
    path to "return an empty doc" fails the step (and would let a
    transient outage silently skip the item).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.gotenberg import (
    GotenbergExtractor,
    make_extractor,
    version,
)
from tests.fakes import FakePdfFallbackExtractor

pytestmark = pytest.mark.bdd

# Legacy .doc — a format with no in-process extractor, so gotenberg claims
# it. Modern OOXML docx is NOW handled in-process (markitdown / docx) and is
# refused by gotenberg (the @error OOXML scenario pins that refusal).
_DOC_MIME = "application/msword"
_PDF_BYTES = b"%PDF-1.7\n" + (b"converted-pdf-payload " * 8)


def _pdf_returning_client() -> httpx.Client:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PDF_BYTES)

    return httpx.Client(transport=httpx.MockTransport(_handler))


def _unreachable_client() -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.Client(transport=httpx.MockTransport(_handler))


@pytest.fixture
def gotenberg_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "name": "gotenberg",
        "client": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
        "raised": None,
    }


def _build(gotenberg_state: dict[str, Any]) -> GotenbergExtractor:
    """Construct the real extractor from the scenario's wired seams."""
    real = make_extractor()
    assert isinstance(real, GotenbergExtractor)
    assert real.name == gotenberg_state["name"]
    return GotenbergExtractor(
        version=version,
        http_client=gotenberg_state["client"] or _pdf_returning_client(),
        pdf_extractor=FakePdfFallbackExtractor(),
    )


@given(parsers.parse('the gotenberg extractor is registered under the name "{name}"'))
def _register_gotenberg(gotenberg_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, GotenbergExtractor)
    assert real.name == name
    gotenberg_state["name"] = name


@given("the gotenberg service is configured to return a converted PDF")
def _service_returns_pdf(gotenberg_state: dict[str, Any]) -> None:
    gotenberg_state["client"] = _pdf_returning_client()


@given("the gotenberg service is unreachable")
def _service_unreachable(gotenberg_state: dict[str, Any]) -> None:
    gotenberg_state["client"] = _unreachable_client()


@given("the operator has raw bytes for a legacy office document with a doc mime")
def _office_bytes(gotenberg_state: dict[str, Any]) -> None:
    # OLE2 compound-file magic — the legacy .doc container signature.
    gotenberg_state["raw"] = b"\xd0\xcf\x11\xe0" + (b"office-ole2-payload " * 16)


@when("the operator asks the gotenberg extractor whether it can extract the office mime")
def _ask_can_extract_office_mime(gotenberg_state: dict[str, Any]) -> None:
    extractor = _build(gotenberg_state)
    gotenberg_state["claimed"] = extractor.can_extract(_DOC_MIME, gotenberg_state["raw"][:8])


@when(parsers.parse('the operator asks the gotenberg extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(gotenberg_state: dict[str, Any], mime: str) -> None:
    extractor = _build(gotenberg_state)
    gotenberg_state["claimed"] = extractor.can_extract(mime, gotenberg_state["raw"][:8])


@when("the operator invokes the gotenberg extractor's extract method on the bytes")
def _invoke_extract(gotenberg_state: dict[str, Any]) -> None:
    extractor = _build(gotenberg_state)
    gotenberg_state["extractor"] = extractor
    gotenberg_state["doc"] = extractor.extract(gotenberg_state["raw"], _DOC_MIME)


@when("the operator invokes the gotenberg extractor's extract method expecting a failure")
def _invoke_extract_expecting_failure(gotenberg_state: dict[str, Any]) -> None:
    extractor = _build(gotenberg_state)
    try:
        extractor.extract(gotenberg_state["raw"], _DOC_MIME)
    except (RuntimeError, ValueError) as exc:
        # The @error scenario asserts gotenberg raises an escalation-
        # triggering error (never a silent empty doc) on an outage.
        gotenberg_state["raised"] = exc


@then("the gotenberg extractor claims the mime type")
def _then_claims(gotenberg_state: dict[str, Any]) -> None:
    assert gotenberg_state["claimed"] is True


@then("the gotenberg extractor does not claim the mime type")
def _then_does_not_claim(gotenberg_state: dict[str, Any]) -> None:
    assert gotenberg_state["claimed"] is False


@then("the gotenberg document carries non-empty markdown")
def _then_non_empty(gotenberg_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = gotenberg_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@then("the gotenberg extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(gotenberg_state: dict[str, Any]) -> None:
    extractor: GotenbergExtractor = gotenberg_state["extractor"]
    doc: ExtractedDocument = gotenberg_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the gotenberg extractor's version string is non-empty")
def _then_version_non_empty(gotenberg_state: dict[str, Any]) -> None:
    extractor: GotenbergExtractor = gotenberg_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
    # Pin to the canonical module-level value (F40).
    assert extractor.version == version


@then("the gotenberg extractor raises so the orchestrator escalates")
def _then_raised(gotenberg_state: dict[str, Any]) -> None:
    raised = gotenberg_state["raised"]
    assert raised is not None
    # A transient gotenberg outage must surface a retryable error, not a
    # silent empty doc — the escalation orchestrator escalates to ocr.
    assert isinstance(raised, (RuntimeError, ValueError))
