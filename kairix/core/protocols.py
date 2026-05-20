"""
Domain protocol definitions for kairix core boundaries.

Each Protocol represents the agreed interface between bounded contexts.
All protocols use @runtime_checkable so contract tests can verify
conformance via isinstance() checks.

Follows the same pattern as kairix.platform.llm.protocol.LLMBackend.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kairix.core.search.intent import QueryIntent


@runtime_checkable
class IntentClassifier(Protocol):
    """Classifies a search query into a QueryIntent dispatch category."""

    def classify(self, query: str) -> QueryIntent: ...


@runtime_checkable
class DocumentRepository(Protocol):
    """Read/write interface for the document store (SQLite FTS5 backed)."""

    def search_fts(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    def get_by_path(self, path: str) -> dict[str, Any] | None: ...

    def get_chunk_dates(self, paths: list[str]) -> dict[str, str]: ...

    def insert_or_update(
        self,
        path: str,
        collection: str,
        title: str,
        content: str,
        content_hash: str,
    ) -> None: ...


@runtime_checkable
class GraphRepository(Protocol):
    """Interface for the entity graph (Neo4j backed)."""

    @property
    def available(self) -> bool: ...

    def find_entity(self, name: str) -> dict[str, Any] | None: ...

    def entity_in_degrees(self) -> list[dict[str, Any]]: ...

    def cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


@runtime_checkable
class VectorRepository(Protocol):
    """Interface for the vector index (usearch backed)."""

    def search(
        self,
        query_vec: list[float],
        k: int,
        collections: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def add_vectors(self, items: list[tuple[str, list[float]]]) -> int: ...

    def count(self) -> int: ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Text embedding interface (single and batch)."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class FusionStrategy(Protocol):
    """Fuses BM25 and vector result lists into a single ranked list."""

    def fuse(self, bm25: list[Any], vec: list[Any]) -> list[Any]: ...


@runtime_checkable
class BoostStrategy(Protocol):
    """Post-fusion boost strategy (entity, procedural, temporal, etc.)."""

    def boost(self, results: list[Any], query: str, context: dict[str, Any]) -> list[Any]: ...


@runtime_checkable
class ScoringStrategy(Protocol):
    """Scores retrieved results against gold-standard documents."""

    def score(self, retrieved: list[str], gold: list[dict[str, Any]]) -> float: ...


@runtime_checkable
class SearchLogger(Protocol):
    """Structured logging for search and query events."""

    def log_search(self, event: dict[str, Any]) -> None: ...

    def log_query(self, event: dict[str, Any]) -> None: ...


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

    def resolve(self, agent: str | None, scope: Any) -> list[str] | None: ...


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

    def list_agents(self) -> list[Any]: ...

    def collection_for(self, name: str) -> str: ...

    def validate_write(self, agent_name: str, path: str) -> bool: ...


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
    ) -> str: ...


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
    ) -> Any: ...

    def calibrate(self) -> bool: ...


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
    ) -> list[Any]: ...


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
    ) -> Any: ...


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
    def add(self, content: str, *, metadata: dict[str, Any] | None = None) -> str: ...

    # search(query, *, top_k): up to top_k Memory-shaped objects, best
    # first. Empty list is a valid "no relevant content" signal — callers
    # MUST tolerate it. Implementations may return fewer than top_k.
    def search(self, query: str, *, top_k: int = 10) -> list[Any]: ...

    # update(memory_id, content): replace content of an existing memory.
    # Raises KeyError (or backend equivalent) if id absent. Backends with
    # append-only semantics (mem0 consolidation) may supersede rather than
    # replace — the Protocol does not pin the strategy.
    def update(self, memory_id: str, content: str) -> None: ...

    # delete(memory_id): remove. No-op if id is already absent.
    def delete(self, memory_id: str) -> None: ...


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

    def add(self, content: str, *, metadata: dict[str, Any] | None = None) -> str: ...
    def search(self, query: str, *, top_k: int = 10) -> list[Any]: ...
    def update(self, memory_id: str, content: str) -> None: ...
    def delete(self, memory_id: str) -> None: ...

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
    ) -> str: ...


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
    def id(self) -> str: ...
    @property
    def content(self) -> str: ...
    @property
    def score(self) -> float: ...
    @property
    def metadata(self) -> dict[str, Any]: ...


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
    """

    @property
    def id(self) -> str: ...
    @property
    def entity(self) -> str: ...
    @property
    def attribute(self) -> str: ...
    @property
    def value(self) -> str: ...
    @property
    def confidence(self) -> float: ...
    @property
    def source_turn_ids(self) -> tuple[str, ...]: ...
    @property
    def extracted_at(self) -> str: ...
    @property
    def superseded_by(self) -> str | None: ...
    @property
    def namespace(self) -> str: ...


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
    def record(self) -> FactRecord: ...
    @property
    def score(self) -> float: ...


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

    # extract(turns, window_hint): zero or more FactRecords grounded in turns.
    # turns: list of turn dicts with at least id, speaker/role, content/text
    # keys (LoCoMo / chat-message shape). window_hint reserved for future
    # prompt-engineering knobs; production extractors may ignore it.
    # Empty-list return is a valid "no facts groundable" signal — callers
    # MUST tolerate it without raising.
    def extract(
        self, *, turns: list[dict[str, Any]], window_hint: dict[str, Any] | None = None
    ) -> list[FactRecord]: ...


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
    def add(self, fact: FactRecord) -> None: ...

    # search(query, *, top_k, namespace): up to top_k facts matching query,
    # best first. namespace=non-None restricts to that namespace (engagement-
    # scoped recall for consultancy-in-a-box); None means "all namespaces".
    # Empty list is a valid "no facts" signal; by default excludes superseded.
    def search(self, query: str, *, top_k: int = 10, namespace: str | None = None) -> list[FactHit]: ...

    # find_conflicts(*, entity, attribute, namespace): live (non-superseded)
    # facts for the (entity, attribute) key. Used by the consolidation pass:
    # on every new fact, the ingest pipeline finds existing facts about the
    # same entity-attribute pair, then runs the contradict use case to decide
    # whether the new fact supersedes them.
    def find_conflicts(self, *, entity: str, attribute: str, namespace: str | None = None) -> list[FactRecord]: ...

    # supersede(*, old_id, new_id): mark old_id as superseded by new_id.
    # After this, old_id no longer appears in default search but stays
    # retrievable for audit (future include_superseded=True kwarg).
    # Raises KeyError if either id is absent.
    def supersede(self, *, old_id: str, new_id: str) -> None: ...
