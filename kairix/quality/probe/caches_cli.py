"""``kairix caches`` — operator surface over the W-B cache stats (#396).

Surfaces per-cache observability for the TTL LRUs added by Workstream B
of the MCP performance sprint:

* ``query_result_cache`` — search-pipeline ``(query, scope, agent, collections)`` LRU
* ``scope_collection_cache`` — topology_v2 collection resolver cache
* ``prep_summary_cache`` — LLM ``chat()`` synthesis cache for ``run_prep``
* ``brief_output_cache`` — assembled ``BriefOutput`` cache for ``run_brief``
* ``brief_source_cache`` — per-source TTL cache for the 5 cheap brief fetchers
* ``health_probe_cache`` — ``probe_health`` snapshot cache

The CLI shape::

    kairix caches [--json]

Text mode (default) prints one row per cache with name, size, hits,
misses, evictions, hit_rate %. JSON mode emits the same data as a
dict so load-test runners can parse hit rates programmatically.

PR 3.1 / #422 — when a warm MCP server is responsive the CLI routes
through ``tool_caches_status`` so operators see the long-lived MCP
process's cache state instead of the freshly-spawned CLI's empty
caches. When MCP is not responsive the CLI falls through to the
in-process collectors AND prints a banner to stderr so the operator
knows the report reflects this CLI invocation only.

The implementation is read-only — it only calls each cache's
``stats()`` method, never mutates state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CacheRow:
    """One row in the probe-caches report.

    Frozen-dc so the projection from each cache's stats() is immutable
    and easy to envelope as JSON.
    """

    name: str
    size: int
    hits: int
    misses: int
    evictions: int
    hit_rate_pct: float

    @classmethod
    def from_envelope(cls, row: dict[str, Any]) -> CacheRow:
        """Rebuild a ``CacheRow`` from the per-row dict ``caches_rows_to_envelope`` emits.

        The seam for warm-MCP text-mode routing (PR 3.1 / #422). The
        CLI dispatcher receives a JSON envelope from ``tool_caches_status``;
        this adapter projects each row back to the dataclass shape so
        ``_format_text`` can render byte-identical output to the
        in-process path.

        Defensive coercion mirrors the other ``from_envelope`` readers in
        the codebase: any missing key defaults to the dataclass-default
        zero value so the rebuild does not crash on a partial envelope.
        """
        return cls(
            name=str(row.get("name", "")),
            size=int(row.get("size", 0) or 0),
            hits=int(row.get("hits", 0) or 0),
            misses=int(row.get("misses", 0) or 0),
            evictions=int(row.get("evictions", 0) or 0),
            hit_rate_pct=float(row.get("hit_rate_pct", 0.0) or 0.0),
        )


def _collect_query_result_cache() -> CacheRow:
    from kairix.core.factory import get_query_cache

    stats = get_query_cache().stats()
    return CacheRow(
        name="query_result_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_prep_summary_cache() -> CacheRow:
    from kairix.use_cases.prep import get_prep_summary_cache

    stats = get_prep_summary_cache().stats()
    return CacheRow(
        name="prep_summary_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_brief_output_cache() -> CacheRow:
    from kairix.use_cases.brief import get_brief_output_cache

    stats = get_brief_output_cache().stats()
    return CacheRow(
        name="brief_output_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_brief_source_cache() -> CacheRow:
    from kairix.agents.briefing.sources import get_brief_source_cache

    stats = get_brief_source_cache().stats()
    return CacheRow(
        name="brief_source_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_health_probe_cache() -> CacheRow:
    """Build a row for the health-probe cache (single-slot, no hit/miss counter).

    The health-probe cache is a single-slot TTL cache, not an LRU, so
    it doesn't track hit/miss counts the way the other caches do. The
    row reports size (0 cold / 1 warm) + the age of the cached entry
    in seconds, surfaced as the ``hit_rate_pct`` slot for backward-
    compatible row shape — operators read this as "snapshot freshness"
    rather than a true hit-rate.
    """
    from kairix.use_cases.brief import get_health_probe_cache_age_s

    age = get_health_probe_cache_age_s()
    size = 1 if age is not None else 0
    return CacheRow(
        name="health_probe_cache",
        size=size,
        hits=0,
        misses=0,
        evictions=0,
        # Stash the age (in seconds) here so operators can see how
        # stale the cached snapshot is; the rest of the row stays
        # zero because the single-slot cache has no hit/miss notion.
        hit_rate_pct=round(age, 1) if age is not None else 0.0,
    )


def _collect_all_rows() -> list[CacheRow]:
    """Return every cache's row in the canonical display order.

    Defensive: per-cache collectors are wrapped in try/except so a
    single broken cache doesn't blank the whole report. The catch
    surfaces an inline "unavailable" row so operators see the failure
    rather than wonder why a cache is missing.
    """
    collectors: list[tuple[str, Callable[[], CacheRow]]] = [
        ("query_result_cache", _collect_query_result_cache),
        ("prep_summary_cache", _collect_prep_summary_cache),
        ("brief_output_cache", _collect_brief_output_cache),
        ("brief_source_cache", _collect_brief_source_cache),
        ("health_probe_cache", _collect_health_probe_cache),
    ]
    rows: list[CacheRow] = []
    for name, fn in collectors:
        try:
            rows.append(fn())
        except Exception as exc:
            # Defensive: don't fail the report because one cache
            # accessor blew up. Operators see the error inline.
            rows.append(
                CacheRow(
                    name=f"{name} (unavailable: {type(exc).__name__})",
                    size=0,
                    hits=0,
                    misses=0,
                    evictions=0,
                    hit_rate_pct=0.0,
                )
            )
    return rows


def format_text(rows: list[CacheRow]) -> str:
    """Render the text-mode report — one row per cache, fixed-width columns."""
    if not rows:
        return "probe caches: no caches reported.\n"

    longest = max(len(r.name) for r in rows)
    lines = ["kairix caches"]
    header = (
        f"  {'name'.ljust(longest)}  "
        f"{'size'.rjust(6)}  "
        f"{'hits'.rjust(8)}  "
        f"{'misses'.rjust(8)}  "
        f"{'evictions'.rjust(10)}  "
        f"{'hit_rate%'.rjust(10)}"
    )
    lines.append(header)
    for row in rows:
        lines.append(
            f"  {row.name.ljust(longest)}  "
            f"{row.size:6d}  "
            f"{row.hits:8d}  "
            f"{row.misses:8d}  "
            f"{row.evictions:10d}  "
            f"{row.hit_rate_pct:10.1f}"
        )
    return "\n".join(lines) + "\n"


def caches_rows_to_envelope(rows: list[CacheRow]) -> dict[str, Any]:
    """Build the JSON-mode envelope from a list of ``CacheRow``.

    Top-level key ``caches`` is a list of dicts in canonical display
    order so operators piping into ``jq`` see the same shape as the
    text report. Public surface so:

    * the MCP ``tool_caches_status`` reuses the projection (envelope
      parity between CLI in-process mode and warm-MCP mode), and
    * the round-trip contract test in
      ``tests/contracts/test_caches_status_envelope_parity.py`` can
      exercise the seam without reaching into private helpers.

    The function replaces the older private ``_envelope_for_json``
    helper, which was renamed to surface the projection at the module
    boundary for the warm-MCP envelope-parity contract.
    """
    return {
        "caches": [
            {
                "name": r.name,
                "size": r.size,
                "hits": r.hits,
                "misses": r.misses,
                "evictions": r.evictions,
                "hit_rate_pct": r.hit_rate_pct,
            }
            for r in rows
        ]
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix caches",
        description=(
            "Inspect every TTL LRU added by issue #396 Workstream B. "
            "Run this after a load-test or dogfood session to see "
            "which caches are paying off."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit JSON envelope on stdout; suppress human-readable output.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "operator affordance flag — accepted for parity with "
            "``kairix mcp-calls`` but caches are point-in-time "
            "snapshots, so the value is ignored. Documented here so "
            "operators piping cache + mcp-calls reports through the "
            "same shell loop don't see an argparse error."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Warm-MCP routing seam (PR 3.1 / #422)
# ---------------------------------------------------------------------------

_BANNER = (
    "NOTE: MCP server not responsive — showing in-process cache state for this CLI invocation only.\n"
    "To see warm MCP cache state, ensure the MCP server is running (see docs/operations/MCP-DEPLOYMENT.md).\n"
)


def default_dispatch(subcommand: str, argv: list[str]) -> int | None:
    """Production default for :attr:`CachesDeps.dispatch`.

    Lazy-imports the dispatcher so the CLI doesn't pay the cost when
    routing isn't reachable. Returns ``None`` (in-process fall-through)
    when the optional ``mcp`` extra isn't installed — bit-identical to
    today's behaviour.
    """
    from kairix.agents.mcp.client_dispatcher import try_dispatch_via_mcp

    return try_dispatch_via_mcp(subcommand, argv)


def default_is_mcp_responsive() -> bool:
    """Production default for :attr:`CachesDeps.is_mcp_responsive`.

    Re-uses the dispatcher's HTTP probe so the responsiveness signal
    matches what ``try_dispatch_via_mcp`` saw. Any import / probe
    failure → ``False`` so the banner fires (safe default).
    """
    try:
        from kairix.agents.mcp.client_dispatcher import HttpMcpDispatchClient
        from kairix.paths import mcp_endpoint, mcp_routing_enabled
    except Exception:
        return False
    if not mcp_routing_enabled():
        return False
    try:
        return HttpMcpDispatchClient().is_responsive(mcp_endpoint(), 0.1)
    except Exception:
        return False


@dataclass(frozen=True)
class CachesDeps:
    """Injection seam for :func:`main`.

    Production callers leave fields at their defaults; tests pass a
    fake ``dispatch`` callable + an ``is_mcp_responsive`` lambda to
    drive every branch of the routing logic without binding ports
    (F1/F2-clean by construction).

    Attributes:
        dispatch: Callable invoked first; mirrors ``try_dispatch_via_mcp``.
            Returns an int exit code when the warm-MCP path ran; returns
            ``None`` when the CLI should fall through to in-process.
        is_mcp_responsive: Callable that returns True iff the MCP server
            is reachable. Used only to decide whether to print the
            fall-through banner — when MCP is unreachable the banner
            fires; when MCP is reachable but ``dispatch`` returned None
            (e.g. the operator opted out via flag) the banner stays
            quiet because in-process IS the operator's chosen path.
        client: Optional pre-constructed dispatch client; reserved for
            test scaffolding that pre-records calls. Production leaves
            this ``None``.
    """

    dispatch: Callable[[str, list[str]], int | None] = field(default=default_dispatch)
    is_mcp_responsive: Callable[[], bool] = field(default=default_is_mcp_responsive)
    client: object | None = None


def main(argv: list[str] | None = None, *, deps: CachesDeps | None = None) -> int:
    """Entry point dispatched from ``kairix.cli.COMMANDS``.

    Args:
        argv: argv slice after the ``caches`` token; None means the
              parser reads from sys.argv directly.
        deps: PR 3.1 injection seam; production callers leave ``None``
              and the real ``try_dispatch_via_mcp`` is used.

    Returns:
        0 — report emitted (even when every cache is cold).
        2 — invalid args (the parser handles this via SystemExit).
        Other — the warm-MCP path's exit code when routing succeeded.
    """
    # Parse args once up front so an invalid argv exits before any
    # network probe / collector runs (matches the historical contract).
    _build_parser().parse_args(argv)
    effective_deps = deps if deps is not None else CachesDeps()
    effective_argv = list(argv) if argv is not None else []

    # PR 3.1 — try the warm-MCP path first. When the dispatcher returns
    # an int exit code, it has already written the rendered output (text
    # or JSON) to stdout and we use that exit code as our own — the
    # in-process collectors do NOT run.
    routed = effective_deps.dispatch("caches", effective_argv)
    if routed is not None:
        return routed

    # Fall-through path. When MCP is NOT responsive, surface a banner on
    # stderr so the operator knows the in-process report reflects this
    # CLI invocation only (its caches are brand-new + empty). Banner
    # stays on stderr so JSON-mode stdout remains pipe-safe.
    if not effective_deps.is_mcp_responsive():
        sys.stderr.write(_BANNER)

    args = _build_parser().parse_args(argv)
    rows = _collect_all_rows()
    if args.as_json:
        print(json.dumps(caches_rows_to_envelope(rows), indent=2))
    else:
        sys.stdout.write(format_text(rows))
    return 0


__all__ = [
    "CacheRow",
    "CachesDeps",
    "caches_rows_to_envelope",
    "default_dispatch",
    "default_is_mcp_responsive",
    "format_text",
    "main",
]
