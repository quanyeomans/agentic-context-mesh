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

The implementation is read-only — it only calls each cache's
``stats()`` method, never mutates state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class _CacheRow:
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


def _hit_rate_pct_from_counts(hits: int, misses: int) -> float:
    """Compute hit-rate percentage with safe zero-division."""
    total = hits + misses
    if total <= 0:
        return 0.0
    return round(100.0 * hits / total, 1)


def _collect_query_result_cache() -> _CacheRow:
    from kairix.core.factory import get_query_cache

    stats = get_query_cache().stats()
    return _CacheRow(
        name="query_result_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_prep_summary_cache() -> _CacheRow:
    from kairix.use_cases.prep import get_prep_summary_cache

    stats = get_prep_summary_cache().stats()
    return _CacheRow(
        name="prep_summary_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_brief_output_cache() -> _CacheRow:
    from kairix.use_cases.brief import get_brief_output_cache

    stats = get_brief_output_cache().stats()
    return _CacheRow(
        name="brief_output_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_brief_source_cache() -> _CacheRow:
    from kairix.agents.briefing.sources import get_brief_source_cache

    stats = get_brief_source_cache().stats()
    return _CacheRow(
        name="brief_source_cache",
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate_pct=round(100.0 * stats.hit_rate, 1),
    )


def _collect_health_probe_cache() -> _CacheRow:
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
    return _CacheRow(
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


def _collect_all_rows() -> list[_CacheRow]:
    """Return every cache's row in the canonical display order.

    Defensive: per-cache collectors are wrapped in try/except so a
    single broken cache doesn't blank the whole report. The catch
    surfaces an inline "unavailable" row so operators see the failure
    rather than wonder why a cache is missing.
    """
    collectors: list[tuple[str, Callable[[], _CacheRow]]] = [
        ("query_result_cache", _collect_query_result_cache),
        ("prep_summary_cache", _collect_prep_summary_cache),
        ("brief_output_cache", _collect_brief_output_cache),
        ("brief_source_cache", _collect_brief_source_cache),
        ("health_probe_cache", _collect_health_probe_cache),
    ]
    rows: list[_CacheRow] = []
    for name, fn in collectors:
        try:
            rows.append(fn())
        except Exception as exc:
            # Defensive: don't fail the report because one cache
            # accessor blew up. Operators see the error inline.
            rows.append(
                _CacheRow(
                    name=f"{name} (unavailable: {type(exc).__name__})",
                    size=0,
                    hits=0,
                    misses=0,
                    evictions=0,
                    hit_rate_pct=0.0,
                )
            )
    return rows


def _format_text(rows: list[_CacheRow]) -> str:
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


def _envelope_for_json(rows: list[_CacheRow]) -> dict[str, object]:
    """Build the JSON-mode envelope.

    Top-level key ``caches`` is a list of dicts in canonical display
    order so operators piping into ``jq`` see the same shape as the
    text report.
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


def main(argv: list[str] | None = None) -> int:
    """Entry point dispatched from ``kairix.cli.COMMANDS``.

    Args:
        argv: argv slice after the ``caches`` token; None means the
              parser reads from sys.argv directly.

    Returns:
        0 — report emitted (even when every cache is cold).
        2 — invalid args (the parser handles this via SystemExit).
    """
    _build_parser().parse_args(argv)
    rows = _collect_all_rows()

    args = _build_parser().parse_args(argv)
    if args.as_json:
        print(json.dumps(_envelope_for_json(rows), indent=2))
    else:
        sys.stdout.write(_format_text(rows))
    return 0


__all__ = ["main"]
