"""ADR-029 G.1: carry-along middleware.

Reads up to ``CARRY_ALONG_CAP`` completed pending_queries rows for the
given agent, marks each ``delivered``, and returns a formatted text
prefix to prepend to the current tool response. Caps the prefix at
5 results so a backed-up queue never blows the response budget.

The UPDATE site here is the symmetric pair to the INSERT site in
``dispatch.py`` — F70 closes.

Sabotage proof for the integration test: drop the
``UPDATE ... SET status='delivered'`` and the second carry-along call
will re-deliver the same row, breaking the dedup contract.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Cap the number of completed rows carried per call so the prefix
# stays bounded. ADR-029 §"Carry-along middleware" — 5 is the suggested
# default; revisit if dogfood shows agents commonly accumulating more.
CARRY_ALONG_CAP = 5

# Module-level lock — shares the spirit of dispatch._db_lock so two
# concurrent agent calls don't race on the UPDATE. We intentionally
# keep separate locks because carry-along never writes to the
# same connection as dispatch in production (both share the queue
# module's singleton).
_carry_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_result_summary(tool: str, result_json: str | None) -> str:
    """Render one row into a single text line for the prefix.

    Defensive — handles None / non-JSON / oversized JSON without
    raising so a malformed row never breaks the entire prefix.
    """
    if not result_json:
        return f"tool={tool} result=<empty>"
    try:
        parsed = json.loads(result_json)
    except (TypeError, ValueError):
        return f"tool={tool} result=<unparseable>"

    # Prefer a short summary over the full envelope.
    if isinstance(parsed, dict):
        if "query" in parsed and "results" in parsed:
            results = parsed.get("results", [])
            count = len(results) if isinstance(results, list) else 0
            query = parsed.get("query", "")
            return f"tool={tool} query={query!r} results={count}"
        if "error" in parsed and parsed.get("error"):
            return f"tool={tool} error={parsed['error']!r}"
        # Generic dict — show top-level keys for orientation.
        keys = sorted(k for k in parsed if not k.startswith("_"))
        return f"tool={tool} keys={keys}"
    return f"tool={tool} result={parsed!r}"


def _format_prefix(rows: list[tuple[str, str, str | None]]) -> str:
    """Build the carry-along prefix string from ordered rows."""
    lines = ["Earlier results now available:"]
    for query_id, tool, result_json in rows:
        summary = _format_result_summary(tool, result_json)
        lines.append(f"- [{query_id}] {summary}")
    lines.append("")  # trailing blank line so the next tool response sits cleanly below
    return "\n".join(lines)


def carry_along_prefix(
    agent_id: str,
    db: sqlite3.Connection,
    *,
    cap: int = CARRY_ALONG_CAP,
) -> str:
    """Read up to ``cap`` completed rows for ``agent_id``, mark them delivered, return prefix.

    Returns the empty string when no completed rows exist for the
    agent. Failures are surfaced as ``"error=..."`` lines so the agent
    knows something previously queued didn't return cleanly.

    The mark-as-delivered UPDATE is the symmetric pair to the INSERT
    in :func:`dispatch_or_queue` — together they close F70 for the
    ``pending_queries`` table.
    """
    if not agent_id:
        return ""

    with _carry_lock:
        rows = db.execute(
            "SELECT id, tool, result_json FROM pending_queries "
            "WHERE agent_id = ? AND status IN ('completed', 'failed') "
            "ORDER BY completed_at LIMIT ?",  # F63-bounded: explicit LIMIT
            (agent_id, cap),
        ).fetchall()

        if not rows:
            return ""

        now = _now_iso()
        db.executemany(
            "UPDATE pending_queries SET status = 'delivered', delivered_at = ? WHERE id = ?",
            [(now, row[0]) for row in rows],
        )
        db.commit()

    return _format_prefix(rows)


def carry_along_prefix_safe(
    agent_id: str,
    db: sqlite3.Connection | None,
    *,
    cap: int = CARRY_ALONG_CAP,
) -> str:
    """Production-safe wrapper — swallows DB errors so a misconfigured queue
    never breaks the tool response.

    Returns ``""`` when the DB is unavailable or the read raises.
    """
    if db is None:
        return ""
    try:
        return carry_along_prefix(agent_id, db, cap=cap)
    except sqlite3.Error as exc:
        logger.warning("carry_along_prefix failed: %s", exc, exc_info=True)
        return ""


def resolve_agent_id(headers: dict[str, str] | None) -> tuple[str, bool]:
    """Resolve the canonical agent_id from a request's headers.

    Priority order per ADR-029 §"Agent identity":

    1. ``Mcp-Session-Id`` (canonical — MCP streamable-HTTP per-session id)
    2. ``X-Kairix-Agent`` (explicit operator override)
    3. Process-global fallback ``"unknown-agent"`` (logged as F21
       affordance so operators see why carry-along didn't fire).

    Returns ``(agent_id, used_fallback)`` so the caller can emit the
    F21 affordance log exactly once per session rather than per call.
    """
    if headers:
        # Match common case variations on case-insensitive header lookup.
        for key in ("Mcp-Session-Id", "mcp-session-id", "MCP-SESSION-ID"):
            if headers.get(key):
                return headers[key], False
        for key in ("X-Kairix-Agent", "x-kairix-agent", "X-KAIRIX-AGENT"):
            if headers.get(key):
                return headers[key], False
    return "unknown-agent", True


def log_agent_fallback_affordance(used_fallback: bool, *, logger_: Any = None) -> None:
    """F21 affordance log emitted when the unknown-agent fallback fires.

    Idempotent at the call site — the caller decides cadence (once per
    session rather than once per call).
    """
    if not used_fallback:
        return
    log = logger_ if logger_ is not None else logger
    log.info(
        "agent identity unset — set the Mcp-Session-Id header (per the MCP "
        "streamable-HTTP spec) or X-Kairix-Agent (operator override) to enable "
        "carry-along delivery. "
        "fix: pass the per-session identifier in either header on every tool call. "
        "next: see docs/operations/runbooks/agent-query-queue.md for the header layout. "
        "run: kairix features status — confirm agent_query_queue is enabled."
    )
