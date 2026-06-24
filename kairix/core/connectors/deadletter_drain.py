"""Auto-drain of permanently-unprocessable dead-letters (PR-4).

A dead-letter row is "permanently unprocessable" when no retry — however
many ticks pass — can ever succeed: the bytes are a format with no
extractor, a corrupt archive, or binary garbage that was mistakenly fed
to a text decoder. Such rows accrete forever in ``connector_deadletter``,
inflating the operator's poisoned backlog and the ``failed`` counter with
noise that is not actionable.

This module adds a *conservative* drain pass that runs once per connector
per sync tick (wired into :func:`kairix.worker._run_one_connector_batch`,
AFTER the batch, so the pre-extract compat gate has already kept the
tick's own unsupported items out of the queue and the drain only mops up
the historical backlog). For each eligible row it writes a
``skipped_unsupported`` ``documents_media`` outcome (operator visibility,
best-effort) and then :meth:`DeadLetterStore.clear`\\ s the row.

Eligibility is deliberately narrow — see :func:`is_drain_eligible`. The
load-bearing rule: drain ONLY items that are genuinely, permanently
unprocessable — a ``corrupt_zip`` archive, or a MIME that is positively
KNOWN_UNSUPPORTED (no extractor will ever claim it). NEVER drain a
transient / retryable failure (timeout, 403, 404, 429, no-space,
missing-dependency) on a supported / unknown / missing MIME, and — most
importantly — NEVER drain a ``decode`` failure: a decode error on a
SUPPORTED binary (PDF, OOXML docx/pptx/xlsx, image) or on text/octet-
stream is RECOVERABLE operator state. Those items stay in the queue,
re-runnable via ``kairix worker reextract`` through the new compat gate;
auto-drain must never remove a potentially-recoverable item.

The pass is:

* **idempotent** — clearing a row twice is a no-op
  (:meth:`DeadLetterStore.clear` returns ``False`` on the second pass);
* **best-effort** — a failure draining one row logs and continues; the
  drain (and the surrounding sync) never aborts because of one bad row;
* **cheap when clean** — a single ``DeadLetterStore.list`` read short-
  circuits the whole pass when the queue holds no rows for the source.

The key on which dead-letter / bronze rows are queried is the connector
KIND (``connector.name`` — a class constant like ``"sharepoint"``), NOT
the cc_pair routing name; every dead-letter write keys on ``connector.name``
so the drain MUST query on the same value or it silently drains nothing.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from kairix.core.connectors.compat import known_unsupported_mime
from kairix.core.connectors.dead_letter import DeadLetterEntry, DeadLetterStore
from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.observability.dead_letter_status import classify_error
from kairix.core.protocols import BronzeRef

logger = logging.getLogger(__name__)

# Outcome status written to documents_media for a drained item. Mirrors
# kairix.core.connectors.silver._EXTRACTION_STATUS_SKIPPED_UNSUPPORTED;
# named here so the duplicate-string check (F17) never fires on the
# write-call site + the log lines + the tests' shared expectation.
_DRAIN_EXTRACTION_STATUS = "skipped_unsupported"

# The single failure class that is PERMANENTLY unprocessable on its own:
#   corrupt_zip — a truncated / malformed archive; re-fetching the same
#                 bytes always fails the same way.
# `decode` is DELIBERATELY absent: a decode failure is never drained, because
# it is a recoverable problem on a SUPPORTED binary (PDF / OOXML / image —
# re-runnable through the compat gate) or a wrong-codepage problem on text.
_FAILURE_CLASS_CORRUPT_ZIP = "corrupt_zip"

# Drain-eligibility bucket name — the non-corrupt_zip key of the summary
# tally and the return value of ``_bucket_for``. Named once (F17) so the
# tally dict, the bucket dispatch, and the DrainSummary field-feeding all
# reference a single literal rather than repeating it across the module.
_BUCKET_UNSUPPORTED_MIME = "unsupported_mime"

# Per-tick cap on rows scanned. ``DeadLetterStore.list`` is unbounded
# (operator-triage surface, F63 note); the drain runs inside the tick loop
# so it must bound its own work. A generous ceiling — the backlog drains
# over a few ticks rather than one giant pass that could stall a tick.
_DEFAULT_PER_TICK_MAX_ITEMS = 500


@dataclass(frozen=True)
class _DrainCandidate:
    """One dead-letter row joined to its bronze MIME + content_hash.

    ``mime`` is ``None`` when no bronze row exists (fetch-failure
    dead-letters never reach the bronze write); ``content_hash`` is
    ``None`` for those and for pre-Phase-2 bronze rows.
    """

    item_id: str
    last_error: str
    failure_class: str
    mime: str | None
    content_hash: str | None
    raw_path: str | None
    fetched_at: str | None


@dataclass(frozen=True)
class DrainSummary:
    """Per-connector-per-tick drain tally, returned for logging + tests.

    ``left`` is the TRUE post-drain queue depth for the connector —
    ``SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?``
    AFTER the drain — NOT ``scanned - drained``. The scan is capped at
    ``max_items`` so a ``scanned - drained`` figure would understate the
    real backlog whenever the queue is deeper than the per-tick cap; this
    field reflects everything still queued (drainable-but-uncapped rows on
    a later tick, plus every row left for retry).
    """

    connector_name: str
    drained: int
    corrupt_zip: int
    unsupported_mime: int
    left: int


def is_drain_eligible(mime: str | None, failure_class: str) -> bool:
    """Decide whether a dead-letter row is permanently unprocessable.

    An item is drained IFF EITHER:

    * ``failure_class`` is ``corrupt_zip`` — a malformed / truncated
      archive that re-fetch can never repair; OR
    * the bronze ``mime`` is positively KNOWN_UNSUPPORTED per the compat
      classifier (:func:`known_unsupported_mime` — legacy binary Office,
      Visio, ODF, executables, MS-Publisher): no extractor will ever
      claim those, so they are permanent under ANY failure class.

    Every other row is LEFT for retry. In particular the transient
    classes (timeout / forbidden_403 / not_found_404 / rate_limit /
    no_space / missing_dependency / other) are left UNLESS the MIME is
    KNOWN_UNSUPPORTED — a KNOWN_UNSUPPORTED MIME drains even under a
    transient class because no retry can ever succeed.

    Critically, a ``decode`` failure is NEVER drained on its own: a
    decode error on a SUPPORTED binary (application/pdf, the OOXML
    docx/pptx/xlsx, images) is RECOVERABLE operator state — re-runnable
    via ``kairix worker reextract`` through the compat gate — and a
    decode error on text is a recoverable wrong-codepage problem.
    Missing / octet-stream / text / unknown MIME is likewise never
    drained. When in doubt, returns ``False`` — do NOT drain.
    """
    if failure_class == _FAILURE_CLASS_CORRUPT_ZIP:
        return True
    return known_unsupported_mime(mime or "")


def _load_candidates(
    db: sqlite3.Connection,
    connector_name: str,
    *,
    max_items: int,
) -> tuple[_DrainCandidate, ...]:
    """Enumerate dead-letter rows for ``connector_name`` joined to bronze.

    Keys on ``connector_name`` (the connector KIND) for BOTH the
    dead-letter list and the bronze join — the same value every
    dead-letter write used. Capped at ``max_items`` so the per-tick scan
    is bounded even when ``DeadLetterStore.list`` returns a large backlog.
    """
    store = DeadLetterStore(db)
    entries: tuple[DeadLetterEntry, ...] = store.list(connector_name)
    candidates: list[_DrainCandidate] = []
    for entry in entries[:max_items]:
        bronze = db.execute(
            "SELECT mime, content_hash, raw_path, fetched_at FROM bronze_records WHERE source_name = ? AND item_id = ?",
            (connector_name, entry.item_id),
        ).fetchone()
        mime = str(bronze[0]) if bronze is not None and bronze[0] is not None else None
        content_hash = str(bronze[1]) if bronze is not None and bronze[1] is not None else None
        raw_path = str(bronze[2]) if bronze is not None and bronze[2] is not None else None
        fetched_at = str(bronze[3]) if bronze is not None and bronze[3] is not None else None
        candidates.append(
            _DrainCandidate(
                item_id=entry.item_id,
                last_error=entry.last_error,
                failure_class=classify_error(entry.last_error),
                mime=mime,
                content_hash=content_hash,
                raw_path=raw_path,
                fetched_at=fetched_at,
            )
        )
    return tuple(candidates)


def _bucket_for(candidate: _DrainCandidate) -> str:
    """Name the eligibility bucket a drained candidate falls into.

    Used only for the per-connector summary tally (corrupt_zip /
    unsupported_mime). Mirrors the precedence of
    :func:`is_drain_eligible`: corrupt_zip first, else unsupported-MIME
    (the only other way a row can be eligible).
    """
    if candidate.failure_class == _FAILURE_CLASS_CORRUPT_ZIP:
        return _FAILURE_CLASS_CORRUPT_ZIP
    return _BUCKET_UNSUPPORTED_MIME


def _write_outcome(silver: DefaultSilverProcessor, connector_name: str, candidate: _DrainCandidate) -> None:
    """Best-effort ``skipped_unsupported`` documents_media row for visibility.

    Silent no-op inside ``write_extraction_outcome`` when the content_hash
    is missing (fetch-failure / pre-Phase-2 rows) — the ``clear`` is the
    load-bearing mutation; this row is operator-visibility only.
    """
    ref = BronzeRef(
        source_name=connector_name,
        item_id=candidate.item_id,
        raw_path=candidate.raw_path,
        mime=candidate.mime or "",
        fetched_at=candidate.fetched_at or "",
        content_hash=candidate.content_hash,
    )
    silver.write_extraction_outcome(
        raw=ref,
        _source_modified_at=candidate.fetched_at or "",
        extractor_name=None,
        extractor_version=None,
        extraction_status=_DRAIN_EXTRACTION_STATUS,
    )


def _drain_one(
    db: sqlite3.Connection,
    *,
    connector_name: str,
    candidate: _DrainCandidate,
    silver: DefaultSilverProcessor,
    dead_letter: DeadLetterStore,
) -> bool:
    """Drain one eligible row: outcome-write + clear + commit.

    Returns ``True`` when the row was cleared. Best-effort: any exception
    rolls back this row's transaction, logs, and returns ``False`` so the
    surrounding loop continues to the next candidate.
    """
    try:
        _write_outcome(silver, connector_name, candidate)
        dead_letter.clear(connector_name, candidate.item_id)
        db.commit()
    except Exception as exc:  # one bad row must not abort the drain
        db.rollback()
        logger.warning(
            "auto-drain: failed to drain connector=%s item_id=%s mime=%s class=%s — %s",
            connector_name,
            candidate.item_id,
            candidate.mime,
            candidate.failure_class,
            exc,
        )
        return False
    logger.info(
        "auto-drain: drained connector=%s item_id=%s mime=%s class=%s",
        connector_name,
        candidate.item_id,
        candidate.mime,
        candidate.failure_class,
    )
    return True


def _remaining_deadletter_count(db: sqlite3.Connection, connector_name: str) -> int:
    """TRUE post-drain dead-letter depth for ``connector_name``.

    ``SELECT COUNT(*)`` over the whole ``connector_deadletter`` table for
    the source — NOT ``scanned - drained``. The scan window is capped at
    ``max_items`` so a derived figure would understate the backlog when
    the queue is deeper than the cap; this counts everything still queued.
    """
    row = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        (connector_name,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def drain_connector_deadletters(
    db: sqlite3.Connection,
    *,
    connector_name: str,
    silver: DefaultSilverProcessor,
    max_items: int = _DEFAULT_PER_TICK_MAX_ITEMS,
) -> DrainSummary:
    """Drain permanently-unprocessable dead-letters for one connector.

    Runs once per connector per sync tick. Enumerates the connector's
    dead-letter backlog (keyed on ``connector_name`` — the connector
    KIND), drains every :func:`is_drain_eligible` row (outcome-write +
    clear + per-row commit), and leaves every other row for retry. Emits
    a single summary log line and returns the tally.

    ``DrainSummary.left`` is the TRUE post-drain queue depth (a direct
    ``COUNT(*)``), so it stays accurate even when the backlog exceeds the
    per-tick ``max_items`` scan cap.

    Cheap-when-clean guard: an empty backlog short-circuits to a zero
    :class:`DrainSummary` without any per-row work.
    """
    candidates = _load_candidates(db, connector_name, max_items=max_items)
    if not candidates:
        return DrainSummary(connector_name, drained=0, corrupt_zip=0, unsupported_mime=0, left=0)

    dead_letter = DeadLetterStore(db)
    tally = {_FAILURE_CLASS_CORRUPT_ZIP: 0, _BUCKET_UNSUPPORTED_MIME: 0}
    drained = 0
    for candidate in candidates:
        if not is_drain_eligible(candidate.mime, candidate.failure_class):
            continue
        if _drain_one(db, connector_name=connector_name, candidate=candidate, silver=silver, dead_letter=dead_letter):
            drained += 1
            tally[_bucket_for(candidate)] += 1

    summary = DrainSummary(
        connector_name=connector_name,
        drained=drained,
        corrupt_zip=tally[_FAILURE_CLASS_CORRUPT_ZIP],
        unsupported_mime=tally[_BUCKET_UNSUPPORTED_MIME],
        left=_remaining_deadletter_count(db, connector_name),
    )
    if drained:
        logger.info(
            "auto-drain: connector=%s drained=%d (corrupt_zip=%d, unsupported_mime=%d) left_total=%d",
            summary.connector_name,
            summary.drained,
            summary.corrupt_zip,
            summary.unsupported_mime,
            summary.left,
        )
    return summary
