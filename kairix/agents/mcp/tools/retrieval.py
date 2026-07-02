"""MCP tool adapters — retrieval domain (``search`` / ``entity`` / ``timeline``
/ ``expand``).

Each ``tool_<name>`` body is a thin adapter: parse args → call the matching
``run_<name>`` use case in :mod:`kairix.use_cases` → serialise via the use
case's ``<name>_output_to_envelope``. The module also publishes the registered
:data:`BINDINGS` (name + description + warm-gate + a ``make(ctx)`` factory that
returns the correctly-signed body closure) so ``server.py`` registers this
surface by walking ``CAPABILITIES_CATALOG`` instead of hand-writing the
``@server.tool`` defs. Behaviour is byte-identical to the pre-split server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kairix.agents.mcp.cold_start import require_ready
from kairix.agents.mcp.tools._common import DEFAULT_SCOPE, RegistrationContext, ToolBinding
from kairix.core.search.scope import Scope

logger = logging.getLogger(__name__)

__all__ = [
    "BINDINGS",
    "QueueAwareSearchDeps",
    "tool_entity",
    "tool_expand",
    "tool_search",
    "tool_search_queue_aware",
    "tool_timeline",
]


# ---------------------------------------------------------------------------
# Neo4j entity-card helpers — shared with the entity_get / search use cases.
# ---------------------------------------------------------------------------


def _build_entity_summary(row: dict[str, Any]) -> str:
    """Build human-readable summary line from type-specific Neo4j entity fields.

    Each branch appends 0 or 1 phrase; ``industry`` may be a list (joined).
    """
    parts: list[str] = []
    if row.get("role"):
        parts.append(row["role"])
    if row.get("org"):
        parts.append(f"at {row['org']}")
    if row.get("tier"):
        parts.append(f"Tier {row['tier']}")
    if row.get("engagement_status"):
        parts.append(f"({row['engagement_status']})")
    industry = row.get("industry")
    if industry:
        parts.append(", ".join(industry) if isinstance(industry, list) else industry)
    if row.get("domain"):
        parts.append(row["domain"])
    if row.get("category"):
        parts.append(row["category"])
    return " — ".join(parts) if parts else ""


def _default_neo4j_client_factory() -> Any:
    """Production factory — defers the heavy graph-client import until call time."""
    from kairix.knowledge.graph.client import get_client

    return get_client()


def _resolve_neo4j_client(
    neo4j_client: Any | None,
    *,
    client_factory: Callable[[], Any] = _default_neo4j_client_factory,
) -> Any:
    """Return the supplied client, or fall back to ``client_factory()``.

    The ``client_factory`` kwarg is the public DI seam: production callers
    leave it at the default; tests pass a stub factory to exercise the
    fallback path without monkey-patching ``graph_client.get_client``.
    """
    if neo4j_client is not None:
        return neo4j_client
    return client_factory()


def _entity_card_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map a Neo4j row into the entity-card dict shape."""
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "type": row.get("type", ""),
        "summary": _build_entity_summary(row),
        "vault_path": row.get("vault_path") or "",
    }


# Match order matters: slug-id first (cheapest, most precise), then exact
# canonical-name match, then alias match. Without the alias check the
# common "lookup the entity I call X but the crawler stored it as Y"
# case returned not-found — #253. coalesce() guards against nodes that
# pre-date the aliases field (older Neo4j upserts didn't always set it).
_ENTITY_CARD_CYPHER = (
    "MATCH (n) WHERE n.id = $id "
    "   OR toLower(n.name) = toLower($name) "
    "   OR any(alias IN coalesce(n.aliases, []) WHERE toLower(alias) = toLower($name)) "
    "RETURN labels(n)[0] AS type, n.id AS id, n.name AS name, "
    "n.vault_path AS vault_path, "
    "n.role AS role, n.org AS org, "
    "n.tier AS tier, n.engagement_status AS engagement_status, "
    "n.domain AS domain, n.industry AS industry, "
    "n.category AS category "
    "LIMIT 1"
)


def _fetch_entity_card(
    name: str,
    *,
    neo4j_client: Any | None = None,
    client_factory: Callable[[], Any] = _default_neo4j_client_factory,
) -> dict[str, Any] | None:
    """Fetch entity card directly from Neo4j, bypassing MCP tool layer.

    Returns a dict with id, name, type, summary, vault_path on success,
    or None if the entity is not found or Neo4j is unavailable.

    Args:
        neo4j_client: Injectable Neo4j client for testing.
                      Defaults to the production client via ``client_factory``.
        client_factory: Public DI seam — tests pass a stub factory to
                        exercise the "no client → factory()" fallback.
    """
    try:
        from kairix.utils import slugify as _slugify

        neo4j = _resolve_neo4j_client(neo4j_client, client_factory=client_factory)
        if not neo4j.available:
            return None
        rows = neo4j.cypher(_ENTITY_CARD_CYPHER, {"id": _slugify(name), "name": name})
        if not rows:
            return None
        return _entity_card_from_row(rows[0])
    except (ImportError, RuntimeError, OSError, KeyError) as exc:
        logger.warning("_fetch_entity_card failed: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Tool bodies — pure Python, no mcp dependency.
# ---------------------------------------------------------------------------


def tool_search(
    query: str,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = 3000,
    limit: int = 10,
    max_tier: str = "L2",
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Search the knowledge store.

    Thin adapter around ``kairix.use_cases.search.run_search``. CLI and
    MCP both delegate to the same use case so the surfaces stay aligned
    (closes Phase-2 drift in #168).

    ``max_tier`` (PLA-270) lets the agent request the cheapest sufficient
    context per hit: ``"L0"`` abstracts, ``"L1"`` overviews, or ``"L2"``
    full snippets (the default). Use a cheaper ceiling to triage many hits
    within a tight token budget, then re-query a promising hit at ``"L2"``.

    The optional ``deps`` parameter forwards a ``SearchDeps`` directly
    to the use case — production callers leave it None; tests pass a
    ``SearchDeps`` to drive without touching live services.
    """
    from kairix.use_cases.search import run_search, search_output_to_envelope

    logger.info("mcp.search: agent=%r scope=%r max_tier=%r", agent, scope, max_tier)
    out = run_search(
        query,
        agent=agent,
        scope=scope,
        budget=budget,
        limit=limit,
        max_tier=max_tier,
        deps=deps,
    )
    return search_output_to_envelope(out)


def _default_flag_reader(name: str) -> bool:
    """Production feature-flag reader — defers the import to call time."""
    from kairix.core.features import flag

    return flag(name)


@dataclass
class QueueAwareSearchDeps:
    """Injectable dependencies for :func:`tool_search_queue_aware`.

    F6-clean: every field has a ``default_factory`` so production
    callers construct ``QueueAwareSearchDeps()`` and get the real
    boundary calls; tests construct
    ``QueueAwareSearchDeps(flag_reader=lambda _: True, ...)`` and pass
    it as a single argument. Matches :class:`WorkerDeps`'s discipline
    for the sibling worker callables.

    Fields:
      * ``flag_reader`` — feature-flag lookup; default
        :func:`_default_flag_reader` (calls
        :func:`kairix.core.features.flag`).
      * ``search_fn`` — search delegate; default :func:`tool_search`.
        Tests pass a stub so the dispatch/queue surface is the
        property under test, not the search pipeline.
      * ``queue_db_factory`` — returns the SQLite connection used for
        carry-along reads. Default returns ``None`` so production
        callers opt-in by passing a connection-returning callable
        once the production wiring is in place; tests pass a
        ``tmp_path``-backed factory.
    """

    flag_reader: Callable[[str], bool] = field(default_factory=lambda: _default_flag_reader)
    search_fn: Callable[..., dict[str, Any]] = field(default_factory=lambda: tool_search)
    queue_db_factory: Callable[[], Any] = field(default_factory=lambda: _default_queue_db)


def _default_queue_db() -> Any:
    """Default queue-db factory — None until G.2 wires the production conn through."""
    return None


def tool_search_queue_aware(
    query: str,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = 3000,
    limit: int = 10,
    *,
    agent_id: str | None = None,
    deps: Any = None,
    queue_deps: QueueAwareSearchDeps | None = None,
) -> Any:
    """Queue-aware wrapper around :func:`tool_search` for the ADR-029 G.1 spike.

    When the ``agent_query_queue`` feature flag is OFF (default),
    delegates straight to :func:`tool_search` — the response shape is
    byte-identical to the pre-spike behaviour. When ON, the call routes
    through :func:`kairix.core.queue.dispatch_or_queue` and any
    completed pending_queries rows for ``agent_id`` are carried back
    as a prefix string keyed under ``"carry_along"`` in the response
    envelope.

    Args mirror :func:`tool_search` plus two seams:

    * ``agent_id`` — the canonical agent identifier from MCP session
      headers; used as the dedup key for the queue. Falls back to
      ``"unknown-agent"`` when None (logged once per call via F21).
    * ``queue_deps`` — :class:`QueueAwareSearchDeps` holding the
      flag-reader / search-delegate / queue-db factory. Production
      callers leave None and the dataclass's ``default_factory`` shape
      wires the real boundary calls; tests pass an instance with
      stubs to drive both branches without touching live services.

    Returns the same dict shape as :func:`tool_search` when the queue
    path is OFF or the handler returns within budget. When the queue
    path is ON and the budget is exceeded, returns the plain string
    ``"Processing your request (id: q_<hash>)..."`` — NOT an error
    envelope — so the agent interprets it as "accepted, continue".
    """
    from kairix.core.queue import carry_along
    from kairix.core.queue.dispatch import dispatch_or_queue

    resolved_deps = queue_deps if queue_deps is not None else QueueAwareSearchDeps()
    delegate = resolved_deps.search_fn
    reader = resolved_deps.flag_reader

    if not reader("agent_query_queue"):
        return delegate(query=query, agent=agent, scope=scope, budget=budget, limit=limit, deps=deps)

    resolved_agent_id = agent_id or "unknown-agent"

    @dispatch_or_queue(tool_name="tool_search")
    def _handler(
        query: str,
        agent: str | None,
        scope: Scope,
        budget: int,
        limit: int,
        *,
        agent_id: str,
        deps: Any,
    ) -> dict[str, Any]:
        # `agent_id` is part of the dispatch_or_queue contract — the
        # decorator reads it via kwargs.get('agent_id') on the wrapper
        # layer to build the dedup hash + the pending_queries row owner;
        # log it here so the parameter has a real consumer (F19 — every
        # named parameter must be load-bearing).
        logger.debug("tool_search dispatched for agent_id=%r", agent_id)
        return delegate(query=query, agent=agent, scope=scope, budget=budget, limit=limit, deps=deps)

    result = _handler(
        query,
        agent,
        scope,
        budget,
        limit,
        agent_id=resolved_agent_id,
        deps=deps,
    )

    # Plain-text queued path — pass through unchanged so the agent
    # reads it as "accepted, continue".
    if isinstance(result, str):
        return result

    prefix = carry_along.carry_along_prefix_safe(resolved_agent_id, resolved_deps.queue_db_factory())
    if prefix and isinstance(result, dict):
        result = {**result, "carry_along": prefix}
    return result


def tool_entity(
    name: str,
    *,
    deps: Any = None,
    neo4j_client: Any | None = None,
    client_factory: Callable[[], Any] = _default_neo4j_client_factory,
) -> dict[str, Any]:
    """Look up a specific person, company, or topic by name.

    Thin adapter around ``kairix.use_cases.entity_get.run_entity_get``.
    This is a quick, direct lookup from the knowledge graph (Neo4j) —
    use it when you already know the name of what you're looking for.

    The optional ``deps`` parameter forwards an ``EntityGetDeps`` directly
    to the use case — production callers leave it None.

    Two test seams sit below ``deps``:
      - ``neo4j_client``: legacy explicit-client kwarg; overrides the
        default ``_fetch_entity_card`` helper's Neo4j client when set.
      - ``client_factory``: drives the "no client → factory()" fallback
        path in ``_resolve_neo4j_client`` without monkey-patching the
        ``graph_client.get_client`` import.
    Prefer ``deps`` for new code.
    """
    from kairix.use_cases.entity_get import EntityGetDeps, entity_get_output_to_envelope, run_entity_get

    if deps is None:
        deps = EntityGetDeps(
            fetch_fn=lambda n: _fetch_entity_card(n, neo4j_client=neo4j_client, client_factory=client_factory)
        )

    out = run_entity_get(name, deps=deps)
    return entity_get_output_to_envelope(out)


def tool_timeline(
    query: str,
    anchor_date: str | None = None,
    agent: str | None = None,
    scope: Scope = DEFAULT_SCOPE,
) -> dict[str, Any]:
    """Date-aware retrieval: rewrite a temporal query and fetch results.

    Thin adapter around ``kairix.use_cases.timeline.run_timeline``. CLI and
    MCP both call the same use case so behaviour is identical (closes #163,
    Phase 1 of #168). The use case extracts a time window from the query
    (or accepts explicit since/until), tries the temporal-chunks index
    first, then falls through to the search pipeline.
    """
    from datetime import date as _date

    from kairix.use_cases.timeline import run_timeline, timeline_output_to_envelope

    anchor: _date | None = None
    if anchor_date:
        try:
            anchor = _date.fromisoformat(anchor_date)
        except ValueError:
            pass

    result = run_timeline(
        query,
        anchor_date=anchor,
        agent=agent,
        scope=scope,
    )

    return timeline_output_to_envelope(result)


def tool_expand(
    source_uri: str,
    seq: int | None = None,
    token_budget: int = 2000,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Expand a search hit to its neighbouring chunks within a token budget.

    Thin adapter around ``kairix.use_cases.expand.run_expand``. CLI and MCP
    both delegate to the same use case so the surfaces stay aligned (#168).

    Call this after a search / recall hit when you need the surrounding
    context: pass the hit's ``source_uri`` + ``seq`` (the typed PLA-270
    fields on every ``SearchHit``) and expand returns the matched chunk plus
    the preceding and following chunks — so you read context WITHOUT
    re-ingesting the whole document. For a doc / section-level (L2) hit whose
    ``seq`` is null, omit ``seq`` (PLA-297): expand resolves the document's
    chunks by ``source_uri`` and anchors on the first, so the handoff never
    dead-ends. When the source has no finer chunks, the envelope returns the
    whole-document content with ``no_finer_chunks=True``.

    The optional ``deps`` parameter forwards an ``ExpandDeps`` directly to the
    use case — production callers leave it None; tests pass an ``ExpandDeps``
    carrying a fake chunk reader to drive without touching SQLite.
    """
    from kairix.use_cases.expand import expand_output_to_envelope, run_expand

    logger.info("mcp.expand: source_uri=%r seq=%s budget=%d", source_uri, seq, token_budget)
    out = run_expand(source_uri, seq, token_budget=token_budget, deps=deps)
    return expand_output_to_envelope(out)


# ---------------------------------------------------------------------------
# Registration bindings — one per registered MCP tool in this domain.
# ---------------------------------------------------------------------------

_SEARCH_DESCRIPTION = (
    "Call before answering any factual question about prior work, decisions, or context — "
    "kairix indexes the team's knowledge store and finds relevant prior material. "
    "Use this proactively at session start and whenever a question touches the team's history. "
    "If the result has error_code=KAIRIX_COLD_START, do not answer from memory or fallback; "
    "wait retry_after_ms and retry the same call once. "
    "Expected p99: 3s warm, 15s cold. Recommended client timeout: 30s."
)

_ENTITY_DESCRIPTION = (
    "Call when you need facts about a specific named entity (person, company, project) — "
    "direct knowledge-graph lookup, faster than search. "
    "Expected p99: 5s warm, 10s cold. Recommended client timeout: 30s."
)

_TIMELINE_DESCRIPTION = (
    "Call for date-aware retrieval when a question depends on timing. "
    "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry the same call. "
    "Expected p99: 15s warm, 30s cold. Recommended client timeout: 60s."
)

_EXPAND_DESCRIPTION = (
    "Call after a search/recall hit when you need the surrounding context — "
    "expand pulls the matched chunk's neighbouring chunks (the preceding and "
    "following ones) within a token budget, so you read context WITHOUT "
    "re-ingesting the whole document. Pass the hit's source_uri + seq (the "
    "typed fields on every search result). For a document/section-level hit "
    "whose seq is null, pass source_uri alone and expand resolves the "
    "document's chunks for you. Works even while kairix is still "
    "warming up — it only reads the local index."
)


def _make_search(ctx: RegistrationContext) -> Callable[..., Any]:
    def search(
        query: str,
        agent: str | None = None,
        scope: Scope = DEFAULT_SCOPE,
        budget: int = 3000,
        limit: int = 10,
    ) -> Any:
        """Search your knowledge store — finds the best answers to any question.

        Routes through :func:`tool_search_queue_aware` so the ADR-029 G.1
        agent_query_queue flag chooses sync-only (OFF, today's behaviour)
        vs dispatch-or-queue + carry-along (ON, the spike).
        """
        if cold := require_ready("search", ctx.readiness_check):
            return cold
        return tool_search_queue_aware(query=query, agent=agent, scope=scope, budget=budget, limit=limit)

    return search


def _make_entity(_ctx: RegistrationContext) -> Callable[..., Any]:
    def entity(name: str) -> dict[str, Any]:
        """Entity lookup from Neo4j."""
        return tool_entity(name=name)

    return entity


def _make_timeline(ctx: RegistrationContext) -> Callable[..., Any]:
    def timeline(
        query: str,
        anchor_date: str | None = None,
        agent: str | None = None,
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Temporal query rewriting + date-aware retrieval."""
        if cold := require_ready("timeline", ctx.readiness_check):
            return cold
        return tool_timeline(
            query=query,
            anchor_date=anchor_date,
            agent=agent,
            scope=scope,
        )

    return timeline


def _make_expand(_ctx: RegistrationContext) -> Callable[..., Any]:
    def expand(source_uri: str, seq: int | None = None, token_budget: int = 2000) -> dict[str, Any]:
        """Expand a search hit to its neighbouring chunks within a token budget."""
        return tool_expand(source_uri=source_uri, seq=seq, token_budget=token_budget)

    return expand


BINDINGS: tuple[ToolBinding, ...] = (
    ToolBinding(name="search", description=_SEARCH_DESCRIPTION, make=_make_search, warm_gated=True),
    ToolBinding(name="entity", description=_ENTITY_DESCRIPTION, make=_make_entity, warm_gated=True),
    ToolBinding(name="timeline", description=_TIMELINE_DESCRIPTION, make=_make_timeline, warm_gated=True),
    ToolBinding(name="expand", description=_EXPAND_DESCRIPTION, make=_make_expand, warm_gated=False),
)
