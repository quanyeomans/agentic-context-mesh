"""Recommender use case — rank capabilities for a described task.

The hot path of the capability recommender (Spec A,
``docs/architecture/capability-recommender/recommender-mvp-design.md``).
Given a task description, ``run_recommend`` retrieves over the dedicated
``capabilities`` search collection (BM25 + vector RRF + force-enabled
cross-encoder rerank), maps each hit to a ranked
:class:`CapabilityRecommendation` carrying a ready-to-call invocation, and
returns a :class:`RecommendOutput`. No LLM sits between the agent and its
tools.

The corpus is fed by two feeders (Feeder 1: kairix's own
``tool_capabilities()`` catalogue; Feeder 2: the local ``skills``
connector over ``~/.claude``). This module only *reads* the collection;
the surfaces (CLI ``kairix recommend`` + the ``recommend_capabilities``
MCP tool) are thin adapters added later.

Two scope namespaces appear in capability ids:

* ``capability://kairix/<name>`` — a kairix tool (CLI and/or MCP). The
  invocation (``mcp_tool`` / ``cli``) + ``when_to_use`` are enriched from
  the in-process catalogue keyed by name.
* ``capability://{skill,command,agent}/<name>`` — an external local
  capability. ``when_to_use`` comes from the retrieval snippet/content;
  there is no kairix ``mcp_tool`` / ``cli`` binding.

The use case **never raises** — every failure mode populates
:attr:`RecommendOutput.error` and returns an otherwise-empty result, per
the use-cases-never-raise contract.

Sabotage-proof log (executed mutate -> fail -> restore): see the test
module ``tests/use_cases/test_recommend.py``
(``test_run_recommend_records_explicit_collection_contract``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The dedicated collection the recommender retrieves over. ``agent=None``
# makes the pipeline use this list verbatim (the collection is globally
# readable and need not be registered in collections.yaml).
_CAPABILITIES_COLLECTION = "capabilities"

# Capability-id scheme: ``capability://<scope>/<name>`` (an optional
# ``#<seq>`` chunk suffix is stripped). The ``kairix`` scope maps to a
# kairix tool; the others are external local capabilities.
_CAPABILITY_URI_PREFIX = "capability://"
_KAIRIX_SCOPE = "kairix"
_KIND_TOOL = "tool"
_SURFACE_EXTERNAL = "external"

# The recommender excludes itself from its own results (self-reference
# guard) — an agent asking "which tool fits this task" never wants
# "use the recommender" back.
_SELF_REFERENCE_NAME = "recommend"

# F17 — envelope keys appear in the per-recommendation projection AND the
# top-level envelope writer; extract so a rename is a single edit site.
_KEY_NAME = "name"
_KEY_KIND = "kind"
_KEY_SURFACE = "surface"
_KEY_WHEN_TO_USE = "when_to_use"
_KEY_SCORE = "score"
_KEY_MCP_TOOL = "mcp_tool"
_KEY_CLI = "cli"
_KEY_SOURCE = "source"


# ---------------------------------------------------------------------------
# Result + deps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRecommendation:
    """One ranked capability, ready to call.

    Attributes:
        name: The capability's short name (the agent's call target).
        kind: ``"tool"`` (kairix) | ``"skill"`` | ``"command"`` | ``"agent"``.
        surface: ``"mcp"`` | ``"cli"`` | ``"both"`` (kairix tools) |
            ``"external"`` (local skills/commands/agents).
        when_to_use: Task-conditioned trigger text — why reach for this.
        score: The retrieval/rerank score (best-first ordering).
        mcp_tool: The MCP tool binding, empty when not applicable.
        cli: The CLI invocation (e.g. ``"kairix search"``), empty when N/A.
        source: Plugin/version provenance for external caps, else empty.
    """

    name: str
    kind: str
    surface: str
    when_to_use: str
    score: float
    mcp_tool: str = ""
    cli: str = ""
    source: str = ""


@dataclass(frozen=True)
class RecommendOutput:
    """Outcome of one ``run_recommend`` invocation.

    Attributes:
        task: The caller's task description, unchanged.
        recommendations: Up to ``limit`` ranked capabilities, best-first.
        correlation_id: A per-call id (forward-compat: Spec B keys its
            outcome log on this). Minted even on failure so the shape is
            stable.
        error: Empty on success; an operator/agent-actionable message or a
            structured ``"<Class>: <msg>"`` on a top-level failure.
    """

    task: str = ""
    recommendations: tuple[CapabilityRecommendation, ...] = ()
    correlation_id: str = ""
    error: str = ""


def recommender_config(base: Any) -> Any:
    """Return ``base`` with cross-encoder rerank force-enabled.

    Cross-encoder rerank is force-enabled for the ``capabilities``
    collection (precision@1-3 over a small corpus matters more than
    recall). ``replace`` over a frozen :class:`RetrievalConfig` yields a
    distinct value, which the pipeline factory caches in its own bucket —
    no collision with the main search pipeline. Public so the
    force-rerank contract is unit-pinnable without a provider.
    """
    from dataclasses import replace

    from kairix.core.search.config import RerankConfig

    return replace(base, rerank=RerankConfig(enabled=True))


def _default_search(**kwargs: Any) -> Any:
    """Lazy DI default: the production search pipeline, rerank force-on.

    ``load_config()`` returns a ``RetrievalConfig`` (verified:
    ``kairix/core/search/config_loader.py`` ``load_config`` ->
    ``_load_cached_layered``/``load_cached``, both ``-> RetrievalConfig``),
    so :func:`recommender_config`'s ``replace(...)`` is valid.
    """
    from kairix.core.factory import build_search_pipeline
    from kairix.core.search.config_loader import load_config

    cfg = recommender_config(load_config())
    return build_search_pipeline(config=cfg).search(**kwargs)


def _default_catalogue() -> list[dict[str, Any]]:
    """Lazy DI default: kairix's own capability catalogue (name->row enrich)."""
    from kairix.agents.mcp.server import tool_capabilities

    return list(tool_capabilities()["capabilities"])


def _default_correlation_id() -> str:
    """Lazy DI default: a fresh uuid4 hex (tests inject a fixed value)."""
    return uuid.uuid4().hex


@dataclass(frozen=True)
class RecommendDeps:
    """Injectable dependencies for ``run_recommend``.

    Non-Optional fields wired to production defaults via
    ``default_factory`` — the #204 frozen-deps pattern (never
    ``Optional[Callable]``). Tests construct
    ``RecommendDeps(search_fn=fake, ...)`` with explicit overrides;
    ``RecommendDeps()`` resolves to the module-level ``_default_*``
    callables.
    """

    search_fn: Callable[..., Any] = field(default_factory=lambda: _default_search)
    catalogue_fn: Callable[[], list[dict[str, Any]]] = field(default_factory=lambda: _default_catalogue)
    correlation_id_fn: Callable[[], str] = field(default_factory=lambda: _default_correlation_id)


# ---------------------------------------------------------------------------
# Hit -> recommendation mapping
# ---------------------------------------------------------------------------


def _parse_capability_uri(path: str) -> tuple[str, str] | None:
    """Parse ``capability://<scope>/<name>`` -> ``(scope, name)``.

    A trailing ``#<seq>`` chunk suffix is stripped. Returns ``None`` for a
    path that isn't a capability URI (the caller drops it rather than
    failing the whole call).
    """
    if not path.startswith(_CAPABILITY_URI_PREFIX):
        return None
    rest = path[len(_CAPABILITY_URI_PREFIX) :].split("#", 1)[0]
    scope, sep, name = rest.partition("/")
    if not sep or not scope or not name:
        return None
    return scope, name


def _row_score(inner: Any) -> float:
    """Best available score: rerank > boosted > rrf (all default 0.0)."""
    score = (
        getattr(inner, "rerank_score", 0.0) or getattr(inner, "boosted_score", 0.0) or getattr(inner, "rrf_score", 0.0)
    )
    return float(score or 0.0)


def _hit_text(budgeted: Any, inner: Any) -> str:
    """The retrieval text for a hit — budget-trimmed content, then snippet."""
    return str(getattr(budgeted, "content", "") or getattr(inner, "snippet", "") or "")


def _kairix_recommendation(name: str, score: float, catalogue: dict[str, dict[str, Any]]) -> CapabilityRecommendation:
    """Build a kairix-tool recommendation, enriched from the catalogue row."""
    row = catalogue.get(name, {})
    mcp_tool = str(row.get("mcp_tool") or "")
    cli = str(row.get("cli") or "")
    return CapabilityRecommendation(
        name=name,
        kind=_KIND_TOOL,
        surface=_surface_for(mcp_tool, cli),
        when_to_use=str(row.get("when_to_use", "")),
        score=score,
        mcp_tool=mcp_tool,
        cli=cli,
    )


def _external_recommendation(scope: str, name: str, score: float, when_to_use: str) -> CapabilityRecommendation:
    """Build an external-capability recommendation (skill/command/agent)."""
    return CapabilityRecommendation(
        name=name,
        kind=scope,
        surface=_SURFACE_EXTERNAL,
        when_to_use=when_to_use,
        score=score,
    )


def _surface_for(mcp_tool: str, cli: str) -> str:
    """Derive a kairix tool's binding surface from its invocations."""
    if mcp_tool and cli:
        return "both"
    if mcp_tool:
        return "mcp"
    return "cli"


def _hit_to_recommendation(budgeted: Any, catalogue: dict[str, dict[str, Any]]) -> CapabilityRecommendation | None:
    """Map one ``BudgetedResult`` to a recommendation, or ``None`` to drop it.

    Drops the hit when the path isn't a capability URI or when it is the
    recommender's own self-reference.
    """
    inner = getattr(budgeted, "result", None)
    if inner is None:
        return None
    parsed = _parse_capability_uri(str(getattr(inner, "path", "")))
    if parsed is None:
        return None
    scope, name = parsed
    if name == _SELF_REFERENCE_NAME:
        return None
    score = _row_score(inner)
    if scope == _KAIRIX_SCOPE:
        return _kairix_recommendation(name, score, catalogue)
    return _external_recommendation(scope, name, score, _hit_text(budgeted, inner))


def _map_results(
    results: Any, catalogue: dict[str, dict[str, Any]], limit: int
) -> tuple[CapabilityRecommendation, ...]:
    """Map the pipeline's hits to up to ``limit`` recommendations."""
    out: list[CapabilityRecommendation] = []
    for budgeted in results:
        rec = _hit_to_recommendation(budgeted, catalogue)
        if rec is not None:
            out.append(rec)
        if len(out) >= limit:
            break
    return tuple(out)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def run_recommend(
    task: str,
    *,
    agent: str | None = None,
    limit: int = 5,
    deps: RecommendDeps | None = None,
) -> RecommendOutput:
    """Rank capabilities for a described task.

    Never raises — failures populate :attr:`RecommendOutput.error` and
    return an otherwise-empty result.

    Args:
        task: Natural-language description of what the agent wants to do.
        agent: Accepted for parity / forward use (an agent's available
            toolset may differ); v1 does not personalise ranking. The
            *capability query* always runs with ``agent=None`` so the
            unregistered ``capabilities`` collection is used verbatim.
        limit: Maximum number of recommendations returned.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or RecommendDeps()
    correlation_id = d.correlation_id_fn()
    # ``agent`` is accepted for surface parity + forward use (Spec B keys its
    # outcome log on the requesting agent). v1 records it but does not
    # personalise ranking — the capability query always runs with
    # ``agent=None`` so the unregistered ``capabilities`` collection is used
    # verbatim.
    logger.debug("run_recommend: agent=%s correlation_id=%s", agent, correlation_id)
    try:
        sr = d.search_fn(query=task, collections=[_CAPABILITIES_COLLECTION], agent=None, limit=limit)
        catalogue = {row["name"]: row for row in d.catalogue_fn()}
        recommendations = _map_results(getattr(sr, "results", []), catalogue, limit)
        return RecommendOutput(task=task, recommendations=recommendations, correlation_id=correlation_id)
    except Exception as exc:  # never raise — surface via .error
        # No exc_info: the ``error`` field carries the type + message, which
        # is the observable contract the tests pin.
        logger.warning("run_recommend failed: %s: %s", type(exc).__name__, exc)
        return RecommendOutput(task=task, correlation_id=correlation_id, error=f"{type(exc).__name__}: {exc}")


def recommend_output_to_envelope(out: RecommendOutput) -> dict[str, Any]:
    """Project a ``RecommendOutput`` to the JSON envelope callers receive.

    Both adapters (the CLI ``--json`` path and the MCP ``tool_recommend``)
    serialise from this helper so the envelope shape has one definition.
    """
    return {
        "task": out.task,
        "recommendations": [
            {
                _KEY_NAME: r.name,
                _KEY_KIND: r.kind,
                _KEY_SURFACE: r.surface,
                _KEY_WHEN_TO_USE: r.when_to_use,
                _KEY_SCORE: r.score,
                _KEY_MCP_TOOL: r.mcp_tool,
                _KEY_CLI: r.cli,
                _KEY_SOURCE: r.source,
            }
            for r in out.recommendations
        ],
        "correlation_id": out.correlation_id,
        "error": out.error,
    }
