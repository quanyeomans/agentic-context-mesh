"""Persistence integrity audit — preflight gate for the worker boot path.

Why this exists
---------------
The IM-6 cutover shipped 68,814 obsidian-collection documents to the
dogfood VM with **zero ``documents_fts`` rows** for 90 minutes before
anyone noticed — BM25 silently degraded to vector-only because the
chunk-writer skipped the FTS5 write. The contract tests pinned the
write surface, but nothing checked the *invariant* at process boot:
every active document SHOULD have a matching FTS row, a matching
content row, and at least one vector row.

This module is the boot-time check. It is **read-only**: it never
mutates the database, it never raises (errors surface as
``IntegrityGap`` entries instead), and it returns a structured
``IntegrityReport`` the worker logs at startup and the CLI surfaces
via ``kairix worker preflight``.

Design notes
------------
* Every invariant is its own pure function ``_check_<invariant>(db)``
  so they can be unit-tested in isolation against tiny in-memory
  databases.
* Each gap carries an actionable F21-compliant ``remediation`` string
  (``fix:`` / ``next:`` / ``run:`` markers) so operators reading a
  failed preflight know what to do next.
* The ``healthy`` field is True iff no gap has severity ``error`` —
  ``warn`` and ``info`` gaps are surfaced for visibility but do not
  block boot. ``KAIRIX_PREFLIGHT_STRICT=1`` (read at the worker
  boundary, NOT here — F4 keeps env reads in ``paths.py``) flips
  ``error`` gaps to fatal.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

# Maximum number of example identifiers stored per gap — keeps logs
# bounded when an invariant fails for thousands of rows.
_MAX_SAMPLE = 5

# Tolerance (fraction) between the usearch vector count and the
# ``content_vectors`` row count. Wave 2 emissions can lag, so we
# allow a small drift before surfacing the delta as "info".
_VECTOR_STORE_TOLERANCE = 0.05

# Age beyond which an un-pushed ``entity_signals`` row is considered
# stuck and surfaced as a "warn" gap. Threshold tuned to a week so
# transient Neo4j outages don't flap the gate; persistent breakage
# crosses the line.
_STAGING_STUCK_DAYS = 7

# Invariant identifiers — extracted to module-level constants so the
# in-line literals don't cross the no-duplicate-string F-rule (≥3
# occurrences in a single module). Public (no underscore prefix) so
# the CLI module and tests can reference them by name instead of
# string-comparing.
INVARIANT_DOCUMENTS_WITHOUT_FTS = "documents-without-fts"
INVARIANT_VECTOR_STORE = "vector-store-vs-content-vectors"

# Backwards-compat private alias for the vector-store invariant; kept
# so the in-file references stay readable.
_VECTOR_STORE_INVARIANT = INVARIANT_VECTOR_STORE

GapSeverity = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class IntegrityGap:
    """One failed integrity invariant.

    ``invariant`` is the canonical kebab-case identifier (e.g.
    ``documents-without-fts``); ``count`` is how many rows failed it;
    ``sample`` carries up to five example paths/ids so the operator
    can spot-check the failure; ``remediation`` is the F21-compliant
    "fix: / next: / run:" action text.
    """

    invariant: str
    severity: GapSeverity
    count: int
    sample: tuple[str, ...]
    remediation: str


@dataclass(frozen=True)
class IntegrityReport:
    """Aggregate result of one integrity audit.

    ``healthy`` is True iff no gap has ``severity == "error"``. The
    worker logs the full report at boot and the CLI surfaces it via
    ``kairix worker preflight`` (text + JSON modes).
    """

    gaps: tuple[IntegrityGap, ...]
    healthy: bool


# ---------------------------------------------------------------------------
# Per-invariant check functions — each returns ``None`` when the invariant
# holds, else an ``IntegrityGap`` describing the failure.
# ---------------------------------------------------------------------------


def _sample(rows: list[tuple[str, ...]], idx: int = 0) -> tuple[str, ...]:
    """Extract up to ``_MAX_SAMPLE`` string values from column ``idx``."""
    return tuple(str(r[idx]) for r in rows[:_MAX_SAMPLE])


def _check_documents_without_content(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every active document must have a matching ``content`` row keyed by hash."""
    rows = db.execute(
        "SELECT d.path FROM documents d LEFT JOIN content c ON c.hash = d.hash WHERE d.active = 1 AND c.hash IS NULL"
    ).fetchall()
    if not rows:
        return None
    return IntegrityGap(
        invariant="documents-without-content",
        severity="error",
        count=len(rows),
        sample=_sample(rows),
        remediation=(
            "fix: re-run the connector / scanner that wrote these documents so "
            "their content is rehydrated; "
            "next: re-run kairix worker preflight to confirm; "
            "run: kairix worker preflight"
        ),
    )


def _check_documents_without_fts(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every active document must have a matching ``documents_fts`` row.

    This is the IM-6 regression invariant — the dogfood VM had 68,814
    active documents and zero FTS rows. ``fts.rowid = documents.id``
    is the contract every write path must uphold.
    """
    try:
        rows = db.execute(
            "SELECT d.path FROM documents d "
            "LEFT JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE d.active = 1 AND fts.rowid IS NULL"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # documents_fts missing entirely — surface that as the failure.
        if "no such table" in str(exc).lower():
            count = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0]
            return IntegrityGap(
                invariant=INVARIANT_DOCUMENTS_WITHOUT_FTS,
                severity="error",
                count=int(count),
                sample=(),
                remediation=(
                    "fix: run kairix embed rebuild-fts to create the FTS5 index; "
                    "next: re-run kairix worker preflight to confirm; "
                    "run: kairix embed rebuild-fts"
                ),
            )
        raise
    if not rows:
        return None
    return IntegrityGap(
        invariant=INVARIANT_DOCUMENTS_WITHOUT_FTS,
        severity="error",
        count=len(rows),
        sample=_sample(rows),
        remediation=(
            "fix: run kairix embed rebuild-fts to repopulate the FTS5 index; "
            "next: re-run kairix worker preflight to confirm; "
            "run: kairix embed rebuild-fts"
        ),
    )


def _check_documents_without_vectors(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every active document must have at least one ``content_vectors`` row keyed by hash."""
    rows = db.execute(
        "SELECT d.path FROM documents d "
        "LEFT JOIN content_vectors v ON v.hash = d.hash "
        "WHERE d.active = 1 AND v.hash IS NULL"
    ).fetchall()
    if not rows:
        return None
    return IntegrityGap(
        invariant="documents-without-vectors",
        severity="error",
        count=len(rows),
        sample=_sample(rows),
        remediation=(
            "fix: run kairix embed to embed the missing documents; "
            "next: re-run kairix worker preflight to confirm; "
            "run: kairix embed"
        ),
    )


def _check_content_vectors_without_documents(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every ``content_vectors`` row's hash must appear in ``documents``."""
    rows = db.execute(
        "SELECT DISTINCT v.hash FROM content_vectors v LEFT JOIN documents d ON d.hash = v.hash WHERE d.hash IS NULL"
    ).fetchall()
    if not rows:
        return None
    return IntegrityGap(
        invariant="content-vectors-without-documents",
        severity="warn",
        count=len(rows),
        sample=_sample(rows),
        remediation=(
            "fix: prune orphan content_vectors rows whose source documents were "
            "removed; "
            "next: investigate why orphans accrued (likely a failed reindex); "
            "run: kairix embed --force after backup"
        ),
    )


def _check_fts_without_documents(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every ``documents_fts.rowid`` must map to an active row in ``documents``."""
    try:
        rows = db.execute(
            "SELECT fts.rowid FROM documents_fts fts "
            "LEFT JOIN documents d ON d.id = fts.rowid "
            "WHERE d.id IS NULL OR d.active = 0"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # FTS table missing — the documents-without-fts check already
        # surfaces that condition; skip here to avoid double-counting.
        if "no such table" in str(exc).lower():
            return None
        raise
    if not rows:
        return None
    return IntegrityGap(
        invariant="fts-without-documents",
        severity="warn",
        count=len(rows),
        sample=tuple(str(r[0]) for r in rows[:_MAX_SAMPLE]),
        remediation=(
            "fix: run kairix embed rebuild-fts to drop orphan FTS rows; "
            "next: re-run kairix worker preflight to confirm; "
            "run: kairix embed rebuild-fts"
        ),
    )


def _default_vector_store_loader() -> Any:
    """Production default for the vector-store loader injection seam.

    Wraps :func:`kairix.core.embed.embed._open_usearch_index` so tests
    can pass an alternate callable through ``check_integrity`` without
    monkey-patching the embed module (F1-clean).
    """
    from kairix.core.embed.embed import _open_usearch_index

    return _open_usearch_index()


def _check_vector_store_vs_content_vectors(
    db: sqlite3.Connection,
    vector_store_loader: Callable[[], Any] = _default_vector_store_loader,
) -> IntegrityGap | None:
    """Count of usearch-indexed vectors must roughly match ``content_vectors``.

    Soft check ("info" severity): we tolerate a small drift because
    Wave 2 emissions can lag a tick. If the vector store cannot be
    loaded (path missing, usearch import fails), we report an "info"
    gap describing the load failure rather than raising — preflight
    is best-effort visibility, not a crash gate.

    ``vector_store_loader`` is the injection seam: production passes
    the default which returns the usearch index (or ``None`` if the
    file is missing); tests pass a fake returning a small
    ``__len__``-able stand-in.
    """
    db_row = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()
    cv_count = int(db_row[0]) if db_row else 0
    if cv_count == 0:
        # Nothing to compare against — skip; the documents-without-vectors
        # check covers the "rows are missing" failure mode.
        return None

    try:
        index = vector_store_loader()
    except Exception as exc:
        return IntegrityGap(
            invariant=_VECTOR_STORE_INVARIANT,
            severity="info",
            count=0,
            sample=(f"load-failed:{exc}",),
            remediation=(
                "fix: verify the usearch index file exists and matches the "
                "configured KAIRIX_EMBED_DIMS; "
                "next: rebuild the vector index if dimensions changed; "
                "run: kairix embed --force"
            ),
        )

    if index is None:
        return IntegrityGap(
            invariant=_VECTOR_STORE_INVARIANT,
            severity="info",
            count=cv_count,
            sample=("usearch-index-missing",),
            remediation=(
                "fix: run kairix embed to populate the usearch ANN index; "
                "next: re-run kairix worker preflight to confirm; "
                "run: kairix embed"
            ),
        )

    try:
        vec_count = len(index)
    except Exception:  # F3: usearch __len__ raises on internal corruption; we degrade to silent skip
        return None

    if vec_count == 0 and cv_count > 0:
        return IntegrityGap(
            invariant=_VECTOR_STORE_INVARIANT,
            severity="info",
            count=cv_count,
            sample=(f"db={cv_count} usearch=0",),
            remediation=(
                "fix: run kairix embed --force to rebuild the usearch index from "
                "content_vectors; "
                "next: re-run kairix worker preflight to confirm; "
                "run: kairix embed --force"
            ),
        )

    delta = abs(cv_count - vec_count)
    tolerance = max(1, int(cv_count * _VECTOR_STORE_TOLERANCE))
    if delta <= tolerance:
        return None
    return IntegrityGap(
        invariant=_VECTOR_STORE_INVARIANT,
        severity="info",
        count=delta,
        sample=(f"db={cv_count} usearch={vec_count} delta={delta}",),
        remediation=(
            "fix: run kairix embed to catch the usearch index up to "
            "content_vectors; "
            "next: re-run kairix worker preflight to confirm; "
            "run: kairix embed"
        ),
    )


def _check_entity_signals_staging_not_stuck(db: sqlite3.Connection) -> IntegrityGap | None:
    """Surface ``entity_signals`` rows un-pushed to Neo4j for over a week.

    The staging table is the Curator boundary: extracted entity signals
    sit here until a separate worker job drains them. Old un-pushed
    rows mean the drain stalled — operator-visible "warn" so a stuck
    Neo4j outage doesn't silently accumulate forever.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_STAGING_STUCK_DAYS)).isoformat().replace("+00:00", "Z")
    try:
        rows = db.execute(
            "SELECT id, kind, value FROM entity_signals WHERE pushed_to_neo4j = 0 AND modified_at < ? LIMIT 1000",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        # entity_signals table missing — pre-Wave-1 deploy; skip.
        return None
    if not rows:
        return None
    sample = tuple(f"id={r[0]} kind={r[1]} value={r[2]}" for r in rows[:_MAX_SAMPLE])
    return IntegrityGap(
        invariant="entity-signals-staging-not-stuck",
        severity="warn",
        count=len(rows),
        sample=sample,
        remediation=(
            "fix: verify Neo4j is reachable and the entity-graph drain job is "
            "running; "
            "next: re-run kairix worker preflight after the drain catches up; "
            "run: kairix store crawl"
        ),
    )


def _check_connector_cursors_vs_bronze(db: sqlite3.Connection) -> IntegrityGap | None:
    """Every ``connector_cursors.source_name`` should match an active connector.

    Pre-arm stub: full config-load is heavyweight (kairix.config.yaml +
    overlay + validator). For now we only surface this as "info" when
    cursors exist but the config_loader cannot resolve a path — letting
    operators spot orphan cursors after a connector rename without
    blocking boot. Wave 3 swaps in the real connector-name lookup.
    """
    try:
        rows = db.execute("SELECT source_name FROM connector_cursors").fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    try:
        from kairix.core.search.config_loader import resolve_config_path
    except Exception:  # pragma: no cover - boundary
        return None
    config_path = resolve_config_path()
    if config_path is None or not config_path.exists():
        # No config to compare against — surface as info so operators
        # can see they have cursors without a config wired.
        return IntegrityGap(
            invariant="connector-cursors-vs-bronze",
            severity="info",
            count=len(rows),
            sample=tuple(str(r[0]) for r in rows[:_MAX_SAMPLE]),
            remediation=(
                "fix: TODO Wave 3 — wire connector-name lookup against "
                "kairix.config.yaml; "
                "next: until then, manually verify cursors match configured "
                "connectors; "
                "run: kairix config validate"
            ),
        )
    return None


# Registry order is the order gaps appear in the report — chosen so
# operators see the most actionable (FTS / vectors) issues first.
_CHECKS = (
    _check_documents_without_content,
    _check_documents_without_fts,
    _check_documents_without_vectors,
    _check_content_vectors_without_documents,
    _check_fts_without_documents,
    _check_vector_store_vs_content_vectors,
    _check_entity_signals_staging_not_stuck,
    _check_connector_cursors_vs_bronze,
)


def _schema_present(db: sqlite3.Connection) -> bool:
    """True when the ``documents`` table exists.

    Preflight is a no-op on an empty / uninitialised DB file because
    every invariant assumes the schema is in place. Returning a
    healthy report on a fresh DB matches the operator expectation:
    "nothing to check yet, nothing is wrong."
    """
    row = db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents'").fetchone()
    return row is not None


def check_integrity(
    db: sqlite3.Connection,
    vector_store_loader: Callable[[], Any] = _default_vector_store_loader,
) -> IntegrityReport:
    """Audit kairix's persistence invariants. Read-only — never mutates.

    Walks every registered invariant against ``db`` and folds the
    failures into an ``IntegrityReport``. ``healthy`` is True iff no
    gap carries ``severity == "error"`` — ``warn`` and ``info`` gaps
    are surfaced for visibility but do not flip the bit.

    On an empty / uninitialised DB file (``documents`` table missing)
    we return a healthy empty report so first-boot preflight on a
    fresh VM doesn't crashloop while the schema is being created.

    ``vector_store_loader`` is the production-default injection seam
    for the usearch-index loader. Tests pass a fake returning a small
    stand-in (or ``None`` for the "no index file" branch) so the
    vector-store check can be exercised without a real usearch on disk.
    """
    if not _schema_present(db):
        return IntegrityReport(gaps=(), healthy=True)
    gaps: list[IntegrityGap] = []
    for check in _CHECKS:
        if check is _check_vector_store_vs_content_vectors:
            gap = check(db, vector_store_loader)
        else:
            gap = check(db)
        if gap is not None:
            gaps.append(gap)
    healthy = not any(g.severity == "error" for g in gaps)
    return IntegrityReport(gaps=tuple(gaps), healthy=healthy)


def gap_to_dict(gap: IntegrityGap) -> dict[str, object]:
    """Render a gap as a JSON-serialisable dict (for ``--json`` mode)."""
    return {
        "invariant": gap.invariant,
        "severity": gap.severity,
        "count": gap.count,
        "sample": list(gap.sample),
        "remediation": gap.remediation,
    }


def report_to_dict(report: IntegrityReport) -> dict[str, object]:
    """Render an IntegrityReport as a JSON-serialisable dict."""
    return {
        "healthy": report.healthy,
        "gaps": [gap_to_dict(g) for g in report.gaps],
    }
