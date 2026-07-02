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

Registration is catalogue-driven (PLA-318): every tool body lives in a
per-domain adapter module under :mod:`kairix.agents.mcp.tools` (``retrieval`` /
``synthesis`` / ``orient`` / ``diagnostic`` / ``operator`` plus the agent-write
adapters ``ingest_chat`` / ``facts_about`` / ``memory_write``), each publishing
a :data:`~kairix.agents.mcp.tools._common.ToolBinding`. :func:`build_server`
walks :data:`CAPABILITIES_CATALOG` and registers the matching binding for each
row, so the agent surface has a single source of truth (the catalogue) instead
of ~37 hand-written ``@server.tool`` defs. The ``tool_<name>`` bodies are
re-exported here so direct-call unit tests and the ``kairix.use_cases`` helpers
keep importing them from ``kairix.agents.mcp.server``.

Design principles:
  - Never raises; returns error dicts on failure so agents can handle gracefully
  - All inputs/outputs are JSON-serialisable primitives
  - Dependencies initialised lazily on first call
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kairix.agents.mcp.errors import async_tool_handler
from kairix.agents.mcp.tools import (
    diagnostic as _diagnostic,
)
from kairix.agents.mcp.tools import (
    facts_about as _facts_about,
)
from kairix.agents.mcp.tools import (
    ingest_chat as _ingest_chat,
)
from kairix.agents.mcp.tools import (
    memory_write as _memory_write,
)
from kairix.agents.mcp.tools import (
    operator as _operator,
)
from kairix.agents.mcp.tools import (
    orient as _orient,
)
from kairix.agents.mcp.tools import (
    retrieval as _retrieval,
)
from kairix.agents.mcp.tools import (
    synthesis as _synthesis,
)
from kairix.agents.mcp.tools._common import (
    MCP_PROBE_CONCURRENCY_CAP,
    MCP_PROBE_QUERIES_CAP,
    RETRIEVAL_RUNBOOK,
    RegistrationContext,
    ToolBinding,
)

# Re-exported tool bodies + DI seams. The ``tool_<name>`` adapters now live in
# the per-domain modules; direct-call unit tests and the ``kairix.use_cases``
# helpers import them from here, so this module keeps exposing them under the
# historical ``kairix.agents.mcp.server`` names.
from kairix.agents.mcp.tools.diagnostic import (
    tool_caches_status,
    tool_dead_letter_status,
    tool_doctor_check_agent,
    tool_doctor_check_all,
    tool_features_status,
    tool_maintenance_analyze,
    tool_onboard_agent,
    tool_onboard_check,
    tool_onboard_scan,
    tool_probe_search,
    tool_warm,
    tool_worker_status,
)
from kairix.agents.mcp.tools.operator import (
    tool_benchmark_run,
    tool_cc_pair,
    tool_embed,
    tool_embed_rebuild_fts,
    tool_probe_burst,
    tool_probe_config,
    tool_soak_run,
    tool_store_crawl,
)
from kairix.agents.mcp.tools.orient import (
    tool_bootstrap,
    tool_entity_suggest,
    tool_entity_validate,
    tool_recommend,
    tool_recommend_capabilities,
    tool_usage_guide,
)
from kairix.agents.mcp.tools.retrieval import (
    QueueAwareSearchDeps,
    tool_entity,
    tool_expand,
    tool_search,
    tool_search_queue_aware,
    tool_timeline,
)
from kairix.agents.mcp.tools.retrieval import (
    _fetch_entity_card as _fetch_entity_card,  # re-export: kairix.use_cases.{entity_get,search} import this seam
)
from kairix.agents.mcp.tools.synthesis import (
    tool_brief,
    tool_contradict,
    tool_prep,
    tool_research,
)

if TYPE_CHECKING:
    from kairix.agents.mcp.tools.facts_about import FactsAboutDeps
    from kairix.use_cases.remember import RememberDeps

logger = logging.getLogger(__name__)

__all__ = [
    "CAPABILITIES_CATALOG",
    "CAPABILITIES_TOOL_NAME",
    "CAP_CATEGORY_AGENT",
    "CAP_CATEGORY_CONFIGURATION",
    "CAP_CATEGORY_DIAGNOSTIC",
    "CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY",
    "CAP_CATEGORY_KNOWLEDGE_WRITE",
    "CAP_CATEGORY_RETRIEVAL",
    "CAP_CATEGORY_SYNTHESIS",
    "CONTRADICT_TOOL_NAME",
    "LOOP_GROUP_ORDER",
    "MCP_PROBE_CONCURRENCY_CAP",
    "MCP_PROBE_QUERIES_CAP",
    "RECOMMEND_CAPABILITIES_TOOL_NAME",
    "Capability",
    "QueueAwareSearchDeps",
    "agent_facing",
    "build_server",
    "by_loop_group",
    "tool_benchmark_run",
    "tool_bootstrap",
    "tool_brief",
    "tool_caches_status",
    "tool_capabilities",
    "tool_cc_pair",
    "tool_contradict",
    "tool_dead_letter_status",
    "tool_doctor_check_agent",
    "tool_doctor_check_all",
    "tool_embed",
    "tool_embed_rebuild_fts",
    "tool_entity",
    "tool_entity_suggest",
    "tool_entity_validate",
    "tool_expand",
    "tool_features_status",
    "tool_maintenance_analyze",
    "tool_onboard_agent",
    "tool_onboard_check",
    "tool_onboard_scan",
    "tool_prep",
    "tool_probe_burst",
    "tool_probe_config",
    "tool_probe_search",
    "tool_recommend",
    "tool_recommend_capabilities",
    "tool_research",
    "tool_search",
    "tool_search_queue_aware",
    "tool_soak_run",
    "tool_store_crawl",
    "tool_timeline",
    "tool_usage_guide",
    "tool_warm",
    "tool_worker_status",
    "warm_gate",
]


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
        is_warm_with_self_heal,
        trigger_background_warm,
    )

    # #425 — use the self-heal variant so a divergence between
    # in-process state (which can drift) and the persisted flag (which
    # the on-disk healthcheck reads) is detected at the request
    # boundary instead of presenting as a 13-hour cold-state regression.
    if is_warm_with_self_heal():
        return None
    trigger_background_warm()
    return cold_start_envelope(tool_name)


def warm_gate(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Decorator: short-circuit MCP tool calls with the ColdStart envelope
    while kairix is warming.

    Applied by :func:`_register_binding` to every binding whose ``warm_gated``
    flag is set, BELOW ``@async_tool_handler`` and ABOVE the body closure. The
    tool name passed to the cold-start envelope is taken from ``fn.__name__`` —
    by convention each registered tool's body closure is named identically to
    its MCP tool name, so no explicit parameter is needed.

    Sabotage-proof: clear a binding's ``warm_gated`` flag and the parametrized
    cold-start test for that tool fails because the tool body runs against the
    not-yet-warm pipeline instead of returning the envelope.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        cold = _is_warm_or_cold_envelope(fn.__name__)
        if cold is not None:
            return cold
        return fn(*args, **kwargs)

    return wrapper


# Canonical runbook reference surfaced in the capability catalogue's
# ``see_also``. The escalation stubs reference the shared copy in
# ``kairix.agents.mcp.tools._common``; this alias keeps the historical name
# for the catalogue projection.
_RETRIEVAL_RUNBOOK = RETRIEVAL_RUNBOOK


# Capability catalogue constants.
#
# CAPABILITIES_TOOL_NAME is the canonical MCP / catalogue name for the
# introspection tool itself; pinned here so the catalogue entry's `name` and
# `mcp_tool` fields stay in sync without literal duplication.
CAPABILITIES_TOOL_NAME = "capabilities"

# RECOMMEND_CAPABILITIES_TOOL_NAME is the canonical MCP / catalogue name for
# the capability recommender tool; pinned here so the registration, the
# catalogue ``_cap`` row, and any cross-reference stay in sync without
# literal duplication (F17).
RECOMMEND_CAPABILITIES_TOOL_NAME = "recommend_capabilities"

# Tool-name constants — pinned to keep the catalogue's `name` / `mcp_tool`
# fields, the `require_ready` lookups, and any other references in sync
# without literal duplication. Add new entries here when a tool's name is
# referenced 3+ times (F17 threshold).
CONTRADICT_TOOL_NAME = "contradict"

# Capability category labels. F25 cross-checks the set against the
# usage-guide capabilities table for sync.
CAP_CATEGORY_RETRIEVAL = "retrieval"
CAP_CATEGORY_SYNTHESIS = "synthesis"
CAP_CATEGORY_DIAGNOSTIC = "diagnostic"
CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY = "diagnostic-operator-only"
CAP_CATEGORY_KNOWLEDGE_WRITE = "knowledge-write"
CAP_CATEGORY_AGENT = "agent"
# PR 1.4 / #420 — agent scope discovery + proposal tools live under
# their own category so agents grouping by category can find them
# without scanning the diagnostic bucket.
CAP_CATEGORY_CONFIGURATION = "configuration"


@dataclass(frozen=True)
class Capability:
    """One row of the agent-capability catalogue as importable typed data.

    Frozen so a catalogue row can't be mutated after construction, and typed
    so CLI dispatch, the usage-guide generator, and the E2E harness can read
    the catalogue WITHOUT booting the MCP server or executing any tool
    semantics — importing :data:`CAPABILITIES_CATALOG` is enough.

    Fields mirror the historical ``_cap`` keys 1:1 (name / mcp_tool / cli /
    category / when_to_use / mcp_caps / escalate_via) so the JSON envelope
    :func:`tool_capabilities` emits stays byte-identical: :meth:`as_dict`
    reproduces the exact key order and the same optional-key omission rules.

    Attributes:
        name: Canonical capability name (the agent's call target).
        mcp_tool: The registered MCP tool name, or ``None`` when the capability
            is CLI-only / escalation-only.
        cli: The CLI invocation string (e.g. ``"kairix search"``).
        category: One of the ``CAP_CATEGORY_*`` labels.
        when_to_use: Task-conditioned trigger text ("Call when…"); empty for
            capabilities that need no ranking hint.
        mcp_caps: Agent-safe caps published to callers (e.g. probe limits), or
            ``None``. Excluded from ``__hash__`` so a ``Capability`` stays
            hashable despite the mapping field.
        escalate_via: The escalation target for operator-only capabilities, or
            ``None`` for agent-callable ones.
    """

    name: str
    mcp_tool: str | None
    cli: str
    category: str
    when_to_use: str = ""
    mcp_caps: Mapping[str, int] | None = field(default=None, hash=False)
    escalate_via: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Project the row to the catalogue's JSON-envelope dict shape.

        Keeps the historical ``_cap`` key order and omits the optional keys
        when unset (``when_to_use`` empty, ``mcp_caps`` / ``escalate_via``
        ``None``) so :func:`tool_capabilities` stays byte-identical across the
        promotion to typed data. ``mcp_caps`` is copied to a fresh ``dict`` so
        the returned envelope never shares mutable state with the module-level
        catalogue.
        """
        entry: dict[str, Any] = {
            "name": self.name,
            "mcp_tool": self.mcp_tool,
            "cli": self.cli,
            "category": self.category,
        }
        if self.when_to_use:
            entry["when_to_use"] = self.when_to_use
        if self.mcp_caps is not None:
            entry["mcp_caps"] = dict(self.mcp_caps)
        if self.escalate_via is not None:
            entry["escalate_via"] = self.escalate_via
        return entry


def _cap(
    *,
    name: str,
    mcp_tool: str | None,
    cli: str,
    category: str,
    when_to_use: str = "",
    mcp_caps: Mapping[str, int] | None = None,
    escalate_via: str | None = None,
) -> Capability:
    """Build one :class:`Capability` catalogue row with a consistent shape.

    Thin keyword-only constructor kept so the :data:`CAPABILITIES_CATALOG` rows
    read the same as before the dict→dataclass promotion. Only the listed
    kwargs may appear in a row; the omission of an empty ``when_to_use`` /
    ``None`` ``mcp_caps`` / ``None`` ``escalate_via`` now happens in
    :meth:`Capability.as_dict` rather than here, so the emitted envelope is
    unchanged. F99 (usage-guide currency) AST-walks these ``_cap(...)`` rows.

    ``when_to_use`` carries task-conditioned trigger text ("Call when…") so the
    capability recommender can rank a capability against a described task.
    """
    return Capability(
        name=name,
        mcp_tool=mcp_tool,
        cli=cli,
        category=category,
        when_to_use=when_to_use,
        mcp_caps=mcp_caps,
        escalate_via=escalate_via,
    )


# Loop-ordered information architecture for the agent usage guide. The guide
# walks an agent through one working loop — Orient (learn the surface) → Find
# (retrieve) → Synthesise (combine) → Remember (write back) → Check health
# (diagnose) → Escalate (hand to an operator). ``by_loop_group`` buckets the
# catalogue into these groups so the guide generator and any IA consumer read
# the same ordering without re-deriving it.
LOOP_GROUP_ORIENT = "Orient"
LOOP_GROUP_FIND = "Find"
LOOP_GROUP_SYNTHESISE = "Synthesise"
LOOP_GROUP_REMEMBER = "Remember"
LOOP_GROUP_CHECK_HEALTH = "Check health"
LOOP_GROUP_ESCALATE = "Escalate"

# The loop order the guide renders in — also the key order ``by_loop_group``
# returns, so a consumer iterating the mapping walks the loop in order.
LOOP_GROUP_ORDER: tuple[str, ...] = (
    LOOP_GROUP_ORIENT,
    LOOP_GROUP_FIND,
    LOOP_GROUP_SYNTHESISE,
    LOOP_GROUP_REMEMBER,
    LOOP_GROUP_CHECK_HEALTH,
    LOOP_GROUP_ESCALATE,
)

# category → loop-group map. Operator-only escalation rows (``escalate_via``
# set) always land in Escalate regardless of category (see
# :func:`_loop_group_for`); that rule is what splits the ``knowledge-write``
# category — agent-callable writes (ingest_chat / memory_write) fall through to
# Remember while operator-only writes (embed / store_crawl / …) route to
# Escalate. ``diagnostic-operator-only`` maps to Escalate too as a defensive
# fallback (its rows already carry ``escalate_via``).
_CATEGORY_TO_LOOP_GROUP: dict[str, str] = {
    CAP_CATEGORY_RETRIEVAL: LOOP_GROUP_FIND,
    CAP_CATEGORY_SYNTHESIS: LOOP_GROUP_SYNTHESISE,
    CAP_CATEGORY_AGENT: LOOP_GROUP_ORIENT,
    CAP_CATEGORY_KNOWLEDGE_WRITE: LOOP_GROUP_REMEMBER,
    CAP_CATEGORY_DIAGNOSTIC: LOOP_GROUP_CHECK_HEALTH,
    CAP_CATEGORY_CONFIGURATION: LOOP_GROUP_CHECK_HEALTH,
    CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY: LOOP_GROUP_ESCALATE,
}


def _loop_group_for(cap: Capability) -> str:
    """Return the loop-ordered IA group a capability belongs to.

    Operator-only escalation rows (``escalate_via`` set) always route to
    ``Escalate`` — that's the rule that splits the ``knowledge-write``
    category, sending agent-callable writes to ``Remember`` and operator-only
    writes to ``Escalate``. Every other row maps by its ``category`` via
    :data:`_CATEGORY_TO_LOOP_GROUP`.
    """
    if cap.escalate_via is not None:
        return LOOP_GROUP_ESCALATE
    return _CATEGORY_TO_LOOP_GROUP[cap.category]


# The agent-capability catalogue — the single source of truth for the kairix
# agent surface, promoted out of ``tool_capabilities()`` into importable typed
# data (PLA-317). CLI dispatch, the usage-guide generator, and the E2E harness
# read these rows directly (via :data:`CAPABILITIES_CATALOG` /
# :func:`agent_facing` / :func:`by_loop_group`) without booting the MCP server;
# ``tool_capabilities()`` projects them to the JSON introspection envelope. F99
# (usage-guide currency) AST-walks these ``_cap(...)`` rows. ``build_server``
# registers a per-row ``ToolBinding`` by walking this catalogue. Hand-maintained
# — F25 (capability-affordance) keeps it in sync with the CLI/MCP surface.
CAPABILITIES_CATALOG: tuple[Capability, ...] = (
    # Retrieval
    _cap(
        name="search",
        mcp_tool="search",
        cli="kairix search",
        category=CAP_CATEGORY_RETRIEVAL,
        when_to_use="Call before answering any factual question about prior work or decisions.",
    ),
    _cap(
        name="entity",
        mcp_tool="entity",
        cli="kairix entity",
        category=CAP_CATEGORY_RETRIEVAL,
        when_to_use="Look up a named person, organisation, or project across the knowledge store.",
    ),
    _cap(
        name="timeline",
        mcp_tool="timeline",
        cli="kairix timeline",
        category=CAP_CATEGORY_RETRIEVAL,
        when_to_use="Trace how a topic or project changed over time, in date order.",
    ),
    _cap(
        name="expand",
        mcp_tool="expand",
        cli="kairix expand",
        category=CAP_CATEGORY_RETRIEVAL,
        when_to_use="Pull the chunks surrounding a search hit instead of re-reading the whole document.",
    ),
    # Synthesis
    _cap(
        name="prep",
        mcp_tool="prep",
        cli="kairix prep",
        category=CAP_CATEGORY_SYNTHESIS,
        when_to_use="Pull a tiered context summary before a meeting or a task hand-off.",
    ),
    _cap(
        name="research",
        mcp_tool="research",
        cli="kairix research",
        category=CAP_CATEGORY_SYNTHESIS,
        when_to_use="Gather and synthesise everything the store knows about a broad question.",
    ),
    _cap(
        name=CONTRADICT_TOOL_NAME,
        mcp_tool=CONTRADICT_TOOL_NAME,
        cli="kairix contradict",
        category=CAP_CATEGORY_SYNTHESIS,
        when_to_use="Check new content for conflicts with what the store already knows.",
    ),
    _cap(
        name="brief",
        mcp_tool="brief",
        cli="kairix brief",
        category=CAP_CATEGORY_SYNTHESIS,
        when_to_use="Produce a session briefing that synthesises recent activity.",
    ),
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
        name="recommend",
        mcp_tool=RECOMMEND_CAPABILITIES_TOOL_NAME,
        cli="kairix recommend",
        category=CAP_CATEGORY_AGENT,
        when_to_use="Find the right tool, skill, or workflow when you are unsure which one fits a task.",
    ),
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
    # PR 1.4 / #420 — agent scope discovery + proposal
    _cap(
        name="onboard_scan",
        mcp_tool="onboard_scan",
        cli="kairix onboard scan",
        category=CAP_CATEGORY_CONFIGURATION,
    ),
    _cap(
        name="onboard_agent",
        mcp_tool="onboard_agent",
        cli="kairix onboard agent",
        category=CAP_CATEGORY_CONFIGURATION,
    ),
    # PR 1.5 / #420 — agent scope drift detection
    _cap(
        name="doctor_check_all",
        mcp_tool="doctor_check_all",
        cli="kairix doctor agent --all",
        category=CAP_CATEGORY_DIAGNOSTIC,
    ),
    _cap(
        name="doctor_check_agent",
        mcp_tool="doctor_check_agent",
        cli="kairix doctor agent --name",
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
    _cap(
        name="secrets_verify",
        mcp_tool="secrets_verify",
        cli="kairix secrets verify",
        category=CAP_CATEGORY_DIAGNOSTIC,
    ),
    _cap(
        name="dead_letter_status",
        mcp_tool="dead_letter_status",
        cli="kairix dead-letter status",
        category=CAP_CATEGORY_DIAGNOSTIC,
    ),
    _cap(
        name="caches_status",
        mcp_tool="caches_status",
        cli="kairix caches",
        category=CAP_CATEGORY_DIAGNOSTIC,
    ),
    _cap(name="warm", mcp_tool="warm", cli="kairix warm", category=CAP_CATEGORY_DIAGNOSTIC),
    # Probe search — capped MCP variant. The legacy ``kairix probe
    # search`` CLI was retired in v2026.6; the diagnostic registry
    # entry now names the Python-API entry point until the unified
    # ``kairix benchmark run --mode concurrent`` dispatcher lands.
    _cap(
        name="probe_search",
        mcp_tool="probe_search",
        cli="python -c 'from kairix.quality.probe import run_probe_search; ...'",
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
        cli="python -c 'from kairix.quality.soak import run_soak; ...'",
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
        cli="python -c 'from kairix.quality.probe import run_probe_burst; ...'",
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
        when_to_use="Save a chat transcript into the knowledge store for later recall.",
    ),
    _cap(
        name="facts_about",
        mcp_tool="facts_about",
        cli="kairix facts about",
        category=CAP_CATEGORY_RETRIEVAL,
        when_to_use="Recall the stored facts about a person, project, or topic.",
    ),
    # #472 — agent-facing memory write (same use case as `kairix remember`)
    _cap(
        name="memory_write",
        mcp_tool="memory_write",
        cli="kairix remember",
        category=CAP_CATEGORY_KNOWLEDGE_WRITE,
        when_to_use="Remember a fact or decision now so it can be recalled later.",
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
    _cap(
        name="maintenance_analyze",
        mcp_tool="maintenance_analyze",
        cli="kairix maintenance analyze",
        category=CAP_CATEGORY_DIAGNOSTIC,
    ),
)


def agent_facing() -> tuple[Capability, ...]:
    """Return the catalogue rows an agent can call directly.

    A row is agent-facing when it (a) exposes an MCP tool, (b) is not an
    operator-only escalation stub (``escalate_via`` unset), and (c) is not the
    ``recommend_capabilities`` recommender — that surface is gated behind the
    ``recommender`` feature flag, which defaults OFF, so it returns a disabled
    envelope and must not read as live (mirrors F99's exclusion of the
    recommender from the agent guide). CLI dispatch and the guide generator
    read this instead of re-deriving the filter.
    """
    return tuple(
        cap
        for cap in CAPABILITIES_CATALOG
        if cap.mcp_tool is not None and cap.escalate_via is None and cap.mcp_tool != RECOMMEND_CAPABILITIES_TOOL_NAME
    )


def by_loop_group() -> dict[str, tuple[Capability, ...]]:
    """Group the catalogue into the usage-guide's loop-ordered IA.

    Returns a mapping keyed in :data:`LOOP_GROUP_ORDER` (Orient → Find →
    Synthesise → Remember → Check health → Escalate); each value is the tuple
    of rows in that group, in catalogue order. Every row lands in exactly one
    group (see :func:`_loop_group_for`), so the six groups partition the whole
    catalogue — the guide generator renders the sections straight from this.
    """
    grouped: dict[str, list[Capability]] = {group: [] for group in LOOP_GROUP_ORDER}
    for cap in CAPABILITIES_CATALOG:
        grouped[_loop_group_for(cap)].append(cap)
    return {group: tuple(members) for group, members in grouped.items()}


def tool_capabilities() -> dict[str, Any]:
    """Return the full kairix capability catalogue for programmatic introspection.

    Per affordance pattern 4 (docs/architecture/operational-tests-design.md):
    AI-driven SRE agents call this to discover bindings rather than guess. Each
    entry tells the caller (a) the canonical name, (b) the MCP tool name if
    callable (None when CLI-only or escalation-only), (c) the CLI invocation,
    (d) the category, and (e) any MCP caps or escalation pointer.

    The rows come verbatim from :data:`CAPABILITIES_CATALOG` (each projected via
    :meth:`Capability.as_dict`), so the JSON envelope is byte-identical to the
    pre-PLA-317 hand-built dict. The catalogue is hand-maintained — F25
    (capability-affordance) keeps it in sync with the actual CLI dispatch + MCP
    registry.
    """
    return {
        "capabilities": [cap.as_dict() for cap in CAPABILITIES_CATALOG],
        "schema_version": "1",
        "see_also": [_RETRIEVAL_RUNBOOK, "docs/architecture/operational-tests-design.md"],
    }


# ---------------------------------------------------------------------------
# capabilities MCP tool — coupled to the catalogue, so its binding lives here
# rather than in a domain adapter module.
# ---------------------------------------------------------------------------

_CAPABILITIES_DESCRIPTION = (
    "Programmatic capability catalogue — every kairix capability with its "
    "MCP tool name, CLI command, category, and (for capped MCP variants) "
    "the agent-safe caps. AI-driven SRE agents call this to discover the "
    "surface instead of guessing. See affordance pattern 4."
)


def _make_capabilities(_ctx: RegistrationContext) -> Callable[..., Any]:
    def capabilities() -> dict[str, Any]:
        """Full kairix capability catalogue. Read-only. Identical to tool_capabilities()."""
        return tool_capabilities()

    return capabilities


_CAPABILITIES_BINDING = ToolBinding(
    name=CAPABILITIES_TOOL_NAME,
    description=_CAPABILITIES_DESCRIPTION,
    make=_make_capabilities,
    warm_gated=False,
)


# ---------------------------------------------------------------------------
# Catalogue-driven FastMCP registration.
# ---------------------------------------------------------------------------


def _all_bindings() -> dict[str, ToolBinding]:
    """Index every domain adapter's :class:`ToolBinding` by its registered name.

    The union of the per-domain ``BINDINGS`` tuples (plus the catalogue-coupled
    ``capabilities`` binding) is the registry :func:`build_server` looks each
    ``CAPABILITIES_CATALOG`` row up in.
    """
    bindings: dict[str, ToolBinding] = {}
    for group in (
        _retrieval.BINDINGS,
        _synthesis.BINDINGS,
        _orient.BINDINGS,
        _diagnostic.BINDINGS,
        _operator.BINDINGS,
        _ingest_chat.BINDINGS,
        _facts_about.BINDINGS,
        _memory_write.BINDINGS,
        (_CAPABILITIES_BINDING,),
    ):
        for binding in group:
            bindings[binding.name] = binding
    return bindings


def _register_binding(server: Any, binding: ToolBinding, ctx: RegistrationContext) -> None:
    """Register one binding's body closure on the FastMCP ``server``.

    Rebuilds the historical decorator stack from the binding's data: build the
    correctly-signed body via ``make(ctx)``, wrap in ``@warm_gate`` when the
    binding is warm-gated, then ``@async_tool_handler``, then
    ``@server.tool(description=...)`` (FastMCP falls back to the closure's
    docstring when ``description`` is ``None``).
    """
    body = binding.make(ctx)
    gated = warm_gate(body) if binding.warm_gated else body
    wrapped = async_tool_handler(gated)
    server.tool(description=binding.description)(wrapped)


def _register_from_catalogue(server: Any, ctx: RegistrationContext) -> None:
    """Register every ``CAPABILITIES_CATALOG`` row's tool on ``server``.

    Each row maps to exactly one binding by its ``mcp_tool`` (agent-callable)
    or ``escalate_via`` (operator-only stub) name — the catalogue is the single
    source of truth for the agent surface, so a new capability is registered by
    adding its catalogue row + a matching adapter binding, never by hand-writing
    a ``@server.tool`` def here.
    """
    bindings = _all_bindings()
    for cap in CAPABILITIES_CATALOG:
        # Every registrable row carries a non-None mcp_tool OR escalate_via;
        # ``cap.name`` is the never-None final fallback so the key type is str
        # (a name with no matching binding raises KeyError below — a loud
        # registration bug, never a silent skip).
        key = cap.mcp_tool or cap.escalate_via or cap.name
        _register_binding(server, bindings[key], ctx)


def build_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    readiness_check: Callable[[], bool] | None = None,
    mark_ready: Callable[[], None] | None = None,
    remember_deps: RememberDeps | None = None,
    facts_about_deps: FactsAboutDeps | None = None,
) -> Any:
    """Construct the FastMCP server with every kairix tool registered.

    Args:
        host: Bind address for SSE transport.
        port: Port for SSE transport.
        readiness_check: Optional cold-start gate. When supplied and it
                         returns False, retrieval tools return a canonical
                         retryable envelope instead of executing a lower-
                         quality or partially-initialised path.
        mark_ready: Optional callback to open the readiness gate after a
                    successful manual warm-up. Paired with ``readiness_check``
                    in long-running HTTP deployments.
        remember_deps: Optional ``RememberDeps`` injection seam for the
                       ``memory_write`` tool. Production leaves it ``None``
                       (the use case wires real config / paths / index step);
                       an integration test passes a tmp-path ``RememberDeps``
                       so the registered tool writes to a temp knowledge store
                       — the F1/F2-clean way to prove the cold-write path
                       through the live dispatch surface.
        facts_about_deps: Optional ``FactsAboutDeps`` injection seam for the
                       ``facts_about`` tool. Production leaves it ``None`` (the
                       tool resolves the real SQLite fact store + document
                       repository); an integration test passes fakes so it can
                       prove the cold-read path — fact + entity summary served
                       while kairix is still warming — through the live
                       dispatch surface (F1/F2-clean).

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
    ctx = RegistrationContext(
        readiness_check=readiness_check,
        mark_ready=mark_ready,
        remember_deps=remember_deps,
        facts_about_deps=facts_about_deps,
    )
    _register_from_catalogue(server, ctx)
    return server
