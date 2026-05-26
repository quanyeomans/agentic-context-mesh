"""Tests for :class:`EscalatingExtractor` — chain orchestration over the Extractor Protocol.

Drives the framework via the canonical fakes from :mod:`tests.fakes`
(F1-clean: no monkeypatching; F46-clean: composition through the
fakes the production code accepts). Each test sabotage-proves by
mutating production code (recorded inline) so the assertion has teeth.
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.escalation import (
    EscalatingExtractor,
    EscalationResult,
    EscalationStep,
    EscalationTrace,
)
from kairix.core.protocols import DocMetadata, ExtractedDocument
from tests.fakes import (
    FakeMarkitdownExtractor,
    FakeOcrExtractor,
    FakePassthroughExtractor,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: scripted "image-only PDF" raw bytes — short, looks like PDF
# magic but the fake markitdown returns near-empty (we override via
# scripted_markdown=""); the fake ocr returns real text.
# ---------------------------------------------------------------------------


PDF_MAGIC = b"%PDF-1.7\n" + b"\x00" * 1024


def _doc(markdown: str, confidence: float = 0.5) -> ExtractedDocument:
    return ExtractedDocument(
        markdown=markdown,
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_empty_chain_raises_at_construction() -> None:
    """Passing zero members fails fast — there's nothing to orchestrate.

    Sabotage proof: remove the ``if not members`` guard; the test
    passes silently when constructed with empty (no immediate error)
    but later extract() calls fail confusingly. Restored, the
    operator-facing error fires at construction with a fix pointer.
    """
    with pytest.raises(ValueError, match="at least one member"):
        EscalatingExtractor(())


def test_name_composes_chain_member_names() -> None:
    chain = EscalatingExtractor((FakeMarkitdownExtractor(), FakeOcrExtractor()))
    assert chain.name == "escalating(markitdown,ocr)"


def test_version_composes_chain_member_versions_for_f40_retractability() -> None:
    """``version`` carries every tier's version so F40 fires re-extract
    when ANY tier bumps. Sabotage: change ``|`` to ``,`` (looks similar
    but breaks the parse downstream). The exact format assertion
    catches the drift.
    """
    chain = EscalatingExtractor(
        (
            FakeMarkitdownExtractor(version="0.1.5"),
            FakeOcrExtractor(version="0.3.10"),
        )
    )
    assert chain.version == "markitdown@0.1.5|ocr@0.3.10"


# ---------------------------------------------------------------------------
# can_extract
# ---------------------------------------------------------------------------


def test_can_extract_true_if_any_member_claims_mime() -> None:
    """Passthrough refuses PDF; markitdown claims it. Chain claims it."""
    chain = EscalatingExtractor((FakePassthroughExtractor(), FakeMarkitdownExtractor()))
    assert chain.can_extract("application/pdf", b"%PDF")


def test_can_extract_false_when_no_member_claims_mime() -> None:
    chain = EscalatingExtractor((FakeMarkitdownExtractor(),))
    # markitdown refuses an arbitrary mime that isn't in its supported set + no PDF magic
    assert not chain.can_extract("application/x-unknown", b"\x00\x00")


# ---------------------------------------------------------------------------
# extract — happy path: first tier passes quality_ok, no escalation
# ---------------------------------------------------------------------------


def test_first_tier_quality_ok_short_circuits_chain() -> None:
    """When markitdown returns quality_ok=True, ocr is never called.

    Sabotage: remove the ``if ok: return`` from extract_with_trace;
    the chain continues into ocr and the trace shows two steps
    instead of one. Restored, only markitdown runs.
    """
    chain = EscalatingExtractor((FakeMarkitdownExtractor(), FakeOcrExtractor()))
    result = chain.extract_with_trace(PDF_MAGIC, "application/pdf")
    assert isinstance(result, EscalationResult)
    assert result.trace.winning_extractor == "markitdown"
    assert result.trace.exhausted is False
    assert len(result.trace.steps) == 1
    assert result.trace.steps[0].quality_ok is True


# ---------------------------------------------------------------------------
# extract — escalation: first tier fails quality_ok, second tier passes
# ---------------------------------------------------------------------------


def test_quality_ok_false_falls_through_to_next_tier() -> None:
    """When markitdown's scripted markdown is too short (image-only PDF
    proxy), the chain escalates to ocr which returns a longer scripted
    output. The winning extractor is ocr.

    Sabotage: change ``if not ok: continue`` to ``return doc`` regardless;
    the test fails because winning_extractor stays 'markitdown'.
    Restored, ocr wins.
    """
    chain = EscalatingExtractor(
        (
            FakeMarkitdownExtractor(scripted_markdown="x"),  # 1 char → quality_ok=False
            FakeOcrExtractor(),
        )
    )
    result = chain.extract_with_trace(PDF_MAGIC, "application/pdf")
    assert result.trace.winning_extractor == "ocr"
    assert result.trace.exhausted is False
    assert len(result.trace.steps) == 2
    assert result.trace.steps[0].extractor_name == "markitdown"
    assert result.trace.steps[0].quality_ok is False
    assert result.trace.steps[1].extractor_name == "ocr"
    assert result.trace.steps[1].quality_ok is True


# ---------------------------------------------------------------------------
# extract — chain exhausted: every tier returns quality_ok=False
# ---------------------------------------------------------------------------


def test_chain_exhausted_returns_longest_attempt_marked_exhausted() -> None:
    """When every member's quality_ok returns False, the chain picks
    the longest-markdown attempt so the operator gets SOMETHING
    indexable, and the trace marks the result exhausted.

    Sabotage: change ``max(attempts, key=lambda pair: len(pair[0].markdown))``
    to ``attempts[0]``; the wrong (shorter) extract wins and the
    assertion on doc.markdown length fails.
    """
    # Inverted lengths so attempts[0] != max-by-length — the
    # sabotage-proof for "longest attempt wins" needs the first
    # attempt to be SHORTER than a later one, otherwise picking
    # attempts[0] silently matches picking max-by-length.
    chain = EscalatingExtractor(
        (
            FakeMarkitdownExtractor(scripted_markdown="x"),  # 1 char (1st)
            FakeOcrExtractor(scripted_markdown="longer fallback"),  # 15 chars (2nd, longer)
        )
    )
    result = chain.extract_with_trace(PDF_MAGIC, "application/pdf")
    assert result.trace.exhausted is True
    # Longest attempt = ocr's 15 chars (not the first, which is 1 char)
    assert result.document.markdown == "longer fallback"
    assert result.trace.winning_extractor == "ocr"


# ---------------------------------------------------------------------------
# extract — exception handling: failing extractor doesn't abort chain
# ---------------------------------------------------------------------------


class _RaisingExtractor:
    """Test seam — extractor whose ``extract()`` always raises.

    F1-clean: not monkeypatching anything; this is a real Extractor
    Protocol impl that happens to raise. Used to assert the chain
    catches per-tier exceptions and continues.
    """

    name = "raising"
    version = "1.0.0"

    def can_extract(self, mime: str, magic_bytes: bytes) -> bool:
        return True

    def extract(self, raw: bytes, mime: str) -> ExtractedDocument:
        raise RuntimeError("scripted failure")

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        return True


def test_extract_exception_caught_and_chain_continues() -> None:
    """A raising tier doesn't kill the chain — it's logged and the
    next tier runs.

    Sabotage: remove the ``try/except`` in extract_with_trace; the
    test fails with RuntimeError. Restored, the chain falls through
    to ocr which returns valid output.
    """
    chain = EscalatingExtractor((_RaisingExtractor(), FakeOcrExtractor()))
    result = chain.extract_with_trace(PDF_MAGIC, "application/pdf")
    assert result.trace.winning_extractor == "ocr"
    assert result.trace.steps[0].raised == "RuntimeError"
    assert result.trace.steps[1].raised is None


def test_all_tiers_raise_propagates_last_exception() -> None:
    """When every tier raises, the chain re-raises the LAST exception
    so the caller sees real failure (not a silent empty doc).

    Sabotage: change ``raise last_exc`` to ``return empty_doc``; the
    test fails because no exception is raised. Restored, RuntimeError
    propagates.
    """
    chain = EscalatingExtractor((_RaisingExtractor(), _RaisingExtractor()))
    with pytest.raises(RuntimeError, match="scripted failure"):
        chain.extract(PDF_MAGIC, "application/pdf")


# ---------------------------------------------------------------------------
# extract — no member claims the mime
# ---------------------------------------------------------------------------


def test_no_member_claims_mime_raises_keyerror_with_fix_pointer() -> None:
    """Mirrors ExtractorRegistry's KeyError shape so the operator sees
    a consistent error across single-extractor and chain paths.

    Sabotage: change ``raise KeyError(...)`` to ``return empty_doc``;
    the test fails because no error is raised.
    """
    chain = EscalatingExtractor((FakePassthroughExtractor(),))
    with pytest.raises(KeyError, match=r"escalating.*passthrough.*claims mime"):
        chain.extract(b"\x00", "application/x-totally-unknown")


# ---------------------------------------------------------------------------
# extract Protocol shape — chain is a drop-in Extractor
# ---------------------------------------------------------------------------


def test_chain_satisfies_extractor_protocol_methods() -> None:
    """Duck-typed Protocol check: the chain has ``name``, ``version``,
    ``can_extract``, ``extract``, ``quality_ok`` — same shape as a
    single extractor. Production wiring can swap chain in without
    structural changes.
    """
    chain = EscalatingExtractor((FakeMarkitdownExtractor(),))
    assert hasattr(chain, "name")
    assert hasattr(chain, "version")
    assert callable(chain.can_extract)
    assert callable(chain.extract)
    assert callable(chain.quality_ok)
    # And it actually runs:
    doc = chain.extract(PDF_MAGIC, "application/pdf")
    assert isinstance(doc, ExtractedDocument)


# ---------------------------------------------------------------------------
# Frozen-dataclass discipline at the boundary (F42)
# ---------------------------------------------------------------------------


def test_escalation_step_is_frozen_per_f42() -> None:
    step = EscalationStep(
        extractor_name="markitdown",
        can_extract=True,
        raised=None,
        quality_ok=True,
        markdown_chars=128,
    )
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        step.extractor_name = "mutated"  # type: ignore[misc] — F42 frozen-dc proof; mypy correctly objects, runtime FrozenInstanceError is the assertion


def test_escalation_trace_is_frozen_per_f42() -> None:
    trace = EscalationTrace(steps=(), winning_extractor="x", exhausted=False)
    with pytest.raises((AttributeError, Exception)):
        trace.exhausted = True  # type: ignore[misc] — F42 frozen-dc proof; mypy correctly objects, runtime FrozenInstanceError is the assertion
