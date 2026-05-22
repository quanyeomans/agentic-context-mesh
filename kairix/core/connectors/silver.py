"""Silver processing - the SINGULAR chunking + entity-signal extraction surface.

F38 makes this file the one and only home for chunking and entity-
signal extraction in the connector framework. No per-connector
chunker. No per-extractor chunker. The orchestrator
(:mod:`kairix.core.connectors.pipeline`) hands every Bronze record plus
its :class:`~kairix.core.protocols.ExtractedDocument` to
:meth:`SilverProcessor.process`; Silver returns a
:class:`~kairix.core.protocols.SilverOutput` carrying
``(chunks, entity_signals)`` - a tuple of frozen dataclasses per F42.

Plain Python, no LLM (per KFEAT-005). LLM-driven work (fact
extraction in :mod:`kairix.corpus.ingest`, Curator enrichment) stays on
existing surfaces. The connector path and the conversational corpus
path are disjoint.

The :class:`~kairix.core.protocols.SilverProcessor` Protocol on
:mod:`kairix.core.protocols` is the public seam; this module ships the
production implementation skeleton (Wave 2 fills in the body).

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons.
"""

from __future__ import annotations

from kairix.core.protocols import (
    BronzeRef,
    ExtractedDocument,
    Sensitivity,
    SilverOutput,
)


class DefaultSilverProcessor:
    """Production :class:`~kairix.core.protocols.SilverProcessor` implementation.

    SINGULAR Silver surface per F38 - chunking + entity-signal
    extraction live ONLY here in production code. Per-connector
    chunkers are a regression and pre-commit blocks them.

    Wave 1 ships the seam-and-shape only; :meth:`process` raises
    :class:`NotImplementedError`. Wave 2 (IM-1 / IM-2) lands the real
    chunker + signal extractor; the resulting chunks carry
    ``source_uri`` + ``source_modified_at`` + ``sensitivity`` per F39.
    """

    # process(raw, extracted, source_uri, source_modified_at, sensitivity) -> SilverOutput
    # Wave 2: split ``extracted.markdown`` into chunks (page-aware
    # citation via ``extracted.pages``); run the Plain-Python entity-
    # signal extractor over the rendered text; emit a
    # :class:`SilverOutput` with the chunks tagged
    # ``(source_uri, source_modified_at, sensitivity)`` per F39.
    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
    ) -> SilverOutput:
        raise NotImplementedError("DefaultSilverProcessor.process - Wave 2 (SC-1 ships the seam only).")
