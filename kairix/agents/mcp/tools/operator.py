"""MCP tool adapters — operator-only escalation stubs.

These capabilities take minutes, mutate state, or are destructive recovery
actions. An agent that calls one receives a structured
``OperatorOnlyCapability`` envelope naming the exact CLI command to surface to
its admin — it never runs the workload. Each ``tool_<name>`` body just shapes
that escalation envelope via :func:`operator_only_envelope`. :data:`BINDINGS`
publishes the registered stubs so ``server.py`` registers them by walking
``CAPABILITIES_CATALOG`` (each row's ``escalate_via`` names the stub).
Behaviour is byte-identical to the pre-split server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


# ---------------------------------------------------------------------------
# Tool bodies — operator-only escalation envelopes.
# ---------------------------------------------------------------------------


def tool_soak_run(suite: str = "reflib", repeat: int = 3) -> dict[str, Any]:
    """Stub for the soak capability — operator-only, escalation envelope.

    Soak runs take minutes and stress the system under sustained load.
    Agents that hit this tool receive the exact CLI command and
    runbook pointer so they can escalate to an operator.
    """
    # The legacy ``kairix soak run`` CLI was retired in v2026.6; until the
    # unified ``kairix benchmark run --mode soak`` dispatcher lands (P3.c),
    # operators drive soak from the Python API. The envelope names the
    # canonical entry point and the runbook for context.
    return _operator_only_envelope(
        capability="soak run",
        operator_command=(
            f"python -c 'from kairix.quality.soak import run_soak; print(run_soak(suite=\"{suite}\", repeat={repeat}))'"
        ),
        reason="Soak runs take minutes and stress the system under sustained load. Agents must escalate.",
        expected_runtime_seconds=60 * repeat,
        see_also=[_RETRIEVAL_RUNBOOK],
    )


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
    # The legacy ``kairix probe burst`` CLI was retired in v2026.6; operators
    # drive the burst probe directly from the Python API.
    return _operator_only_envelope(
        capability="probe burst",
        operator_command=(
            f"python -c 'from kairix.quality.probe import run_probe_burst; "
            f'print(run_probe_burst(suite="{suite}", total_queries={total_queries}, '
            f"peak_concurrency={peak_concurrency}))'"
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
    "cc-pair escalation — Wave D topology v2 cc_pair lifecycle (list / create / "
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
    ToolBinding(name="probe_burst", description=_PROBE_BURST_DESCRIPTION, make=_make_probe_burst, warm_gated=False),
    ToolBinding(name="probe_config", description=_PROBE_CONFIG_DESCRIPTION, make=_make_probe_config, warm_gated=False),
    ToolBinding(
        name="benchmark_run", description=_BENCHMARK_RUN_DESCRIPTION, make=_make_benchmark_run, warm_gated=False
    ),
    ToolBinding(name="embed", description=_EMBED_DESCRIPTION, make=_make_embed, warm_gated=False),
    ToolBinding(name="store_crawl", description=_STORE_CRAWL_DESCRIPTION, make=_make_store_crawl, warm_gated=False),
    ToolBinding(
        name="embed_rebuild_fts",
        description=_EMBED_REBUILD_FTS_DESCRIPTION,
        make=_make_embed_rebuild_fts,
        warm_gated=False,
    ),
    ToolBinding(name="cc_pair", description=_CC_PAIR_DESCRIPTION, make=_make_cc_pair, warm_gated=False),
)
