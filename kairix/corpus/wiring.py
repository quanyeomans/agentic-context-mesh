"""Composition root for the unified corpus-ingest layer.

This module is the single place that builds production
implementations of the corpus-ingest Protocols
(:class:`~kairix.core.protocols.FactExtractor`,
:class:`~kairix.core.protocols.DocumentWriter`,
:class:`~kairix.core.protocols.CorpusEmbedder`) and the optional
:class:`~kairix.core.facts.consolidation.ConsolidationPass`.

Why a composition root? The Plan B-parity post-mortem traced the
LoCoMo 5% collapse to ``kairix eval`` defaulting to
``_NullFactExtractor`` when no override was passed — 0 facts extracted,
recall floors at 0. The fix is one production-default factory the CLI
use cases call when their caller passes no override. Keeping the
wiring in one place (rather than re-resolving in three call sites:
``ingest-chat``, ``eval``, the LoCoMo harness) means there's exactly
one knob to turn when the production wiring changes.

Design contract
---------------

- **F26 carve-out.** ``kairix/core/**`` may not import providers/transport,
  but :mod:`kairix.corpus.wiring` IS the composition root — it is
  explicitly allowed to import concrete provider/transport
  implementations and any other production layer. The Protocols still
  live in :mod:`kairix.core.protocols`; this module just builds the
  concrete objects domain code receives via Protocol.
- **Lazy imports.** Heavy provider/transport modules are imported
  inside each factory so a caller asking for just one production
  default doesn't pay the cost of resolving the others.
- **Pure functions, no global state.** Every factory takes the
  resources it depends on (the LLM backend, the resolved
  :class:`~kairix.paths.KairixPaths`) as explicit arguments. No env-var
  reads, no module-level singletons.
- **F21 affordance on failure paths.** Today, only
  :func:`make_production_fact_extractor` is implemented — it has the
  smallest dependency graph (LLM only) and is the alpha-blocker for
  the LoCoMo verification gap. The other three factories raise
  :class:`NotImplementedError` with ``fix:`` / ``next:`` / ``run:``
  markers so any caller wiring them prematurely gets an actionable
  error pointing at the deferred work.
"""

from __future__ import annotations

from kairix.core.facts.consolidation import ConsolidationPass
from kairix.core.facts.extractor import LLMFactExtractor
from kairix.core.protocols import (
    CorpusEmbedder,
    DocumentWriter,
    FactExtractor,
    FactStore,
)
from kairix.paths import KairixPaths
from kairix.platform.llm.protocol import LLMBackend

__all__ = [
    "make_production_consolidation",
    "make_production_document_writer",
    "make_production_embedder",
    "make_production_fact_extractor",
]


def make_production_fact_extractor(llm: LLMBackend) -> FactExtractor:
    """Return the production :class:`FactExtractor` wired to ``llm``.

    Wraps :class:`~kairix.core.facts.extractor.LLMFactExtractor`, the
    one Protocol-compliant extractor that actually drives an LLM to
    extract grounded facts from conversation turns. Every
    pre-`make_production_fact_extractor` caller that left
    ``fact_extractor=None`` got a ``_NullFactExtractor`` (returns
    ``[]`` regardless of input) — which is the LoCoMo 5% collapse:
    `kairix eval` returns 0 facts on conversational corpora before
    this wiring lands.

    The default temperature is 0.0 (CI-determinism), matching the
    extractor's own default. Callers wanting non-default sampling
    construct :class:`LLMFactExtractor` directly.

    Args:
        llm:
            Any :class:`LLMBackend` Protocol implementation — typically
            the result of :func:`kairix.platform.llm.get_default_backend`,
            but tests may pass a Fake from ``tests/fakes.py``.

    Returns:
        A :class:`FactExtractor` Protocol implementation ready to be
        passed to :func:`kairix.corpus.ingest.ingest_corpus`,
        :class:`kairix.quality.eval.suite_runner.SuiteRunner`, or
        :func:`kairix.use_cases.ingest_chat.ingest_chat`.
    """
    return LLMFactExtractor(llm=llm)


# ---------------------------------------------------------------------------
# F21 deferral stubs — Phase 2/3 of the corpus-ingest migration.
#
# These three factories are documented placeholders that point callers
# at the canonical hand-wire pattern until the upstream production
# implementations (``MarkdownDocumentWriter``, ``EmbedPipelineEmbedder``,
# the consolidation contradict-fn registry) land. The stubs intentionally
# raise rather than returning a Null* so a caller can't silently degrade
# the way ``_NullFactExtractor`` did before this module existed.
# ---------------------------------------------------------------------------


def make_production_document_writer(paths: KairixPaths) -> DocumentWriter:
    """Production :class:`DocumentWriter` factory — Phase 2 deferral.

    No production :class:`DocumentWriter` implementation exists yet —
    the conversational-ingest writers in :func:`kairix.use_cases.ingest_chat`
    write markdown via private helpers, not a Protocol-shaped writer.
    The Phase 2 migration extracts ``MarkdownDocumentWriter`` from
    those helpers into :mod:`kairix.corpus.writers` and wires it here.

    Callers needing markdown writes today should keep using
    :func:`kairix.use_cases.ingest_chat.ingest_chat` directly until
    this factory lands.

    fix: extract ``MarkdownDocumentWriter`` from ``kairix.use_cases.ingest_chat``
    into ``kairix.corpus.writers.MarkdownDocumentWriter`` and wire here.
    next: see kairix/corpus/README.md "Where each piece lives" — the
    Phase 2 row.
    run: ``grep -rn 'MarkdownDocumentWriter' docs/architecture/`` for the
    migration plan.
    """
    raise NotImplementedError(
        f"make_production_document_writer is deferred (Phase 2). "
        f"paths={paths.document_root!s} would seed the writer's root. "
        f"fix: extract MarkdownDocumentWriter from kairix.use_cases.ingest_chat. "
        f"next: see kairix/corpus/README.md 'Where each piece lives'. "
        f"run: grep -rn 'MarkdownDocumentWriter' docs/architecture/"
    )


def make_production_embedder(paths: KairixPaths) -> CorpusEmbedder:
    """Production :class:`CorpusEmbedder` factory — Phase 2 deferral.

    No production :class:`CorpusEmbedder` implementation exists yet.
    ``kairix embed`` runs the incremental embed pipeline as a CLI
    subprocess after ingest; the Phase 2 migration extracts an in-process
    ``EmbedPipelineEmbedder`` shim that calls the same
    ``run_incremental_embed_pipeline`` entry point.

    Callers needing chunk indexing today should keep the subprocess
    pattern: ``kairix ingest-chat`` → ``kairix embed`` until this
    factory lands.

    fix: wrap ``kairix.core.embed.run_incremental_embed_pipeline`` in
    ``kairix.corpus.embedders.EmbedPipelineEmbedder`` and wire here.
    next: see kairix/corpus/README.md "Where each piece lives" — the
    Phase 2 row.
    run: ``grep -rn 'EmbedPipelineEmbedder' docs/architecture/`` for
    the migration plan.
    """
    raise NotImplementedError(
        f"make_production_embedder is deferred (Phase 2). "
        f"paths={paths.document_root!s} would seed the embedder's scan root. "
        f"fix: wrap kairix.core.embed.run_incremental_embed_pipeline. "
        f"next: see kairix/corpus/README.md 'Where each piece lives'. "
        f"run: grep -rn 'EmbedPipelineEmbedder' docs/architecture/"
    )


def make_production_consolidation(fact_store: FactStore) -> ConsolidationPass:
    """Production :class:`ConsolidationPass` factory — Phase 3 deferral.

    :class:`kairix.core.facts.consolidation.ConsolidationPass` already
    has a clean default constructor —
    ``ConsolidationPass(fact_store=fact_store, contradict=default_contradict)``
    — and :func:`kairix.use_cases.ingest_chat.ingest_chat` builds it
    inline. Once the contradict-fn registry pattern is in place (Phase 3
    — pluggable contradictors per attribute family), this factory will
    be the single place that resolves the operator's registry.

    Today, callers can build it directly:

    .. code-block:: python

        from kairix.core.facts.consolidation import (
            ConsolidationPass,
            default_contradict,
        )

        pass_ = ConsolidationPass(fact_store=store, contradict=default_contradict)

    fix: keep using the direct ``ConsolidationPass(...)`` constructor for now.
    next: register Phase 3's contradict-fn registry here once it lands.
    run: ``grep -rn 'contradict-fn registry' docs/architecture/`` for the
    Phase 3 plan.
    """
    raise NotImplementedError(
        f"make_production_consolidation is deferred (Phase 3). "
        f"fact_store={type(fact_store).__name__} would seed the pass. "
        f"fix: keep using ConsolidationPass(fact_store=store, "
        f"contradict=default_contradict) directly. "
        f"next: see kairix/corpus/README.md 'Where each piece lives'. "
        f"run: grep -rn 'contradict-fn registry' docs/architecture/"
    )
