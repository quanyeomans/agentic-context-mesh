"""
8-step briefing pipeline for kairix session briefings.

Steps:
  1. Recent memory log files (last 7 days, tagged items)
  2. Today's + yesterday's memory file (full content)
  3. Entity stub for agent
  4. Agent knowledge rules
  5. Recent decisions (last 30 days)
  6. Hybrid search on agent name
  7. GPT-4o-mini synthesis
  8. Write to /data/kairix/briefing/<agent>-latest.md

Steps 1-6 run concurrently. Total context is capped at 3000 tokens with
priority-based truncation (step 6 first, then 5, 4, etc.).

Never raises — returns partial briefing on any failure. Sources that
exceed their per-source wall-clock budget contribute an empty section
("section unavailable") rather than aborting the whole brief. The brief
never raises TimeoutError to the caller — partial-result is always
acceptable. See issue #397 Workstream C.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kairix.text import estimate_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)

# F17 — source-name keys appear in caps dict, truncation order, fetch-task tuples,
# and resolver calls; extract so renames are a single edit.
_KEY_MEMORY_LOGS = "memory_logs"
_KEY_RECENT_MEMORY = "recent_memory"
_KEY_ENTITY_STUB = "entity_stub"
_KEY_KNOWLEDGE_RULES = "knowledge_rules"
_KEY_RECENT_DECISIONS = "recent_decisions"
_KEY_HYBRID_SEARCH = "hybrid_search"


def _default_synthesise() -> Callable[..., str]:
    """Return the production synthesiser. Lazy import breaks the module cycle."""
    from kairix.agents.briefing.synthesiser import synthesise

    return synthesise


def _default_write_briefing() -> Callable[..., Path]:
    """Return the production writer. Lazy import breaks the module cycle."""
    from kairix.agents.briefing.writer import write_briefing

    return write_briefing


@dataclass
class BriefingDeps:
    """Injectable dependencies for ``generate_briefing``.

    Each field defaults to the production implementation via
    ``default_factory`` — fields are typed as concrete callables so mypy
    sees a real type at every call site (no ``assert deps.x is not None``
    ladder). Tests construct ``BriefingDeps(synthesise_fn=fake, ...)`` to
    swap individual collaborators.
    """

    synthesise_fn: Callable[..., str] = field(default_factory=_default_synthesise)
    write_fn: Callable[..., Path] = field(default_factory=_default_write_briefing)


# Token caps per source (approximate)
_SOURCE_TOKEN_CAPS: dict[str, int] = {
    _KEY_MEMORY_LOGS: 500,
    _KEY_RECENT_MEMORY: 300,
    _KEY_ENTITY_STUB: 400,
    _KEY_KNOWLEDGE_RULES: 300,
    _KEY_RECENT_DECISIONS: 400,
    _KEY_HYBRID_SEARCH: 600,
}

# Total context budget before truncation (3000 tokens ~ 2300 words)
TOTAL_CONTEXT_CAP = 3000

# Per-source wall-clock budgets (seconds). The five cheap sources finish
# well under their slice in steady state; the budget is the graceful-
# degradation cliff for the long tail (slow disk, slow neo4j round-trip).
# Hybrid search needs the longer budget because it embeds the query
# (Azure HTTP call, 250-1000ms tail) before hitting BM25 + vector.
#
# A source that exceeds its budget contributes None — the brief still
# assembles + synthesises with the remaining sources. No TimeoutError
# ever surfaces to the caller. #397 Workstream C.
_SOURCE_BUDGETS_S: dict[str, float] = {
    _KEY_MEMORY_LOGS: 3.0,
    _KEY_RECENT_MEMORY: 3.0,
    _KEY_ENTITY_STUB: 3.0,
    _KEY_KNOWLEDGE_RULES: 3.0,
    _KEY_RECENT_DECISIONS: 3.0,
    _KEY_HYBRID_SEARCH: 15.0,
}

# Priority order for truncation when over budget (lowest priority first)
_TRUNCATION_ORDER = [
    _KEY_HYBRID_SEARCH,
    _KEY_RECENT_DECISIONS,
    _KEY_KNOWLEDGE_RULES,
    _KEY_ENTITY_STUB,
    _KEY_RECENT_MEMORY,
    _KEY_MEMORY_LOGS,
]


async def _bounded_source(
    name: str,
    fn: Callable,
    args: tuple,
    budget_s: float,
) -> tuple[str, str | None]:
    """Run one source fetcher under a per-source wall-clock budget.

    Returns ``(name, text)`` on success, ``(name, None)`` when the
    budget elapses or the fetcher raises. None is the "section
    unavailable" sentinel the assembler renders as an empty placeholder.

    The fetcher itself runs in a worker thread via ``asyncio.to_thread``
    because the source fetchers are sync (sqlite + file I/O); wrapping
    in ``wait_for`` enforces the budget at the asyncio scheduler level
    without needing the fetcher to know anything about cancellation.
    """
    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(fn, *args),
            timeout=budget_s,
        )
        return name, text or ""
    except TimeoutError:
        # The slow source is the diagnostic — log enough to find it in
        # logs without poisoning the brief result.
        logger.warning(
            "pipeline: source %r exceeded %.1fs budget — section unavailable",
            name,
            budget_s,
        )
        return name, None
    except Exception as exc:
        logger.warning(
            "pipeline: source %r raised — section unavailable: %s",
            name,
            exc,
            exc_info=True,
        )
        return name, None


def trim_context(context: dict[str, str]) -> dict[str, str]:
    """
    Trim context sources if total token estimate exceeds TOTAL_CONTEXT_CAP.
    Truncates lowest-priority sources first.
    """
    total = sum(estimate_tokens(v) for v in context.values())
    if total <= TOTAL_CONTEXT_CAP:
        return context

    trimmed = dict(context)
    for source_name in _TRUNCATION_ORDER:
        if total <= TOTAL_CONTEXT_CAP:
            break
        if trimmed.get(source_name):
            current = trimmed[source_name]
            current_tokens = estimate_tokens(current)
            cap = _SOURCE_TOKEN_CAPS.get(source_name, 200)
            if current_tokens > cap // 2:
                # Halve the allocation
                new_cap = max(cap // 2, 50)
                trimmed[source_name] = truncate_to_tokens(current, new_cap)
                total -= current_tokens - estimate_tokens(trimmed[source_name])

    return trimmed


async def _fetch_sources_async(
    source_tasks: list[tuple[str, Callable, str, int]],
    budgets_s: dict[str, float] | None = None,
) -> dict[str, str]:
    """Run all source fetchers concurrently under per-source budgets.

    Returns a ``name -> content`` map containing only sources that
    returned non-empty text within their budget. Sources that timed out
    or raised contribute ``None`` and are filtered out of the map (so
    downstream truncation + synthesis sees the same "empty section"
    shape it always did for failed fetches).

    Never raises — every per-source exception (including
    ``asyncio.TimeoutError``) is caught inside ``_bounded_source`` and
    converted to a None section.

    ``budgets_s`` is a public override seam (defaults to
    :data:`_SOURCE_BUDGETS_S`); operators tuning a deployment with
    different latency characteristics, or tests injecting short
    budgets to exercise the timeout path, pass a custom dict here.
    """
    budgets = budgets_s if budgets_s is not None else _SOURCE_BUDGETS_S
    coros = [_bounded_source(name, fn, tuple(args), budgets[name]) for (name, fn, *args) in source_tasks]
    # return_exceptions=False is intentional — _bounded_source already
    # converts every per-source error into a (name, None) tuple, so
    # gather can never see an unhandled exception. If it ever did,
    # raising up would be a bug we want to surface, not silently bury.
    results = await asyncio.gather(*coros, return_exceptions=False)

    context: dict[str, str] = {}
    for source_name, content in results:
        if content:
            context[source_name] = content
            logger.debug(
                "pipeline: source %r returned %d tokens",
                source_name,
                estimate_tokens(content),
            )
    return context


def _fetch_sources_concurrently(
    source_tasks: list[tuple[str, Callable, str, int]],
    budgets_s: dict[str, float] | None = None,
) -> dict[str, str]:
    """Sync wrapper around :func:`_fetch_sources_async`.

    Existing sync callers (``generate_briefing``, CLI tests, MCP tool
    handler thread) drive the async fan-out via ``asyncio.run`` — this
    keeps the call-site signature unchanged. When invoked from inside
    a running loop, callers should ``await _fetch_sources_async(...)``
    directly instead.
    """
    return asyncio.run(_fetch_sources_async(source_tasks, budgets_s=budgets_s))


def generate_briefing(
    agent: str,
    *,
    deps: BriefingDeps | None = None,
    sources: dict[str, Callable] | None = None,
    budgets_s: dict[str, float] | None = None,
) -> str:
    """
    Generate a session briefing for the given agent.

    Runs the full 8-step pipeline:
    1-6: Concurrent source fetching
    7:   GPT-4o-mini synthesis
    8:   Write to file

    Args:
        agent:      Agent name (e.g. "builder", "shape").
        deps:       Injectable dependencies (synthesise_fn, write_fn).
                    Production callers leave None — the dataclass wires
                    real implementations via ``default_factory``. Tests
                    construct ``BriefingDeps(synthesise_fn=fake, ...)``.
        sources:    Per-source callable overrides (key = source name).
        budgets_s:  Per-source wall-clock budgets (key = source name,
                    value = seconds). Defaults to the production
                    :data:`_SOURCE_BUDGETS_S` (five cheap sources at 3s,
                    hybrid_search at 15s). Operators wiring a deployment
                    with different latency characteristics — and tests
                    forcing the timeout path — pass an override here.

    Returns:
        Full briefing content (with header). Never raises.
    """
    d = deps or BriefingDeps()
    synthesise = d.synthesise_fn
    write_briefing = d.write_fn

    t_start = time.monotonic()
    logger.info("pipeline: generating briefing for agent %r", agent)

    # Resolve source fetchers — allow per-source overrides via the `sources` dict
    _src = sources or {}

    def _resolve_source(name: str, default_import_path: str) -> Callable:
        if name in _src:
            return _src[name]
        # Lazy import from default module
        from kairix.agents.briefing import sources as _sources_mod

        return getattr(_sources_mod, default_import_path)

    _fetch_memory_logs = _resolve_source(_KEY_MEMORY_LOGS, "fetch_memory_logs")
    _fetch_recent_memory = _resolve_source(_KEY_RECENT_MEMORY, "fetch_recent_memory")
    _fetch_entity_stub = _resolve_source(_KEY_ENTITY_STUB, "fetch_entity_stub")
    _fetch_knowledge_rules = _resolve_source(_KEY_KNOWLEDGE_RULES, "fetch_knowledge_rules")
    _fetch_recent_decisions = _resolve_source(_KEY_RECENT_DECISIONS, "fetch_recent_decisions")
    _fetch_hybrid_search = _resolve_source(_KEY_HYBRID_SEARCH, "fetch_hybrid_search")

    # Steps 1-6: concurrent source fetching
    source_tasks = [
        (_KEY_MEMORY_LOGS, _fetch_memory_logs, agent, _SOURCE_TOKEN_CAPS[_KEY_MEMORY_LOGS]),
        (
            _KEY_RECENT_MEMORY,
            _fetch_recent_memory,
            agent,
            _SOURCE_TOKEN_CAPS[_KEY_RECENT_MEMORY],
        ),
        (_KEY_ENTITY_STUB, _fetch_entity_stub, agent, _SOURCE_TOKEN_CAPS[_KEY_ENTITY_STUB]),
        (
            _KEY_KNOWLEDGE_RULES,
            _fetch_knowledge_rules,
            agent,
            _SOURCE_TOKEN_CAPS[_KEY_KNOWLEDGE_RULES],
        ),
        (
            _KEY_RECENT_DECISIONS,
            _fetch_recent_decisions,
            agent,
            _SOURCE_TOKEN_CAPS[_KEY_RECENT_DECISIONS],
        ),
        (
            _KEY_HYBRID_SEARCH,
            _fetch_hybrid_search,
            agent,
            _SOURCE_TOKEN_CAPS[_KEY_HYBRID_SEARCH],
        ),
    ]

    context = _fetch_sources_concurrently(source_tasks, budgets_s=budgets_s)
    sources_count = len(context)
    logger.info("pipeline: collected %d sources for %r", sources_count, agent)

    # Surface missing memory — helps users diagnose stale briefings.
    # PR 1.2 / #420 — resolve the memory surface via AgentScope (which
    # honours the operator's ``agents:`` / ``agent_defaults:`` blocks)
    # rather than the deleted hardcoded ``<root>/<agent>/memory`` formula.
    memory_keys = {_KEY_MEMORY_LOGS, _KEY_RECENT_MEMORY}
    if not (memory_keys & context.keys()):
        from kairix.agents.briefing.sources import resolve_memory_dirs

        surfaces = resolve_memory_dirs(agent)
        surfaces_text = ", ".join(str(p) for p in surfaces) if surfaces else "<no surfaces configured>"
        first_surface = str(surfaces[0]) if surfaces else "<surface>"
        context["_missing_memory_note"] = (
            f"No agent memory logs found at: {surfaces_text}. "
            f"Briefing is based on knowledge store and entity data only. "
            f"To enable memory-based briefing, create daily log files at "
            f"{first_surface}/YYYY-MM-DD.md"
        )
        logger.warning(
            "pipeline: no memory sources for agent %r — briefing may be stale",
            agent,
        )

    # Trim context if over budget
    context = trim_context(context)

    # Step 7: Synthesise
    briefing_body = synthesise(agent, context, max_tokens=800)

    # Token estimate for output
    token_estimate = estimate_tokens(briefing_body)

    # Step 8: Write to file
    try:
        out_path = write_briefing(
            agent=agent,
            content=briefing_body,
            sources_count=sources_count,
            token_estimate=token_estimate,
        )
        logger.info(
            "pipeline: briefing written to %s in %.1fs",
            out_path,
            time.monotonic() - t_start,
        )
    except OSError:
        logger.exception("pipeline: could not write briefing file")
        # Return the content anyway

    # Read back what was written (includes header added by writer)
    try:
        from kairix.agents.briefing.writer import BRIEFING_DIR

        out_path = BRIEFING_DIR / f"{agent}-latest.md"
        if out_path.exists():
            return out_path.read_text(encoding="utf-8")
    except Exception as _exc:
        logger.debug("pipeline: could not read back briefing file — %s", _exc)

    # Fallback: build content inline
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")
    header = (
        f"# Agent Briefing — {agent} — {date_str}\n"
        f"_Generated: {ts} | Sources: {sources_count} | Tokens: ~{token_estimate}_\n\n"
    )
    return header + briefing_body


# ---------------------------------------------------------------------------
# BriefingPipeline class — composable orchestrator
# ---------------------------------------------------------------------------


@dataclass
class BriefingPipeline:
    """Composable briefing orchestrator.

    Wraps the procedural generate_briefing() in a dataclass so callers
    can construct it once with injected dependencies and call generate()
    for each agent.

    Attributes:
        sources:    Per-source callable overrides (key = source name).
        deps:       Injectable dependencies (synthesise_fn, write_fn).
                    Defaults to production implementations.
        budgets_s:  Per-source wall-clock budgets (seconds). None means
                    use the production defaults (5 cheap sources at 3s,
                    hybrid_search at 15s). Operators tuning a deployment
                    or tests forcing the timeout path pass a custom dict.
    """

    sources: dict[str, Callable] = field(default_factory=dict)
    deps: BriefingDeps = field(default_factory=BriefingDeps)
    budgets_s: dict[str, float] | None = None

    def generate(self, agent: str) -> str:
        """Generate a session briefing for the given agent.

        Delegates to the procedural generate_briefing() with the
        configured dependencies.

        Args:
            agent: Agent name (e.g. "builder", "shape").

        Returns:
            Full briefing content string. Never raises.
        """
        return generate_briefing(
            agent=agent,
            deps=self.deps,
            sources=self.sources if self.sources else None,
            budgets_s=self.budgets_s,
        )
