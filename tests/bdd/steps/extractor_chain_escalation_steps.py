"""Step definitions for ``extractor_chain_escalation.feature``.

Drives the real :class:`EscalatingExtractor` through the public
:func:`build_extractor_from_entry` config seam (F46-clean: composition
through the production helper, not direct instantiation of the
pipeline) AND through direct construction for trace-shape assertions
that aren't reachable through the config seam.

The canonical fakes from :mod:`tests.fakes` (FakeMarkitdownExtractor,
FakeOcrExtractor, FakePassthroughExtractor) compose the chain — F1-clean
(no monkeypatching of kairix internals) and F47-clean (factory
composition is the only direct construction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.escalation import EscalatingExtractor, EscalationResult
from kairix.core.connectors.registry import build_extractor_from_entry
from kairix.core.protocols import ExtractedDocument
from tests.fakes import FakeMarkitdownExtractor, FakeOcrExtractor

pytestmark = pytest.mark.bdd


@dataclass
class _ChainCtx:
    """Mutable per-scenario context for the chain steps."""

    chain: EscalatingExtractor | None = None
    config_entry: dict[str, Any] | None = None
    build_result: Any = None
    build_exception: Exception | None = None
    extract_result: EscalationResult | None = None


@pytest.fixture
def chain_ctx() -> _ChainCtx:
    return _ChainCtx()


PDF_MAGIC = b"%PDF-1.7\n" + b"\x00" * 1024


class _RaisingExtractor:
    """Test seam — F1-clean; not monkeypatching anything."""

    name = "raising"
    version = "1.0.0"

    def can_extract(self, mime: str, magic_bytes: bytes) -> bool:
        return True

    def extract(self, raw: bytes, mime: str) -> ExtractedDocument:
        raise RuntimeError("scripted chain-tier failure")

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        return True


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('an escalating chain wrapping "markitdown" then "ocr"'))
def given_chain_markitdown_then_ocr(chain_ctx: _ChainCtx) -> None:
    """Build a real EscalatingExtractor with the canonical fakes —
    payload scripted per step that follows."""
    chain_ctx.chain = EscalatingExtractor((FakeMarkitdownExtractor(), FakeOcrExtractor()))


@given(parsers.parse("a payload that markitdown will recover with quality_ok true"))
def given_markitdown_quality_ok(chain_ctx: _ChainCtx) -> None:
    # Default FakeMarkitdownExtractor scripted_markdown is long enough for quality_ok=True
    pass


@given(
    parsers.parse(
        "a payload that markitdown will recover with quality_ok false but ocr will recover with quality_ok true"
    )
)
def given_markitdown_fails_ocr_recovers(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.chain is not None
    # Replace markitdown member with one that scripts an empty result (quality_ok=False)
    chain_ctx.chain = EscalatingExtractor(
        (
            FakeMarkitdownExtractor(scripted_markdown="x"),  # 1 char → quality_ok=False
            FakeOcrExtractor(),  # long scripted output → quality_ok=True
        )
    )


@given(parsers.parse("every tier in the chain will return quality_ok false"))
def given_every_tier_fails(chain_ctx: _ChainCtx) -> None:
    # Both tiers script-short → quality_ok=False everywhere. Ocr longer so it wins the
    # exhausted longest-attempt tiebreak.
    chain_ctx.chain = EscalatingExtractor(
        (
            FakeMarkitdownExtractor(scripted_markdown="md"),  # 2 chars
            FakeOcrExtractor(scripted_markdown="longer ocr output"),  # 17 chars
        )
    )


@given(parsers.parse("an escalating chain whose first tier raises during extract"))
def given_first_tier_raises(chain_ctx: _ChainCtx) -> None:
    chain_ctx.chain = EscalatingExtractor((_RaisingExtractor(), FakeOcrExtractor()))


@given(parsers.parse("a second tier that recovers cleanly"))
def given_second_tier_clean(chain_ctx: _ChainCtx) -> None:
    pass  # FakeOcrExtractor's default script recovers cleanly


@given(parsers.parse('a connector config with extractor_chain set to "passthrough,passthrough"'))
def given_config_chain_passthrough(chain_ctx: _ChainCtx) -> None:
    chain_ctx.config_entry = {"extractor_chain": ["passthrough", "passthrough"]}


@given(parsers.parse('a connector config with extractor set to "passthrough"'))
def given_config_single_passthrough(chain_ctx: _ChainCtx) -> None:
    chain_ctx.config_entry = {"extractor": "passthrough"}


@given(parsers.parse("a connector config with extractor_chain set to a single string instead of a list"))
def given_config_chain_typo(chain_ctx: _ChainCtx) -> None:
    chain_ctx.config_entry = {"extractor_chain": "passthrough"}


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator invokes extract on the chain"))
def when_invoke_extract(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.chain is not None
    chain_ctx.extract_result = chain_ctx.chain.extract_with_trace(PDF_MAGIC, "application/pdf")


@when(parsers.parse("the operator builds the extractor from the config entry"))
def when_build_from_config(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.config_entry is not None
    try:
        chain_ctx.build_result = build_extractor_from_entry(chain_ctx.config_entry)
    except Exception as exc:
        chain_ctx.build_exception = exc


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the chain returns the markitdown output"))
def then_markitdown_output(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert "scripted markdown" in chain_ctx.extract_result.document.markdown


@then(parsers.parse("the chain returns the ocr output"))
def then_ocr_output(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    # FakeOcrExtractor's default markdown is long, recognisable
    assert len(chain_ctx.extract_result.document.markdown) > 50


@then(parsers.parse("the chain returns the longest-markdown attempt"))
def then_longest_attempt(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    # The exhausted-chain scenario set ocr to 17 chars vs markitdown's 2 chars
    assert chain_ctx.extract_result.document.markdown == "longer ocr output"


@then(parsers.parse("the chain returns the second tier's output"))
def then_second_tier_output(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.winning_extractor == "ocr"


@then(parsers.parse("the escalation trace shows markitdown won"))
def then_trace_markitdown_won(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.winning_extractor == "markitdown"


@then(parsers.parse("the escalation trace shows ocr won"))
def then_trace_ocr_won(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.winning_extractor == "ocr"


@then(parsers.parse("the escalation trace records exactly one step"))
def then_one_step(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert len(chain_ctx.extract_result.trace.steps) == 1


@then(parsers.parse("the escalation trace records exactly two steps"))
def then_two_steps(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert len(chain_ctx.extract_result.trace.steps) == 2


@then(parsers.parse("the escalation trace is not marked exhausted"))
def then_not_exhausted(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.exhausted is False


@then(parsers.parse("the escalation trace is marked exhausted"))
def then_exhausted(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.exhausted is True


@then(parsers.parse("the escalation trace records the first tier's exception class"))
def then_trace_exception(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.extract_result is not None
    assert chain_ctx.extract_result.trace.steps[0].raised == "RuntimeError"


@then(parsers.parse("the result is an EscalatingExtractor wrapping the named tiers"))
def then_result_is_chain(chain_ctx: _ChainCtx) -> None:
    assert isinstance(chain_ctx.build_result, EscalatingExtractor)


@then(parsers.parse("the result is a single passthrough extractor"))
def then_result_is_single(chain_ctx: _ChainCtx) -> None:
    assert chain_ctx.build_result is not None
    assert chain_ctx.build_result.name == "passthrough"


@then(parsers.parse("the result is not an EscalatingExtractor"))
def then_result_not_chain(chain_ctx: _ChainCtx) -> None:
    assert not isinstance(chain_ctx.build_result, EscalatingExtractor)


@then(parsers.parse('the build raises ValueError mentioning "{phrase}"'))
def then_raises_valueerror(chain_ctx: _ChainCtx, phrase: str) -> None:
    assert isinstance(chain_ctx.build_exception, ValueError)
    assert phrase in str(chain_ctx.build_exception)
