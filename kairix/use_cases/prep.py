"""Prep use case — tiered L0/L1 context summary shared by CLI and MCP.

Phase 3c of the CLI/MCP feature parity initiative (#168). Pre-Phase-3c
``prep`` was MCP-only — operators couldn't reproduce an agent's prep
output from a shell. This module wraps the existing tool_prep logic
so both surfaces call the same ``run_prep``.

Synthesis shape (#397 W-C C2 investigation): ``run_prep`` is a
single-section synthesis. One ``search(...)`` call + one
``_format_context(...)`` + one ``chat(messages=..., max_tokens=...)``
call. There is no per-section fan-out, so no ``asyncio.gather``
opportunity exists at this layer. The L0/L1 tier selector chooses
prompt + budget shape, not multiple sections to synthesise.
Re-evaluate when a future tier requirement adds parallel sub-syntheses.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from kairix.core.search.prep_summary_cache import (
    DEFAULT_MAX_AGE_S as _PREP_DEFAULT_MAX_AGE_S,
)
from kairix.core.search.prep_summary_cache import (
    DEFAULT_MAX_ENTRIES as _PREP_DEFAULT_MAX_ENTRIES,
)
from kairix.core.search.prep_summary_cache import (
    PrepSummaryCache,
    make_prep_cache_key,
)
from kairix.core.search.scope import Scope
from kairix.paths import trace_enabled
from kairix.text import estimate_tokens

logger = logging.getLogger(__name__)


# Process-shared PrepSummaryCache. Lazy-initialised on first prep call
# so the env-var bounds (if any) are read once at startup. Mirrors the
# ``_QUERY_CACHE`` pattern in ``kairix.core.factory`` so the operator
# surface (``probe caches``) sees both caches via the same accessor
# pattern.
_PREP_SUMMARY_CACHE: PrepSummaryCache | None = None
_PREP_SUMMARY_CACHE_LOCK = threading.Lock()


def _get_or_create_prep_summary_cache() -> PrepSummaryCache:
    """Return the process-shared :class:`PrepSummaryCache`, building it lazily.

    Mirrors :func:`kairix.core.factory._get_or_create_query_cache`. The
    cache's bounds are the module defaults today — env-var overrides
    can be threaded through the same pattern as ``KAIRIX_QUERY_CACHE_*``
    when an operator's prep workload demands it.
    """
    global _PREP_SUMMARY_CACHE
    with _PREP_SUMMARY_CACHE_LOCK:
        if _PREP_SUMMARY_CACHE is None:
            _PREP_SUMMARY_CACHE = PrepSummaryCache(
                max_entries=_PREP_DEFAULT_MAX_ENTRIES,
                max_age_s=_PREP_DEFAULT_MAX_AGE_S,
            )
        return _PREP_SUMMARY_CACHE


def get_prep_summary_cache() -> PrepSummaryCache:
    """Public accessor for the process-shared prep summary cache.

    Used by the ``kairix caches`` CLI to surface hit / miss /
    eviction counts. Going through this helper keeps the module-global
    hidden so callers can't accidentally rebind ``_PREP_SUMMARY_CACHE``.
    """
    return _get_or_create_prep_summary_cache()


def reset_prep_summary_cache() -> None:
    """Drop every cached prep summary. Tests + operator reload paths call this."""
    with _PREP_SUMMARY_CACHE_LOCK:
        if _PREP_SUMMARY_CACHE is not None:
            _PREP_SUMMARY_CACHE.clear()


_L0_BUDGET = 1500
_L1_BUDGET = 3000
_L0_MAX_TOKENS = 150
_L1_MAX_TOKENS = 600


def _build_production_search_pipeline() -> Any:
    from kairix.core.factory import build_search_pipeline

    return build_search_pipeline()


def _resolve_production_provider_name() -> str | None:
    from kairix.paths import provider_name

    return provider_name()


def _resolve_production_provider(name: str) -> Any:
    from kairix.providers import get_provider

    return get_provider(name)


def default_search_callable(
    *,
    pipeline_factory: Callable[[], Any] = _build_production_search_pipeline,
    **kwargs: Any,
) -> Any:
    """Production search adapter used by ``PrepDeps`` when no override is passed.

    The ``pipeline_factory`` kwarg is the public DI seam: tests pass a fake
    factory returning a stub pipeline whose ``.search(**kwargs)`` returns the
    desired ``SearchResult`` shape, exercising this adapter end-to-end.
    """
    pipeline = pipeline_factory()
    return pipeline.search(**kwargs)


def default_chat_callable(
    *,
    provider_name_fn: Callable[[], str | None] = _resolve_production_provider_name,
    provider_resolver: Callable[[str], Any] = _resolve_production_provider,
    chat_backend_factory: Callable[[Any], Any] | None = None,
    **kwargs: Any,
) -> str:
    """Production chat adapter used by ``PrepDeps`` when no override is passed.

    Resolves the configured plugin via ``provider_name_fn`` + ``provider_resolver``,
    wraps it in :class:`ProviderChatBackend` (override via ``chat_backend_factory``
    for tests), and forwards ``**kwargs`` to ``backend.chat``. Raises ``ValueError``
    when no provider is configured — surfacing a config error at the boundary
    rather than letting the call vanish into a generic plugin failure.
    """
    from kairix.transport.embed_service import ProviderChatBackend

    name = provider_name_fn()
    if name is None:
        raise ValueError("kairix.config.yaml is missing the required 'provider:' field")
    provider = provider_resolver(name)
    backend = (chat_backend_factory or ProviderChatBackend)(provider)
    return backend.chat(**kwargs)


@dataclass(frozen=True)
class PrepOutput:
    """Outcome of one ``run_prep`` invocation.

    Attributes:
        query: The caller's query, unchanged.
        tier: Either ``"l0"`` (2-3 sentences) or ``"l1"`` (structured overview).
        summary: LLM-generated summary grounded in retrieved documents.
            Empty when no relevant documents were found, or on error.
        tokens: Estimated token count of ``summary``.
        sources: Up to 5 source titles/paths used as context.
        error: Empty on success; structured ``"<Class>: <msg>"`` on
            top-level failure.
    """

    query: str
    tier: str
    summary: str = ""
    tokens: int = 0
    sources: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class PrepDeps:
    """Injectable dependencies for ``run_prep``.

    Non-Optional fields wired to production defaults via ``default_factory``
    — eliminates the ``Optional[Callable]`` mypy regression class flagged
    in #204. Tests construct ``PrepDeps(search_fn=fake, chat_fn=fake)``
    with explicit overrides; ``PrepDeps()`` with no kwargs resolves to
    the production callables defined above.
    """

    search_fn: Callable[..., Any] = field(default_factory=lambda: default_search_callable)
    chat_fn: Callable[..., str] = field(default_factory=lambda: default_chat_callable)


_GROUND_RULES = (
    "If the documents do not contain information about the topic, "
    'reply with exactly: "No relevant content found in the knowledge store." '
    "Do NOT fabricate, infer, or fill in plausible-sounding details. "
    "Do NOT add information that is not in the documents."
)


def _build_messages(query: str, tier: str, context: str) -> list[dict[str, str]]:
    if tier == "l0":
        system = (
            "You are a concise knowledge assistant. Based ONLY on the provided documents, "
            "summarise what is known about the topic in 2-3 sentences. "
            f"{_GROUND_RULES}"
        )
    else:
        system = (
            "You are a knowledge assistant. Based ONLY on the provided documents, "
            "provide a structured overview of the topic. "
            f"{_GROUND_RULES}"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Topic: {query}\n\nDocuments:\n{context}"},
    ]


# Without this floor, a top-5 chunk hit with a 12-character snippet ("see
# ref-001") gets fed to the LLM as "context" — the model treats it as
# authoritative and hallucinates to fill the gap (#254 dogfood). 40 chars
# is empirical: a sentence-worth of grounding; anything shorter is
# title-equivalent. The floor is CHUNK-tier only — fact rows are
# structured triplets ("Caroline role: VP of People" ~ 27 chars) whose
# compactness is the feature, not a bug, and they get a dedicated
# minimum below (#327 Plan-B remediation D1).
_MIN_USEFUL_SNIPPET_CHARS = 40
_MIN_FACT_SNIPPET_CHARS = 1


@dataclass
class _ContextCounters:
    """Per-row counters accumulated by ``_classify_row`` for the trace log."""

    chunks_total: int = 0
    chunks_kept: int = 0
    facts_total: int = 0
    facts_kept: int = 0


def _classify_row(budgeted: Any, counters: _ContextCounters) -> tuple[str, str] | None:
    """Apply the per-tier snippet floor; return ``(title, formatted_snippet)``
    or ``None`` when the row should be dropped from LLM context.

    Bumps the right counter on ``counters`` for both totals and kept rows
    — keeping the trace log accurate even when ``_format_context`` is
    extracted into helpers.
    """
    inner = getattr(budgeted, "result", None)
    if inner is None:
        return None
    title = getattr(inner, "title", "") or getattr(inner, "path", "")
    snippet = (getattr(budgeted, "content", "") or "").strip()
    is_fact = str(getattr(inner, "path", "")).startswith("facts://")
    if is_fact:
        counters.facts_total += 1
        floor = _MIN_FACT_SNIPPET_CHARS
    else:
        counters.chunks_total += 1
        floor = _MIN_USEFUL_SNIPPET_CHARS
    if len(snippet) < floor:
        return None
    if is_fact:
        counters.facts_kept += 1
    else:
        counters.chunks_kept += 1
    return str(title), f"[{title}]\n{snippet[:500]}"


def _format_context(search_result: Any) -> tuple[str, list[str]]:
    """Project a SearchResult's top 5 hits into a context string + source titles.

    Chunk hits need ``_MIN_USEFUL_SNIPPET_CHARS`` of snippet content to
    earn LLM context inclusion — anything shorter is title-equivalent
    and feeds hallucination. Fact rows (synthesised under the
    ``facts://`` path by SearchPipeline's fact federation) carry
    intentionally compact entity-attribute-value triplets and are
    exempt from the chunk floor; they only need to be non-empty.

    Returns ``("", [])`` when no hit has usable snippet content — the
    caller treats this as "no relevant documents" rather than calling
    the LLM.

    Emits a single ``KAIRIX_TRACE``-gated INFO log capturing how many
    chunk vs fact hits were considered vs kept and the resulting LLM
    context size. Plan B-parity post-mortem (D4 remediation): this is
    the diagnostic that would have made D1 (fact snippets filtered by
    the chunk floor) obvious in seconds rather than two days.
    """
    parts: list[str] = []
    sources: list[str] = []
    counters = _ContextCounters()
    for budgeted in getattr(search_result, "results", [])[:5]:
        classified = _classify_row(budgeted, counters)
        if classified is None:
            continue
        title, formatted = classified
        sources.append(title)
        parts.append(formatted)
    context = "\n\n---\n\n".join(parts) if parts else ""
    if trace_enabled():
        logger.info(
            "prep.context: chunks %d/%d kept, facts %d/%d kept, %d ctx chars",
            counters.chunks_kept,
            counters.chunks_total,
            counters.facts_kept,
            counters.facts_total,
            len(context),
        )
    return context, sources


def run_prep(
    query: str,
    *,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    tier: Literal["l0", "l1"] = "l0",
    deps: PrepDeps | None = None,
) -> PrepOutput:
    """Run grounded summarisation over retrieved documents.

    Never raises — failures populate ``PrepOutput.error``.

    Args:
        query: Topic to summarise.
        agent: Agent name for collection scoping.
        scope: Multi-agent scope (default shared+agent).
        tier: ``"l0"`` for 2-3 sentences, ``"l1"`` for structured overview.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or PrepDeps()
    search = d.search_fn
    chat = d.chat_fn

    try:
        budget = _L0_BUDGET if tier == "l0" else _L1_BUDGET
        sr = search(query=query, agent=agent, scope=scope, budget=budget)
        context, sources = _format_context(sr)

        if not context:
            return PrepOutput(
                query=query,
                tier=tier,
                summary="No relevant documents found for this topic.",
            )

        max_tokens = _L0_MAX_TOKENS if tier == "l0" else _L1_MAX_TOKENS
        messages = _build_messages(query, tier, context)

        # Cache-aside: identical ``(query, tier, retrieved-context)``
        # triples short-circuit the LLM call. Cache miss → call the
        # chat fn + store; cache hit → return the cached summary. The
        # cache key folds the context (via sha256) so callers asking
        # the same question over different retrieved-context blocks
        # never collide.
        cache = _get_or_create_prep_summary_cache()
        cache_key = make_prep_cache_key(query, tier, context)
        cached_summary = cache.get(cache_key)
        if cached_summary is not None:
            summary = cached_summary
        else:
            summary = chat(messages=messages, max_tokens=max_tokens)
            cache.put(cache_key, summary)

        return PrepOutput(
            query=query,
            tier=tier,
            summary=summary,
            tokens=estimate_tokens(summary),
            sources=sources,
        )
    except Exception as exc:
        logger.warning("run_prep failed: %s", exc, exc_info=True)
        return PrepOutput(query=query, tier=tier, error=f"{type(exc).__name__}: {exc}")


def prep_output_to_envelope(out: PrepOutput) -> dict[str, Any]:
    """Project a ``PrepOutput`` to the JSON envelope MCP callers receive."""
    return {
        "query": out.query,
        "tier": out.tier,
        "summary": out.summary,
        "tokens": out.tokens,
        "sources": out.sources,
        "error": out.error,
    }
