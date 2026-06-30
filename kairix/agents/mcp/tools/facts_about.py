"""MCP tool — ``facts_about``: agent-facing knowledge introspection.

Lets an agent ask "what does kairix know about <entity>?" without
running the full SearchPipeline. The tool runs a free-text recall over
two cheap, local SQLite surfaces and returns a flat dict:

- the configured :class:`FactStore` — a free-text recall over the
  extracted entity-attribute-value records. ``FactStore.search`` is an
  FTS/BM25 (optionally vector-fused) match over the concatenated
  ``(entity, attribute, value)`` text, NOT an exact ``record.entity ==
  entity`` key lookup — so a query surfaces every fact whose text the
  recall ranks, filtered to non-superseded records so the agent sees the
  current ground truth; and
- the synthetic ``entity-summaries`` collection (#467, populated by the
  ``entity_summary_indexing_enabled`` projector) — so an entity that has
  a Wikidata-style summary chunk surfaces that summary even when no
  conversation fact about it has been extracted yet.

Cold-safe (PLA-263): both reads are local SQLite (FTS5 + an indexed
lookup) with no embedding model and no network, so the tool is NOT
warm-gated — an agent introspecting at session start, while the
retrieval stack is still warming, still gets an answer.

Dependency injection:

- ``fact_store`` / ``document_repo`` are constructor-injected. Production
  callers leave them ``None`` and the tool resolves a real
  :class:`SQLiteFactStore` + :class:`SQLiteDocumentRepository` against
  the configured ``paths.db_path``. Tests inject ``FakeFactStore`` /
  ``FakeDocumentRepository`` pre-loaded with scripted data.

Errors:

- Returns ``{"error": "<Name>", ...}`` rather than raising — agents
  read the ``error`` key to decide whether the call succeeded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kairix.core.facts.records import resolve_fact_source_uri
from kairix.core.protocols import DocumentRepository, FactStore, SourceRef
from kairix.paths import KairixPaths

logger = logging.getLogger(__name__)

__all__ = ["FactView", "FactsAboutDeps", "tool_facts_about"]


ERROR_INVALID_INPUT = "InvalidInput"
ERROR_LOOKUP_FAILED = "LookupFailed"

# The synthetic projector-fed collection that ``entity_summary_indexing_enabled``
# populates (#467 / ADR-036). Single-sourced here so the entity-summary read
# and the response key stay in lockstep (F17).
ENTITY_SUMMARIES_COLLECTION = "entity-summaries"


@dataclass(frozen=True)
class FactsAboutDeps:
    """Injection seam for the registered ``facts_about`` MCP tool.

    Production callers (the ``build_server`` default) leave this ``None``
    so the tool resolves the real ``SQLiteFactStore`` +
    ``SQLiteDocumentRepository`` against the configured db path. An
    integration test passes fakes here so it can drive the registered
    tool — through the live MCP dispatch surface, while cold — against
    scripted facts + entity summaries without touching the live tree
    (F1/F2-clean: a constructor seam, no monkeypatch, no env vars).
    """

    fact_store: FactStore | None = None
    document_repo: DocumentRepository | None = None
    canonicals: list[Any] | None = None
    paths: KairixPaths | None = None


@dataclass(frozen=True)
class FactView:
    """Agent-facing ``facts_about`` result row carrying the resolvable breadcrumb (PLA-261).

    The fact-store leg of ``facts_about`` historically returned only opaque
    ``source_turn_ids`` — an agent could not open the source to verify or act
    on a recalled fact (#467), breaking the recall→verify→act loop. ``FactView``
    keeps the Protocol-pinned FactRecord fields AND surfaces the conversation
    the fact was grounded in (``conversation_id``) plus the canonical,
    re-openable ``source_uri`` resolved via the shared
    :func:`kairix.core.facts.resolve_fact_source_uri`.

    Conforms to the shared SourceRef breadcrumb contract (F97) via
    :meth:`source_ref` (the RETURN option) so this surface cites/re-opens its
    source the SAME way as search / timeline / entity / prep / research /
    contradict — no per-surface pointer drift (PLA-274).
    """

    id: str
    entity: str
    attribute: str
    value: str
    confidence: float
    source_turn_ids: tuple[str, ...]
    extracted_at: str
    evidence_at: str | None
    namespace: str
    conversation_id: str | None
    source_uri: str
    score: float

    @classmethod
    def from_hit(cls, hit: Any) -> FactView:
        """Project a :class:`FactHit` onto the agent read surface.

        ``source_uri`` is resolved through the shared breadcrumb resolver so
        the opaque turn-ids now travel with a re-openable pointer; legacy /
        federated records resolve via the same fallback chain.
        """
        record = hit.record
        return cls(
            id=record.id,
            entity=record.entity,
            attribute=record.attribute,
            value=record.value,
            confidence=record.confidence,
            source_turn_ids=tuple(record.source_turn_ids),
            extracted_at=record.extracted_at,
            evidence_at=record.evidence_at,
            namespace=record.namespace,
            conversation_id=getattr(record, "conversation_id", None),
            source_uri=resolve_fact_source_uri(record),
            score=hit.score,
        )

    def source_ref(self) -> SourceRef:
        """Return the shared breadcrumb (F97 RETURN option).

        Built through :meth:`SourceRef.of` so the source_uri→path fallback
        and non-paged locator derivation apply uniformly with every other
        surface. ``path`` carries the same resolvable pointer as
        ``source_uri`` (a fact has no separate display path); the entity +
        attribute become the human title.
        """
        return SourceRef.of(
            path=self.source_uri,
            source_uri=self.source_uri,
            title=f"{self.entity} — {self.attribute}",
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the flat dict the agent reads.

        Keeps every key the pre-PLA-261 surface emitted (so existing agents
        are unaffected) and adds ``conversation_id`` + ``source_uri`` + the
        shared ``source_ref`` breadcrumb envelope.
        """
        return {
            "id": self.id,
            "entity": self.entity,
            "attribute": self.attribute,
            "value": self.value,
            "confidence": self.confidence,
            "source_turn_ids": list(self.source_turn_ids),
            "extracted_at": self.extracted_at,
            "evidence_at": self.evidence_at,
            "namespace": self.namespace,
            "conversation_id": self.conversation_id,
            "source_uri": self.source_uri,
            "source_ref": self.source_ref().to_envelope(),
            "score": self.score,
        }


def _hit_to_dict(hit: Any) -> dict[str, Any]:
    """Project a :class:`FactHit` onto the agent-facing read surface (via :class:`FactView`)."""
    return FactView.from_hit(hit).to_dict()


def _entity_summary_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Project one ``entity-summaries`` FTS row onto the agent read surface.

    ``summary`` is the FTS5 snippet window centred on the matched entity
    terms — for a short Wikidata-style summary that is the whole summary;
    ``source`` is the ``entity://<qid>`` chunk path so the agent can cite
    it. Falls back across the row-shape keys the production BM25 row and
    the in-memory fake emit (``snippet``/``content``, ``file``/``path``).
    """
    return {
        "summary": str(row.get("snippet") or row.get("content") or ""),
        "source": str(row.get("file") or row.get("path") or ""),
        "score": float(row.get("score", 0.0) or 0.0),
    }


def _query_entity_summaries(
    document_repo: DocumentRepository,
    entity: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Free-text recall over the synthetic ``entity-summaries`` collection.

    Failure-isolated: a degraded document store yields ``[]`` and the
    response carries facts only — the same defensive contract the
    canonical lookup uses. The production ``SQLiteDocumentRepository``
    already never raises (returns ``[]`` on any failure); the broad guard
    covers injected fakes that model an outage.
    """
    try:
        rows = document_repo.search_fts(entity, collections=[ENTITY_SUMMARIES_COLLECTION], limit=top_k)
    except Exception as exc:
        logger.warning("tool_facts_about: entity-summary lookup failed: %s", exc)
        return []
    return [_entity_summary_to_dict(r) for r in rows]


def _resolve_canonical_match(entity: str, canonicals: list[Any]) -> dict[str, Any] | None:
    """Return ``{name, type, summary, aliases}`` if ``entity`` matches a
    declared canonical (by name OR alias); otherwise ``None``.

    Match is case-insensitive on name + aliases. The first match wins
    so operators with overlapping aliases see deterministic results.

    Internal helper for #431's canonical-first ordering on
    ``facts_about``. Kept module-local because the only caller is
    ``tool_facts_about``.
    """
    if not entity:
        return None
    needle = entity.strip().lower()
    for c in canonicals:
        name = str(getattr(c, "name", "")).strip()
        aliases = tuple(getattr(c, "aliases", ()) or ())
        if name.lower() == needle:
            return {
                "name": name,
                "type": str(getattr(c, "entity_type", "")),
                "summary": str(getattr(c, "summary", "")),
                "aliases": list(aliases),
            }
        if any(a.lower() == needle for a in aliases if isinstance(a, str)):
            return {
                "name": name,
                "type": str(getattr(c, "entity_type", "")),
                "summary": str(getattr(c, "summary", "")),
                "aliases": list(aliases),
            }
    return None


def _default_canonical_loader() -> list[Any]:
    """Production default — reads the operator's YAML via config_loader."""
    from kairix.core.search.config_loader import load_canonical_entities

    return load_canonical_entities()


def _error_envelope(
    *,
    error: str,
    detail: str,
    entity: str,
    namespace: str | None,
    top_k: int,
) -> dict[str, Any]:
    """Build a zero-results failure envelope.

    Centralises the shape so the ``canonical`` / ``entity_summaries`` /
    ``hits`` keys agents read are always present on the error paths too
    (F17 — one edit site if the read surface grows).
    """
    return {
        "error": error,
        "detail": detail,
        "entity": entity,
        "namespace": namespace,
        "top_k": top_k,
        "canonical": None,
        "entity_summaries": [],
        "hits": [],
    }


def tool_facts_about(
    entity: str,
    namespace: str | None = None,
    top_k: int = 20,
    *,
    paths: KairixPaths | None = None,
    fact_store: FactStore | None = None,
    document_repo: DocumentRepository | None = None,
    canonicals: list[Any] | None = None,
) -> dict[str, Any]:
    """Return what kairix knows about an entity.

    Parameters
    ----------
    entity:
        The name to search for. Empty string is rejected with the
        ``InvalidInput`` envelope — there is no meaningful "all facts"
        semantics on this surface; agents that want a broad sweep should
        call ``search`` instead.
    namespace:
        Engagement-scope filter on the fact-store leg. ``None`` (the
        default) means "across all namespaces". Passing a string restricts
        fact hits to that namespace — used by agents whose session is
        pinned to a single engagement scope.
    top_k:
        Maximum number of hits to return from each leg. Default 20 mirrors
        a typical agent introspection budget.
    paths / fact_store / document_repo:
        Optional DI seams — production callers leave them ``None`` and the
        tool resolves a real ``SQLiteFactStore`` + ``SQLiteDocumentRepository``
        against ``paths.db_path``. Tests inject fakes from ``tests/fakes.py``.
    canonicals:
        Optional declared-canonical list (#431). ``None`` resolves the
        operator's YAML via the config loader; tests pass a list directly.

    Returns
    -------
    dict
        Success: ``{"entity", "namespace", "top_k", "canonical",
        "entity_summaries", "hits", "error": ""}``. ``hits`` is the fact
        store's free-text recall (each a FactRecord read surface + the
        recall ``score``; superseded facts filtered by ``FactStore.search``).
        ``entity_summaries`` is the recall over the synthetic
        ``entity-summaries`` collection (each ``{summary, source, score}``).
        Failure: ``{"error": "<Name>", "detail": "...", "hits": [],
        "entity_summaries": []}``.
    """
    if not entity:
        return _error_envelope(
            error=ERROR_INVALID_INPUT,
            detail="entity was empty; pass a non-empty name",
            entity=entity,
            namespace=namespace,
            top_k=top_k,
        )

    # Resolve the two SQLite-backed read seams. Both the fact store and the
    # entity-summaries collection are cheap local SQLite reads — no embedding
    # model, no network — which is why facts_about serves while kairix is
    # still warming (PLA-263). Defer the heavy imports to call time so cold
    # module imports stay cheap.
    if fact_store is None or document_repo is None:
        resolved_paths = paths if paths is not None else KairixPaths.resolve()
        if fact_store is None:
            from kairix.core.facts import SQLiteFactStore

            fact_store = SQLiteFactStore(db_path=resolved_paths.db_path)
        if document_repo is None:
            from kairix.core.db.repository import SQLiteDocumentRepository

            document_repo = SQLiteDocumentRepository(resolved_paths.db_path)

    try:
        hits = fact_store.search(entity, top_k=top_k, namespace=namespace)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("tool_facts_about: fact_store.search raised: %s", exc, exc_info=True)
        return _error_envelope(
            error=ERROR_LOOKUP_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            entity=entity,
            namespace=namespace,
            top_k=top_k,
        )

    # #431 — canonical-first ordering. When the looked-up entity matches
    # an operator-declared canonical (by name or alias), surface the
    # canonical's summary in the response so agents can render
    # 'this is a canonical entity: <summary>' above the fact-store
    # hits. Lookup is failure-isolated; a degraded load yields
    # ``canonical: None`` and the response carries facts only.
    canonical_match = None
    try:
        resolved_canonicals = canonicals if canonicals is not None else _default_canonical_loader()
        canonical_match = _resolve_canonical_match(entity, resolved_canonicals)
    except Exception as exc:
        logger.warning("tool_facts_about: canonical lookup failed: %s", exc)

    # #467 / PLA-263 — surface the entity-summary chunk that the
    # entity_summary_indexing projector landed in the synthetic
    # ``entity-summaries`` collection, so facts_about('X') answers even
    # when no conversation fact about X has been extracted yet.
    entity_summaries = _query_entity_summaries(document_repo, entity, top_k)

    response: dict[str, Any] = {
        "entity": entity,
        "namespace": namespace,
        "top_k": top_k,
        "canonical": canonical_match,
        "entity_summaries": entity_summaries,
        "hits": [_hit_to_dict(h) for h in hits],
        "error": "",
    }
    return response
