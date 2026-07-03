"""MCP tool adapters — operator-only escalation stubs.

These capabilities take minutes, mutate state, or are destructive recovery
actions. An agent that calls one receives a structured
``OperatorOnlyCapability`` envelope naming the exact CLI command to surface to
its admin — it never runs the workload. Every escalation envelope is shaped by
ONE code path — :func:`_escalation_envelope` — keyed on the catalogue's
``escalate_via`` name: the per-capability label, reason, operator command, and
runtime estimate live as data in :data:`_ESCALATION_SPECS`, so the eight
``tool_<name>`` stubs each delegate in one line instead of carrying their own
copy of the envelope construction. :data:`BINDINGS` publishes the registered
stubs so ``server.py`` registers them by walking ``CAPABILITIES_CATALOG`` (each
row's ``escalate_via`` names the stub). Behaviour is byte-identical to the
pre-split server.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from kairix.agents.mcp.tools._common import (
    RETRIEVAL_RUNBOOK as _RETRIEVAL_RUNBOOK,
)
from kairix.agents.mcp.tools._common import (
    RegistrationContext,
    ToolBinding,
)
from kairix.agents.mcp.tools._common import (
    operator_only_envelope as _operator_only_envelope,
)

__all__ = [
    "BINDINGS",
    "tool_benchmark_run",
    "tool_cc_pair",
    "tool_embed",
    "tool_embed_rebuild_fts",
    "tool_probe_burst",
    "tool_probe_config",
    "tool_soak_run",
    "tool_store_crawl",
]

_T = TypeVar("_T")

# Escalation-command wire names (the catalogue's ``escalate_via`` values) —
# declared once so the spec-table key, the ``_escalation_envelope`` call, and
# the ``ToolBinding`` name share one edit site (F17: coupling made explicit).
_ESC_PROBE_BURST = "probe_burst"
_ESC_PROBE_CONFIG = "probe_config"
_ESC_BENCHMARK_RUN = "benchmark_run"
_ESC_STORE_CRAWL = "store_crawl"
_ESC_EMBED_REBUILD_FTS = "embed_rebuild_fts"


# ---------------------------------------------------------------------------
# Escalation-envelope spec table — the single source of per-capability data the
# one shared code path reads, keyed on the catalogue's ``escalate_via`` name.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EscalationSpec:
    """Per-capability escalation metadata read by :func:`_escalation_envelope`.

    The capability label + escalation reason are static per capability, while
    ``command`` and ``runtime`` are resolvers that read the caller's params
    (soak repeat count, embed limit, cc-pair verb, …) so the parametrized stubs
    still forward their arguments into the operator command / runtime estimate.
    Static capabilities use :func:`_const`. ``see_also`` is fixed to the
    retrieval runbook for every escalation, so the OperatorOnlyCapability
    envelope is constructed in exactly one place rather than eight copies.
    """

    capability: str
    reason: str
    command: Callable[[Mapping[str, Any]], str]
    runtime: Callable[[Mapping[str, Any]], int]


def _const(value: _T) -> Callable[[Mapping[str, Any]], _T]:
    """Return a resolver that ignores the params and yields a fixed value.

    Used for the escalation stubs whose operator command / runtime estimate
    carries no argument (probe-config, store crawl, rebuild-fts, …).
    """

    def resolver(_params: Mapping[str, Any]) -> _T:
        return value

    return resolver


def _soak_run_command(params: Mapping[str, Any]) -> str:
    """Operator command for the retired ``kairix soak run`` — Python-API one-liner."""
    suite = params["suite"]
    repeat = params["repeat"]
    return f"python -c 'from kairix.quality.soak import run_soak; print(run_soak(suite=\"{suite}\", repeat={repeat}))'"


def _probe_burst_command(params: Mapping[str, Any]) -> str:
    """Operator command for the retired ``kairix probe burst`` — Python-API one-liner."""
    suite = params["suite"]
    total_queries = params["total_queries"]
    peak_concurrency = params["peak_concurrency"]
    return (
        f"python -c 'from kairix.quality.probe import run_probe_burst; "
        f'print(run_probe_burst(suite="{suite}", total_queries={total_queries}, '
        f"peak_concurrency={peak_concurrency}))'"
    )


def _embed_command(params: Mapping[str, Any]) -> str:
    """Operator command for ``kairix embed`` — optional ``--limit`` suffix."""
    limit = params["limit"]
    flag = "" if limit == 0 else f" --limit {limit}"
    return f"kairix embed{flag}"


def _cc_pair_command(params: Mapping[str, Any]) -> str:
    """Operator command for ``kairix cc-pair`` — ``--id <id>`` for mutating verbs."""
    verb = params["verb"]
    suffix = "" if verb == "list" else " --id <id>"
    return f"kairix cc-pair {verb}{suffix}"


_ESCALATION_SPECS: dict[str, _EscalationSpec] = {
    "soak_run": _EscalationSpec(
        capability="soak run",
        # The legacy ``kairix soak run`` CLI was retired in v2026.6; until the
        # unified ``kairix benchmark run --mode soak`` dispatcher lands (P3.c),
        # operators drive soak from the Python API named in the command.
        reason="Soak runs take minutes and stress the system under sustained load. Agents must escalate.",
        command=_soak_run_command,
        runtime=lambda params: 60 * int(params["repeat"]),
    ),
    _ESC_PROBE_BURST: _EscalationSpec(
        capability="probe burst",
        reason=(
            "Probe burst injects queries as fast as possible against the "
            "production retrieval pipeline; load-generating by design. Agents must escalate."
        ),
        command=_probe_burst_command,
        runtime=lambda params: max(30, int(params["total_queries"]) // 5),
    ),
    _ESC_PROBE_CONFIG: _EscalationSpec(
        capability="probe-config",
        reason=(
            "probe-config runs an embed workload against the operator's configured "
            "provider endpoint and surfaces config-tuning advice the operator applies. "
            "Agents must escalate."
        ),
        command=_const("kairix probe-config"),
        runtime=_const(60),
    ),
    _ESC_BENCHMARK_RUN: _EscalationSpec(
        capability="benchmark run",
        reason="Benchmark runs take minutes and load the system; agents must escalate.",
        command=lambda params: f"kairix benchmark run --suite {params['suite']}",
        runtime=_const(120),
    ),
    "embed": _EscalationSpec(
        capability="embed",
        reason="Embed mutates the vector index and is metered against an Azure quota; agents must escalate.",
        command=_embed_command,
        runtime=_const(300),
    ),
    _ESC_STORE_CRAWL: _EscalationSpec(
        capability="store crawl",
        reason="Crawl mutates Neo4j entity graph and takes minutes; agents must escalate.",
        command=_const("kairix store crawl"),
        runtime=_const(300),
    ),
    _ESC_EMBED_REBUILD_FTS: _EscalationSpec(
        capability="embed rebuild-fts",
        reason="rebuild-fts drops and re-creates the documents_fts table; agents must escalate.",
        command=_const("kairix embed rebuild-fts"),
        runtime=_const(60),
    ),
    "cc_pair": _EscalationSpec(
        capability="cc-pair",
        reason=(
            "cc-pair mutates the topology cc_pair lifecycle (status state machine "
            "+ topology_cc_pairs rows); operators run via the CLI so transitions are "
            "audited. Agents read state via `tool_features_status(topology=True)`."
        ),
        command=_cc_pair_command,
        runtime=_const(5),
    ),
}


def _escalation_envelope(escalate_via: str, **params: Any) -> dict[str, Any]:
    """Shape the OperatorOnlyCapability envelope for one escalation stub.

    The single code path behind all eight operator-only stubs: it reads the
    :class:`_EscalationSpec` keyed by the catalogue's ``escalate_via`` name and
    builds the envelope through :func:`operator_only_envelope`, resolving the
    per-call operator command + runtime estimate from ``params``. Each
    ``tool_<name>`` stub is a one-line delegation to this, so the escalation
    envelope (including its fixed retrieval-runbook ``see_also``) is constructed
    in exactly one place.
    """
    spec = _ESCALATION_SPECS[escalate_via]
    return _operator_only_envelope(
        capability=spec.capability,
        operator_command=spec.command(params),
        reason=spec.reason,
        expected_runtime_seconds=spec.runtime(params),
        see_also=[_RETRIEVAL_RUNBOOK],
    )


# ---------------------------------------------------------------------------
# Tool bodies — each a one-line delegation to the shared escalation code path,
# keeping its documented signature + defaults as the operator-visible contract.
# ---------------------------------------------------------------------------


def tool_soak_run(suite: str = "reflib", repeat: int = 3) -> dict[str, Any]:
    """Stub for the soak capability — operator-only, escalation envelope.

    Soak runs take minutes and stress the system under sustained load.
    Agents that hit this tool receive the exact CLI command and runbook
    pointer so they can escalate to an operator.
    """
    return _escalation_envelope("soak_run", suite=suite, repeat=repeat)


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
    return _escalation_envelope(
        _ESC_PROBE_BURST,
        suite=suite,
        total_queries=total_queries,
        peak_concurrency=peak_concurrency,
    )


def tool_probe_config() -> dict[str, Any]:
    """Stub for the probe-config capability — operator-only, escalation envelope.

    ``kairix probe-config`` runs a small representative embed workload against
    the operator's configured provider to verify the setup and emit tuning
    recommendations. It is load-generating against the provider's real endpoint
    and surfaces config-shaped advice an operator (not an agent) applies; agents
    must escalate.
    """
    return _escalation_envelope(_ESC_PROBE_CONFIG)


def tool_benchmark_run(suite: str = "reflib") -> dict[str, Any]:
    """Stub for the benchmark capability — operator-only, escalation envelope."""
    return _escalation_envelope(_ESC_BENCHMARK_RUN, suite=suite)


def tool_embed(limit: int = 0) -> dict[str, Any]:
    """Stub for the embed capability — operator-only, mutates state."""
    return _escalation_envelope("embed", limit=limit)


def tool_store_crawl() -> dict[str, Any]:
    """Stub for the store-crawl capability — operator-only, mutates Neo4j."""
    return _escalation_envelope(_ESC_STORE_CRAWL)


def tool_embed_rebuild_fts() -> dict[str, Any]:
    """Stub for the FTS-rebuild capability — operator-only, destructive recovery action."""
    return _escalation_envelope(_ESC_EMBED_REBUILD_FTS)


def tool_cc_pair(verb: str = "list") -> dict[str, Any]:
    """Stub for the cc-pair capability — Wave D lifecycle is operator-owned.

    cc_pair create / pause / resume / delete mutate the topology state
    machine (kairix.core.connectors.cc_pair F57 lifecycle); agents must
    escalate to an operator running `kairix cc-pair <verb>` so the
    transition gets logged + observable via `kairix features status
    --topology`. The read-only ``list`` verb is also returned through
    the escalation envelope so agents get one consistent shape per
    capability.
    """
    return _escalation_envelope("cc_pair", verb=verb)


# ---------------------------------------------------------------------------
# Registration bindings — one per registered escalation stub in this domain.
# ---------------------------------------------------------------------------

_SOAK_RUN_DESCRIPTION = (
    "Soak test escalation — soak runs are multi-minute load tests. Returns the "
    "OperatorOnlyCapability envelope pointing the operator at the "
    "`kairix.quality.soak.run_soak` Python API (the legacy `kairix soak run` CLI was retired in v2026.6)."
)

_PROBE_BURST_DESCRIPTION = (
    "Burst-probe escalation — load-generating throughput-drop probe. Returns the "
    "OperatorOnlyCapability envelope pointing the operator at the "
    "`kairix.quality.probe.run_probe_burst` Python API "
    "(the legacy `kairix probe burst` CLI was retired in v2026.6)."
)

_PROBE_CONFIG_DESCRIPTION = (
    "Probe-config escalation — runs an embed workload against the configured "
    "provider endpoint and emits tuning advice the operator applies. Returns the "
    "OperatorOnlyCapability envelope with the exact `kairix probe-config` command."
)

_BENCHMARK_RUN_DESCRIPTION = (
    "Benchmark escalation — benchmark runs take minutes and load the system. "
    "Returns the OperatorOnlyCapability envelope with the exact `kairix benchmark run` command."
)

_EMBED_DESCRIPTION = (
    "Embed escalation — embed mutates the vector index against an Azure quota. "
    "Returns the OperatorOnlyCapability envelope with the exact `kairix embed` command."
)

_STORE_CRAWL_DESCRIPTION = (
    "Store-crawl escalation — mutates Neo4j entity graph. Returns the "
    "OperatorOnlyCapability envelope with the exact `kairix store crawl` command."
)

_EMBED_REBUILD_FTS_DESCRIPTION = (
    "FTS-rebuild escalation — drops + re-creates the documents_fts table. "
    "Returns the OperatorOnlyCapability envelope with the exact recovery command."
)

_CC_PAIR_DESCRIPTION = (
    "cc-pair escalation — Wave D topology cc_pair lifecycle (list / create / "
    "pause / resume / delete) mutates the state machine; agents must escalate. "
    "Returns the OperatorOnlyCapability envelope with the exact `kairix cc-pair` command."
)


def _make_soak_run(_ctx: RegistrationContext) -> Callable[..., Any]:
    def soak_run(suite: str = "reflib", repeat: int = 3) -> dict[str, Any]:
        """Operator-only soak test. Returns escalation envelope for the agent's admin."""
        return tool_soak_run(suite=suite, repeat=repeat)

    return soak_run


def _make_probe_burst(_ctx: RegistrationContext) -> Callable[..., Any]:
    def probe_burst(
        suite: str = "reflib",
        total_queries: int = 200,
        peak_concurrency: int = 20,
    ) -> dict[str, Any]:
        """Operator-only burst probe. Returns escalation envelope."""
        return tool_probe_burst(suite=suite, total_queries=total_queries, peak_concurrency=peak_concurrency)

    return probe_burst


def _make_probe_config(_ctx: RegistrationContext) -> Callable[..., Any]:
    def probe_config() -> dict[str, Any]:
        """Operator-only probe-config. Returns escalation envelope."""
        return tool_probe_config()

    return probe_config


def _make_benchmark_run(_ctx: RegistrationContext) -> Callable[..., Any]:
    def benchmark_run(suite: str = "reflib") -> dict[str, Any]:
        """Operator-only benchmark run. Returns escalation envelope."""
        return tool_benchmark_run(suite=suite)

    return benchmark_run


def _make_embed(_ctx: RegistrationContext) -> Callable[..., Any]:
    def embed(limit: int = 0) -> dict[str, Any]:
        """Operator-only embed. Returns escalation envelope."""
        return tool_embed(limit=limit)

    return embed


def _make_store_crawl(_ctx: RegistrationContext) -> Callable[..., Any]:
    def store_crawl() -> dict[str, Any]:
        """Operator-only graph crawl. Returns escalation envelope."""
        return tool_store_crawl()

    return store_crawl


def _make_embed_rebuild_fts(_ctx: RegistrationContext) -> Callable[..., Any]:
    def embed_rebuild_fts() -> dict[str, Any]:
        """Operator-only FTS recovery. Returns escalation envelope."""
        return tool_embed_rebuild_fts()

    return embed_rebuild_fts


def _make_cc_pair(_ctx: RegistrationContext) -> Callable[..., Any]:
    def cc_pair(verb: str = "list") -> dict[str, Any]:
        """Operator-only cc_pair lifecycle. Returns escalation envelope."""
        return tool_cc_pair(verb=verb)

    return cc_pair


BINDINGS: tuple[ToolBinding, ...] = (
    ToolBinding(name="soak_run", description=_SOAK_RUN_DESCRIPTION, make=_make_soak_run, warm_gated=False),
    ToolBinding(name=_ESC_PROBE_BURST, description=_PROBE_BURST_DESCRIPTION, make=_make_probe_burst, warm_gated=False),
    ToolBinding(
        name=_ESC_PROBE_CONFIG, description=_PROBE_CONFIG_DESCRIPTION, make=_make_probe_config, warm_gated=False
    ),
    ToolBinding(
        name=_ESC_BENCHMARK_RUN, description=_BENCHMARK_RUN_DESCRIPTION, make=_make_benchmark_run, warm_gated=False
    ),
    ToolBinding(name="embed", description=_EMBED_DESCRIPTION, make=_make_embed, warm_gated=False),
    ToolBinding(name=_ESC_STORE_CRAWL, description=_STORE_CRAWL_DESCRIPTION, make=_make_store_crawl, warm_gated=False),
    ToolBinding(
        name=_ESC_EMBED_REBUILD_FTS,
        description=_EMBED_REBUILD_FTS_DESCRIPTION,
        make=_make_embed_rebuild_fts,
        warm_gated=False,
    ),
    ToolBinding(name="cc_pair", description=_CC_PAIR_DESCRIPTION, make=_make_cc_pair, warm_gated=False),
)
