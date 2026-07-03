"""MCP tool adapters — diagnostic domain (read-only kairix state for agents to
introspect, plus the warm entry-point and the agent-safe probe surface).

Tools here exist to diagnose kairix — several must remain callable *while cold*
so operators can diagnose the cold state itself. Each ``tool_<name>`` body is a
thin adapter over the same Python API the matching CLI subcommand calls, so
CLI + MCP return byte-identical envelopes. :data:`BINDINGS` publishes the
registered tools so ``server.py`` registers this surface by walking
``CAPABILITIES_CATALOG``. Behaviour is byte-identical to the pre-split server.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kairix.agents.mcp.cold_start import warm_retrieval_stack
from kairix.agents.mcp.tools._common import (
    MCP_PROBE_CONCURRENCY_CAP,
    MCP_PROBE_QUERIES_CAP,
    RegistrationContext,
    ToolBinding,
)
from kairix.agents.mcp.tools._common import (
    RETRIEVAL_RUNBOOK as _RETRIEVAL_RUNBOOK,
)
from kairix.agents.mcp.tools._common import (
    operator_only_envelope as _operator_only_envelope,
)

logger = logging.getLogger(__name__)

# Module-import time — the MCP process's effective birth moment for the
# operator-facing ``process_uptime_s`` field on ``tool_caches_status``.
# Captured once at module import so every subsequent call reports
# wall-clock seconds since the long-running process started.
_PROCESS_STARTED_AT_MONOTONIC: float = time.monotonic()

__all__ = [
    "BINDINGS",
    "tool_caches_status",
    "tool_dead_letter_status",
    "tool_doctor_check_agent",
    "tool_doctor_check_all",
    "tool_features_status",
    "tool_maintenance_analyze",
    "tool_onboard_agent",
    "tool_onboard_check",
    "tool_onboard_scan",
    "tool_probe_search",
    "tool_warm",
    "tool_worker_status",
]


# ---------------------------------------------------------------------------
# Tool bodies — pure Python, no mcp dependency.
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


def tool_onboard_scan(
    memory_root: str,
    workspace_root: str = "",
) -> dict[str, Any]:
    """Discover agent scopes on disk and return them as an envelope.

    Mirrors ``kairix onboard scan --json``. Wraps
    :func:`kairix.agents.onboarding.scanner.scan_for_agents` so the
    CLI + MCP return byte-identical envelopes for the same disk state.

    Never raises — disk IO failures collapse into an empty ``agents``
    list with the exception string preserved on ``error``.
    """
    from kairix.agents.onboarding.cli import scope_to_envelope
    from kairix.agents.onboarding.scanner import scan_for_agents

    try:
        scopes = scan_for_agents(
            memory_root=Path(memory_root),
            workspace_root=Path(workspace_root) if workspace_root else None,
        )
        return {
            "agents": [scope_to_envelope(s) for s in scopes],
            "error": "",
        }
    except Exception as exc:
        logger.warning("tool_onboard_scan failed: %s", exc, exc_info=True)
        return {
            "agents": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def tool_onboard_agent(
    agent_name: str,
    memory_root: str,
    workspace_root: str = "",
    harness: str = "",
) -> dict[str, Any]:
    """Discover surfaces for one named agent and return as an envelope.

    Mirrors ``kairix onboard agent --name <name> --json``. Returns
    ``{"agent": None, "error": "..."}`` when the agent has no detector
    matches AND no .md files at ``memory_root/<name>`` — never raises.
    """
    from kairix.agents.onboarding.cli import scope_to_envelope
    from kairix.agents.onboarding.scanner import discover_single_agent

    try:
        scope = discover_single_agent(
            agent_name,
            memory_root=Path(memory_root),
            workspace_root=Path(workspace_root) if workspace_root else None,
            harness=harness or None,
        )
        return {"agent": scope_to_envelope(scope), "error": ""}
    except ValueError as exc:
        # ValueError is the documented "no proposal" signal; surface
        # the agent name so callers can branch on it.
        return {"agent": None, "error": str(exc)}
    except Exception as exc:
        logger.warning("tool_onboard_agent failed: %s", exc, exc_info=True)
        return {"agent": None, "error": f"{type(exc).__name__}: {exc}"}


def tool_doctor_check_all(
    *,
    config: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Re-validate every configured agent scope against disk state.

    Mirrors ``kairix doctor agent --all --json`` — same Python API
    (``doctor_check_all`` + ``report_to_envelope``) backs both
    surfaces, so CLI and MCP return byte-identical envelopes for the
    same configured + on-disk state.

    Never raises — disk-IO errors collapse into per-surface issues.
    """
    from kairix.agents.onboarding.doctor import doctor_check_all
    from kairix.agents.onboarding.doctor_cli import report_to_envelope

    try:
        report = doctor_check_all(config=config)
        return report_to_envelope(report)
    except Exception as exc:
        logger.warning("tool_doctor_check_all failed: %s", exc, exc_info=True)
        return {
            "agents": [],
            "overall": "error",
            "summary_text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def tool_doctor_check_agent(
    agent_name: str,
    *,
    config: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Re-validate a single configured agent scope against disk state.

    Mirrors ``kairix doctor agent --name <name> --json``. Never raises
    — unknown agents collapse into an :class:`AgentHealth` with
    ``overall="error"`` and the error captured in ``issues``.
    """
    from kairix.agents.onboarding.doctor import doctor_check_agent
    from kairix.agents.onboarding.doctor_cli import agent_health_to_envelope

    try:
        health = doctor_check_agent(agent_name, config=config)
        return {"agent": agent_health_to_envelope(health), "error": ""}
    except Exception as exc:
        logger.warning("tool_doctor_check_agent failed: %s", exc, exc_info=True)
        return {"agent": None, "error": f"{type(exc).__name__}: {exc}"}


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


def _default_topology_db_path() -> Path:
    """Production callable that returns the configured kairix SQLite path."""
    from kairix.paths import db_path

    return db_path()


def tool_features_status(
    topology: bool = False,
    *,
    read_db_path: Callable[[], Path] = _default_topology_db_path,
) -> dict[str, Any]:
    """Per F53 + the feature-flag-architecture spec §3.5, agents introspect
    the live flag state through this tool. Thin adapter — delegates to
    :func:`kairix.core.features.status` so CLI and MCP stay aligned and
    the returned envelope matches ``kairix features status --json``.

    ``topology=True`` extends the envelope with a ``topology``
    key carrying the Wave D diagnostics (declared cc_pairs +
    per-actor scope-profile resolution). Default-off so existing agents
    see byte-identical pre-Wave-D output.

    ``read_db_path`` is the unit-test DI seam: leaving it ``None``
    routes through the production :func:`kairix.paths.db_path` resolver;
    tests pass a callable returning a tmp_path so the topology read
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
        if topology:
            envelope["topology"] = _read_topology_diagnostics_for_mcp(read_db_path)
        return envelope
    except Exception as exc:
        logger.warning("tool_features_status failed: %s", exc, exc_info=True)
        return {
            "flags": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _read_topology_diagnostics_for_mcp(
    read_db_path: Callable[[], Path] = _default_topology_db_path,
) -> dict[str, Any]:
    """Read the Wave D topology diagnostics for the MCP envelope.

    Isolated helper so :func:`tool_features_status` stays under the
    F16 cognitive-complexity ceiling AND the topology read can
    degrade independently (a missing topology schema returns the
    zero-snapshot rather than crashing the whole MCP envelope).

    ``read_db_path`` is the DI seam — production callers leave it
    ``None`` to delegate to :func:`kairix.paths.db_path`; tests pass a
    tmp-path-returning callable.
    """
    import sqlite3
    from contextlib import closing

    from kairix.core.features.topology_status import (
        build_topology_diagnostics,
        render_topology_json,
    )

    resolved = read_db_path()

    try:
        conn = sqlite3.connect(str(resolved))
        with closing(conn):
            diag = build_topology_diagnostics(conn)
        return render_topology_json(diag)
    except sqlite3.Error as exc:
        logger.warning("topology diagnostics read failed: %s", exc, exc_info=True)
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


def _default_dead_letter_db_path() -> Path:
    """Production callable that returns the configured kairix SQLite path."""
    from kairix.paths import db_path

    return db_path()


def tool_dead_letter_status(
    source_name: str | None = None,
    *,
    read_db_path: Callable[[], Path] = _default_dead_letter_db_path,
) -> dict[str, Any]:
    """Per-source dead-letter triage envelope.

    Mirrors ``kairix dead-letter status --json``. Agents call this to
    decide whether the operator should run a re-extract or whether the
    failure class needs an upstream code fix. Returns the same shape
    documented in the dispatch brief: ``{total, per_source: [...]}``.

    On any exception, surfaces a typed error string and an empty
    ``per_source`` list so the agent can decide whether to fall back
    or escalate.

    ``read_db_path`` is the unit-test DI seam: leaving it at the
    default routes through :func:`kairix.paths.db_path`; tests pass a
    callable returning a tmp_path so the read hits a sandbox.
    """
    import sqlite3 as _sqlite3
    from contextlib import closing as _closing

    from kairix.core.observability.dead_letter_status import (
        build_status as _build_status,
    )
    from kairix.core.observability.dead_letter_status import (
        render_json as _render_json,
    )

    try:
        resolved = read_db_path()
        conn = _sqlite3.connect(str(resolved))
        with _closing(conn):
            report = _build_status(conn, source_name=source_name)
        envelope = _render_json(report)
        envelope["error"] = ""
        return envelope
    except Exception as exc:
        logger.warning("tool_dead_letter_status failed: %s", exc, exc_info=True)
        return {
            "total": 0,
            "per_source": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def tool_caches_status() -> dict[str, Any]:
    """Return per-cache stats for every TTL LRU in this MCP process.

    Wraps the same ``_collect_*`` collectors used by ``kairix caches``
    CLI in-process mode, but executed inside the MCP server's address
    space so the returned stats reflect the warm long-lived process
    (the CLI's freshly-spawned process always sees zeros — this tool
    is how operators see real cache effectiveness).

    PR 3.1 / #422 — paired with the CLI dispatcher routing so
    ``kairix caches`` shows MCP-side cache state by default.

    Envelope shape::

        {
            "caches": [
                {"name": str, "size": int, "hits": int, "misses": int,
                 "evictions": int, "hit_rate_pct": float},
                ...
            ],
            "process_pid": int,        # operator sanity-check that this is the MCP process
            "process_uptime_s": float, # how long this MCP process has been up
        }
    """
    from kairix.quality.probe.caches_cli import (
        _collect_all_rows,
        caches_rows_to_envelope,
    )

    rows = _collect_all_rows()
    envelope = caches_rows_to_envelope(rows)
    envelope["process_pid"] = os.getpid()
    envelope["process_uptime_s"] = round(time.monotonic() - _PROCESS_STARTED_AT_MONOTONIC, 3)
    return envelope


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
        # The legacy ``kairix probe search`` CLI was retired in v2026.6;
        # operators drive the probe directly from the Python API until the
        # unified ``kairix benchmark run --mode concurrent`` dispatcher
        # lands (P3.b).
        return _operator_only_envelope(
            capability="probe search (above cap)",
            operator_command=(
                f"python -c 'from kairix.quality.probe import run_probe_search; "
                f'print(run_probe_search(suite="{suite}", queries={queries}, '
                f"concurrency={concurrency}, seed={seed}))'"
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


def tool_maintenance_analyze(
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run ``ANALYZE`` on the kairix index DB and return the result envelope.

    Operator-callable diagnostic that refreshes ``sqlite_stat1`` so the
    query planner picks the right index for hot-path queries (#376).
    Mirrors ``kairix maintenance analyze`` — same envelope shape.

    Parameters
    ----------
    db_path:
        Optional path to the SQLite index. ``None`` resolves via
        :func:`kairix.paths.db_path`. Tests pass an explicit ``tmp_path``
        so the call is F2-clean (no env-var manipulation).

    Returns
    -------
    dict
        Success: ``{"analyze_ran", "reason", "rows_analyzed",
        "previous_doc_count", "elapsed_ms", "plan_before", "plan_after",
        "sample_query", "error": ""}``. The ``error`` key is always
        present (empty string on success) so agents can branch on it
        consistently.
        Failure: ``{"error": "<Name>", "detail": "...", ...}``.
    """
    try:
        import sqlite3 as _sqlite3

        from kairix.core.maintenance.cli import build_analyze_envelope

        if db_path is None:
            from kairix.paths import db_path as resolved_db_path

            db_path = resolved_db_path()

        db = _sqlite3.connect(str(db_path))
        try:
            # Same use case the CLI ``kairix maintenance analyze`` calls, so
            # both surfaces render byte-identical envelope content; the MCP
            # contract adds the always-present ``error`` key on top.
            envelope = build_analyze_envelope(db)
        finally:
            db.close()

        return {**envelope, "error": ""}
    except Exception as exc:
        logger.warning("tool_maintenance_analyze failed: %s", exc, exc_info=True)
        return {
            "analyze_ran": False,
            "rows_analyzed": 0,
            "elapsed_ms": 0.0,
            "error": type(exc).__name__,
            "detail": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# Registration bindings — one per registered MCP tool in this domain.
# ---------------------------------------------------------------------------

_ONBOARD_CHECK_DESCRIPTION = (
    "Run the kairix deployment health probes. Call when search seems degraded, "
    "before triaging 'I expected more results', or after a config change. "
    "Returns {passed, total, fully_passed, failures[]} — same shape as `kairix onboard check --json`."
)

_ONBOARD_SCAN_DESCRIPTION = (
    "Discover agent scopes on disk under memory_root and propose "
    "`agents:` config blocks for kairix.config.yaml. Read-only. "
    "Identical envelope to `kairix onboard scan --json`. Call this "
    "during first-time onboarding or when adding a new agent to "
    "an existing kairix install."
)

_ONBOARD_AGENT_DESCRIPTION = (
    "Discover surfaces for one named agent — single-target counterpart "
    "to `onboard_scan`. Returns {agent: {...}, error: ''} or "
    "{agent: None, error: '<why>'} when nothing matches. Read-only."
)

_DOCTOR_CHECK_ALL_DESCRIPTION = (
    "Re-validate every configured agent scope against disk state. "
    "Returns drift (missing dirs, stale memory, glob misses, ambiguous "
    "cross-agent overlap) before agents hit it. Read-only. Identical "
    "envelope to `kairix doctor agent --all --json`. Call this after "
    "changing kairix.config.yaml or rotating an agent's memory tree."
)

_DOCTOR_CHECK_AGENT_DESCRIPTION = (
    "Re-validate one configured agent's scope against disk state — "
    "single-target counterpart to `doctor_check_all`. Returns "
    "{agent: {...}, error: ''} with the per-surface health probe. "
    "Read-only."
)

_WORKER_STATUS_DESCRIPTION = (
    "Read the kairix-worker state file. Call to verify the embed/maintenance loop is running. "
    "Returns the worker's phase, counters, last-run timestamp, and last-error string."
)

_FEATURES_STATUS_DESCRIPTION = (
    "List the registered kairix feature flags + their effective values. "
    "Use to self-introspect what's enabled before relying on flag-gated behaviour. "
    "Read-only. Identical envelope to `kairix features status --json`."
)

_SECRETS_VERIFY_DESCRIPTION = (
    "Operator-facing credential preflight. Walks every kairix-bound secret "
    "(LLM, embed, Neo4j, every connector) and reports which canonical KV "
    "names resolve, which resolve via a deprecated legacy alias, and which "
    "are MISSING. Never returns secret VALUES — only canonical names + "
    "resolution status. Use when an agent or operator wants to know "
    "'is auth healthy on this deployment?' without docker exec access. "
    "Read-only. Identical envelope to `kairix secrets verify --json`."
)

_DEAD_LETTER_STATUS_DESCRIPTION = (
    "Operator-facing dead-letter triage view. Returns per-source counts, "
    "failure-class buckets (best-effort regex on last_error), "
    "MIME breakdown (LEFT JOIN on bronze_records), and the oldest five failures. "
    "Read-only. Identical envelope to `kairix dead-letter status --json`."
)

_CACHES_STATUS_DESCRIPTION = (
    "Per-cache stats for every TTL LRU in this MCP process. Use to see "
    "how effective the warm caches are after a session of agent work. "
    "Includes process_pid + process_uptime_s so operators confirm the "
    "envelope reflects the warm MCP process, not a freshly-spawned CLI. "
    "Read-only. Identical envelope to `kairix caches --json` when routed "
    "through warm MCP."
)

_WARM_DESCRIPTION = (
    "Warm kairix retrieval caches + pay factory-init costs. Retryable cold-start "
    "affordance: first call constructs the SearchPipeline and runs a tiny read-only "
    "probe; agents and entrypoint scripts call this at session start and retry if "
    "cold-start is reported (ready=False). Idempotent — subsequent calls are sub-ms. "
    "Expected p99: 120s warm, 120s cold. Recommended client timeout: 180s."
)

_PROBE_SEARCH_DESCRIPTION = (
    "Concurrent-load latency probe — capped agent-safe surface "
    f"(queries<={MCP_PROBE_QUERIES_CAP}, concurrency<={MCP_PROBE_CONCURRENCY_CAP}). "
    "Returns probe envelope below cap; OperatorOnlyCapability envelope above. "
    "Use to confirm retrieval is healthy before a long task."
)

_MAINTENANCE_ANALYZE_DESCRIPTION = (
    "Run ANALYZE on the kairix SQLite index to refresh planner statistics. "
    "Use after large ingests or when query plans look wrong. Reports the "
    "EXPLAIN QUERY PLAN before/after on a representative hot-path query so "
    "callers can confirm the planner picked up the new stats. Equivalent of "
    "the operator-side `kairix maintenance analyze` (#376)."
)


def _make_onboard_check(_ctx: RegistrationContext) -> Callable[..., Any]:
    def onboard_check() -> dict[str, Any]:
        """Health-probe envelope. Read-only. Identical to `kairix onboard check --json`."""
        return tool_onboard_check()

    return onboard_check


def _make_onboard_scan(_ctx: RegistrationContext) -> Callable[..., Any]:
    # F45-feature: tests/bdd/features/onboard_scan_discovers_agents.feature
    def onboard_scan(memory_root: str, workspace_root: str = "") -> dict[str, Any]:
        """Discovery envelope. Read-only. Identical to `kairix onboard scan --json`."""
        return tool_onboard_scan(memory_root=memory_root, workspace_root=workspace_root)

    return onboard_scan


def _make_onboard_agent(_ctx: RegistrationContext) -> Callable[..., Any]:
    # F45-feature: tests/bdd/features/onboard_scan_discovers_agents.feature
    def onboard_agent(
        agent_name: str,
        memory_root: str,
        workspace_root: str = "",
        harness: str = "",
    ) -> dict[str, Any]:
        """Single-agent discovery envelope. Read-only."""
        return tool_onboard_agent(
            agent_name=agent_name,
            memory_root=memory_root,
            workspace_root=workspace_root,
            harness=harness,
        )

    return onboard_agent


def _make_doctor_check_all(_ctx: RegistrationContext) -> Callable[..., Any]:
    # F45-feature: tests/bdd/features/cli_doctor.feature
    def doctor_check_all(config: dict[str, object] | None = None) -> dict[str, Any]:
        """Bulk doctor envelope. Read-only. Identical to `kairix doctor agent --all --json`."""
        return tool_doctor_check_all(config=config)

    return doctor_check_all


def _make_doctor_check_agent(_ctx: RegistrationContext) -> Callable[..., Any]:
    # F45-feature: tests/bdd/features/cli_doctor.feature
    def doctor_check_agent(
        agent_name: str,
        config: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Single-agent doctor envelope. Read-only."""
        return tool_doctor_check_agent(agent_name=agent_name, config=config)

    return doctor_check_agent


def _make_worker_status(_ctx: RegistrationContext) -> Callable[..., Any]:
    def worker_status() -> dict[str, Any]:
        """Worker state envelope. Read-only. Identical to `kairix worker status`."""
        return tool_worker_status()

    return worker_status


def _make_features_status(_ctx: RegistrationContext) -> Callable[..., Any]:
    def features_status() -> dict[str, Any]:
        """Feature-flag status envelope. Read-only. Identical to `kairix features status --json`."""
        return tool_features_status()

    return features_status


def _make_secrets_verify(_ctx: RegistrationContext) -> Callable[..., Any]:
    def secrets_verify() -> dict[str, Any]:
        """Secrets resolution envelope. Read-only. Identical to `kairix secrets verify --json`."""
        from kairix.agents.mcp.secrets_status import tool_secrets_verify

        return tool_secrets_verify()

    return secrets_verify


def _make_dead_letter_status(_ctx: RegistrationContext) -> Callable[..., Any]:
    def dead_letter_status(source_name: str | None = None) -> dict[str, Any]:
        """Dead-letter status envelope. Read-only. Identical to `kairix dead-letter status --json`."""
        return tool_dead_letter_status(source_name=source_name)

    return dead_letter_status


def _make_caches_status(_ctx: RegistrationContext) -> Callable[..., Any]:
    def caches_status() -> dict[str, Any]:
        """Cache stats envelope. Read-only. Reflects the warm MCP process's state."""
        return tool_caches_status()

    return caches_status


def _make_warm(ctx: RegistrationContext) -> Callable[..., Any]:
    def warm() -> dict[str, Any]:
        """Warm kairix retrieval caches via the cold-start affordance.

        On ready=True the readiness gate is flipped so /healthz/ready returns 200.
        See ``kairix.agents.mcp.cold_start.warm_retrieval_stack`` for the
        production warm semantics this tool exposes.
        """
        result = warm_retrieval_stack()
        if result.get("ready") is True and ctx.mark_ready is not None:
            ctx.mark_ready()
        return result

    return warm


def _make_probe_search(_ctx: RegistrationContext) -> Callable[..., Any]:
    def probe_search(
        suite: str = "reflib",
        queries: int = 20,
        concurrency: int = 3,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Agent-safe capped probe. Returns ProbeResult envelope or escalation envelope."""
        return tool_probe_search(suite=suite, queries=queries, concurrency=concurrency, seed=seed)

    return probe_search


def _make_maintenance_analyze(_ctx: RegistrationContext) -> Callable[..., Any]:
    def maintenance_analyze() -> dict[str, Any]:
        """Refresh SQLite planner stats. Returns the analyze envelope."""
        return tool_maintenance_analyze()

    return maintenance_analyze


BINDINGS: tuple[ToolBinding, ...] = (
    ToolBinding(name="onboard_check", description=_ONBOARD_CHECK_DESCRIPTION, make=_make_onboard_check),
    ToolBinding(name="onboard_scan", description=_ONBOARD_SCAN_DESCRIPTION, make=_make_onboard_scan),
    ToolBinding(name="onboard_agent", description=_ONBOARD_AGENT_DESCRIPTION, make=_make_onboard_agent),
    ToolBinding(name="doctor_check_all", description=_DOCTOR_CHECK_ALL_DESCRIPTION, make=_make_doctor_check_all),
    ToolBinding(name="doctor_check_agent", description=_DOCTOR_CHECK_AGENT_DESCRIPTION, make=_make_doctor_check_agent),
    ToolBinding(name="worker_status", description=_WORKER_STATUS_DESCRIPTION, make=_make_worker_status),
    ToolBinding(name="features_status", description=_FEATURES_STATUS_DESCRIPTION, make=_make_features_status),
    ToolBinding(name="secrets_verify", description=_SECRETS_VERIFY_DESCRIPTION, make=_make_secrets_verify),
    ToolBinding(name="dead_letter_status", description=_DEAD_LETTER_STATUS_DESCRIPTION, make=_make_dead_letter_status),
    ToolBinding(name="caches_status", description=_CACHES_STATUS_DESCRIPTION, make=_make_caches_status),
    ToolBinding(name="warm", description=_WARM_DESCRIPTION, make=_make_warm),
    ToolBinding(name="probe_search", description=_PROBE_SEARCH_DESCRIPTION, make=_make_probe_search),
    ToolBinding(
        name="maintenance_analyze",
        description=_MAINTENANCE_ANALYZE_DESCRIPTION,
        make=_make_maintenance_analyze,
    ),
)
