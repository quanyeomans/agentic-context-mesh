"""
Domain protocol definitions for kairix core boundaries.

Each Protocol represents the agreed interface between bounded contexts.
All protocols use @runtime_checkable so contract tests can verify
conformance via isinstance() checks.

Follows the same pattern as kairix.platform.llm.protocol.LLMBackend.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from kairix.core.search.intent import QueryIntent

if TYPE_CHECKING:
    # F42: FeatureFlagResolver.iter_all returns FlagStatus (frozen-dc);
    # import behind TYPE_CHECKING to keep protocols.py free of the
    # kairix.core.features import cycle at module-load time.
    from kairix.core.features.resolver import FlagStatus


@runtime_checkable
class IntentClassifier(Protocol):
    """Classifies a search query into a QueryIntent dispatch category."""

    def classify(self, query: str) -> QueryIntent:
        """Return the QueryIntent dispatch category for ``query``."""
        ...


@runtime_checkable
class DocumentRepository(Protocol):
    """Read/write interface for the document store (SQLite FTS5 backed)."""

    def search_fts(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` FTS5-ranked document rows for ``query``."""
        ...

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        """Return the document row for ``path``, or None if absent."""
        ...

    def get_chunk_dates(self, paths: list[str]) -> dict[str, str]:
        """Return a ``path -> ISO-8601 date`` map for the given document paths."""
        ...

    def insert_or_update(
        self,
        path: str,
        collection: str,
        title: str,
        content: str,
        content_hash: str,
    ) -> None:
        """UPSERT the document row keyed on ``path`` and refresh FTS state."""
        ...


@runtime_checkable
class GraphRepository(Protocol):
    """Interface for the entity graph (Neo4j backed)."""

    @property
    def available(self) -> bool:
        """True when the graph backend is reachable; False when degraded or offline."""
        ...

    def find_entity(self, name: str) -> dict[str, Any] | None:
        """Return the entity row whose canonical name matches ``name``, else None."""
        ...

    def entity_in_degrees(self) -> list[dict[str, Any]]:
        """Return per-entity in-degree counts for diagnostics / ranking signals."""
        ...

    def cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute ``query`` with optional ``params`` and return result rows."""
        ...


@runtime_checkable
class VectorRepository(Protocol):
    """Interface for the vector index (usearch backed)."""

    def search(
        self,
        query_vec: list[float],
        k: int,
        collections: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``k`` nearest-neighbour rows for ``query_vec``."""
        ...

    def add_vectors(self, items: list[tuple[str, list[float]]]) -> int:
        """Insert/update the ``(path, vector)`` pairs and return rows written."""
        ...

    def count(self) -> int:
        """Return the total number of vectors currently indexed."""
        ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Text embedding interface (single and batch)."""

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input string, in input order."""
        ...


@runtime_checkable
class FusionStrategy(Protocol):
    """Fuses BM25 and vector result lists into a single ranked list."""

    def fuse(self, bm25: list[Any], vec: list[Any]) -> list[Any]:
        """Merge BM25 and vector hits into a single ranked result list."""
        ...


@runtime_checkable
class BoostStrategy(Protocol):
    """Post-fusion boost strategy (entity, procedural, temporal, etc.)."""

    def boost(self, results: list[Any], query: str, context: dict[str, Any]) -> list[Any]:
        """Re-rank ``results`` in place of strategy-specific signals; return the new order."""
        ...


@runtime_checkable
class ScoringStrategy(Protocol):
    """Scores retrieved results against gold-standard documents."""

    def score(self, retrieved: list[str], gold: list[dict[str, Any]]) -> float:
        """Return the relevance score of ``retrieved`` against ``gold`` (0.0-1.0)."""
        ...


@runtime_checkable
class SearchLogger(Protocol):
    """Structured logging for search and query events."""

    def log_search(self, event: dict[str, Any]) -> None:
        """Emit a structured search-event record (query, hits, latency, …)."""
        ...

    def log_query(self, event: dict[str, Any]) -> None:
        """Emit a structured query-event record (intent, agent, context, …)."""
        ...


@runtime_checkable
class CollectionResolver(Protocol):
    """Resolves the collection list for a search call given an agent + scope.

    Returning None means "no collection filter — search everything". Returning
    a non-empty list scopes BM25 and vector backends to those collection names.
    Returning an empty list is equivalent to None.

    Implementations should be constructed at the boundary (factory.py) with
    the loaded CollectionsConfig and any environment-derived extras, so that
    business logic only depends on the Protocol surface (G4: config at boundary).
    """

    def resolve(self, agent: str | None, scope: Any) -> list[str] | None:
        """Return the concrete collection list for ``(agent, scope)``; None = no filter."""
        ...


@runtime_checkable
class AgentRegistry(Protocol):
    """Declarative agent → collection mapping for the multi-agent architecture.

    Used by:
      - CollectionResolver (resolves scope=all-agents / everything to the
        concrete list of agent collection names).
      - Embed pipeline (validates that writes under an agent's write_path
        are being performed by that agent).

    Implementations are constructed once at startup from the YAML config
    (G4: config at boundary). When the YAML has no ``agents:`` section the
    registry is empty and callers get explicit NotImplementedError for
    ALL_AGENTS / EVERYTHING scope so the misconfiguration is loud.
    """

    def list_agents(self) -> list[Any]:
        """Return all configured agent definitions (declarative YAML rows)."""
        ...

    def collection_for(self, name: str) -> str:
        """Return the collection name owned by agent ``name``. Raises if absent."""
        ...

    def validate_write(self, agent_name: str, path: str) -> bool:
        """Return True if ``agent_name`` is allowed to write under ``path``."""
        ...


# ---------------------------------------------------------------------------
# Eval-module protocols (#143 Phase 1 — paired with FakeXxx in tests/fakes.py)
#
# These four protocols define the boundary between the eval module and the
# external systems it depends on (LLM chat, vector retrieval, the corpus
# itself). Phase 2a/2b refactor judge.py / hybrid_sweep.py / generate.py /
# gold_builder.py to consume these protocols via constructor injection,
# eliminating the *_fn=None test-substitution kwargs scattered across the
# eval surface today.
# ---------------------------------------------------------------------------


@runtime_checkable
class ChatBackend(Protocol):
    """LLM chat-completion surface — substitutable across Azure / OpenRouter / fakes.

    Wraps the OpenAI-API chat-completions call shape that every
    provider plugin (``kairix/providers/<name>/``) speaks. The eval
    module's LLM judge and query generator consume this protocol so
    test code can inject a `FakeChatBackend` rather than reaching past
    `_call_llm` into module-level state.

    Implementations are expected to:
      - Block until the response is complete (no streaming surface here).
      - Apply their own retry / rate-limit policy internally.
      - Raise on credential failure rather than returning empty content.
    """

    def complete(
        self,
        prompt: str,
        *,
        api_key: str,
        endpoint: str,
        deployment: str,
        system: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 30.0,
    ) -> str:
        """Return the completed assistant message text for ``prompt``."""
        ...


@runtime_checkable
class LLMJudge(Protocol):
    """Pairwise / pointwise relevance judge over (query, document) pairs.

    The judge labels each candidate document for a query with a 0/1/2
    relevance grade. Production implementations call out to an LLM via
    `ChatBackend`; tests use `FakeLLMJudge` returning pre-configured grades.

    Implementations are expected to:
      - Never raise — return all-zero grades on any error.
      - Shuffle candidate order before judging to prevent positional bias.
      - Return a `JudgeResult`-shaped value (query, grades, shuffle_order,
        judge_model, calibration_passed).
    """

    def grade(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        *,
        runs: int = 1,
    ) -> Any:
        """Return a JudgeResult-shaped object with per-candidate 0/1/2 relevance grades."""
        ...

    def calibrate(self) -> bool:
        """Return True when the judge passes its sanity-check calibration suite."""
        ...


@runtime_checkable
class QueryGenerator(Protocol):
    """Synthesises retrieval evaluation queries from a corpus document.

    Production implementations call out to an LLM to generate diverse,
    intent-tagged queries that the source document would be the primary
    answer for. Tests use `FakeQueryGenerator` returning pre-configured
    queries.

    Implementations are expected to:
      - Return between 0 and `n` queries (LLM may produce fewer).
      - Tag each query with one of the configured intent categories.
      - Sanitise the source document content against prompt injection.
    """

    def generate(
        self,
        title: str,
        body: str,
        *,
        n: int,
        categories: list[str],
    ) -> list[Any]:
        """Return 0..n intent-tagged eval queries derived from ``(title, body)``."""
        ...


@runtime_checkable
class Retriever(Protocol):
    """Hybrid-search facade for sweep / benchmark / gold-builder callers.

    The eval pipeline retrieves candidate documents via this protocol so
    sweep configurations can be tested against `FakeRetriever` returning
    pre-configured rankings. Production implementations delegate to the
    `SearchPipeline.search` surface but accept the eval-shaped argument
    signature directly.

    Implementations are expected to:
      - Return results in fused-rank order (best first).
      - Honour the `collections` filter when supplied.
      - Surface vec-failed state (e.g. via a `vec_failed: bool` attribute
        on the result) so callers can distinguish "no results" from
        "vector index unavailable".
    """

    def retrieve(
        self,
        query: str,
        *,
        collections: list[str] | None = None,
        cfg: Any = None,
    ) -> Any:
        """Return ranked candidate documents for ``query`` honouring the ``collections`` filter."""
        ...


# ---------------------------------------------------------------------------
# Phase 0 of mem0-vs-kairix-uplift plan — pluggable memory-backend Protocol.
#
# MemoryStore is the boundary between kairix's use cases (prep, search,
# brief, ...) and the configured memory backend. Two production
# implementations are planned: KairixNativeMemoryStore (wraps the existing
# SearchPipeline / chunk store, vault paradigm) and Mem0MemoryStore (wraps
# mem0.Memory, conversation paradigm). The Protocol is intentionally
# backend-agnostic: chunk-shaped and fact-shaped stores both satisfy it;
# the consumer-side code in use cases doesn't know which is configured.
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryStore(Protocol):
    """Memory backend boundary — vault-shaped or conversation-shaped.

    The lingua-franca surface for "store and recall a memory" across
    all backends. Implementations translate their native record shape
    (kairix chunks, mem0 facts, ...) into the common ``Memory`` value
    object returned by ``search``.

    Implementations must be safe to construct repeatedly (idempotent
    init) and tolerate concurrent ``search`` calls from the MCP layer.
    Persistence semantics are backend-specific; the Protocol does not
    pin durability guarantees beyond "writes from ``add`` are visible
    to subsequent ``search`` calls in the same process".
    """

    # add(content, *, metadata): backend-assigned id. metadata carries
    # backend-agnostic fields (source path, agent, timestamp, entity hints);
    # backends ignore unknown keys and round-trip the rest on search.
    def add(self, content: str, *, metadata: dict[str, Any] | None = None) -> str:
        """Persist ``content`` (with optional metadata) and return the backend-assigned id."""
        ...

    # search(query, *, top_k): up to top_k Memory-shaped objects, best
    # first. Empty list is a valid "no relevant content" signal — callers
    # MUST tolerate it. Implementations may return fewer than top_k.
    def search(self, query: str, *, top_k: int = 10) -> list[Any]:
        """Return up to ``top_k`` Memory-shaped objects matching ``query``, best first."""
        ...

    # update(memory_id, content): replace content of an existing memory.
    # Raises KeyError (or backend equivalent) if id absent. Backends with
    # append-only semantics (mem0 consolidation) may supersede rather than
    # replace — the Protocol does not pin the strategy.
    def update(self, memory_id: str, content: str) -> None:
        """Replace or supersede the content of the memory identified by ``memory_id``."""
        ...

    # delete(memory_id): remove. No-op if id is already absent.
    def delete(self, memory_id: str) -> None:
        """Delete the memory identified by ``memory_id``; no-op if already absent."""
        ...


@runtime_checkable
class ConversationStore(Protocol):
    """Chat-paradigm memory store — adds turn-level ingestion.

    Specialisation of ``MemoryStore`` for backends that ingest
    individual conversation turns (mem0, future LLM-fact-extractor
    layer in kairix-native uplift). Backends typically run an
    LLM-extraction pass per turn or sliding window; ``add_turn``
    returns the id of the most recently-produced memory record.

    Composition note: implementations of ``ConversationStore`` MUST
    also satisfy ``MemoryStore`` (the ``add``/``search``/``update``/
    ``delete`` surface) — the duplication here keeps the
    ``runtime_checkable`` isinstance() probe simple while documenting
    the chat-specific entry point.
    """

    def add(self, content: str, *, metadata: dict[str, Any] | None = None) -> str:
        """Persist ``content`` (with optional metadata) and return the backend-assigned id."""
        ...

    def search(self, query: str, *, top_k: int = 10) -> list[Any]:
        """Return up to ``top_k`` Memory-shaped objects matching ``query``, best first."""
        ...

    def update(self, memory_id: str, content: str) -> None:
        """Replace or supersede the content of the memory identified by ``memory_id``."""
        ...

    def delete(self, memory_id: str) -> None:
        """Delete the memory identified by ``memory_id``; no-op if already absent."""
        ...

    # add_turn(*, message, role, conversation_id, timestamp): adds a single
    # turn; returns the id of the most-recently-produced memory record.
    # Distinct from add(): backends like mem0 run an LLM-extraction pass
    # per turn or batch to emit canonical records. timestamp is ISO-8601
    # (RFC3339 subset); benchmarks against historical corpora MUST pass
    # the in-corpus timestamp so temporal queries resolve.
    def add_turn(
        self,
        *,
        message: str,
        role: str,
        conversation_id: str,
        timestamp: str | None = None,
    ) -> str:
        """Ingest one conversation turn; return the id of the produced memory record."""
        ...


@runtime_checkable
class Memory(Protocol):
    """A recalled memory across any ``MemoryStore`` backend.

    Lingua-franca shape for what ``MemoryStore.search`` returns. The
    Protocol pins the four attributes use-case code reads
    (``id``/``content``/``score``/``metadata``). Implementations are
    typically frozen dataclasses; concrete backends may carry
    additional fields beyond these four.

    ``score`` is rescaled to ``[0.0, 1.0]`` (1.0 = best match).
    Backends translate their native ranking into this scale so
    cross-backend fusion at the use-case layer is meaningful.
    """

    @property
    def id(self) -> str:
        """Stable identifier assigned by the backend on add()."""
        ...

    @property
    def content(self) -> str:
        """The recalled memory text."""
        ...

    @property
    def score(self) -> float:
        """Recall score rescaled to [0.0, 1.0] (1.0 = best match)."""
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        """Backend-agnostic metadata round-tripped from add() (path, agent, timestamp, …)."""
        ...


# ---------------------------------------------------------------------------
# Plan B-parity (mem0-vs-kairix-uplift) — fact-extraction Protocols.
#
# Companion to the chunk-shaped retrieval already in SearchPipeline.
# Conversation corpora are ingested turn-by-turn; an LLM fact extractor
# converts windowed turns into canonical entity-attribute-value records
# (``FactRecord``); a fact-shaped store keeps these alongside chunks; the
# SearchPipeline federates retrieval across both.
#
# Single-hop LoCoMo evidence: chunks alone score 9%; mem0's fact pattern
# scores 21% on the same questions. Adding the fact layer inside kairix-native
# closes that gap while keeping the multi-hop win chunks already provide
# (kairix 12% vs mem0 5%). Federation is the strategic moat.
# ---------------------------------------------------------------------------


@runtime_checkable
class FactRecord(Protocol):
    """A canonical entity-attribute-value record extracted from conversation turns.

    Lingua franca between :class:`FactExtractor` (emits) and
    :class:`FactStore` (persists + recalls). Implementations are
    typically frozen dataclasses; the Protocol pins the read surface
    that everything downstream consumes (retrieval, consolidation,
    Surface-B hydration, Surface-C learning).

    Identity contract:

    - ``id`` is deterministic — derived from ``(entity, attribute,
      source_turn_ids)``. Same triple from same turns → same id. This
      makes idempotent re-ingest safe and lets ``FactStore.add`` do a
      cheap UPSERT.

    Provenance contract:

    - ``source_turn_ids`` MUST trace back to raw turns the extractor
      consumed. Empty tuples are invalid (callers will raise).

    Versioning / consolidation contract:

    - ``superseded_by`` is ``None`` for live facts; set to another
      fact's id once a conflict-detection pass identifies a newer
      record about the same (entity, attribute). The full history
      stays queryable (audit + Surface-C signal); production search
      filters to ``superseded_by IS NULL`` by default.

    Temporal-anchor contract (Stream A, Lever A):

    - ``evidence_at`` is an optional ISO-8601 string carrying the
      *event-time* the fact occurred in the world — distinct from
      ``extracted_at`` which is wall-clock at extraction time. For
      session-windowed extraction the default is the session's
      ``date_time``; the LLM MAY resolve relative references in the
      turn ("last night", "the week before") to a specific date and
      emit a different value. ``None`` means the corpus had no
      temporal anchor for the fact (legacy rows from pre-Lever-A
      ingests, or sessions ingested without session_metadata).
    """

    @property
    def id(self) -> str:
        """Deterministic id derived from (entity, attribute, source_turn_ids)."""
        ...

    @property
    def entity(self) -> str:
        """The canonical entity this fact is about (subject)."""
        ...

    @property
    def attribute(self) -> str:
        """The attribute / predicate (e.g. 'role', 'lives_in')."""
        ...

    @property
    def value(self) -> str:
        """The attribute value extracted by the LLM."""
        ...

    @property
    def confidence(self) -> float:
        """LLM-rated confidence in [0.0, 1.0]; calibrated against ground truth."""
        ...

    @property
    def source_turn_ids(self) -> tuple[str, ...]:
        """Raw turn ids this fact was grounded in; never empty for a valid record."""
        ...

    @property
    def extracted_at(self) -> str:
        """Wall-clock ISO-8601 timestamp at extraction time."""
        ...

    @property
    def superseded_by(self) -> str | None:
        """Id of a newer fact that replaces this one; None for live (current) facts."""
        ...

    @property
    def namespace(self) -> str:
        """Engagement / tenant namespace for scoped recall."""
        ...

    @property
    def evidence_at(self) -> str | None:
        """Event-time ISO-8601 anchor for the fact (Lever A); None for legacy rows."""
        ...


@runtime_checkable
class FactHit(Protocol):
    """A FactRecord plus a recall score, returned by :meth:`FactStore.search`.

    The minimum read surface downstream code needs from a search hit:
    the underlying ``record`` + the ``score`` retrieval assigned to it.
    Implementations may carry additional diagnostic fields (which
    sub-retriever produced the hit, raw BM25/vector contribution, etc.)
    but downstream code only depends on these two.
    """

    @property
    def record(self) -> FactRecord:
        """The underlying FactRecord this hit refers to."""
        ...

    @property
    def score(self) -> float:
        """Retrieval score assigned to this hit (higher = better)."""
        ...


@runtime_checkable
class FactExtractor(Protocol):
    """Convert a window of conversation turns into canonical fact records.

    Production implementation is an LLM-driven extractor that runs
    against the configured provider (azure_foundry / openai /
    anthropic / bedrock / ...). The prompt asks the LLM to surface
    every entity-attribute-value triple that can be grounded in the
    given turns. Confidence is the LLM's own per-record rating; the
    eval gate calibrates it against ground truth.

    Test fakes (``FakeFactExtractor``) return scripted records so
    contract tests can pin the consumer-side behaviour without an
    LLM call.

    Idempotency: re-extracting the same windowed turns SHOULD produce
    facts with the same ids (because ``FactRecord.id`` is deterministic
    from ``(entity, attribute, source_turn_ids)``). Implementations
    that pass turns through an LLM with non-zero temperature won't
    achieve strict idempotency — that's why the production wire-up
    runs the extractor with temperature=0.0 in CI.
    """

    # extract(turns, window_hint, session_metadata): zero or more FactRecords
    # grounded in turns.
    # turns: list of turn dicts with at least id, speaker/role, content/text
    # keys (LoCoMo / chat-message shape). window_hint reserved for future
    # prompt-engineering knobs; production extractors may ignore it.
    # session_metadata (Stream A Lever A): optional dict carrying the
    # session ``date_time`` + ``session_id`` etc. The extractor pins
    # ``FactRecord.evidence_at`` to the session's default anchor when
    # the LLM omits a per-fact override.
    # Empty-list return is a valid "no facts groundable" signal — callers
    # MUST tolerate it without raising.
    def extract(
        self,
        *,
        turns: list[dict[str, Any]],
        window_hint: dict[str, Any] | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> list[FactRecord]:
        """Return zero or more FactRecords grounded in the supplied turns."""
        ...


@runtime_checkable
class FactStore(Protocol):
    """Persist + recall ``FactRecord`` data; back-compat companion to chunks.

    The fact-shaped sibling of the SearchPipeline's chunk store.
    Production implementation wraps SQLite (FTS5 + an index on
    ``entity, attribute``) plus a usearch vector index over the
    concatenated ``(entity, attribute, value)`` strings. Test fakes
    are dict-backed.

    Backwards compatibility: deployments that never call ``add``
    have an empty fact store. ``search`` against empty returns ``[]``.
    The SearchPipeline tolerates ``fact_retriever=None`` and runs
    the chunk-only pipeline (today's behaviour).
    """

    # add(fact): persist a fact. Idempotent on the fact's deterministic id.
    # Adding a fact whose id already exists is a no-op — the existing record
    # stays. Contract that makes ingest pipelines safely re-runnable.
    def add(self, fact: FactRecord) -> None:
        """Persist ``fact``; idempotent on its deterministic id."""
        ...

    # search(query, *, top_k, namespace): up to top_k facts matching query,
    # best first. namespace=non-None restricts to that namespace (engagement-
    # scoped recall for consultancy-in-a-box); None means "all namespaces".
    # Empty list is a valid "no facts" signal; by default excludes superseded.
    def search(self, query: str, *, top_k: int = 10, namespace: str | None = None) -> list[FactHit]:
        """Return up to ``top_k`` non-superseded facts matching ``query`` in ``namespace``."""
        ...

    # find_conflicts(*, entity, attribute, namespace): live (non-superseded)
    # facts for the (entity, attribute) key. Used by the consolidation pass:
    # on every new fact, the ingest pipeline finds existing facts about the
    # same entity-attribute pair, then runs the contradict use case to decide
    # whether the new fact supersedes them.
    def find_conflicts(self, *, entity: str, attribute: str, namespace: str | None = None) -> list[FactRecord]:
        """Return all live facts for the (entity, attribute) key in ``namespace``."""
        ...

    # supersede(*, old_id, new_id): mark old_id as superseded by new_id.
    # After this, old_id no longer appears in default search but stays
    # retrievable for audit (future include_superseded=True kwarg).
    # Raises KeyError if either id is absent.
    def supersede(self, *, old_id: str, new_id: str) -> None:
        """Mark ``old_id`` as superseded by ``new_id``; raises KeyError if either is absent."""
        ...


# ---------------------------------------------------------------------------
# Corpus-ingest Protocols — Spike C1 unified ingest contract.
#
# ``DocumentWriter`` and ``CorpusEmbedder`` are the two optional
# collaborators ``kairix.corpus.ingest.ingest_corpus`` composes alongside
# ``FactStore`` + ``FactExtractor``. Both are Protocols (not callables)
# so production wire-ups can hold cached state (DB handles, body-hash
# caches) and tests can inject capture-only fakes. Both are nullable:
# passing ``None`` on either is the documented opt-out for chunks-only
# or facts-only modes.
#
# Locked here in ``kairix.core.protocols`` so domain code references
# them via Protocol only — F26: ``kairix.core.**`` must not import
# providers/transport. The production implementations live in
# ``kairix.corpus.wiring`` (Phase 2/3).
# ---------------------------------------------------------------------------


@runtime_checkable
class DocumentWriter(Protocol):
    """Persist one rendered conversation document to the document store.

    Boundary between corpus-ingest (knows session shape, frontmatter
    conventions) and the document store (knows on-disk layout, FTS
    reindexing). Production wraps
    :class:`SQLiteDocumentRepository.insert_or_update` plus a markdown
    materialiser; tests use a capture-only fake.

    The returned :class:`Path` is what callers populate
    ``IngestResult.document_paths`` from. Implementations MUST be
    idempotent on body content — re-ingesting the same
    ``(corpus_id, session_id, rendered_body)`` is a no-op for both
    filesystem and DB writes.

    F26 note: keep concrete implementations OUT of ``kairix/core/**``.
    Wire production writers from ``kairix.corpus.wiring`` so the domain
    layer talks to writers via this Protocol only.
    """

    def write(
        self,
        *,
        corpus_id: str,
        session_id: str,
        rendered_body: str,
        frontmatter: dict[str, Any],
    ) -> Path:
        """Persist one rendered session document; return its on-disk path. Idempotent."""
        ...


@runtime_checkable
class CorpusEmbedder(Protocol):
    """Embed the documents this ingest pass just wrote into the vector index.

    Boundary between corpus-ingest and ``kairix embed``'s chunk-and-
    vectorise pipeline. Production wraps the in-process
    ``run_incremental_embed_pipeline`` and surfaces the chunks-indexed
    count; tests use a counter-only fake.

    ``paths_to_embed`` is the document subset — typically the Paths
    just returned from a ``DocumentWriter.write`` round-trip. Empty
    tuple is a legal no-op signal (e.g. embedder is wired but
    ``document_writer`` was None and no markdown was created). Returns
    the count of chunks actually indexed this call so
    :class:`IngestResult` can carry an honest ``chunks_indexed``.
    """

    def embed(self, paths_to_embed: tuple[Path, ...]) -> int:
        """Embed the supplied document paths; return the count of chunks indexed."""
        ...


# ---------------------------------------------------------------------------
# Connector framework — Wave 1 SC-1 surface.
#
# See ``docs/architecture/connector-ingestion-architecture.md`` §2-§4 for
# the architecture and the two-scope / storage-tiering / language-strategy
# context that drives the layer split.
#
# Three-layer split (locked by F26 / F34 / F35):
#
#   * ``kairix/core/connectors/`` — orchestration. Owns the per-batch
#     SQLite transaction: list_changes → fetch → bronze → silver →
#     index → advance. Knows nothing about specific sources or formats.
#
#   * ``kairix/connectors/<name>/`` — one source (Obsidian, SharePoint,
#     dex_crm, …). Implements :class:`SourceConnector` and registers via
#     the ``kairix.connectors`` entry-point group.
#
#   * ``kairix/extractors/<name>/`` — one format family (markitdown,
#     pdf_fallback, ocr, …). Implements :class:`Extractor` and registers
#     via the ``kairix.extractors`` entry-point group.
#
# Every value object that crosses the boundary is a ``@dataclass(frozen=True)``
# per F42 — no ``dict[str, Any]``, no ``list[dict]``, no bare ``Any``.
# Pydantic stays at the JSON edge (HTTP / MCP / config); inside kairix,
# frozen dataclasses everywhere.
# ---------------------------------------------------------------------------


# Opaque resumption token a connector uses to checkpoint progress.
Cursor = str

# MIME type hint, e.g. "application/pdf" or "text/markdown".
MimeType = str

# Sensitivity tier — populated on every chunk write per F39. Defaults
# drift to ``"public"``; connectors that handle confidential data must
# declare a non-public tier in config.
Sensitivity = Literal["public", "internal", "client-confidential", "personal"]


@dataclass(frozen=True)
class ChangeEvent:
    """One change the connector observed since the last cursor.

    Streamed from :meth:`SourceConnector.list_changes`. ``op`` follows
    the create/modify/delete trichotomy every source surfaces in some
    form (filesystem mtime + tombstone; Graph delta tokens; CRM
    last_modified_at; …). ``modified_at`` is the source's own
    timestamp (ISO-8601 UTC), travelled through to
    :class:`Chunk.source_modified_at` so search can boost recency.
    """

    # Topology v2 Wave A: extended enum with `archived` (recoverable soft-delete,
    # chunks remain but marked) and `access_lost` (credential revoked, chunks
    # frozen but not re-fetchable). Old emitters keep using the original three;
    # only flag-gated Wave C+ paths emit the new values.
    op: Literal["created", "modified", "archived", "access_lost", "deleted"]
    item_id: str
    modified_at: str
    parent_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawArtefact:
    """Raw bytes as fetched from the source, plus a mime hint.

    Produced by :meth:`SourceConnector.fetch`. The orchestrator hands
    the bytes off to Bronze (persistence) AND to the extractor registry
    (format detection via mime + magic bytes). ``fetched_at`` is the
    wall-clock at fetch time, distinct from ``ChangeEvent.modified_at``
    (the source's own modify time).

    Topology v2 Wave A: ``sensitivity_hint`` carries per-item sensitivity
    when the source surfaces it (SharePoint Purview labels, Slack channel
    privacy, GitHub repo visibility, Drive sharing tier). Silver applies
    a 5-step fallback chain — hint > collection-source override >
    collection default > cc_pair access-type→F39 map > connector default.
    """

    raw: bytes
    mime: MimeType
    fetched_at: str
    sensitivity_hint: Sensitivity | None = None


@dataclass(frozen=True)
class Page:
    """One page / slide / sheet of an extracted document."""

    page_number: int
    text: str
    has_images: bool


@dataclass(frozen=True)
class Image:
    """One image lifted from an extracted document, classified."""

    page_number: int
    classification: Literal["photo", "diagram", "chart", "decorative"]
    data: bytes


@dataclass(frozen=True)
class DocMetadata:
    """Format-derived metadata for an :class:`ExtractedDocument`."""

    title: str | None
    author: str | None
    created_date: str | None
    language: str | None
    page_count: int | None


@dataclass(frozen=True)
class ExtractedDocument:
    """Output of an :class:`Extractor`.

    ``markdown`` is the unified rendering (for chunking + indexing).
    ``pages`` carries per-page extractions so chunks can cite back to
    a page / slide / sheet. ``images`` are extracted + classified.
    ``confidence`` is the average across pages; the orchestrator
    consults :meth:`Extractor.quality_ok` to decide whether to
    escalate (markitdown → pdf_fallback → ocr → vision).
    """

    markdown: str
    pages: tuple[Page, ...]
    images: tuple[Image, ...]
    metadata: DocMetadata
    confidence: float


@dataclass(frozen=True)
class BronzeRef:
    """Pointer to a Bronze record.

    Two storage shapes coexist:

    * **FilesystemBronzeStore** writes the raw bytes to
      ``<bronze_root>/<raw_path>`` and surfaces a ``BronzeRef`` whose
      ``raw_path`` is the relative on-disk location of the blob.
    * **StreamingBronzeStore** (Phase 1+ of the streaming-bronze rollout)
      writes no on-disk blob and surfaces a ``BronzeRef`` whose
      ``raw_path`` is ``None``. Re-extract paths recover the bytes via
      ``connector.fetch(item_id)`` instead of ``bronze.read(ref)``.

    Consumers MUST handle ``raw_path is None`` by routing through the
    connector re-fetch path; ``FilesystemBronzeStore.read`` raises a
    fix-pointer error if asked to read a streaming-shape ref.

    ``content_hash`` is the SHA-256 of the raw bytes at write time,
    populated by both impls in Phase 2. Rows written before Phase 2
    carry ``None`` until re-written; consumers handle that gracefully.
    """

    source_name: str
    item_id: str
    raw_path: str | None
    mime: MimeType
    fetched_at: str
    content_hash: str | None = None


@dataclass(frozen=True)
class SourceMetadata:
    """Per-source envelope metadata, surfaced before silver chunking.

    Connectors that have envelope metadata at the source surface
    (SharePoint drive items, GitHub commits, Notion blocks, etc.)
    populate the relevant fields via :meth:`SourceConnector.metadata_for`.
    Per-format extractors fill in document-body metadata (PDF authors,
    Office properties, EXIF) via :meth:`Extractor.metadata_for`. The
    silver merge layer combines them with connector > extractor >
    defaults priority — see ADR-021 §"Silver merge logic".

    All fields default to None / empty so connectors implement only
    what their source surfaces. F65 blocks plugin classes that do not
    implement ``metadata_for`` (or do not opt out via
    ``# F65-exempt: <reason>``). Spec:
    ``docs/architecture/ADR-021-per-source-metadata-normalisation.md``.
    """

    modified_at: str | None = None
    created_at: str | None = None
    author: str | None = None
    author_email: str | None = None
    tags: tuple[str, ...] = ()
    properties: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """One chunk written to the retrieval index.

    F39 enforces that every chunk write carries ``source_uri``,
    ``source_modified_at``, AND ``sensitivity`` explicitly — default-
    to-public is only valid when the connector config declares the
    public tier explicitly. ``source_page`` is non-``None`` for PDF /
    PPTX / XLSX content; it lets retrieval cite a specific page back
    to the operator.

    Topology v2 Wave C: ``chunker_version`` is the version-string of the
    Chunker plugin that emitted this chunk (mirrors
    ``documents_media.extractor_version``). ``None`` for legacy paths
    that pre-date the chunker registry; new emitters fill it via
    ``chunker_version=self.version`` per F55.

    ADR-021 (Wave E.5): ``author`` / ``author_email`` / ``tags`` /
    ``metadata`` surface :class:`SourceMetadata` from the connector
    envelope and the extractor body so downstream temporal-boost,
    entity-graph and metadata-filtered search work for every source.
    All four default to None / empty so existing call sites stay
    compatible.
    """

    text: str
    content_hash: str
    source_name: str
    source_uri: str
    source_modified_at: str
    source_page: int | None
    sensitivity: Sensitivity
    chunker_version: str | None = None
    author: str | None = None
    author_email: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EntitySignal:
    """One entity-graph signal extracted by Silver from a document.

    Staged in SQLite (``entity_signals`` table); a separate worker job
    (decoupled per the Curator coupling boundary) pushes to Neo4j.
    Direct-to-Neo4j writes from the connector pipeline are rejected —
    see :class:`EntityGraphSink` for the staging boundary.
    """

    kind: Literal["person", "org", "relationship"]
    value: str
    source_uri: str
    modified_at: str
    confidence: float
    sensitivity: Sensitivity


@dataclass(frozen=True)
class SilverOutput:
    """Output of :meth:`SilverProcessor.process` — chunks + entity signals."""

    chunks: tuple[Chunk, ...]
    entity_signals: tuple[EntitySignal, ...]


@runtime_checkable
class SourceConnector(Protocol):
    """One external source family.

    Implementations under ``kairix/connectors/<name>/`` register via
    the ``kairix.connectors`` entry-point group. The Protocol is
    deliberately narrow — chunking, signal extraction, and Bronze
    persistence are NOT on this surface, they live in the
    orchestration tree (see :class:`BronzeStore`, :class:`SilverProcessor`).
    F38 locks chunking to one canonical home; F35 locks each connector
    to its own directory tree.
    """

    name: str
    per_tick_max_items: int = 500
    disk_watermark_min_free_bytes: int | None = None

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Yield ChangeEvents observed since ``cursor`` (None = full enumeration)."""
        ...

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the raw bytes + mime hint for one source item."""
        ...

    def source_link(self, item_id: str) -> str:
        """Return a stable URI back to the item in its source system."""
        ...

    def sensitivity_for(self, item_id: str) -> Sensitivity:
        """Return the configured sensitivity tier for the given item."""
        ...

    def next_cursor(self) -> str | None:
        """Return the cursor token to persist after the most recent ``list_changes`` drain.

        Populated by the connector during :meth:`list_changes` and read
        by the orchestration layer (``ConnectorPipeline._commit_and_flush``)
        once per chunk-commit. ``None`` means "no cursor advance this
        tick" — the orchestrator MUST NOT clobber a previously-persisted
        cursor when ``next_cursor()`` returns ``None``.

        Why this is on the Protocol: per-item ``modified_at`` is NOT a
        valid cursor for connectors whose change-detection API uses
        opaque continuation tokens (Graph ``@odata.deltaLink``, Slack
        ``ts``, Notion ``last_edited_time`` map, GitHub ``Last-Event-ID``).
        Writing ``modified_at`` for those connectors corrupts the cursor
        and forces a full resync on every tick — see the deltaLink
        clobber incident in ``docs/runbooks/`` for the failure mode.

        Connectors whose cursor IS an ISO-8601 timestamp (Obsidian,
        Dex CRM, m365_calendar single-cursor path) should track the
        max ``modified_at`` observed in the last drain and return it
        here.
        """
        ...

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return :class:`SourceMetadata` populated from the source's envelope.

        ADR-021 (Wave E.5): called per-item by Silver, BEFORE the
        extractor runs. Connectors that surface no structured metadata
        can return ``SourceMetadata()``. Connectors with envelope
        metadata MUST surface it — F65 blocks connectors that don't
        implement this method or opt out via ``# F65-exempt:``.

        The orchestrator merges this with
        :meth:`Extractor.metadata_for` (connector > extractor >
        defaults). See ADR-021 §"Silver merge logic" for the precise
        merge rule.
        """
        ...


@runtime_checkable
class Extractor(Protocol):
    """One format family.

    Implementations under ``kairix/extractors/<name>/`` register via
    the ``kairix.extractors`` entry-point group. F40 requires every
    plugin module to declare a ``version: str`` written through to
    ``documents_media.extractor_version`` so re-extracts on version
    bump are tractable. ``quality_ok`` drives the escalation chain
    (markitdown → pdf_fallback → ocr → vision).
    """

    name: str
    version: str

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        """Return True if this extractor can handle the given mime / magic bytes."""
        ...

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        """Return an ExtractedDocument (markdown + pages + images + metadata) from ``raw``."""
        ...

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """Return True if the extraction is good enough to skip the next escalation tier."""
        ...

    def metadata_for(self, raw: bytes, mime: MimeType) -> SourceMetadata:
        """Return :class:`SourceMetadata` from the raw bytes.

        ADR-021 (Wave E.5): for PDFs read XMP / Info dict
        (``CreationDate`` / ``Author`` / ``Title`` / ``Keywords``); for
        Office formats read core properties (created / modified /
        creator / keywords / category); for markdown parse the
        ``---`` frontmatter block. Passthrough / OCR may return
        ``SourceMetadata()`` when the format carries no body-level
        metadata.

        The orchestrator merges this with
        :meth:`SourceConnector.metadata_for` (connector wins on
        collisions). See ADR-021 §"Silver merge logic".
        """
        ...


@runtime_checkable
class BronzeStore(Protocol):
    """Raw-bytes-as-fetched persistence (filesystem-with-pointer, ADR-018).

    Bronze is replayable: ``replay`` streams every record for a source
    (optionally since a timestamp) so re-extraction with a newer
    :class:`Extractor` version can recover from the originals without
    re-fetching from the source system.
    """

    def write(self, source_name: str, item_id: str, raw: bytes, mime: MimeType) -> BronzeRef:
        """Persist ``raw`` and return a BronzeRef pointer for later replay."""
        ...

    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        """Return the raw bytes + mime for ``ref``."""
        ...

    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        """Yield every BronzeRef for ``source_name`` (optionally since ``since``)."""
        ...


@runtime_checkable
class SilverProcessor(Protocol):
    """Chunking + entity-signal extraction (Plain Python, no LLM).

    Per F38 + KFEAT-005, Silver processing lives ONLY in
    ``kairix/core/connectors/silver.py`` — no per-connector chunker,
    no per-extractor chunker. The orchestrator hands every Bronze
    record plus its :class:`ExtractedDocument` to ``process``; Silver
    returns a :class:`SilverOutput`.
    """

    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
        connector_metadata: SourceMetadata | None = None,
        extractor_metadata: SourceMetadata | None = None,
        extractor_name: str | None = None,
        extractor_version: str | None = None,
        extraction_status: str = "ok",
    ) -> SilverOutput:
        """Return a SilverOutput (chunks + entity signals) for one extracted document.

        ADR-021 (Wave E.5): ``connector_metadata`` and
        ``extractor_metadata`` carry envelope- and body-derived
        metadata respectively; both default to ``None`` so legacy
        callers stay back-compatible. When both are ``None`` Silver
        falls back to the legacy single-source path (no author /
        tags / properties).

        GH #336 (ADR-024 Bundle B): ``extractor_name`` /
        ``extractor_version`` / ``extraction_status`` are surfaced by
        the orchestrator so Silver can write the per-document
        ``documents_media`` row. All three default to None / "ok" so
        non-pipeline callers (legacy tests) stay back-compatible. The
        ``extraction_status`` enum is ``"ok" | "failed" | "unsupported"``;
        the orchestrator catches the ``failed`` / ``unsupported``
        branches before reaching :meth:`process` so those statuses
        flow via the separate ``write_extraction_outcome`` shape on
        :class:`~kairix.core.connectors.silver.DefaultSilverProcessor`.
        """
        ...


@runtime_checkable
class ChunkWriter(Protocol):
    """Where :class:`Chunk` records land.

    The connector pipeline composes a :class:`ChunkWriter` so the
    Silver output can be persisted to the retrieval index without
    Silver itself knowing how the index is shaped. Production wires a
    SQLite-backed writer (``documents`` table + FTS5 reindex);
    integration tests use a capture-only fake.

    Moved here from ``kairix.core.connectors.pipeline`` in ADR-026 A.0b
    pre-work — the canonical Protocol surface lives at
    ``kairix.core.protocols`` so the upcoming first-class ``Stage``
    abstraction can reference :class:`ChunkWriter` without coupling to
    the pipeline module.
    """

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Persist ``chunks``; return the count successfully written.

        Must NOT commit — the caller's per-batch transaction owns the
        commit so chunk writes, cursor advance, and Bronze writes
        commit together or roll back together.
        """
        ...


@runtime_checkable
class EntityGraphSink(Protocol):
    """Where :class:`EntitySignal` records land.

    DECISION RULING (per spec §6 Decision 1, ratified 2026-05-22):
    signals are staged in SQLite; a separate worker job (not in
    Wave 1) pushes to Neo4j. Direct-to-Neo4j writes from the connector
    pipeline are rejected — the Curator coupling boundary stays
    asynchronous, batched, and idempotent.
    """

    def buffer(self, signals: Sequence[EntitySignal]) -> int:
        """Write the given signals to the SQLite staging table; return the
        count actually inserted.

        Renamed from ``stage()`` in ADR-026 A.0a pre-work — the original
        name collided with the upcoming first-class ``Stage`` abstraction
        for pipeline-step instrumentation. The semantics are unchanged:
        signals land in the SQLite staging buffer, a separate worker
        drain pushes to Neo4j asynchronously.
        """
        ...


@runtime_checkable
class FeatureFlagResolver(Protocol):
    """Test seam for feature-flag resolution.

    Per ``docs/architecture/feature-flag-architecture.md`` §3.3 step 4 —
    the resolver Protocol exposes the boundary tests use; the canonical
    ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` lets unit tests
    pin specific flag states without touching the global registry.

    ``get`` returns a bool (the resolved effective value);
    ``iter_all`` yields :class:`kairix.core.features.resolver.FlagStatus`
    snapshots, which is a ``@dataclass(frozen=True)`` per F42.
    """

    def get(self, name: str) -> bool:
        """Return the resolved effective value for flag ``name``."""
        ...

    def iter_all(self) -> Iterator[FlagStatus]:
        """Yield a FlagStatus snapshot for every flag in the registry."""
        ...


# =============================================================================
# Topology v2 (Wave A) — connector / collection / scope topology
# =============================================================================
#
# These dataclasses + enums + exceptions are the v2 vocabulary. They land
# in Wave A as pure definitions — no behaviour wired yet. The
# ``topology_v2_schema`` feature flag gates whether any code path
# WRITES to the schema tables that these dataclasses represent.
#
# See docs/architecture/connector-scope-topology/ADR.md for the
# canonical decision; this surface mirrors that ADR's sections 1-6.


F39Tier = Literal["public", "internal", "confidential", "restricted"]
"""F39 sensitivity tier — strictly richer than the legacy ``Sensitivity``
literal (kept for back-compat). New code uses ``F39Tier``; legacy code
keeps ``Sensitivity``. Both are valid until Wave G retires the legacy
literal."""


HierarchyNodeType = Literal[
    "FOLDER",
    "SOURCE",
    "SHARED_DRIVE",
    "MY_DRIVE",
    "SPACE",
    "PAGE",
    "PROJECT",
    "DATABASE",
    "WORKSPACE",
    "SITE",
    "DRIVE",
    "CHANNEL",
]
"""12-value normalised vocabulary for source-side container shapes
(per Onyx's `HierarchyNodeType` enum). Used by the
``HierarchyConnector`` capability to emit the source's own folder /
space / site / channel tree as first-class structured data."""


ContainerAccessState = Literal[
    "ACCESSIBLE",
    "REVOKED",
    "NOT_YET_GRANTED",
    "TRANSIENT_ERROR",
]
"""Container access lifecycle. ``REVOKED`` means a previously-accessible
container is now permission-denied (e.g. SharePoint Sites.Selected grant
removed). ``NOT_YET_GRANTED`` means the credential reaches the parent
but this container hasn't been explicitly added (e.g. Notion integration
not connected to a page). ``TRANSIENT_ERROR`` means rate-limited or
temporary outage — retry per ``retry_after``."""


CCPairAccessType = Literal["PUBLIC", "PRIVATE", "SYNC"]
"""Per-cc_pair access mode. PUBLIC = any actor in engagement sees
everything. PRIVATE = only explicit cc_pair group grants see. SYNC =
pull ACLs from source and enforce per-doc; ``perm_sync_freq`` controls
cadence."""


CCPairStatus = Literal[
    "SCHEDULED",
    "INITIAL_INDEXING",
    "ACTIVE",
    "PAUSED",
    "DELETING",
    "INVALID",
]
"""cc_pair lifecycle state machine. Valid transitions:
SCHEDULED → INITIAL_INDEXING → ACTIVE ↔ PAUSED / DELETING / INVALID.
F57 enforces transition integrity at runtime."""


ScopeProfileActorKind = Literal["agent", "human", "team", "group", "skill"]
"""Kind of actor a ScopeProfile applies to."""


CollectionVisibility = Literal["public", "engagement", "team", "private"]
"""Collection visibility tier — controls default broad access."""


@dataclass(frozen=True)
class ConnectorInstance:
    """Topology v2 §1 — Connector (kind + config).

    The configured target. Decoupled from credentials (see ``Credential``)
    and from operational state (see ``ConnectorCredentialPair``).
    """

    id: int
    kind: str
    name: str
    connector_specific_config: Mapping[str, Any]
    refresh_freq_seconds: int | None
    prune_freq_seconds: int | None
    perm_sync_freq_seconds: int | None
    default_sensitivity: F39Tier
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Credential:
    """Topology v2 §2 — Credential (auth shape).

    Encrypted at rest. Decoupled from connector so the same auth blob
    can drive multiple scoped connectors AND a connector's credential
    can rotate without losing operational state.
    """

    id: int
    kind: str
    name: str
    credential_ref: str
    user_id: str | None
    admin_public: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConnectorCredentialPair:
    """Topology v2 §3 — the operational unit.

    Binding of one Connector + one Credential with its own cursor scope,
    status, audit timestamps, and access mode. The cc_pair_id is the
    cursor + deadletter scope key.
    """

    id: int
    connector_id: int
    credential_id: int | None
    name: str
    access_type: CCPairAccessType
    status: CCPairStatus
    last_successful_index_time: str | None
    last_time_perm_sync: str | None
    last_time_external_group_sync: str | None
    last_time_hierarchy_fetch: str | None
    in_repeated_error_state: bool
    total_docs_indexed: int
    refresh_freq_override_seconds: int | None
    prune_freq_override_seconds: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Container:
    """Topology v2 §4 — per-cc_pair internal scope unit.

    Each Container has its own delta cursor (replacing the v1 single-
    cursor-per-connector). Per-drive (SharePoint), per-mailbox (Graph),
    per-channel (Slack), per-repo (GitHub), per-user-shared-drive
    (Drive) — all map to a Container row.
    """

    cc_pair_id: int
    container_id: str
    access_state: ContainerAccessState
    cursor_token: str | None
    last_synced_at: str | None


@dataclass(frozen=True)
class HierarchyNode:
    """Topology v2 §4 — source-side folder / space / site / channel tree.

    Emitted by ``HierarchyConnector`` capability parent-before-child.
    Lets the search layer answer "files in this folder", "siblings of
    this doc", "all docs under site:X" without re-deriving from
    ``source_uri`` prefixes.
    """

    cc_pair_id: int
    raw_node_id: str
    raw_parent_id: str | None
    display_name: str
    link: str | None
    node_type: HierarchyNodeType
    external_access_json: str | None
    sensitivity_hint: F39Tier | None


@dataclass(frozen=True)
class CollectionSource:
    """Topology v2 §5 — one (cc_pair, filter) mapping into a Collection."""

    cc_pair_id: int
    source_path_filter: str
    sensitivity_override: F39Tier | None


@dataclass(frozen=True)
class FederatedConnector:
    """Topology v2 §5 — external search-index member of a Collection.

    Lets a Collection compose external search endpoints (Vespa, Elastic,
    MCP) alongside ingested cc_pair sources without re-ingesting.
    """

    id: int
    collection_id: int
    kind: str
    endpoint: str
    query_strategy: str


@dataclass(frozen=True)
class GroupGrant:
    """Topology v2 §5 — per-group access to a Collection.

    Per-group is operationally cheaper than per-actor at team scale —
    add Alice to ``team-engagement`` and she inherits the group's
    grants without per-actor profile changes.
    """

    id: int
    collection_id: int
    group_id: str
    can_read: bool
    can_write: bool
    max_sensitivity: F39Tier


@dataclass(frozen=True)
class Collection:
    """Topology v2 §5 — retrieval bucket, decoupled from connectors.

    Aggregates one or more cc_pairs via filters, plus optional
    federated members. Search ranks within / over the Collection's
    chunks.
    """

    id: int
    name: str
    default_sensitivity: F39Tier
    on_unmapped_item: Literal["land_in_default_collection", "drop"]
    visibility: CollectionVisibility
    sources: tuple[CollectionSource, ...]
    federated_members: tuple[FederatedConnector, ...]
    group_grants: tuple[GroupGrant, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ScopeEntry:
    """Topology v2 §6 — one (collection, rights) entry in a ScopeProfile."""

    collection_name: str
    can_read: bool
    can_write: bool
    max_sensitivity: F39Tier


@dataclass(frozen=True)
class ScopeProfile:
    """Topology v2 §6 — per-actor (or per-group) access bundle.

    Composition rules: collections by intersection across requesting
    principals; ``max_sensitivity`` by F39-min (least permissive); write
    rights by AND. Caller can opt into union via authorised
    ``scope_composition: "union"`` token.
    """

    id: int
    actor_id: str
    actor_kind: ScopeProfileActorKind
    inherits_from: tuple[str, ...]
    entries: tuple[ScopeEntry, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskCollection:
    """Topology v2 §6 — one named member of a Skill's task_collections.

    May match a real Collection or be a virtual aggregator scoped to
    the skill invocation.
    """

    name: str
    sources: tuple[CollectionSource, ...]
    weight: float


@dataclass(frozen=True)
class Skill:
    """Topology v2 §6 — composable search strategy.

    Defines an ordered set of task_collections + ranking + iteration
    shape. Invoked via ``kairix skill <name>`` or via the MCP
    ``tool_invoke_skill`` surface.
    """

    id: int
    name: str
    task_collections: tuple[TaskCollection, ...]
    ranking: str
    iteration: Literal["one_shot", "sequential_per_task_collection", "graph_anchored"]
    created_at: str
    updated_at: str


# =============================================================================
# Typed exceptions at the connector framework boundary (Onyx-derived)
# =============================================================================


class ConnectorValidationError(Exception):
    """Base for all connector-validation failures. Configuration / credential
    / scope problems all derive from this so the runner can type-narrow."""


class CredentialInvalidError(ConnectorValidationError):
    """Credential is structurally wrong (missing fields, malformed)."""


class CredentialExpiredError(ConnectorValidationError):
    """Credential expired (OAuth refresh failed, token revoked)."""


class InsufficientPermissionsError(ConnectorValidationError):
    """Auth succeeded but the credential lacks permission for the requested scope."""


class ContainerAccessDeniedError(Exception):
    """A specific Container is no longer reachable (e.g. SharePoint Sites.Selected
    grant revoked). cc_pair stays alive for its other containers."""


class ContainerTransientError(Exception):
    """Container is temporarily unavailable (rate limit, 503, timeout).
    Retry per ``retry_after`` seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# Back-compat aliases — keep the ADR's named exceptions importable under
# their ADR shape; the underscore-error suffix versions above are the
# canonical names per ruff N818 (mandatory "Error" suffix on exception classes).
ContainerAccessDenied = ContainerAccessDeniedError
ContainerTransient = ContainerTransientError


class UnexpectedValidationError(ConnectorValidationError):
    """Transient validation failure — does NOT disable the cc_pair."""


class CCPairTransitionError(Exception):
    """Illegal cc_pair status transition rejected by the lifecycle validator.

    Raised by :func:`kairix.core.connectors.cc_pair.transition_cc_pair`
    when a caller asks for a status jump that is not in
    ``_ALLOWED_TRANSITIONS`` (per F57). Carries the current and target
    statuses so callers can log a precise rejection reason without
    re-querying the row.
    """

    def __init__(self, current: str, target: str, reason: str | None = None) -> None:
        self.current = current
        self.target = target
        self.reason = reason
        suffix = f" (reason: {reason})" if reason else ""
        super().__init__(f"illegal cc_pair transition {current!r} → {target!r}{suffix}")


# =============================================================================
# Topology v2 (Wave B) — capability mix-in Protocols (Onyx-derived)
# =============================================================================
#
# Wave B splits the single flat :class:`SourceConnector` Protocol into
# a base + optional capabilities. A connector implementation advertises
# capabilities by satisfying the relevant Protocols; the framework's
# runner dispatches via runtime ``isinstance`` checks against each
# capability Protocol.
#
# These Protocols land as additive shapes — Wave B itself is pure-
# additive (the 4 shipped connectors get default-impl shims so they
# continue to satisfy the new surfaces without behavioural change). The
# ``topology_v2_protocol`` feature flag gates whether the worker's
# connector-sync dispatch routes through the new capability path; Wave
# C activates it at runtime.
#
# See docs/architecture/connector-scope-topology/ADR.md §"Connector
# Protocol — capability mix-ins (Onyx-derived)" for the canonical
# decision; this surface mirrors that section.


@runtime_checkable
class PollConnector(Protocol):
    """Connectors that pull changes via a delta cursor (most current shape).

    The container-scoped delta-poll surface — :meth:`list_changes_for_container`
    accepts a :class:`Container` (carrying its own per-cc_pair cursor
    token) and yields :class:`ChangeEvent` items observed since that
    cursor. Distinct from the legacy single-cursor
    :class:`SourceConnector.list_changes` because the v2 topology
    surfaces a per-container cursor (per-drive, per-mailbox, per-channel)
    instead of a single connector-wide token.
    """

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Yield ChangeEvents observed since ``container.cursor_token``."""
        ...


@runtime_checkable
class CheckpointedConnector(Protocol):
    """Connectors whose cursor is a richer per-batch Checkpoint blob (Onyx pattern).

    Some sources (Microsoft Graph delta, Confluence cursor pages,
    Notion incremental sync) carry richer per-batch state than a single
    timestamp — full deltaLink URLs, opaque resumption tokens, pagination
    cursors. The CheckpointedConnector surface accepts an opaque
    checkpoint string (``None`` for first sync) and yields
    :class:`ChangeEvent` items for that batch.
    """

    def load_from_checkpoint(self, container: Container, checkpoint: str | None) -> Iterator[ChangeEvent]:
        """Yield ChangeEvents from the given checkpoint (None = first sync)."""
        ...


@runtime_checkable
class SlimConnector(Protocol):
    """ID-only enumeration for prune cycles — separate from full retrieval.

    Prune cycles need to know "what ids does the source still have?"
    without paying the cost of fetching every body. SlimConnector
    yields item_ids only — the orchestrator diffs against
    ``documents.item_id`` to detect deletes and stage tombstones.
    """

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """Yield the item_id strings the source currently exposes for ``container``."""
        ...


@runtime_checkable
class SlimConnectorWithPermSync(Protocol):
    """Slim retrieval that also reports per-doc ACL (for AccessType.SYNC cc_pairs).

    Sources with per-doc permissions (SharePoint, Drive, Confluence
    restricted pages) need a permission-sync cycle that pulls the
    current ACL alongside the id list. Each yielded tuple is
    ``(item_id, acl_serialised)`` — the serialised form is opaque to
    the framework; the connector's perm-sync handler parses it.
    """

    def retrieve_all_slim_docs_with_perms(self, container: Container) -> Iterator[tuple[str, str]]:
        """Yield ``(item_id, acl_serialised)`` tuples for ``container``."""
        ...


@runtime_checkable
class EventConnector(Protocol):
    """Webhook-driven push surface (sub-minute freshness).

    Sources with webhook / change-notification surfaces (Microsoft
    Graph subscriptions, Slack RTM events, Notion webhook, GitHub
    webhook) implement EventConnector. The framework subscribes once,
    holds the subscription_id, renews on TTL, and routes inbound events
    through :meth:`handle_event` which yields :class:`ChangeEvent` items.
    """

    def subscribe(self, callback_url: str) -> str | None:
        """Subscribe to source events; return an opaque subscription_id (None = unsupported)."""
        ...

    def renew_subscription(self, subscription_id: str) -> str:
        """Renew a subscription before its TTL; return the (possibly new) id."""
        ...

    def unsubscribe(self, subscription_id: str) -> None:
        """Cancel the subscription cleanly (idempotent on unknown id)."""
        ...

    def handle_event(self, event: Mapping[str, Any]) -> Iterator[ChangeEvent]:
        """Translate one inbound webhook payload into ChangeEvent items."""
        ...


@runtime_checkable
class Resolver(Protocol):
    """Per-document failure replay — cheaper than re-running the window.

    When a sync tick fails partway through (rate limit, transient 503,
    extractor crash on one item), the orchestrator stages the failed
    item_ids in the deadletter table. Resolver lets the framework
    re-pull only those items on the next tick instead of replaying the
    whole window. ``include_permissions=True`` re-pulls the ACL too —
    used when the failure originated in the perm-sync path.
    """

    def reindex(self, failed_item_ids: tuple[str, ...], *, include_permissions: bool = False) -> Iterator[ChangeEvent]:
        """Yield ChangeEvent items for the given failed ids (optionally ACL-included)."""
        ...


@runtime_checkable
class HierarchyConnector(Protocol):
    """Emits source's folder/space/site tree as HierarchyNodes (parent-before-child).

    Sources with a folder / space / channel hierarchy (SharePoint
    sites, Drive folders, Notion pages, Slack channels) implement
    :class:`HierarchyConnector` so the search layer can answer "files
    in this folder", "siblings of this doc", "all docs under site:X"
    without re-deriving from ``source_uri`` prefixes. Emission order is
    parent-before-child so the receiver can build the tree in one pass.
    """

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """Yield HierarchyNode items parent-before-child for the given cc_pair."""
        ...


@runtime_checkable
class OAuthConnector(Protocol):
    """For source kinds needing three-legged OAuth flow.

    OAuthConnector exposes the two URL-building classmethods every
    three-legged OAuth flow needs: the authorization URL the operator
    visits, and the code-to-token exchange that swaps the redirect
    code for an access+refresh token pair. Classmethods (not instance
    methods) because the flow happens BEFORE the connector instance
    exists — the resulting tokens get stored as a :class:`Credential`
    that the constructor later loads.
    """

    @classmethod
    def oauth_authorization_url(cls, state: str) -> str:
        """Return the URL the operator visits to grant OAuth consent."""
        ...

    @classmethod
    def oauth_code_to_token(cls, code: str) -> dict[str, Any]:
        """Exchange the OAuth code for a token dict (access_token + refresh_token + expiry)."""
        ...


@runtime_checkable
class CredentialsConnector(Protocol):
    """For source kinds that load credentials from a separate Credential record.

    Some connectors transform / normalise / validate the raw credential
    blob (e.g. unwrap a Key Vault reference, decrypt an AES-wrapped
    secret, fetch a downstream service token). ``load_credentials``
    accepts the raw mapping and returns the transformed mapping —
    returning ``None`` signals the credential is invalid for this
    connector's source kind.
    """

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """Transform / validate the raw credential mapping; ``None`` = invalid."""
        ...


# =============================================================================
# Topology v2 (Wave C) — Chunker Protocol (per-(kind, mime) dispatch)
# =============================================================================
#
# ADR v2 §6 introduces a chunker registry behind the SilverProcessor so
# different source kinds chunk on their natural unit (code via tree-sitter,
# tickets per-ticket, slides per-slide, tabular per-row-group, …) instead
# of one uniform paragraph splitter. Wave C lands the Protocol + a fallback
# implementation; Wave F lands the per-kind plugins under
# ``kairix/chunkers/<name>/``.


@runtime_checkable
class Chunker(Protocol):
    """One chunker plugin — emits :class:`Chunk` from one extractor section.

    Plugins register via the future ``kairix.chunkers`` entry-point group.
    Each plugin declares ``version: str`` at module level (F55) and writes
    that string through to every emitted :class:`Chunk` via
    ``chunker_version=self.version`` so re-chunk sweeps after a version
    bump can filter the affected corpus.

    The ``section_kind`` argument lets a single chunker route per typed
    section (text / tabular / image) — Wave F-era chunkers can branch on
    it; the Wave C fallback is paragraph-shaped over text only.
    """

    version: str

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        """Return :class:`Chunk` items derived from ``text``.

        ``section_kind`` is the typed-section discriminator
        (``"text"`` / ``"tabular"`` / ``"image"``). ``source_uri`` is
        propagated through to each emitted :class:`Chunk` per F39.
        """
        ...
