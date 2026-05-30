"""Dead-letter status surface — operator triage view over ``connector_deadletter``.

Today's operators have to drop to raw SQL to triage dead-letter state
during incidents (see #337 / #351). This module is the analysis core
behind ``kairix dead-letter status`` (CLI) and ``tool_dead_letter_status``
(MCP). The CLI/MCP modules stay thin — they parse argv / build the
envelope and delegate every read + every render to this module so both
surfaces emit byte-identical structured data.

Three concerns live here:

* :func:`classify_error` — best-effort regex bucketing of ``last_error``
  text into a small set of operator-actionable failure classes. The
  bucket order is deterministic and the last bucket (``other``) catches
  everything unmatched.
* :func:`build_status` — single SQL read against ``connector_deadletter``
  (LEFT JOIN ``bronze_records`` for MIME), returns a frozen
  :class:`DeadLetterStatusReport` snapshot.
* :func:`render_human` / :func:`render_json` — pure renderers over the
  snapshot. The two surfaces guarantee parity because they both call
  these functions.

The snapshot type is a frozen dataclass tree per F42 — no
``dict[str, Any]`` crosses the public boundary. The JSON envelope is
built by walking the snapshot, not by re-reading the DB.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

# Classification rule order is load-bearing — first match wins. Keep this
# in lock-step with the docstring + the BDD scenarios in
# tests/bdd/features/cli_dead_letter.feature.
_CLASS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("missing_dependency", re.compile(r"missingdependencyexception", re.IGNORECASE)),
    ("no_space", re.compile(r"no space left on device|enospc", re.IGNORECASE)),
    ("forbidden_403", re.compile(r"\b403\b|forbidden", re.IGNORECASE)),
    ("not_found_404", re.compile(r"\b404\b|not found", re.IGNORECASE)),
    ("timeout", re.compile(r"timeout|timed out", re.IGNORECASE)),
    ("corrupt_zip", re.compile(r"badzipfile|file is not a zip", re.IGNORECASE)),
    ("rate_limit", re.compile(r"\b429\b|too many requests", re.IGNORECASE)),
    ("decode", re.compile(r"unicode|decode|encodingerror", re.IGNORECASE)),
)

OTHER_CLASS = "other"

# Truncation cap for last_error in JSON / human rendering. Operators want
# enough to recognise the failure mode without page-sized blobs in the
# envelope.
_ERROR_TRUNC_CHARS = 240

# Top-N caps on the per-source bucket renders. Keeps the human output
# scannable and the JSON envelope a predictable size for MCP clients.
_TOP_MIME_LIMIT = 10
_OLDEST_LIMIT = 5


def classify_error(last_error: str | None) -> str:
    """Bucket ``last_error`` into one of the failure classes.

    Order matters — the first matching rule wins. ``None`` / empty
    error text degrades into the ``other`` bucket so the renderer
    never emits ``None`` as a class label.
    """
    if not last_error:
        return OTHER_CLASS
    for cls, rule in _CLASS_RULES:
        if rule.search(last_error):
            return cls
    return OTHER_CLASS


@dataclass(frozen=True)
class FailureCountBucket:
    """One ``failure_count`` row inside a source's breakdown."""

    failure_count: int
    count: int


@dataclass(frozen=True)
class FailureClassBucket:
    """One ``(failure_class, count)`` row inside a source's breakdown."""

    failure_class: str
    count: int


@dataclass(frozen=True)
class MimeBucket:
    """One ``(mime, count)`` row from the bronze_records LEFT JOIN.

    ``mime`` is ``"(unknown)"`` when the dead-letter item has no
    corresponding ``bronze_records`` row (connectors that fail before
    the bronze write never persist a MIME).
    """

    mime: str
    count: int


@dataclass(frozen=True)
class OldestEntry:
    """One row in the per-source "oldest failures" list."""

    item_id: str
    failure_count: int
    mime: str
    last_attempt: str
    last_error_truncated: str


@dataclass(frozen=True)
class SourceReport:
    """Per-source dead-letter snapshot."""

    source_name: str
    count: int
    by_failure_count: tuple[FailureCountBucket, ...] = field(default_factory=tuple)
    by_failure_class: tuple[FailureClassBucket, ...] = field(default_factory=tuple)
    by_mime_top10: tuple[MimeBucket, ...] = field(default_factory=tuple)
    oldest_5: tuple[OldestEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeadLetterStatusReport:
    """Top-level dead-letter triage snapshot returned by :func:`build_status`."""

    total: int
    per_source: tuple[SourceReport, ...]


def _truncate(text: str | None) -> str:
    """Truncate ``last_error`` to a renderer-friendly length."""
    if not text:
        return ""
    if len(text) <= _ERROR_TRUNC_CHARS:
        return text
    return text[: _ERROR_TRUNC_CHARS - 1] + "…"


def _build_source_report(
    db: sqlite3.Connection,
    source_name: str,
) -> SourceReport:
    """Build the per-source bucket breakdown for ``source_name``."""
    # by_failure_count
    fc_rows = db.execute(
        "SELECT failure_count, COUNT(*) FROM connector_deadletter "
        "WHERE source_name = ? GROUP BY failure_count ORDER BY failure_count ASC "
        "LIMIT 100",  # F63-bounded: failure_count is small integer, 100 is a generous ceiling
        (source_name,),
    ).fetchall()
    by_fc = tuple(FailureCountBucket(failure_count=int(r[0]), count=int(r[1])) for r in fc_rows)

    # by_failure_class — Python-side classification because regex
    # rules are too rich to push down to SQLite cleanly.
    # F63-bounded: classification scan, source-filtered, 1M-row ceiling
    err_rows = db.execute(
        "SELECT last_error FROM connector_deadletter WHERE source_name = ? LIMIT 1000000",
        (source_name,),
    ).fetchall()
    cls_tally: dict[str, int] = {}
    for (err,) in err_rows:
        cls = classify_error(err)
        cls_tally[cls] = cls_tally.get(cls, 0) + 1
    by_class = tuple(
        FailureClassBucket(failure_class=cls, count=cnt)
        for cls, cnt in sorted(cls_tally.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    # by_mime_top10 — LEFT JOIN against bronze_records. When the
    # bronze row is absent (connector failed before bronze write) the
    # MIME bucket is "(unknown)".
    mime_rows = db.execute(
        "SELECT COALESCE(b.mime, '(unknown)') AS mime, COUNT(*) AS n "
        "FROM connector_deadletter dl "
        "LEFT JOIN bronze_records b "
        "  ON b.source_name = dl.source_name AND b.item_id = dl.item_id "
        "WHERE dl.source_name = ? "
        "GROUP BY mime ORDER BY n DESC, mime ASC LIMIT ?",
        (source_name, _TOP_MIME_LIMIT),
    ).fetchall()
    by_mime = tuple(MimeBucket(mime=str(r[0]), count=int(r[1])) for r in mime_rows)

    # oldest_5 — ascending by last_attempt; same LEFT JOIN.
    oldest_rows = db.execute(
        "SELECT dl.item_id, dl.failure_count, dl.last_attempt, dl.last_error, "
        "       COALESCE(b.mime, '(unknown)') AS mime "
        "FROM connector_deadletter dl "
        "LEFT JOIN bronze_records b "
        "  ON b.source_name = dl.source_name AND b.item_id = dl.item_id "
        "WHERE dl.source_name = ? "
        "ORDER BY dl.last_attempt ASC LIMIT ?",
        (source_name, _OLDEST_LIMIT),
    ).fetchall()
    oldest = tuple(
        OldestEntry(
            item_id=str(r[0]),
            failure_count=int(r[1]),
            last_attempt=str(r[2]),
            last_error_truncated=_truncate(r[3]),
            mime=str(r[4]),
        )
        for r in oldest_rows
    )

    total = int(sum(b.count for b in by_fc))
    return SourceReport(
        source_name=source_name,
        count=total,
        by_failure_count=by_fc,
        by_failure_class=by_class,
        by_mime_top10=by_mime,
        oldest_5=oldest,
    )


def build_status(
    db: sqlite3.Connection,
    *,
    source_name: str | None = None,
) -> DeadLetterStatusReport:
    """Build the operator triage snapshot from a live SQLite connection.

    ``source_name`` filters to a single connector (useful when one
    source dominates and the operator wants to drill into one). When
    omitted, every source with at least one dead-letter row appears in
    ``per_source``.

    Connection ownership is the caller's: this function does not
    commit, close, or alter the connection state beyond running
    SELECT queries.
    """
    if source_name is not None:
        names = [source_name]
        # When the filter targets a source with zero rows we still
        # want a zero-row report so the operator sees "no dead-letter
        # state for <source>" rather than a confusing empty top.
    else:
        rows = db.execute(
            "SELECT source_name FROM connector_deadletter GROUP BY source_name ORDER BY source_name ASC LIMIT 1000"
            # F63-bounded: source_name cardinality is tiny (one per registered connector)
        ).fetchall()
        names = [str(r[0]) for r in rows]

    per_source = tuple(_build_source_report(db, n) for n in names)
    total = int(sum(s.count for s in per_source))
    return DeadLetterStatusReport(total=total, per_source=per_source)


def _bucket_to_dict(bucket: FailureCountBucket) -> dict[str, Any]:
    return {"failure_count": bucket.failure_count, "count": bucket.count}


def _class_to_dict(bucket: FailureClassBucket) -> dict[str, Any]:
    return {"class": bucket.failure_class, "count": bucket.count}


def _mime_to_dict(bucket: MimeBucket) -> dict[str, Any]:
    return {"mime": bucket.mime, "count": bucket.count}


def _oldest_to_dict(entry: OldestEntry) -> dict[str, Any]:
    return {
        "item_id": entry.item_id,
        "failure_count": entry.failure_count,
        "mime": entry.mime,
        "last_attempt": entry.last_attempt,
        "last_error_truncated": entry.last_error_truncated,
    }


def _source_to_dict(src: SourceReport) -> dict[str, Any]:
    return {
        "source_name": src.source_name,
        "count": src.count,
        "by_failure_count": [_bucket_to_dict(b) for b in src.by_failure_count],
        "by_failure_class": [_class_to_dict(b) for b in src.by_failure_class],
        "by_mime_top10": [_mime_to_dict(b) for b in src.by_mime_top10],
        "oldest_5": [_oldest_to_dict(e) for e in src.oldest_5],
    }


def render_json(report: DeadLetterStatusReport) -> dict[str, Any]:
    """Build the JSON envelope from the snapshot.

    Stable shape — agents + CLI ``--json`` callers both consume this.
    Mirrors the spec block in the dispatch brief.
    """
    return {
        "total": report.total,
        "per_source": [_source_to_dict(s) for s in report.per_source],
    }


def _failure_count_label(failure_count: int) -> str:
    """Operator-friendly label appended to fc= rows in the human render."""
    if failure_count == 0:
        return "eligible for retry"
    if failure_count == 1:
        return "one retry, eligible"
    if failure_count == 2:
        return "two retries, eligible"
    return "poisoned — needs operator action"


def render_human(report: DeadLetterStatusReport) -> str:
    """Render the report as a human-readable text block.

    Empty-state degrades to a friendly single line so the operator
    sees "no dead-letter state" rather than a header with zero rows.
    """
    if report.total == 0:
        return "Dead-letter summary\n\nTotal: 0 items — no dead-letter state."

    lines: list[str] = ["Dead-letter summary", ""]
    n_sources = len(report.per_source)
    suffix = "source" if n_sources == 1 else "sources"
    lines.append(f"Total: {report.total} items across {n_sources} {suffix}")
    lines.append("")
    lines.append("Per source:")
    for src in report.per_source:
        lines.append(f"  {src.source_name:<50} {src.count:>5} items")
        lines.append("    By failure_count:")
        for fc in src.by_failure_count:
            label = _failure_count_label(fc.failure_count)
            lines.append(f"      fc={fc.failure_count} ({label})".ljust(54) + f"{fc.count:>5}")
        lines.append("    By failure class (best-effort regex on last_error):")
        for cls in src.by_failure_class:
            lines.append(f"      {cls.failure_class}".ljust(54) + f"{cls.count:>5}")
        lines.append("    By MIME (joined against bronze_records.mime):")
        for mb in src.by_mime_top10:
            lines.append(f"      {mb.mime[:46]:<46}".ljust(54) + f"{mb.count:>5}")
        lines.append("    Oldest failures:")
        for entry in src.oldest_5:
            short_id = entry.item_id if len(entry.item_id) <= 24 else entry.item_id[:21] + "..."
            lines.append(f"      {entry.last_attempt}  {entry.mime[:18]:<18}  fc={entry.failure_count}  {short_id}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
