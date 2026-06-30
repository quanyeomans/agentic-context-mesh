"""Brief use case — session briefing generation shared by CLI and MCP.

Phase 3a of the CLI/MCP feature parity initiative (#168). Pre-Phase-3a
``kairix brief`` was CLI-only — agents had to shell out via subprocess
to read their own briefing. This use case wraps the existing
``generate_briefing`` pipeline and surfaces both the content and the
on-disk path through a uniform dataclass; the new MCP tool
``tool_brief`` calls it directly.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.core.health import (
    HealthDeps,
    KairixHealth,
    brief_next_action,
    health_to_envelope,
    probe_health,
)
from kairix.core.search.brief_output_cache import (
    DEFAULT_MAX_AGE_S as _BRIEF_DEFAULT_MAX_AGE_S,
)
from kairix.core.search.brief_output_cache import (
    DEFAULT_MAX_ENTRIES as _BRIEF_DEFAULT_MAX_ENTRIES,
)
from kairix.core.search.brief_output_cache import (
    BriefOutputCache,
    make_brief_cache_key,
)

logger = logging.getLogger(__name__)

# F21 affordance appended to every InvalidAgent rejection so the operator
# (or the calling agent) gets the exact fix without reading source. There
# is no fixed four-name allow-list any more (PLA-265): any agent the
# operator has onboarded — an explicit ``agents.<name>`` block, an
# ``agent_defaults`` synthesis, or the built-in document-root default —
# resolves to a surface and is briefable. ``InvalidAgent`` now fires only
# when scope resolution genuinely yields no surfaces.
_INVALID_AGENT_ACTION = (
    "fix: run `kairix onboard agent --name <name>` to configure the agent's "
    "memory surface in kairix.config.yaml. next: re-run kairix brief <name>."
)

# Process-shared BriefOutputCache. Lazy-initialised on first run_brief
# call. Mirrors the prep_summary_cache + query_cache accessor patterns
# so the operator surface (``probe caches``) finds every cache via the
# same shape. The 30s TTL is short because briefing content is meant to
# be near-live (pending items / blocked tags age fast).
_BRIEF_OUTPUT_CACHE: BriefOutputCache | None = None
_BRIEF_OUTPUT_CACHE_LOCK = threading.Lock()

# Budget isn't passed to ``generate_briefing`` today; the cache key
# slot exists so future callers asking for tighter / looser briefs
# don't collapse into one slot. 0 is the "default budget" sentinel.
_DEFAULT_BRIEF_BUDGET = 0


def _get_or_create_brief_output_cache() -> BriefOutputCache:
    """Return the process-shared :class:`BriefOutputCache`, building it lazily."""
    global _BRIEF_OUTPUT_CACHE
    with _BRIEF_OUTPUT_CACHE_LOCK:
        if _BRIEF_OUTPUT_CACHE is None:
            _BRIEF_OUTPUT_CACHE = BriefOutputCache(
                max_entries=_BRIEF_DEFAULT_MAX_ENTRIES,
                max_age_s=_BRIEF_DEFAULT_MAX_AGE_S,
            )
        return _BRIEF_OUTPUT_CACHE


def get_brief_output_cache() -> BriefOutputCache:
    """Public accessor for the process-shared brief output cache.

    Used by the ``kairix caches`` CLI to surface hit / miss /
    eviction counts. Going through this helper keeps the module-global
    hidden so callers can't accidentally rebind ``_BRIEF_OUTPUT_CACHE``.
    """
    return _get_or_create_brief_output_cache()


def reset_brief_output_cache() -> None:
    """Drop every cached brief output. Tests + operator reload paths call this."""
    with _BRIEF_OUTPUT_CACHE_LOCK:
        if _BRIEF_OUTPUT_CACHE is not None:
            _BRIEF_OUTPUT_CACHE.clear()


# ---------------------------------------------------------------------------
# Health probe cache (#396 W-B Commit 6)
# ---------------------------------------------------------------------------
# Each ``probe_health`` call runs four dependency probes (secrets,
# embed, BM25, neo4j) on daemon threads with a per-probe timeout slice.
# Production probes are ~50-100ms each; under typical brief / prep call
# volume that means health resolution alone costs 200-400ms on every
# tool invocation. The probes don't change second-by-second, so caching
# the assembled :class:`KairixHealth` for a short TTL turns repeat
# invocations into a memory lookup. 10s default TTL is the deliberate
# consistency tradeoff: long enough to absorb a tight burst of MCP
# calls, short enough that an operator restoring creds sees the
# refreshed snapshot within a few requests.
#
# Module-level explicit state (not ``functools.lru_cache``) mirrors the
# ``_QUERY_CACHE_LOCK`` pattern in ``kairix.core.factory`` so tests can
# inject a clock via the public accessor.

_HEALTH_PROBE_CACHE_LOCK = threading.Lock()
_HEALTH_PROBE_CACHE_TTL_S = 10.0

# Slot layout: ``(inserted_at, KairixHealth)`` or ``None`` when cold.
_HEALTH_PROBE_CACHE_ENTRY: tuple[float, KairixHealth] | None = None
# Clock seam — production uses :func:`time.time`; tests inject a
# controllable callable so TTL-expiry assertions don't need real sleep.
_HEALTH_PROBE_CACHE_CLOCK: Callable[[], float] = time.time


def reset_health_probe_cache() -> None:
    """Drop the cached :class:`KairixHealth`. Tests + operator reload paths call this."""
    global _HEALTH_PROBE_CACHE_ENTRY
    with _HEALTH_PROBE_CACHE_LOCK:
        _HEALTH_PROBE_CACHE_ENTRY = None


def set_health_probe_cache_clock(clock: Callable[[], float]) -> None:
    """Public DI seam for the health-probe TTL clock.

    Tests pass a controllable callable so a synthetic "11s elapsed"
    advance triggers the refresh branch without real sleep.

    Production never calls this — the default :func:`time.time` is
    wired at module load. Mirrors the ``clock=`` constructor kwarg
    pattern used by :class:`QueryResultCache` etc.
    """
    global _HEALTH_PROBE_CACHE_CLOCK
    with _HEALTH_PROBE_CACHE_LOCK:
        _HEALTH_PROBE_CACHE_CLOCK = clock


def get_health_probe_cache_age_s() -> float | None:
    """Return how long the cached entry has been live, or None when cold.

    Public accessor for the probe-caches CLI so operators can see the
    age of the in-process snapshot.
    """
    with _HEALTH_PROBE_CACHE_LOCK:
        if _HEALTH_PROBE_CACHE_ENTRY is None:
            return None
        inserted_at, _ = _HEALTH_PROBE_CACHE_ENTRY
        return max(0.0, _HEALTH_PROBE_CACHE_CLOCK() - inserted_at)


def _cached_probe_health(deps: HealthDeps) -> KairixHealth:
    """Return :class:`KairixHealth` from cache when fresh; else re-probe.

    The lock serialises miss-path probes so two MCP worker threads
    racing into a cold cache don't both pay the 4-probe cost.
    """
    global _HEALTH_PROBE_CACHE_ENTRY
    with _HEALTH_PROBE_CACHE_LOCK:
        if _HEALTH_PROBE_CACHE_ENTRY is not None:
            inserted_at, value = _HEALTH_PROBE_CACHE_ENTRY
            if (_HEALTH_PROBE_CACHE_CLOCK() - inserted_at) <= _HEALTH_PROBE_CACHE_TTL_S:
                return value
        # Miss / expired: re-probe + store. Probe runs under the lock
        # because it's daemon-thread-based and tight (sub-second);
        # serialising the rare miss path is cheaper than coordinating
        # un-locked re-entry.
        snapshot = probe_health(deps)
        _HEALTH_PROBE_CACHE_ENTRY = (_HEALTH_PROBE_CACHE_CLOCK(), snapshot)
        return snapshot


def _default_generate(agent: str, **kwargs: Any) -> str:
    from kairix.agents.briefing.pipeline import generate_briefing

    return generate_briefing(agent, **kwargs)


def _default_briefing_dir() -> Path:
    from kairix.paths import briefing_dir

    return briefing_dir()


def _default_brief_config() -> dict[str, object] | None:
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def _resolve_agent_surfaces(agent: str, config: dict[str, object] | None) -> tuple[Path, ...]:
    """Return the agent's configured surface paths, or ``()`` when none resolve.

    Routes through :func:`kairix.core.agents.scope.get_agent_scope` — the
    same resolver the briefing pipeline's source fetchers use — so any
    agent the operator has onboarded resolves: an explicit ``agents.<name>``
    block, an ``agent_defaults`` synthesis, or the built-in document-root
    default. Returns ``()`` only when the scope genuinely has no surfaces
    (an explicit empty ``surfaces: []`` entry) or the config is malformed
    (``get_agent_scope`` raises ``ValueError``); :func:`run_brief` maps
    ``()`` to an ``InvalidAgent`` envelope. Replaces the hardcoded
    four-name allow-list so config-onboarded agents (``kairix onboard
    agent --name <name>``) can be briefed (PLA-265).
    """
    from kairix.core.agents.scope import get_agent_scope

    try:
        scope = get_agent_scope(agent, config=config)
    except ValueError:
        return ()
    return tuple(scope.memory_paths())


@dataclass(frozen=True)
class BriefOutput:
    """Outcome of one ``run_brief`` invocation.

    Attributes:
        agent: The agent name used to generate the briefing.
        content: Full briefing markdown (header + body). Empty when
            ``error`` is set.
        path: On-disk path of the briefing file (may be empty if the
            writer step was skipped or failed). The CLI prints this
            for operators; agents prefer ``content``.
        preview: First 30 lines of ``content``, useful for stdout
            previews without re-splitting.
        error: Empty string on success; structured ``"<Class>: <msg>"``
            on top-level failure, or ``"InvalidAgent: <name> resolves to
            no configured surface. ..."`` when scope resolution yields no
            surfaces for the requested agent.
    """

    agent: str
    content: str = ""
    path: str = ""
    preview: str = ""
    health: KairixHealth = field(default_factory=KairixHealth)
    error: str = ""

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> BriefOutput:
        """Rebuild a ``BriefOutput`` from the dict ``brief_output_to_envelope`` emits.

        The seam for warm-MCP text-mode routing (#421 PR 2.1). The CLI
        dispatcher receives a JSON envelope from the MCP worker; this
        adapter projects it back to the dataclass shape ``format_output``
        already consumes, so the in-process and warm paths render
        byte-identical text.

        ``health`` is NOT round-tripped — ``format_output`` reads only
        ``content`` / ``path``; reconstructing a ``KairixHealth`` from
        its envelope dict would require an inverse of ``health_to_envelope``
        that doesn't exist today. The dataclass default ``KairixHealth()``
        is acceptable because the CLI's rendering path never reads it.
        Any future caller that needs the round-tripped health snapshot
        should add an explicit ``envelope_to_health`` and wire it here.
        """
        return cls(
            agent=str(envelope.get("agent", "")),
            content=str(envelope.get("content", "")),
            path=str(envelope.get("path", "")),
            preview=str(envelope.get("preview", "")),
            error=str(envelope.get("error", "")),
        )


@dataclass(frozen=True)
class BriefDeps:
    """Injectable dependencies for ``run_brief``.

    Mirrors ``WorkerDeps`` (kairix/worker.py): each callable is
    non-Optional with a ``default_factory`` returning the production
    helper. Tests construct ``BriefDeps(generate_fn=fake, ...)``;
    production callers leave ``deps=None`` and the run_brief default
    factory wires the real helpers.

    ``config_fn`` is the agent-validation seam: it returns the parsed
    ``kairix.config.yaml`` top-level dict (or None) that drives
    AgentScope resolution. Tests inject a config that declares the
    agent under test (``config_fn=lambda: {"agents": {...}}``) to prove
    a config-onboarded agent is briefable without writing a real file.
    """

    generate_fn: Callable[..., str] = field(default_factory=lambda: _default_generate)
    briefing_dir_fn: Callable[[], Path] = field(default_factory=lambda: _default_briefing_dir)
    config_fn: Callable[[], dict[str, object] | None] = field(default_factory=lambda: _default_brief_config)
    health_deps: HealthDeps = field(default_factory=HealthDeps)


def run_brief(
    agent: str,
    *,
    deps: BriefDeps | None = None,
) -> BriefOutput:
    """Generate a session briefing and return a structured result.

    Never raises — failures populate ``BriefOutput.error``.

    Args:
        agent: Agent name. Any agent the operator has onboarded resolves
            (explicit ``agents.<name>`` block, an ``agent_defaults``
            synthesis, or the built-in document-root default); the brief
            only rejects names that resolve to no surface at all.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or BriefDeps()
    health = _brief_health(_cached_probe_health(d.health_deps))

    normalised = (agent or "").lower().strip()
    surfaces = _resolve_agent_surfaces(normalised, d.config_fn()) if normalised else ()
    if not surfaces:
        return BriefOutput(
            agent=agent,
            health=health,
            error=f"InvalidAgent: {agent!r} resolves to no configured surface. {_INVALID_AGENT_ACTION}",
        )

    # When chat synthesis is offline the envelope returns an empty
    # content body — generate_fn would crash on a real call without an
    # LLM credential. The envelope still tells the agent what to do next
    # via ``health.next_action`` (fall back to tool_search). #246 W3.
    if health.chat != "ok":
        return BriefOutput(
            agent=normalised,
            health=health,
        )

    try:
        # Cache-aside on the full :class:`BriefOutput`. The brief
        # fan-out costs hundreds of ms even with the per-source caches
        # below; a 30s TTL on the assembled output absorbs repeat calls
        # within a short session window. Cache hits return the prior
        # BriefOutput; cache misses run generate_fn + store the result.
        cache = _get_or_create_brief_output_cache()
        cache_key = make_brief_cache_key(normalised, _DEFAULT_BRIEF_BUDGET)
        cached_output = cache.get(cache_key)
        if cached_output is not None:
            # The cache's value type is ``Any`` so it can store any kind
            # of output (the brief cache is the first user; future
            # callers may layer different shapes); narrow back to
            # ``BriefOutput`` here at the read boundary because every
            # ``put`` site below stores exactly that shape.
            assert isinstance(cached_output, BriefOutput)
            return cached_output

        content = d.generate_fn(normalised)
        out_dir = d.briefing_dir_fn()
        path = str(out_dir / f"{normalised}-latest.md") if out_dir else ""
        preview = "\n".join(content.splitlines()[:30])
        output = BriefOutput(
            agent=normalised,
            content=content,
            path=path,
            preview=preview,
            health=health,
        )
        cache.put(cache_key, output)
        return output
    except Exception as exc:
        logger.warning("run_brief failed: %s", exc, exc_info=True)
        return BriefOutput(
            agent=normalised,
            health=health,
            error=f"{type(exc).__name__}: {exc}",
        )


def _brief_health(base: KairixHealth) -> KairixHealth:
    """Overlay the brief-specific ``next_action`` onto the shared snapshot."""
    directive = brief_next_action(base)
    if not directive:
        return base
    return KairixHealth(
        vector_search=base.vector_search,
        bm25=base.bm25,
        chat=base.chat,
        secrets_loaded=base.secrets_loaded,
        degraded_reason=base.degraded_reason,
        next_action=directive,
    )


def brief_output_to_envelope(out: BriefOutput) -> dict[str, Any]:
    """Project a ``BriefOutput`` to the JSON envelope MCP callers receive."""
    return {
        "agent": out.agent,
        "content": out.content,
        "path": out.path,
        "preview": out.preview,
        "health": dict(health_to_envelope(out.health)),
        "error": out.error,
    }
