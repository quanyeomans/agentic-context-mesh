"""
kairix.agents.mcp.server — MCP server exposing kairix tools to MCP-compatible agents.

Provides the following tools:
  bootstrap    Agent orientation envelope: role, board, recent memory, goals, health
  search       Search your knowledge store — finds the best answers to any question
  entity       Entity lookup from Neo4j
  prep         Context preparation: tiered L0/L1 summary generation
  timeline     Temporal query rewriting + date-aware retrieval
  contradict   Check new content against existing knowledge for contradictions
  usage_guide  Return the kairix agent usage guide (self-documentation)
  warm         Pay retrieval initialisation costs and mark the readiness gate ready

The server uses FastMCP (from the ``mcp`` package). Install via:
    pip install kairix[agents]

Tool functions are pure Python functions importable without FastMCP installed —
import them directly for unit testing or programmatic use.

Design principles:
  - Never raises; returns error dicts on failure so agents can handle gracefully
  - All inputs/outputs are JSON-serialisable primitives
  - Dependencies initialised lazily on first call
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from kairix.agents.mcp.cold_start import require_ready, warm_retrieval_stack
from kairix.agents.mcp.errors import async_tool_handler
from kairix.core.search.scope import Scope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SCOPE: Scope = Scope.SHARED_AGENT


# ---------------------------------------------------------------------------
# Cold-start gate decorator
# ---------------------------------------------------------------------------


def _is_warm_or_cold_envelope(tool_name: str) -> dict[str, Any] | None:
    """Single source of truth for the cold-start check.

    Returns ``None`` when kairix is warm — caller proceeds to the real
    tool body. Returns the ColdStart affordance envelope when kairix is
    cold, AND kicks off a background warm-up so subsequent calls land on
    a warm pipeline.

    The lazy imports keep ``kairix.platform.warm`` out of the MCP server
    module's import graph at parse time (matters for ``kairix --help``
    cold-path imports).
    """
    from kairix.platform.warm.state import (
        cold_start_envelope,
        is_warm,
        trigger_background_warm,
    )

    if is_warm():
        return None
    trigger_background_warm()
    return cold_start_envelope(tool_name)


def warm_gate(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Decorator: short-circuit MCP tool calls with the ColdStart envelope
    while kairix is warming.

    Apply ABOVE the function (closest to the body), BELOW
    ``@async_tool_handler``. Decorator order in the registration stack:

        @server.tool(description="...")    # outermost — registers the wrapped fn
        @async_tool_handler                # sync → async wrapping
        @warm_gate                          # innermost — gate before the body
        def my_tool(...) -> dict[str, Any]:
            return tool_my_tool(...)

    The tool name passed to the cold-start envelope is taken from
    ``fn.__name__`` — by convention each ``@server.tool``-registered
    function in ``build_server`` is named identically to its MCP tool
    name, so no explicit parameter is needed.

    Sabotage-proof: remove ``@warm_gate`` from any gated tool and the
    parametrized cold-start test for that tool fails because the tool
    body runs against the not-yet-warm pipeline instead of returning the
    envelope.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        cold = _is_warm_or_cold_envelope(fn.__name__)
        if cold is not None:
            return cold
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Shared service helpers — MCP tools call these, not each other
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
# Tool implementations — pure Python, no mcp dependency
# ---------------------------------------------------------------------------


def tool_search(
    query: str,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = 3000,
    limit: int = 10,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Search the knowledge store.

    Thin adapter around ``kairix.use_cases.search.run_search``. CLI and
    MCP both delegate to the same use case so the surfaces stay aligned
    (closes Phase-2 drift in #168).

    The optional ``deps`` parameter forwards a ``SearchDeps`` directly
    to the use case — production callers leave it None; tests pass a
    ``SearchDeps`` to drive without touching live services.
    """
    from kairix.use_cases.search import run_search, search_output_to_envelope

    logger.info("mcp.search: agent=%r scope=%r", agent, scope)
    out = run_search(
        query,
        agent=agent,
        scope=scope,
        budget=budget,
        limit=limit,
        deps=deps,
    )
    return search_output_to_envelope(out)


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


def tool_prep(
    query: str,
    agent: str | None = None,
    tier: Literal["l0", "l1"] = "l0",
    scope: Scope = DEFAULT_SCOPE,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Get a short summary of a topic before committing to a full search.

    Thin adapter around ``kairix.use_cases.prep.run_prep``. Choose 'l0'
    for 2-3 sentences or 'l1' for a structured overview. Uses less
    resources than a full search — good for quick context checks.
    Retrieves relevant documents first, then summarises from them.

    The optional ``deps`` parameter forwards a ``PrepDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.prep import prep_output_to_envelope, run_prep

    out = run_prep(query, agent=agent, scope=scope, tier=tier, deps=deps)
    return prep_output_to_envelope(out)


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

    from kairix.use_cases.timeline import run_timeline

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

    return {
        "original_query": result.original_query,
        "rewritten_query": result.rewritten_query,
        "is_temporal": result.is_temporal,
        "fell_back": result.fell_back,
        "time_window": result.time_window,
        "results": [
            {
                "path": h.path,
                "title": h.title,
                "snippet": h.snippet,
                "score": h.score,
            }
            for h in result.results
        ],
        "error": result.error,
    }


def tool_research(
    query: str,
    agent: str | None = None,
    max_turns: int = 4,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Ask a research question. The system searches multiple times, refining
    its approach until it finds a good answer or reports what's missing.

    Thin adapter around ``kairix.use_cases.research.run_research_use_case``.
    Use this for complex questions that need more than a quick search.
    For simple lookups, use search instead — it's faster.

    The optional ``deps`` parameter forwards a ``ResearchDeps`` directly
    to the use case — production callers leave it None.

    ``agent`` is accepted for signature parity with the other tools and
    logged for traceability; the research use case is agent-agnostic
    today (no per-agent scope/tier filtering), so it isn't threaded
    further.
    """
    from kairix.use_cases.research import research_output_to_envelope, run_research_use_case

    logger.info("mcp.research: agent=%r turns<=%d", agent, max_turns)
    out = run_research_use_case(query, max_turns=max_turns, deps=deps)
    return research_output_to_envelope(out)


def tool_usage_guide(
    topic: str = "",
    *,
    guide_path: Path | None = None,
    deps: Any = None,
) -> dict[str, Any]:
    """
    Return the kairix agent usage guide, or a section of it filtered by topic.

    Thin adapter around ``kairix.use_cases.usage_guide.run_usage_guide``.
    Use this tool when you are unsure how to use kairix, when a search
    returns unexpected results, or when you want to understand a feature.

    The optional ``deps`` parameter forwards a ``UsageGuideDeps`` directly
    to the use case — production callers leave it None. The legacy
    ``guide_path`` parameter is preserved as the operator-facing override.
    """
    from kairix.use_cases.usage_guide import run_usage_guide, usage_guide_output_to_envelope

    out = run_usage_guide(topic, guide_path=guide_path, deps=deps)
    return usage_guide_output_to_envelope(out)


def tool_contradict(
    content: str,
    agent: str | None = None,
    top_k: int = 5,
    threshold: float = 0.45,
    top_claims: int = 3,
    scope: Scope = DEFAULT_SCOPE,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Check new content against existing knowledge for contradictions.

    Thin adapter around ``kairix.use_cases.contradict.run_contradict``.
    Use before writing new facts — catches conflicts with what's already
    in the knowledge base. Returns a list of contradicting documents with
    scores and explanations.

    The optional ``deps`` parameter forwards a ``ContradictDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.contradict import contradict_output_to_envelope, run_contradict

    out = run_contradict(
        content,
        agent=agent,
        scope=scope,
        top_k=top_k,
        threshold=threshold,
        top_claims=top_claims,
        deps=deps,
    )
    return contradict_output_to_envelope(out)


def tool_entity_suggest(
    text: str,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Suggest entities found in arbitrary text by running NER + Neo4j cross-ref.

    Thin adapter around ``kairix.use_cases.entity.run_entity_suggest``.
    Use to spot people, organisations, places mentioned in prose so an
    operator (or another agent) can decide whether to add them to the
    knowledge graph.
    """
    from kairix.use_cases.entity import entity_suggest_output_to_envelope, run_entity_suggest

    out = run_entity_suggest(text, deps=deps)
    return entity_suggest_output_to_envelope(out)


def tool_entity_validate(
    name: str,
    update: bool = False,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Validate an entity against Wikidata and optionally update Neo4j.

    Thin adapter around ``kairix.use_cases.entity.run_entity_validate``.
    Use to confirm a graph entity has a real-world match (qid) and
    optionally write that qid back to the Neo4j node.
    """
    from kairix.use_cases.entity import entity_validate_output_to_envelope, run_entity_validate

    out = run_entity_validate(name, update=update, deps=deps)
    return entity_validate_output_to_envelope(out)


def tool_brief(
    agent: str,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Generate a session briefing and return its content + path.

    Thin adapter around ``kairix.use_cases.brief.run_brief``. Use before
    starting work — gives an agent the operator's recent decisions,
    notes, and entity stub in one structured payload.

    The optional ``deps`` parameter forwards a ``BriefDeps`` directly to
    the use case — production callers leave it None.
    """
    from kairix.use_cases.brief import brief_output_to_envelope, run_brief

    out = run_brief(agent, deps=deps)
    return brief_output_to_envelope(out)


def tool_bootstrap(
    agent: str,
    max_memory_days: int = 3,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Return the agent orientation envelope (#246 W1).

    Thin adapter around ``kairix.use_cases.bootstrap.run_bootstrap``.
    Returns the agent's role, current ``Board.md``, recent memory
    entries, active goals, and a structured health snapshot — the
    single call an agent makes at session start (or topic switch) to
    absorb its current state. Never raises; degradation is surfaced via
    the ``health`` field with a prescriptive ``next_action``.

    The optional ``deps`` parameter forwards a ``BootstrapDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.bootstrap import bootstrap_output_to_envelope, run_bootstrap

    out = run_bootstrap(agent, deps=deps, max_memory_days=max_memory_days)
    return bootstrap_output_to_envelope(out)


# ---------------------------------------------------------------------------
# Diagnostic capabilities — read-only kairix state for agents to introspect
# ---------------------------------------------------------------------------


def tool_onboard_check() -> dict[str, Any]:
    """Run the kairix deployment health probes and return the structured envelope.

    Mirrors ``kairix onboard check --json`` — the same Python API
    (``run_onboard_check``) backs both surfaces, so CLI and MCP return
    byte-identical envelopes for the same kairix state.

    Read-only, bounded runtime (a few seconds at the worst case).
    """
    from dataclasses import asdict

    from kairix.platform.onboard.check import run_onboard_check

    try:
        outcome = run_onboard_check()
        return {
            "passed": outcome.passed,
            "total": outcome.total,
            "fully_passed": outcome.fully_passed,
            "failures": [asdict(f) for f in outcome.failures],
            "error": "",
        }
    except Exception as exc:
        logger.warning("tool_onboard_check failed: %s", exc, exc_info=True)
        return {
            "passed": 0,
            "total": 0,
            "fully_passed": False,
            "failures": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def tool_warm() -> dict[str, Any]:
    """Pre-load kairix caches + pay factory-init costs.

    Mirrors ``kairix warm`` — calls the same Python API. Idempotent and
    fast once warm, so agents can call this as a health probe ('is
    kairix warm?'); the first invocation costs ~200 MB and a few hundred
    ms, every subsequent call is sub-millisecond.
    """
    try:
        from kairix.platform.warm import run_warm

        return run_warm().to_envelope()
    except Exception as exc:
        logger.warning("tool_warm failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "total_duration_s": 0.0,
            "steps": [],
            "failures": [{"step": "tool_warm", "detail": f"{type(exc).__name__}: {exc}"}],
        }


def _default_topology_v2_db_path() -> Path:
    """Production callable that returns the configured kairix SQLite path."""
    from kairix.paths import db_path

    return db_path()


def tool_features_status(
    topology_v2: bool = False,
    *,
    read_db_path: Callable[[], Path] = _default_topology_v2_db_path,
) -> dict[str, Any]:
    """Return the same JSON envelope as ``kairix features status --json``.

    Per F53 + the feature-flag-architecture spec §3.5, agents introspect
    the live flag state through this tool. Thin adapter — delegates to
    :func:`kairix.core.features.status` so CLI and MCP stay aligned.

    ``topology_v2=True`` extends the envelope with a ``topology_v2``
    key carrying the Wave D diagnostics (declared cc_pairs +
    per-actor scope-profile resolution). Default-off so existing agents
    see byte-identical pre-Wave-D output.

    ``read_db_path`` is the unit-test DI seam: leaving it ``None``
    routes through the production :func:`kairix.paths.db_path` resolver;
    tests pass a callable returning a tmp_path so the topology v2 read
    hits the test-built schema without env-var monkeypatching (F2-clean).

    On exception, surfaces a typed error string and an empty flags list
    so the agent can decide whether to fall back or escalate.
    """
    from dataclasses import asdict

    try:
        from kairix.core.features import status as features_status

        entries = features_status()
        envelope: dict[str, Any] = {
            "flags": [asdict(entry) for entry in entries],
            "error": "",
        }
        if topology_v2:
            envelope["topology_v2"] = _read_topology_v2_diagnostics_for_mcp(read_db_path)
        return envelope
    except Exception as exc:
        logger.warning("tool_features_status failed: %s", exc, exc_info=True)
        return {
            "flags": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _read_topology_v2_diagnostics_for_mcp(
    read_db_path: Callable[[], Path] = _default_topology_v2_db_path,
) -> dict[str, Any]:
    """Read the Wave D topology v2 diagnostics for the MCP envelope.

    Isolated helper so :func:`tool_features_status` stays under the
    F16 cognitive-complexity ceiling AND the topology v2 read can
    degrade independently (a missing topology v2 schema returns the
    zero-snapshot rather than crashing the whole MCP envelope).

    ``read_db_path`` is the DI seam — production callers leave it
    ``None`` to delegate to :func:`kairix.paths.db_path`; tests pass a
    tmp-path-returning callable.
    """
    import sqlite3
    from contextlib import closing

    from kairix.core.features.topology_v2_status import (
        build_topology_v2_diagnostics,
        render_topology_v2_json,
    )

    resolved = read_db_path()

    try:
        conn = sqlite3.connect(str(resolved))
        with closing(conn):
            diag = build_topology_v2_diagnostics(conn)
        return render_topology_v2_json(diag)
    except sqlite3.Error as exc:
        logger.warning("topology v2 diagnostics read failed: %s", exc, exc_info=True)
        return {"cc_pairs": [], "actor_scopes": []}


def tool_worker_status() -> dict[str, Any]:
    """Read the kairix-worker state file and return its current envelope.

    Mirrors ``kairix worker status`` — read-only, sub-second. Returns
    phase, counters, last-run timestamp, last-error string when present.
    """
    from dataclasses import asdict

    try:
        from kairix.paths import worker_state_path
        from kairix.worker_state import read_state

        state = read_state(worker_state_path())
        if state is None:
            return {
                "phase": "unknown",
                "available": False,
                "error": "worker state file not found",
            }
        return {"available": True, "error": "", **asdict(state)}
    except Exception as exc:
        logger.warning("tool_worker_status failed: %s", exc, exc_info=True)
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Operator-only capability stubs — agents that call these get a structured
# escalation envelope naming the exact CLI command to ask their admin to run.
# ---------------------------------------------------------------------------

# Canonical runbook reference for every operator-only escalation envelope.
_RETRIEVAL_RUNBOOK = "docs/runbooks/kairix-retrieval-health.md"


def _operator_only_envelope(
    capability: str,
    operator_command: str,
    reason: str,
    expected_runtime_seconds: int,
    see_also: list[str] | None = None,
) -> dict[str, Any]:
    """Canonical envelope shape for capabilities that can't be safely agent-invoked."""
    return {
        "error": "OperatorOnlyCapability",
        "capability": capability,
        "reason": reason,
        "operator_command": operator_command,
        "expected_runtime_seconds": expected_runtime_seconds,
        "see_also": see_also or [],
    }


def tool_soak_run(suite: str = "reflib", repeat: int = 3) -> dict[str, Any]:
    """Stub for the soak capability — operator-only, escalation envelope.

    Soak runs take minutes and stress the system under sustained load.
    Agents that hit this tool receive the exact CLI command and
    runbook pointer so they can escalate to an operator.
    """
    return _operator_only_envelope(
        capability="soak run",
        operator_command=f"kairix soak run --suite {suite} --repeat {repeat}",
        reason="Soak runs take minutes and stress the system under sustained load. Agents must escalate.",
        expected_runtime_seconds=60 * repeat,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


# Agent-safe caps for the probe surface — exceeding either dimension routes
# the call into the operator-only escalation envelope instead of running.
# Rationale: 20 queries at ~300 ms with concurrency 3 stays under ~6 s
# wallclock and matches typical teaming load. Anything bigger stresses the
# system enough that an operator should be in the loop.
MCP_PROBE_QUERIES_CAP = 20
MCP_PROBE_CONCURRENCY_CAP = 3


def _default_probe_search_runner(**kwargs: Any) -> Any:
    """Production runner — defers the heavy probe import until call time."""
    from kairix.quality.probe import run_probe_search

    return run_probe_search(**kwargs)


def tool_probe_search(
    suite: str = "reflib",
    queries: int = 20,
    concurrency: int = 3,
    seed: int = 0,
    *,
    probe_runner: Callable[..., Any] = _default_probe_search_runner,
) -> dict[str, Any]:
    """Concurrent-load latency probe — capped for agent safety.

    Below the cap (queries<=20 AND concurrency<=3) runs the probe and returns
    the ProbeResult envelope. Above the cap, returns an OperatorOnlyCapability
    envelope pointing the agent at the CLI command for the operator.

    Reason this isn't escalation-only: a small probe is the only way for an
    agent to confirm retrieval is healthy before committing to a long task.
    Larger probes stress the system and must be operator-driven.

    The ``probe_runner`` kwarg is the public DI seam: tests pass a stub
    runner instead of monkey-patching the production module attribute.
    """
    if queries > MCP_PROBE_QUERIES_CAP or concurrency > MCP_PROBE_CONCURRENCY_CAP:
        return _operator_only_envelope(
            capability="probe search (above cap)",
            operator_command=(
                f"kairix probe search --suite {suite} --queries {queries} --concurrency {concurrency} --seed {seed}"
            ),
            reason=(
                f"Probe above the agent-safe cap (queries<={MCP_PROBE_QUERIES_CAP}, "
                f"concurrency<={MCP_PROBE_CONCURRENCY_CAP}) stresses the system; agents must escalate."
            ),
            expected_runtime_seconds=max(30, queries * 2),
            see_also=[_RETRIEVAL_RUNBOOK],
        )

    result = probe_runner(
        suite=suite,
        queries=queries,
        concurrency=concurrency,
        seed=seed,
    )
    envelope: dict[str, Any] = result.to_envelope()
    return envelope


def tool_probe_burst(
    suite: str = "reflib",
    total_queries: int = 200,
    peak_concurrency: int = 20,
) -> dict[str, Any]:
    """Stub for the burst-probe capability — operator-only, escalation envelope.

    Burst is load-generating by design (rapid query injection to measure
    post-warmup throughput drop). Agents calling this tool receive the
    OperatorOnlyCapability envelope with the exact CLI command for the operator.
    """
    return _operator_only_envelope(
        capability="probe burst",
        operator_command=(
            f"kairix probe burst --suite {suite} --total-queries {total_queries} --peak-concurrency {peak_concurrency}"
        ),
        reason=(
            "Probe burst injects queries as fast as possible against the "
            "production retrieval pipeline; load-generating by design. Agents must escalate."
        ),
        expected_runtime_seconds=max(30, total_queries // 5),
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_probe_config() -> dict[str, Any]:
    """Stub for the probe-config capability — operator-only, escalation envelope.

    ``kairix probe-config`` runs a small representative embed workload against
    the operator's configured provider to verify the setup and emit tuning
    recommendations. It is load-generating against the provider's real endpoint
    and surfaces config-shaped advice an operator (not an agent) applies; agents
    must escalate.
    """
    return _operator_only_envelope(
        capability="probe-config",
        operator_command="kairix probe-config",
        reason=(
            "probe-config runs an embed workload against the operator's configured "
            "provider endpoint and surfaces config-tuning advice the operator applies. "
            "Agents must escalate."
        ),
        expected_runtime_seconds=60,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_benchmark_run(suite: str = "reflib") -> dict[str, Any]:
    """Stub for the benchmark capability — operator-only, escalation envelope."""
    return _operator_only_envelope(
        capability="benchmark run",
        operator_command=f"kairix benchmark run --suite {suite}",
        reason="Benchmark runs take minutes and load the system; agents must escalate.",
        expected_runtime_seconds=120,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_embed(limit: int = 0) -> dict[str, Any]:
    """Stub for the embed capability — operator-only, mutates state."""
    flag = "" if limit == 0 else f" --limit {limit}"
    return _operator_only_envelope(
        capability="embed",
        operator_command=f"kairix embed{flag}",
        reason="Embed mutates the vector index and is metered against an Azure quota; agents must escalate.",
        expected_runtime_seconds=300,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_store_crawl() -> dict[str, Any]:
    """Stub for the store-crawl capability — operator-only, mutates Neo4j."""
    return _operator_only_envelope(
        capability="store crawl",
        operator_command="kairix store crawl",
        reason="Crawl mutates Neo4j entity graph and takes minutes; agents must escalate.",
        expected_runtime_seconds=300,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_embed_rebuild_fts() -> dict[str, Any]:
    """Stub for the FTS-rebuild capability — operator-only, destructive recovery action."""
    return _operator_only_envelope(
        capability="embed rebuild-fts",
        operator_command="kairix embed rebuild-fts",
        reason="rebuild-fts drops and re-creates the documents_fts table; agents must escalate.",
        expected_runtime_seconds=60,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


def tool_cc_pair(verb: str = "list") -> dict[str, Any]:
    """Stub for the cc-pair capability — Wave D lifecycle is operator-owned.

    cc_pair create / pause / resume / delete mutate the topology v2 state
    machine (kairix.core.connectors.cc_pair F57 lifecycle); agents must
    escalate to an operator running `kairix cc-pair <verb>` so the
    transition gets logged + observable via `kairix features status
    --topology-v2`. The read-only ``list`` verb is also returned through
    the escalation envelope so agents get one consistent shape per
    capability.
    """
    suffix = "" if verb == "list" else " --id <id>"
    return _operator_only_envelope(
        capability="cc-pair",
        operator_command=f"kairix cc-pair {verb}{suffix}",
        reason=(
            "cc-pair mutates the topology v2 cc_pair lifecycle (status state machine "
            "+ topology_cc_pairs rows); operators run via the CLI so transitions are "
            "audited. Agents read state via `tool_features_status(topology_v2=True)`."
        ),
        expected_runtime_seconds=5,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


# Capability catalogue constants.
#
# CAPABILITIES_TOOL_NAME is the canonical MCP / catalogue name for the
# introspection tool itself; pinned here so the catalogue entry's `name` and
# `mcp_tool` fields stay in sync without literal duplication.
CAPABILITIES_TOOL_NAME = "capabilities"

# Tool-name constants — pinned to keep the catalogue's `name` / `mcp_tool`
# fields, the `require_ready` lookups, and any other references in sync
# without literal duplication. Add new entries here when a tool's name is
# referenced 3+ times (F17 threshold).
CONTRADICT_TOOL_NAME = "contradict"

# Capability category labels — used by tool_capabilities and the usage-guide
# capabilities table. F25 cross-checks these for sync.
CAP_CATEGORY_RETRIEVAL = "retrieval"
CAP_CATEGORY_SYNTHESIS = "synthesis"
CAP_CATEGORY_DIAGNOSTIC = "diagnostic"
CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY = "diagnostic-operator-only"
CAP_CATEGORY_KNOWLEDGE_WRITE = "knowledge-write"
CAP_CATEGORY_AGENT = "agent"


def _cap(
    *,
    name: str,
    mcp_tool: str | None,
    cli: str,
    category: str,
    mcp_caps: dict[str, Any] | None = None,
    escalate_via: str | None = None,
) -> dict[str, Any]:
    """Build a single capability-catalogue entry with consistent key order.

    Keeps tool_capabilities() readable and pins the entry shape — only the
    listed kwargs may appear in a catalogue entry. Optional keys are omitted
    when None so dict equality stays clean for round-trip tests.
    """
    entry: dict[str, Any] = {
        "name": name,
        "mcp_tool": mcp_tool,
        "cli": cli,
        "category": category,
    }
    if mcp_caps is not None:
        entry["mcp_caps"] = mcp_caps
    if escalate_via is not None:
        entry["escalate_via"] = escalate_via
    return entry


def tool_capabilities() -> dict[str, Any]:
    """Return the full kairix capability catalogue for programmatic introspection.

    Per affordance pattern 4 (docs/architecture/operational-tests-design.md):
    AI-driven SRE agents call this to discover bindings rather than guess. Each
    entry tells the caller (a) the canonical name, (b) the MCP tool name if
    callable (None when CLI-only or escalation-only), (c) the CLI invocation,
    (d) the category, and (e) any MCP caps or escalation pointer.

    The catalogue is hand-maintained — F25 (capability-affordance) keeps it in
    sync with the actual CLI dispatch + MCP registry.
    """
    return {
        "capabilities": [
            # Retrieval
            _cap(name="search", mcp_tool="search", cli="kairix search", category=CAP_CATEGORY_RETRIEVAL),
            _cap(name="entity", mcp_tool="entity", cli="kairix entity", category=CAP_CATEGORY_RETRIEVAL),
            _cap(name="timeline", mcp_tool="timeline", cli="kairix timeline", category=CAP_CATEGORY_RETRIEVAL),
            # Synthesis
            _cap(name="prep", mcp_tool="prep", cli="kairix prep", category=CAP_CATEGORY_SYNTHESIS),
            _cap(name="research", mcp_tool="research", cli="kairix research", category=CAP_CATEGORY_SYNTHESIS),
            _cap(
                name=CONTRADICT_TOOL_NAME,
                mcp_tool=CONTRADICT_TOOL_NAME,
                cli="kairix contradict",
                category=CAP_CATEGORY_SYNTHESIS,
            ),
            _cap(name="brief", mcp_tool="brief", cli="kairix brief", category=CAP_CATEGORY_SYNTHESIS),
            # Agent infra
            _cap(
                name="usage_guide",
                mcp_tool="usage_guide",
                cli="kairix usage-guide",
                category=CAP_CATEGORY_AGENT,
            ),
            _cap(
                name=CAPABILITIES_TOOL_NAME,
                mcp_tool=CAPABILITIES_TOOL_NAME,
                cli="kairix capabilities",
                category=CAP_CATEGORY_AGENT,
            ),
            _cap(name="bootstrap", mcp_tool="bootstrap", cli="kairix bootstrap", category=CAP_CATEGORY_AGENT),
            _cap(
                name="entity_suggest",
                mcp_tool="entity_suggest",
                cli="kairix entity suggest",
                category=CAP_CATEGORY_AGENT,
            ),
            _cap(
                name="entity_validate",
                mcp_tool="entity_validate",
                cli="kairix entity validate",
                category=CAP_CATEGORY_AGENT,
            ),
            # Diagnostic (agent-callable)
            _cap(
                name="onboard_check",
                mcp_tool="onboard_check",
                cli="kairix onboard check",
                category=CAP_CATEGORY_DIAGNOSTIC,
            ),
            _cap(
                name="worker_status",
                mcp_tool="worker_status",
                cli="kairix worker status",
                category=CAP_CATEGORY_DIAGNOSTIC,
            ),
            _cap(
                name="features_status",
                mcp_tool="features_status",
                cli="kairix features status",
                category=CAP_CATEGORY_DIAGNOSTIC,
            ),
            _cap(name="warm", mcp_tool="warm", cli="kairix warm", category=CAP_CATEGORY_DIAGNOSTIC),
            # Probe search — capped MCP variant
            _cap(
                name="probe_search",
                mcp_tool="probe_search",
                cli="kairix probe search",
                category=CAP_CATEGORY_DIAGNOSTIC,
                mcp_caps={
                    "queries_max": MCP_PROBE_QUERIES_CAP,
                    "concurrency_max": MCP_PROBE_CONCURRENCY_CAP,
                },
            ),
            # Diagnostic operator-only (escalation stubs)
            _cap(
                name="soak_run",
                mcp_tool=None,
                cli="kairix soak run",
                category=CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
                escalate_via="soak_run",
            ),
            _cap(
                name="benchmark_run",
                mcp_tool=None,
                cli="kairix benchmark run",
                category=CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
                escalate_via="benchmark_run",
            ),
            _cap(
                name="probe_burst",
                mcp_tool=None,
                cli="kairix probe burst",
                category=CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
                escalate_via="probe_burst",
            ),
            _cap(
                name="probe_config",
                mcp_tool=None,
                cli="kairix probe-config",
                category=CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
                escalate_via="probe_config",
            ),
            # Plan B-parity Week 5 Stream A — agent-driven ingest + recall
            _cap(
                name="ingest_chat",
                mcp_tool="ingest_chat",
                cli="kairix ingest-chat",
                category=CAP_CATEGORY_KNOWLEDGE_WRITE,
            ),
            _cap(
                name="facts_about",
                mcp_tool="facts_about",
                cli="kairix facts about",
                category=CAP_CATEGORY_RETRIEVAL,
            ),
            # Knowledge-write operator-only
            _cap(
                name="embed",
                mcp_tool=None,
                cli="kairix embed",
                category=CAP_CATEGORY_KNOWLEDGE_WRITE,
                escalate_via="embed",
            ),
            _cap(
                name="store_crawl",
                mcp_tool=None,
                cli="kairix store crawl",
                category=CAP_CATEGORY_KNOWLEDGE_WRITE,
                escalate_via="store_crawl",
            ),
            _cap(
                name="embed_rebuild_fts",
                mcp_tool=None,
                cli="kairix embed rebuild-fts",
                category=CAP_CATEGORY_KNOWLEDGE_WRITE,
                escalate_via="embed_rebuild_fts",
            ),
            _cap(
                name="cc_pair",
                mcp_tool=None,
                cli="kairix cc-pair",
                category=CAP_CATEGORY_KNOWLEDGE_WRITE,
                escalate_via="cc_pair",
            ),
        ],
        "schema_version": "1",
        "see_also": [_RETRIEVAL_RUNBOOK, "docs/architecture/operational-tests-design.md"],
    }


# ---------------------------------------------------------------------------
# FastMCP server — only constructed when mcp package is available
# ---------------------------------------------------------------------------


def _register_retrieval_tools(server: Any, readiness_check: Callable[[], bool] | None) -> None:
    """Register the cold-start-aware retrieval surface.

    Every tool here is warm-gated AND returns the ColdStart envelope when the
    readiness check is unsatisfied. Tools that only need @warm_gate (and not
    require_ready) live in ``_register_synthesis_and_diagnostic_tools``.
    """

    @server.tool(
        description=(
            "Call before answering any factual question about prior work, decisions, or context — "
            "kairix indexes the team's knowledge store and finds relevant prior material. "
            "Use this proactively at session start and whenever a question touches the team's history. "
            "If the result has error_code=KAIRIX_COLD_START, do not answer from memory or fallback; "
            "wait retry_after_ms and retry the same call once."
        )
    )
    @async_tool_handler
    @warm_gate
    def search(
        query: str,
        agent: str | None = None,
        scope: Scope = DEFAULT_SCOPE,
        budget: int = 3000,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search your knowledge store — finds the best answers to any question."""
        if cold := require_ready("search", readiness_check):
            return cold
        return tool_search(query=query, agent=agent, scope=scope, budget=budget, limit=limit)

    @server.tool(
        description=(
            "Call when you need facts about a specific named entity (person, company, project) — "
            "direct knowledge-graph lookup, faster than search."
        )
    )
    @async_tool_handler
    @warm_gate
    def entity(name: str) -> dict[str, Any]:
        """Entity lookup from Neo4j."""
        return tool_entity(name=name)

    @server.tool(
        description=(
            "Call when you need lightweight context preparation before deeper work. "
            "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry; "
            "do not substitute memory-only context."
        )
    )
    @async_tool_handler
    @warm_gate
    def prep(
        query: str,
        agent: str | None = None,
        tier: Literal["l0", "l1"] = "l0",
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Context preparation: tiered L0/L1 summary generation."""
        if cold := require_ready("prep", readiness_check):
            return cold
        return tool_prep(query=query, agent=agent, tier=tier, scope=scope)

    @server.tool(
        description=(
            "Call for date-aware retrieval when a question depends on timing. "
            "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry the same call."
        )
    )
    @async_tool_handler
    @warm_gate
    def timeline(
        query: str,
        anchor_date: str | None = None,
        agent: str | None = None,
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Temporal query rewriting + date-aware retrieval."""
        if cold := require_ready("timeline", readiness_check):
            return cold
        return tool_timeline(
            query=query,
            anchor_date=anchor_date,
            agent=agent,
            scope=scope,
        )

    @server.tool(
        description=(
            "Call for complex research questions that need iterative retrieval. "
            "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry before answering."
        )
    )
    @async_tool_handler
    @warm_gate
    def research(query: str, agent: str | None = None, max_turns: int = 4) -> dict[str, Any]:
        """Research a complex question. Searches iteratively until it finds a good answer."""
        if cold := require_ready("research", readiness_check):
            return cold
        return tool_research(query=query, agent=agent, max_turns=max_turns)

    @server.tool(
        description=(
            "Call before writing new facts to check for contradictions against existing knowledge. "
            "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry before proceeding."
        )
    )
    @async_tool_handler
    @warm_gate
    def contradict(
        content: str,
        agent: str | None = None,
        top_k: int = 5,
        threshold: float = 0.45,
        top_claims: int = 3,
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Check new content against existing knowledge for contradictions."""
        if cold := require_ready(CONTRADICT_TOOL_NAME, readiness_check):
            return cold
        return tool_contradict(
            content=content,
            agent=agent,
            top_k=top_k,
            threshold=threshold,
            top_claims=top_claims,
            scope=scope,
        )


def _register_synthesis_and_diagnostic_tools(
    server: Any,
    readiness_check: Callable[[], bool] | None,
    mark_ready: Callable[[], None] | None,
) -> None:
    """Register synthesis (brief/bootstrap) + diagnostic (warm/probe) tools.

    These tools are *not* the cold-aware retrieval surface — they include the
    warm() entry-point itself plus health/diagnostic envelopes that must remain
    callable during a cold state so operators can diagnose the cold state.
    """

    @server.tool()
    @async_tool_handler
    def usage_guide(topic: str = "") -> dict[str, Any]:
        """Return the kairix agent usage guide. Call this when unsure how to use kairix."""
        return tool_usage_guide(topic=topic)

    @server.tool(
        description=(
            "Call when you want a synthesised view of a topic — kairix runs a small research loop "
            "across the knowledge store and returns a structured briefing. "
            "Use it when you'd otherwise be tempted to summarise from memory. "
            "If the result has error_code=KAIRIX_COLD_START, do not summarise from memory; "
            "wait retry_after_ms and retry the same call."
        )
    )
    @async_tool_handler
    @warm_gate
    def brief(agent: str) -> dict[str, Any]:
        """Generate a session briefing for an agent. Returns content + on-disk path."""
        if cold := require_ready("brief", readiness_check):
            return cold
        return tool_brief(agent=agent)

    @server.tool(
        description=(
            "Call at session start or whenever you switch topics. "
            "Returns your agent role, current board, recent memory, and active goals — "
            "orients you in the team's current state. "
            "If health.vector_search != 'ok', surface that to your human. "
            "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry; "
            "do not begin the task context-blind."
        )
    )
    @async_tool_handler
    @warm_gate
    def bootstrap(agent: str, max_memory_days: int = 3) -> dict[str, Any]:
        """Return the agent orientation envelope: role, board, recent memory, goals, health."""
        if cold := require_ready("bootstrap", readiness_check):
            return cold
        return tool_bootstrap(agent=agent, max_memory_days=max_memory_days)

    @server.tool()
    @async_tool_handler
    @warm_gate
    def entity_suggest(text: str) -> dict[str, Any]:
        """Suggest entities (people, organisations, places) found in text via NER + Neo4j cross-ref."""
        return tool_entity_suggest(text=text)

    @server.tool()
    @async_tool_handler
    @warm_gate
    def entity_validate(name: str, update: bool = False) -> dict[str, Any]:
        """Validate a named entity against Wikidata and optionally write the qid to Neo4j."""
        return tool_entity_validate(name=name, update=update)

    @server.tool(
        description=(
            "Run the kairix deployment health probes. Call when search seems degraded, "
            "before triaging 'I expected more results', or after a config change. "
            "Returns {passed, total, fully_passed, failures[]} — same shape as `kairix onboard check --json`."
        )
    )
    @async_tool_handler
    def onboard_check() -> dict[str, Any]:
        """Health-probe envelope. Read-only. Identical to `kairix onboard check --json`."""
        return tool_onboard_check()

    @server.tool(
        description=(
            "Read the kairix-worker state file. Call to verify the embed/maintenance loop is running. "
            "Returns the worker's phase, counters, last-run timestamp, and last-error string."
        )
    )
    @async_tool_handler
    def worker_status() -> dict[str, Any]:
        """Worker state envelope. Read-only. Identical to `kairix worker status`."""
        return tool_worker_status()

    @server.tool(
        description=(
            "List the registered kairix feature flags + their effective values. "
            "Use to self-introspect what's enabled before relying on flag-gated behaviour. "
            "Read-only. Identical envelope to `kairix features status --json`."
        )
    )
    @async_tool_handler
    def features_status() -> dict[str, Any]:
        """Feature-flag status envelope. Read-only. Identical to `kairix features status --json`."""
        return tool_features_status()

    @server.tool(
        description=(
            "Warm kairix retrieval caches + pay factory-init costs. Retryable cold-start "
            "affordance: first call constructs the SearchPipeline and runs a tiny read-only "
            "probe; agents and entrypoint scripts call this at session start and retry if "
            "cold-start is reported (ready=False). Idempotent — subsequent calls are sub-ms."
        )
    )
    @async_tool_handler
    def warm() -> dict[str, Any]:
        """Warm kairix retrieval caches via the cold-start affordance.

        On ready=True the readiness gate is flipped so /healthz/ready returns 200.
        See ``kairix.agents.mcp.cold_start.warm_retrieval_stack`` for the
        production warm semantics this tool exposes.
        """
        result = warm_retrieval_stack()
        if result.get("ready") is True and mark_ready is not None:
            mark_ready()
        return result

    @server.tool(
        description=(
            "Concurrent-load latency probe — capped agent-safe surface "
            f"(queries<={MCP_PROBE_QUERIES_CAP}, concurrency<={MCP_PROBE_CONCURRENCY_CAP}). "
            "Returns probe envelope below cap; OperatorOnlyCapability envelope above. "
            "Use to confirm retrieval is healthy before a long task."
        )
    )
    @async_tool_handler
    def probe_search(
        suite: str = "reflib",
        queries: int = 20,
        concurrency: int = 3,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Agent-safe capped probe. Returns ProbeResult envelope or escalation envelope."""
        return tool_probe_search(suite=suite, queries=queries, concurrency=concurrency, seed=seed)

    @server.tool(
        description=(
            "Programmatic capability catalogue — every kairix capability with its "
            "MCP tool name, CLI command, category, and (for capped MCP variants) "
            "the agent-safe caps. AI-driven SRE agents call this to discover the "
            "surface instead of guessing. See affordance pattern 4."
        )
    )
    @async_tool_handler
    def capabilities() -> dict[str, Any]:
        """Full kairix capability catalogue. Read-only. Identical to tool_capabilities()."""
        return tool_capabilities()


def _register_operator_and_ingest_tools(server: Any) -> None:
    """Register operator-only escalation stubs + Plan B-parity ingest surface.

    Operator stubs return ``OperatorOnlyCapability`` envelopes that point the
    calling agent at the exact CLI command to surface to its admin. The ingest
    tools (``ingest_chat`` / ``facts_about``) sit alongside because they share
    the same out-of-band-write hygiene (warm-gated, namespace-scoped).
    """

    # ---- Operator-only escalation stubs ----
    # These capabilities take minutes, mutate state, or are destructive
    # recovery actions. Agents that call them receive a structured
    # OperatorOnlyCapability envelope with the exact CLI command to
    # surface to their admin.

    @server.tool(
        description=(
            "Soak test escalation — soak runs are multi-minute load tests. Returns the "
            "OperatorOnlyCapability envelope with the exact `kairix soak run` command."
        )
    )
    @async_tool_handler
    def soak_run(suite: str = "reflib", repeat: int = 3) -> dict[str, Any]:
        """Operator-only soak test. Returns escalation envelope for the agent's admin."""
        return tool_soak_run(suite=suite, repeat=repeat)

    @server.tool(
        description=(
            "Burst-probe escalation — load-generating throughput-drop probe. Returns the "
            "OperatorOnlyCapability envelope with the exact `kairix probe burst` command."
        )
    )
    @async_tool_handler
    def probe_burst(
        suite: str = "reflib",
        total_queries: int = 200,
        peak_concurrency: int = 20,
    ) -> dict[str, Any]:
        """Operator-only burst probe. Returns escalation envelope."""
        return tool_probe_burst(suite=suite, total_queries=total_queries, peak_concurrency=peak_concurrency)

    @server.tool(
        description=(
            "Probe-config escalation — runs an embed workload against the configured "
            "provider endpoint and emits tuning advice the operator applies. Returns the "
            "OperatorOnlyCapability envelope with the exact `kairix probe-config` command."
        )
    )
    @async_tool_handler
    def probe_config() -> dict[str, Any]:
        """Operator-only probe-config. Returns escalation envelope."""
        return tool_probe_config()

    @server.tool(
        description=(
            "Benchmark escalation — benchmark runs take minutes and load the system. "
            "Returns the OperatorOnlyCapability envelope with the exact `kairix benchmark run` command."
        )
    )
    @async_tool_handler
    def benchmark_run(suite: str = "reflib") -> dict[str, Any]:
        """Operator-only benchmark run. Returns escalation envelope."""
        return tool_benchmark_run(suite=suite)

    @server.tool(
        description=(
            "Embed escalation — embed mutates the vector index against an Azure quota. "
            "Returns the OperatorOnlyCapability envelope with the exact `kairix embed` command."
        )
    )
    @async_tool_handler
    def embed(limit: int = 0) -> dict[str, Any]:
        """Operator-only embed. Returns escalation envelope."""
        return tool_embed(limit=limit)

    @server.tool(
        description=(
            "Store-crawl escalation — mutates Neo4j entity graph. Returns the "
            "OperatorOnlyCapability envelope with the exact `kairix store crawl` command."
        )
    )
    @async_tool_handler
    def store_crawl() -> dict[str, Any]:
        """Operator-only graph crawl. Returns escalation envelope."""
        return tool_store_crawl()

    @server.tool(
        description=(
            "FTS-rebuild escalation — drops + re-creates the documents_fts table. "
            "Returns the OperatorOnlyCapability envelope with the exact recovery command."
        )
    )
    @async_tool_handler
    def embed_rebuild_fts() -> dict[str, Any]:
        """Operator-only FTS recovery. Returns escalation envelope."""
        return tool_embed_rebuild_fts()

    @server.tool(
        description=(
            "cc-pair escalation — Wave D topology v2 cc_pair lifecycle (list / create / "
            "pause / resume / delete) mutates the state machine; agents must escalate. "
            "Returns the OperatorOnlyCapability envelope with the exact `kairix cc-pair` command."
        )
    )
    @async_tool_handler
    def cc_pair(verb: str = "list") -> dict[str, Any]:
        """Operator-only cc_pair lifecycle. Returns escalation envelope."""
        return tool_cc_pair(verb=verb)

    # ---- Plan B-parity Week 5 Stream A — agent-driven ingest + recall ----
    # ingest_chat lets the agent push JSONL transcripts into the conversation
    # store; facts_about gives the agent a direct introspection surface over
    # the fact store. Both are warm-gated because they touch persistence and
    # the LLM extractor — neither makes sense against a cold pipeline.

    @server.tool(
        description=(
            "Ingest a JSONL chat transcript supplied inline. Use this to push a recently-completed "
            "conversation into the knowledge store so future search/prep/recall can see it. "
            "Pass the agent's own engagement namespace — cross-engagement calls are rejected."
        )
    )
    @async_tool_handler
    @warm_gate
    def ingest_chat(
        jsonl_content: str,
        conversation_id: str,
        namespace: str,
        window_turns: int = 5,
        no_extract: bool = False,
    ) -> dict[str, Any]:
        """Push a JSONL chat transcript into the knowledge store."""
        from kairix.agents.mcp.tools.ingest_chat import tool_ingest_chat

        # ``allowed_namespace`` mirrors ``namespace`` at the FastMCP wire — the
        # agent's session is pinned upstream to a single engagement scope and
        # the bootstrap-derived namespace is the same value the agent passes
        # here. Tests bypass this surface and call ``tool_ingest_chat``
        # directly with both values to exercise the reject branch.
        return tool_ingest_chat(
            jsonl_content=jsonl_content,
            conversation_id=conversation_id,
            namespace=namespace,
            allowed_namespace=namespace,
            window_turns=window_turns,
            no_extract=no_extract,
        )

    @server.tool(
        description=(
            "Look up what kairix knows about an entity from the fact store. Returns the "
            "current (non-superseded) entity-attribute-value records with confidence and "
            "source provenance. Pass a namespace to restrict the lookup to a single "
            "engagement scope; pass null/None to search across all namespaces."
        )
    )
    @async_tool_handler
    @warm_gate
    def facts_about(
        entity: str,
        namespace: str | None = None,
        top_k: int = 20,
    ) -> dict[str, Any]:
        """Return fact records about an entity from the fact store."""
        from kairix.agents.mcp.tools.facts_about import tool_facts_about

        return tool_facts_about(entity=entity, namespace=namespace, top_k=top_k)


def build_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    readiness_check: Callable[[], bool] | None = None,
    mark_ready: Callable[[], None] | None = None,
) -> Any:
    """Construct the FastMCP server with every kairix tool registered.

    Args:
        host: Bind address for SSE transport.
        port: Port for SSE transport.
        readiness_check: Optional gate used by long-running HTTP deployments.
                         If it returns False, retrieval tools return a
                         canonical retryable cold-start envelope instead of
                         executing a lower-quality or partially-initialised path.
        mark_ready: Optional callback used by the warm tool to open the HTTP
                    readiness gate after a successful manual warm-up.

    Raises ImportError when the ``mcp`` package is not installed.
    Install via: pip install kairix[agents]
    """
    try:
        from mcp.server.fastmcp import FastMCP
    # The ImportError branch is reachable only when the optional ``mcp`` extra
    # is not installed; the test suite always installs it via ``kairix[agents]``.
    except ImportError as exc:  # pragma: no cover — optional 'mcp' extra; tests always install kairix[agents]
        raise ImportError(
            "The 'mcp' package is required to run the MCP server. Install it with: pip install 'kairix[agents]'"
        ) from exc

    server = FastMCP("kairix", host=host, port=port)
    _register_retrieval_tools(server, readiness_check)
    _register_synthesis_and_diagnostic_tools(server, readiness_check, mark_ready)
    _register_operator_and_ingest_tools(server)
    return server
