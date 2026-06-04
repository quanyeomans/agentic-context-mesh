"""Periodic ANALYZE bookkeeping for the maintenance scheduler.

Bootstrap on warm-up (``kairix.platform.warm.sqlite_stats``) handles the
first-install case. This module owns the steady-state refresh: as
documents grow / collections shift / agents are added, ``sqlite_stat1``
becomes stale and the planner's view drifts from reality. Running
ANALYZE on a cadence keeps the plans current without operator
intervention.

Decision rule (issue #376):
  * If we've never analyzed before -> run.
  * If the last analyze was > ``stale_seconds`` ago (default 24h) -> run.
  * If the documents row count has grown by > ``growth_threshold``
    (default 10%) since the last analyze -> run.
  * Otherwise -> skip.

Bookkeeping lives in the existing ``kairix_meta`` ``(key, value)`` table.
The value column is stringified JSON ``{"ts": <epoch>, "doc_count":
<int>}`` keyed by ``last_analyze`` so the schema stays untouched and we
inherit the existing migration story.

F-rule positioning:
  * F1 / F2 — pure-function API; tests pass open connections and an
    injectable clock. No monkey-patching, no env reads.
  * F42 — :class:`PeriodicAnalyzeResult` is a frozen dataclass.
  * F19 — every helper takes named args; no unused positional params.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GROWTH_THRESHOLD",
    "DEFAULT_STALE_SECONDS",
    "META_KEY",
    "REASON_FRESH",
    "REASON_GROWTH",
    "REASON_NEVER_ANALYZED",
    "REASON_STALE",
    "PeriodicAnalyzeResult",
    "read_last_analyze",
    "run_periodic_analyze",
    "should_run_analyze",
    "write_last_analyze",
]

# Bookkeeping key in kairix_meta. Single canonical name so callers
# (writer + reader + tests) reference the same literal.
META_KEY = "last_analyze"

# 24 hours in seconds — the daily-cadence floor.
DEFAULT_STALE_SECONDS = 86_400.0

# 10% growth ratio — when docs grew by > this fraction since the last
# analyze the plan-stats are likely stale even if we're still inside the
# stale_seconds window.
DEFAULT_GROWTH_THRESHOLD = 0.10

# Reason strings — exposed so callers can branch on them and tests can
# assert on the exact decision the scheduler took, not just whether it
# ran.
REASON_NEVER_ANALYZED = "never analyzed"
REASON_STALE = "last analyze > stale_seconds ago"
REASON_GROWTH = "doc count grew > growth_threshold"
REASON_FRESH = "fresh stats, growth within threshold"


@dataclass(frozen=True)
class PeriodicAnalyzeResult:
    """Frozen envelope returned from :func:`run_periodic_analyze`.

    F42-clean — explicit fields, no dict[str, Any]:

    * ``ran`` — True when ANALYZE actually executed this call.
    * ``reason`` — one of the ``REASON_*`` strings; operators read this
      to understand which condition fired.
    * ``doc_count_at_analyze`` — documents row count captured at the
      moment ANALYZE ran (or 0 when skipped).
    * ``previous_doc_count`` — documents row count at the last analyze
      (0 when never analyzed). Used to compute the growth ratio.
    * ``elapsed_ms`` — wall-clock duration in ms (0 when skipped).
    """

    ran: bool
    reason: str
    doc_count_at_analyze: int
    previous_doc_count: int
    elapsed_ms: float


def _now() -> float:
    """Default clock — wraps :func:`time.time` so callers can inject."""
    return time.time()


def _documents_count(db: sqlite3.Connection) -> int:
    """Return ``COUNT(*)`` on documents, or 0 when the table doesn't exist."""
    try:
        row = db.execute("SELECT COUNT(*) FROM documents").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def read_last_analyze(db: sqlite3.Connection) -> tuple[float, int] | None:
    """Read the ``(ts, doc_count)`` snapshot from the last ANALYZE.

    Returns ``None`` when no prior analyze is recorded (or when the
    kairix_meta value is malformed — defensive: a corrupt JSON blob
    should not crash the scheduler).
    """
    try:
        row = db.execute("SELECT value FROM kairix_meta WHERE key = ?", (META_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    try:
        payload: dict[str, Any] = json.loads(row[0])
        ts = float(payload["ts"])
        doc_count = int(payload["doc_count"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        logger.warning("read_last_analyze: kairix_meta[%r] is malformed; treating as never analyzed", META_KEY)
        return None
    return ts, doc_count


def write_last_analyze(db: sqlite3.Connection, *, ts: float, doc_count: int) -> None:
    """Persist the (timestamp, doc_count) snapshot to ``kairix_meta``.

    Idempotent via ``INSERT OR REPLACE`` — the row key is unique on
    ``META_KEY`` so re-writes simply update the snapshot.

    Defensive: when the ``kairix_meta`` table is missing (e.g. a half-
    initialised DB with no schema applied yet — operator running the
    analyze CLI against a tmp/scratch DB), the write is skipped with a
    warning rather than crashing the caller.
    """
    payload = json.dumps({"ts": ts, "doc_count": doc_count})
    try:
        db.execute(
            "INSERT OR REPLACE INTO kairix_meta (key, value) VALUES (?, ?)",
            (META_KEY, payload),
        )
        db.commit()
    except sqlite3.OperationalError:
        logger.warning("write_last_analyze: kairix_meta missing — snapshot not persisted")


def should_run_analyze(
    *,
    now: float,
    last_ts: float | None,
    last_doc_count: int,
    current_doc_count: int,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    growth_threshold: float = DEFAULT_GROWTH_THRESHOLD,
) -> tuple[bool, str]:
    """Pure decision function — returns ``(should_run, reason)``.

    Extracted so the scheduler step + tests can assert against the same
    logic without spinning up SQLite. The function is the source-of-
    truth for the decision rule; the I/O wrapper just feeds it inputs.

    Sabotage-proof anchor: tests pin each branch (never_analyzed /
    stale / growth / fresh) so a regression in any branch surfaces.
    """
    if last_ts is None:
        return True, REASON_NEVER_ANALYZED

    if (now - last_ts) > stale_seconds:
        return True, REASON_STALE

    # Growth ratio: (current - last) / max(last, 1) — avoids div-by-zero
    # when the last_doc_count snapshot was 0 (shouldn't happen post-
    # bootstrap but defensive).
    baseline = max(last_doc_count, 1)
    growth = (current_doc_count - last_doc_count) / baseline
    if growth > growth_threshold:
        return True, REASON_GROWTH

    return False, REASON_FRESH


def run_periodic_analyze(
    db: sqlite3.Connection,
    *,
    clock: Any = None,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    growth_threshold: float = DEFAULT_GROWTH_THRESHOLD,
) -> PeriodicAnalyzeResult:
    """Run ANALYZE if the decision rule fires; record the new snapshot.

    Parameters
    ----------
    db:
        Open SQLite connection. Caller owns lifecycle.
    clock:
        Injectable ``() -> float`` returning epoch seconds. Default
        :func:`time.time`. Tests pass a pinned clock so the stale /
        growth branches are deterministic.
    stale_seconds:
        Override the 24h staleness floor. Default
        :data:`DEFAULT_STALE_SECONDS`.
    growth_threshold:
        Override the 10% growth-ratio threshold. Default
        :data:`DEFAULT_GROWTH_THRESHOLD`.

    Returns
    -------
    PeriodicAnalyzeResult
        Frozen envelope describing what happened.
    """
    clock_fn = clock if clock is not None else _now

    last = read_last_analyze(db)
    last_ts = last[0] if last is not None else None
    last_doc_count = last[1] if last is not None else 0
    current_doc_count = _documents_count(db)
    now = clock_fn()

    ran, reason = should_run_analyze(
        now=now,
        last_ts=last_ts,
        last_doc_count=last_doc_count,
        current_doc_count=current_doc_count,
        stale_seconds=stale_seconds,
        growth_threshold=growth_threshold,
    )

    if not ran:
        return PeriodicAnalyzeResult(
            ran=False,
            reason=reason,
            doc_count_at_analyze=0,
            previous_doc_count=last_doc_count,
            elapsed_ms=0.0,
        )

    t0 = time.perf_counter()
    db.execute("ANALYZE")
    db.commit()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    write_last_analyze(db, ts=now, doc_count=current_doc_count)

    logger.info(
        "periodic ANALYZE ran reason=%s previous_doc_count=%d current_doc_count=%d elapsed_ms=%.1f",
        reason,
        last_doc_count,
        current_doc_count,
        elapsed_ms,
    )
    return PeriodicAnalyzeResult(
        ran=True,
        reason=reason,
        doc_count_at_analyze=current_doc_count,
        previous_doc_count=last_doc_count,
        elapsed_ms=elapsed_ms,
    )
