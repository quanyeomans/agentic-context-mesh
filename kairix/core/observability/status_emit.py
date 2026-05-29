"""Status emit primitives (ADR-025 §4 Pattern B).

``emit_for`` is the canonical entry-point context manager every pipeline
stage wraps. It enforces P1 (status emit at every stage boundary) by
emitting a fail-safe ``PIPELINE_STAGE_NO_EMIT`` if the body returns
without calling ``ok`` / ``warn`` / ``error`` itself.

Writes to the append-only ``pipeline_item_status`` table (ADR-025 §8).
UPDATE statements against the table are forbidden by P6 — the table is
the audit log. New facts are new rows.

This module's writes are gated by the ``pipeline_status_emit`` feature
flag. When OFF (default), ``emit_for`` is a no-op context manager — no
schema dependency, no rows written, no behaviour change for existing
callers. Phase 1 ships the emit calls everywhere; the cutover to ON is
a separate operator-controlled action per `feature-flag-architecture.md`.
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kairix.core.observability.status_codes import Severity, StatusCode


@dataclass(frozen=True)
class StatusRecord:
    """One row in the ``pipeline_item_status`` timeline.

    Frozen per F42 — emit produces a new record per event; updates are
    forbidden by P6.
    """

    source_name: str
    item_id: str
    stage: str
    status_code: str
    severity: str
    detail_json: str | None
    occurred_at: str
    chunker_version: str | None = None
    extractor_version: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(record: StatusRecord, *, db: sqlite3.Connection) -> None:
    """Append one row to ``pipeline_item_status``.

    The PRIMARY KEY (source_name, item_id, stage, occurred_at) makes
    repeat-on-microsecond-collision an INSERT OR IGNORE situation —
    callers should retry with a fresh occurred_at if they hit the rare
    collision (per P6, never UPDATE).
    """
    db.execute(
        """
        INSERT OR IGNORE INTO pipeline_item_status (
            source_name, item_id, stage, status_code, severity,
            detail_json, occurred_at, chunker_version, extractor_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.source_name,
            record.item_id,
            record.stage,
            record.status_code,
            record.severity,
            record.detail_json,
            record.occurred_at,
            record.chunker_version,
            record.extractor_version,
        ),
    )


class _Emitter:
    """The handle yielded by :func:`emit_for`. Records whether a status
    was emitted; the context manager uses this to fire ``PIPELINE_STAGE_NO_EMIT``
    on bare exit.
    """

    def __init__(
        self,
        source_name: str,
        item_id: str,
        stage: str,
        db: sqlite3.Connection | None,
        chunker_version: str | None = None,
        extractor_version: str | None = None,
    ) -> None:
        self._source_name = source_name
        self._item_id = item_id
        self._stage = stage
        self._db = db
        self._chunker_version = chunker_version
        self._extractor_version = extractor_version
        self._emitted = False

    def ok(self, code: StatusCode, *, detail: dict[str, Any] | None = None) -> None:
        self._emit(code, detail)

    def warn(self, code: StatusCode, *, detail: dict[str, Any] | None = None) -> None:
        self._emit(code, detail)

    def error(self, code: StatusCode, *, detail: dict[str, Any] | None = None) -> None:
        self._emit(code, detail)

    @property
    def emitted(self) -> bool:
        return self._emitted

    def _emit(self, code: StatusCode, detail: dict[str, Any] | None) -> None:
        if code.stage != self._stage:
            # Programmer error: caller used a code from a different stage.
            # We still emit (the timeline is the audit log) but the
            # detail records the mismatch so it shows up at inspect time.
            detail = {**(detail or {}), "_stage_mismatch": f"code.stage={code.stage} ctx.stage={self._stage}"}
        self._emitted = True
        if self._db is None:
            return  # flag-OFF path; no write
        record = StatusRecord(
            source_name=self._source_name,
            item_id=self._item_id,
            stage=self._stage,
            status_code=code.name,
            severity=code.severity.value,
            detail_json=json.dumps(detail) if detail else None,
            occurred_at=_now_iso(),
            chunker_version=self._chunker_version,
            extractor_version=self._extractor_version,
        )
        write_status(record, db=self._db)


@contextmanager
def emit_for(
    source_name: str,
    item_id: str,
    stage: str,
    *,
    db: sqlite3.Connection | None,
    chunker_version: str | None = None,
    extractor_version: str | None = None,
) -> Iterator[_Emitter]:
    """Wrap a pipeline stage. The body must call ``emit.ok/warn/error``
    before exit. If the body returns without emitting, ``PIPELINE_STAGE_NO_EMIT``
    is recorded as a fail-safe. If the body raises, the exception is
    recorded with a generic error code and re-raised.

    ``db=None`` puts the emitter in flag-OFF mode — calls are accepted
    and tracked (for the no-emit fail-safe) but never reach the table.
    """
    emitter = _Emitter(
        source_name=source_name,
        item_id=item_id,
        stage=stage,
        db=db,
        chunker_version=chunker_version,
        extractor_version=extractor_version,
    )
    try:
        yield emitter
    except BaseException as exc:
        # Body raised. If it raised BEFORE emitting, record the failure
        # under PIPELINE_STAGE_NO_EMIT with the exception detail so the
        # timeline isn't blank for the failed pass.
        if not emitter.emitted:
            detail = {
                "exception_class": type(exc).__name__,
                "exception_message": str(exc)[:512],
                "traceback_tail": "".join(traceback.format_exception(exc))[-2048:],
            }
            if db is not None:
                write_status(
                    StatusRecord(
                        source_name=source_name,
                        item_id=item_id,
                        stage=stage,
                        status_code=StatusCode.PIPELINE_STAGE_NO_EMIT.name,
                        severity=Severity.ERROR.value,
                        detail_json=json.dumps(detail),
                        occurred_at=_now_iso(),
                        chunker_version=chunker_version,
                        extractor_version=extractor_version,
                    ),
                    db=db,
                )
        raise
    else:
        # Body exited cleanly. If it never emitted, that's a defect —
        # fire the fail-safe code. Once F74 paydown is at zero, this
        # branch should never trigger in production.
        if not emitter.emitted and db is not None:
            write_status(
                StatusRecord(
                    source_name=source_name,
                    item_id=item_id,
                    stage=stage,
                    status_code=StatusCode.PIPELINE_STAGE_NO_EMIT.name,
                    severity=Severity.ERROR.value,
                    detail_json=json.dumps({"reason": "stage_exited_clean_without_emit"}),
                    occurred_at=_now_iso(),
                    chunker_version=chunker_version,
                    extractor_version=extractor_version,
                ),
                db=db,
            )


# Brief delay helper for guaranteed monotonic occurred_at across rapid
# successive emits within the same stage (PRIMARY KEY collision avoidance).
def _wait_microsecond() -> None:
    """Sleep just long enough to guarantee :func:`_now_iso` advances."""
    time.sleep(0.001)
