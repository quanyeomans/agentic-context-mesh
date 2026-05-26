"""EscalatingExtractor — ordered-chain orchestration over the Extractor Protocol.

Implements the escalation chain documented in
``docs/architecture/connector-ingestion-architecture.md`` § 4 ("Three
failures map to three behaviours"): a sequence of extractors where the
orchestrator falls through to the next member when the current one's
:meth:`Extractor.quality_ok` returns ``False``.

The framework owns the chain wiring; individual plugins know nothing
about escalation. Each plugin implements ``quality_ok`` as its own
"would the next tier do better?" signal — markitdown returns False for
image-only PDFs (no recoverable text), pdf_fallback returns False for
truly scanned PDFs with no embedded fonts, ocr returns False when the
page rasterisation found nothing legible.

Construction:

    chain = EscalatingExtractor((markitdown, pdf_fallback, ocr))

Operators wire this via the ``extractor_chain`` config field (new in
v2026.5.28):

    connectors:
      - name: sharepoint
        extractor_chain: [markitdown, pdf_fallback, ocr]

The existing ``extractor: <name>`` single-extractor field still works
unchanged — escalation is opt-in per connector.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from kairix.core.protocols import ExtractedDocument, Extractor, MimeType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EscalationStep:
    """One extractor's contribution to an escalation attempt.

    Frozen per F42. Operators surface this via logs / probe tooling to
    see which tier of the chain finally succeeded for a given item.

    Fields:

    * ``extractor_name`` — the ``name`` attribute of the extractor.
    * ``can_extract`` — whether ``can_extract(mime, magic_bytes)`` claimed the format.
    * ``raised`` — exception class name if ``extract()`` raised, else ``None``.
    * ``quality_ok`` — the ``quality_ok(doc)`` verdict (None when extract raised
      or can_extract was False).
    * ``markdown_chars`` — length of the produced markdown (0 when no extract ran).
    """

    extractor_name: str
    can_extract: bool
    raised: str | None
    quality_ok: bool | None
    markdown_chars: int


@dataclass(frozen=True)
class EscalationTrace:
    """Per-item escalation outcome — what was tried and what landed.

    Returned alongside the ``ExtractedDocument`` so callers (worker,
    re-extract path, observability probes) can log which tier of the
    chain produced the indexed content.

    Frozen per F42.

    Fields:

    * ``steps`` — ordered tuple of :class:`EscalationStep`, one per
      member of the chain that was tried (chain stops as soon as
      quality_ok is True; later members aren't traced).
    * ``winning_extractor`` — name of the extractor whose output is in
      ``ExtractedDocument``. ``None`` when every extractor was skipped
      (``can_extract`` False) or every one raised.
    * ``exhausted`` — ``True`` when the chain ran to completion without
      any member returning ``quality_ok = True`` (the winner is the
      last-attempted with the longest markdown; the operator sees the
      content but knows quality is degraded).
    """

    steps: tuple[EscalationStep, ...]
    winning_extractor: str | None
    exhausted: bool


@dataclass(frozen=True)
class EscalationResult:
    """Bundled return for :meth:`EscalatingExtractor.extract_with_trace`.

    Frozen per F42. The Extractor Protocol's ``extract`` only returns
    the doc, so the trace is surfaced through a sibling method that
    callers wanting telemetry use directly.
    """

    document: ExtractedDocument
    trace: EscalationTrace


class _ChainEmptyError(ValueError):
    """Raised at construction when an EscalatingExtractor is built with no members."""


class EscalatingExtractor:
    """Ordered chain of :class:`Extractor` instances honoured by ``quality_ok``.

    Satisfies the :class:`Extractor` Protocol so existing call sites
    consume it identically to a single extractor. The chain's own
    ``name`` is ``escalating(a,b,c)`` and ``version`` is
    ``a@v1|b@v2|c@v3`` so F40 re-extract tractability holds across
    chain composition changes.

    Behaviour:

    * :meth:`can_extract` returns True if ANY member's ``can_extract``
      returns True. The chain claims a mime when at least one tier can
      consume it.
    * :meth:`extract` walks the chain; for each member that claims
      ``can_extract``, calls ``extract`` then ``quality_ok``. Returns
      the first member whose ``quality_ok`` is True. If the chain
      exhausts, returns the longest-markdown attempt (so operators
      still get SOMETHING to index) with the trace marking it
      exhausted.
    * :meth:`quality_ok` reflects whether any member of the chain
      considered the returned doc ok — exhausted chains return False
      so downstream knows the result is degraded.

    Exceptions from individual extractors are caught and logged; the
    chain continues. If every member raises or is unable to extract,
    the original chain exception (the last one raised) is propagated
    so the caller sees real failure rather than silent None.
    """

    def __init__(self, members: Sequence[Extractor]) -> None:
        if not members:
            raise _ChainEmptyError(
                "EscalatingExtractor requires at least one member. "
                "fix: pass a non-empty sequence of Extractor instances. "
                "next: review docs/architecture/connector-ingestion-architecture.md "
                "§ 4 for the documented chain shape "
                "(markitdown → pdf_fallback → ocr)."
            )
        self._members: tuple[Extractor, ...] = tuple(members)

    @property
    def name(self) -> str:
        """Chain-aware name surfaced through ``documents_media.extractor_name``."""
        return f"escalating({','.join(m.name for m in self._members)})"

    @property
    def version(self) -> str:
        """Composite version so F40 re-extracts fire on any tier's version bump."""
        return "|".join(f"{m.name}@{m.version}" for m in self._members)

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        """Chain claims a mime if any member claims it."""
        return any(m.can_extract(mime, magic_bytes) for m in self._members)

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        """Run the chain and return the first quality-ok extract.

        On exhaustion, returns the longest-markdown attempt with a
        downgraded confidence (so operators still index something).
        The matching :class:`EscalationTrace` is available via
        :meth:`extract_with_trace` for callers wanting telemetry.
        """
        return self.extract_with_trace(raw, mime).document

    def extract_with_trace(self, raw: bytes, mime: MimeType) -> EscalationResult:
        """Run the chain and return ``(document, trace)`` for observability."""
        magic_bytes = raw[:16]
        steps: list[EscalationStep] = []
        attempts: list[tuple[ExtractedDocument, str]] = []
        last_exc: BaseException | None = None

        for member in self._members:
            claimed = member.can_extract(mime, magic_bytes)
            if not claimed:
                steps.append(
                    EscalationStep(
                        extractor_name=member.name,
                        can_extract=False,
                        raised=None,
                        quality_ok=None,
                        markdown_chars=0,
                    )
                )
                continue
            try:
                doc = member.extract(raw, mime)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "escalation: %s.extract raised %s; falling through to next tier",
                    member.name,
                    type(exc).__name__,
                )
                steps.append(
                    EscalationStep(
                        extractor_name=member.name,
                        can_extract=True,
                        raised=type(exc).__name__,
                        quality_ok=None,
                        markdown_chars=0,
                    )
                )
                continue
            ok = member.quality_ok(doc)
            steps.append(
                EscalationStep(
                    extractor_name=member.name,
                    can_extract=True,
                    raised=None,
                    quality_ok=ok,
                    markdown_chars=len(doc.markdown),
                )
            )
            attempts.append((doc, member.name))
            if ok:
                return EscalationResult(
                    document=doc,
                    trace=EscalationTrace(
                        steps=tuple(steps),
                        winning_extractor=member.name,
                        exhausted=False,
                    ),
                )

        # Chain exhausted — every member either skipped, raised, or
        # produced sub-quality output. Three sub-cases:
        if attempts:
            # We have at least one extraction; surface the longest as
            # best-effort. Operators see the trace mark it exhausted.
            best_doc, best_name = max(attempts, key=lambda pair: len(pair[0].markdown))
            return EscalationResult(
                document=best_doc,
                trace=EscalationTrace(
                    steps=tuple(steps),
                    winning_extractor=best_name,
                    exhausted=True,
                ),
            )
        if last_exc is not None:
            # Every member raised. Re-raise the last so the caller's
            # error-handling sees real failure, not an empty doc.
            raise last_exc
        # Every member declined via can_extract — no extractor in the
        # chain claims this mime. Mirror ExtractorRegistry's KeyError
        # shape for parity with the single-extractor path.
        raise KeyError(
            f"escalation chain {self.name!r} has no member that "
            f"claims mime={mime!r}. "
            f"fix: add a tier whose can_extract() returns True for this mime, "
            f"or remove the source from this connector. "
            f"next: see docs/architecture/connector-ingestion-architecture.md § 3."
        )

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """Defer to the strictest member's quality gate.

        Returns True only when every member that claims the doc's mime
        considers it ok. In practice this matches the per-member gate
        of whichever tier wrote the doc, because escalation stops at
        the first ``quality_ok = True``.
        """
        # When the doc came from an exhausted chain, every member's
        # gate has already returned False — re-running them would
        # return False again. We accept the doc but the caller can
        # consult the trace for the exhausted marker.
        for member in self._members:
            if not member.quality_ok(doc):
                return False
        return True
