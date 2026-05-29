"""Stage Protocol + StageRunner variants — ADR-026 Track A main.

Every pipeline-step body today is a snippet of code inline in either
``ConnectorPipeline._process_item`` (kairix/core/connectors/pipeline.py)
or one of ``MaintenanceScheduler._safe_*`` (kairix/core/maintenance/
scheduler.py). Each repeats the same scaffolding: emit start, run the
work, catch the exception class the stage cares about, decide whether
to dead-letter or re-raise, emit the outcome.

ADR-026 §4 collapses that scaffolding into two layers:

* **`Stage`** — a Protocol every step implements. The stage body is a
  pure transform from :class:`StageContext` to :class:`StageOutcome`.
  No emit calls, no log statements, no telemetry awareness. The
  stage's only responsibility is the work + a one-method exception
  classifier so the runner knows which :class:`StatusCode` to emit on
  failure.
* **`StageRunner`** — wraps a `Stage`. Two variants mirror the two
  existing failure-handling semantics:

  - :class:`IsolatedStageRunner` absorbs every exception into the
    status timeline and returns an outcome — never raises. Mirrors
    :func:`MaintenanceScheduler._safe_*` semantics.
  - :class:`BatchTransactionStageRunner` exposes two methods:
    :meth:`run_per_item` (fetch/extract style — catch + dead-letter +
    continue) and :meth:`run_batch_critical` (silver/chunk/entity
    style — emit + re-raise so the caller rolls back the per-batch
    SQLite transaction).

Track A pre-work A.0a/b/c landed the prerequisites
(:class:`EntityGraphSink.buffer` rename, :class:`ChunkWriter` moved to
``kairix.core.protocols``, typed :class:`StageContext` subclasses).
Track A main lands the abstraction. A.3 / A.4 migrate the 12
existing stages — that work happens in subsequent commits so each
stage migration can be reviewed independently and rolled back if it
surfaces an unforeseen edge case in the production transaction
semantics.

ADR-026 supersedes ADR-025 §4 Pattern B: every new pipeline step goes
through a runner, not a hand-rolled ``with emit_for(...)`` block. F74
(landing alongside the migrations) will mechanically enforce this.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.observability.stage_contexts import StageContext
from kairix.core.observability.status_codes import Severity, StatusCode
from kairix.core.observability.status_emit import emit_for


@dataclass(frozen=True)
class StageOutcome:
    """The unified return envelope for every stage.

    Replaces the heterogeneous return shapes (``None`` / ``int`` /
    ``bool`` / ``BronzeRef`` / ``ExtractedDocument`` / ``SilverOutput``)
    that bedevilled the existing inline scaffolding. Carries:

    * ``code`` — the :class:`StatusCode` for this outcome. Drives the
      emit (via :attr:`StatusCode.severity`) AND the caller's control
      flow (the orchestrator breaks the chain when severity is ERROR).
    * ``output`` — the stage's "useful" return value (e.g. the
      ``RawArtefact`` produced by FetchStage, the ``ExtractedDocument``
      produced by ExtractStage). Threaded forward into the next
      stage's :class:`StageContext`.
    * ``detail`` — emit-forwarded metadata (e.g. ``bytes_written``,
      ``chunk_count``, ``exception_class``). The runner copies this
      into the emit detail JSON; it never participates in dispatch
      (per ADR-025 P2).
    """

    code: StatusCode
    output: Any = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Stage(Protocol):
    """A pure transform from :class:`StageContext` to :class:`StageOutcome`.

    The stage body is the only thing that changes per migration. The
    runner handles emit, exception classification, dead-letter
    routing, and transaction semantics — none of which the stage body
    is aware of.

    Two members:

    * :attr:`name` — matches the existing ``STAGE_*`` constants
      (``"fetch"``, ``"extract"``, ``"silver"``, …). Used as the
      ``stage`` field on every emitted :class:`StatusRecord` row.
    * :meth:`process` — pure transform. Returns an :class:`StageOutcome`
      with the appropriate code + output. On exceptions the runner
      catches and consults :meth:`classify_exception`.
    * :meth:`classify_exception` — maps a raised exception class to a
      :class:`StatusCode` so the emit carries the right severity +
      retry_eligible flag. Centralises the mapping per stage rather
      than per call site (ADR-025 P3).
    """

    name: str

    def process(self, ctx: StageContext) -> StageOutcome:
        """Pure transform. Must not call ``emit_for`` or log — the runner does.

        Implementations should either return an :class:`StageOutcome`
        with the appropriate code (OK / WARN / ERROR per
        :attr:`StatusCode.severity`) or raise — the runner catches.
        """
        ...

    def classify_exception(self, exc: BaseException) -> StatusCode:
        """Map a raised exception to a :class:`StatusCode`.

        Default-impl returns :attr:`StatusCode.PIPELINE_STAGE_NO_EMIT` —
        the audit code for "stage blew up and didn't classify". Every
        concrete stage SHOULD override this so the exception class is
        recorded as a stage-specific error (e.g. ``DiskFullError`` →
        :attr:`StatusCode.EXTRACT_DISK_FULL`).
        """
        ...


def _emit_outcome(emit: Any, code: StatusCode, detail: Mapping[str, Any] | None) -> None:
    """Dispatch ``emit.ok`` / ``emit.warn`` / ``emit.error`` from a code's severity.

    Centralises the severity branch so the runner methods read at a
    glance. ``emit`` is the private ``_Emitter`` yielded by
    :func:`~kairix.core.observability.status_emit.emit_for`; the
    runner is the only caller so we typehint as ``Any`` and trust the
    contract.
    """
    payload = dict(detail) if detail else None
    if code.severity == Severity.OK:
        emit.ok(code, detail=payload)
    elif code.severity == Severity.WARN:
        emit.warn(code, detail=payload)
    else:
        emit.error(code, detail=payload)


def _exception_detail(exc: BaseException) -> dict[str, Any]:
    """Truncated exception payload safe to land in the timeline JSON.

    Cap the message at 512 chars — the timeline is for triage, not
    full traceback capture (the worker log retains the full trace).
    """
    return {
        "exception_class": type(exc).__name__,
        "exception_message": str(exc)[:512],
    }


class IsolatedStageRunner:
    """Stage runner that absorbs every exception into the status timeline.

    Mirrors the existing :func:`MaintenanceScheduler._safe_*` semantics —
    a maintenance pass that hits a corrupt FTS index logs + emits, then
    the next maintenance pass continues. The runner never raises; the
    returned :class:`StageOutcome` is the audit record.

    ``db=None`` is the flag-OFF mode: emit calls are still made (so the
    PIPELINE_STAGE_NO_EMIT fail-safe still fires), but the underlying
    write to ``pipeline_item_status`` is suppressed. Matches the
    ``pipeline_status_emit`` feature flag default.
    """

    def __init__(self, stage: Stage, *, db: sqlite3.Connection | None = None) -> None:
        self._stage = stage
        self._db = db

    def run(self, ctx: StageContext) -> StageOutcome:
        """Run the stage; always return an outcome — never raise."""
        with emit_for(ctx.source_name, ctx.item_id, self._stage.name, db=self._db) as emit:
            try:
                outcome = self._stage.process(ctx)
            except Exception as exc:  # NOSONAR S5754 — runner contract: absorb + route via classify_exception
                code = self._stage.classify_exception(exc)
                detail = _exception_detail(exc)
                _emit_outcome(emit, code, detail)
                return StageOutcome(code=code, output=None, detail=detail)
            _emit_outcome(emit, outcome.code, outcome.detail)
            return outcome


class BatchTransactionStageRunner:
    """Stage runner with two failure semantics on the same stage.

    * :meth:`run_per_item` — fetch/extract semantics. On exception:
      classify, record to dead_letter, emit, return the outcome with
      ``dead_lettered=True`` in detail. Never raises — the caller
      breaks the per-item chain on ``severity == ERROR``.
    * :meth:`run_batch_critical` — silver/chunk/entity semantics. On
      exception: classify, emit, then re-raise so the caller rolls
      back the per-batch SQLite transaction. The emit is the audit
      trail; the re-raise is the control-flow signal.

    Why two methods on one runner rather than two runner classes: the
    *stage's* classification is the same regardless of failure
    handling — a SQLite ``IntegrityError`` is always classified the
    same way. Only the runner's reaction differs. Keeping both methods
    here lets a caller hold one runner per stage and pick the
    semantics at the call site.
    """

    def __init__(
        self,
        stage: Stage,
        *,
        db: sqlite3.Connection | None = None,
        dead_letter: DeadLetterStore | None = None,
    ) -> None:
        self._stage = stage
        self._db = db
        self._dead_letter = dead_letter

    def run_per_item(self, ctx: StageContext) -> StageOutcome:
        """Per-item failure semantics: catch + dead-letter + return outcome.

        The caller checks ``outcome.code.severity`` and breaks the
        per-item chain on ERROR — sibling items still process, the
        batch's cursor advances. Mirrors the fetch + extract branches
        of :func:`ConnectorPipeline._process_item`.

        Requires ``dead_letter`` to have been passed to ``__init__`` —
        run_per_item without a dead_letter store would silently drop
        the per-item failure record, which the production worker
        cannot tolerate. The check fires eagerly so a misconfigured
        runner blows up at construction time, not on the first
        failing item.
        """
        if self._dead_letter is None:
            raise ValueError(
                "BatchTransactionStageRunner.run_per_item requires dead_letter. "
                "fix: pass dead_letter=DeadLetterStore(db=...) to __init__. "
                "next: re-run the test that hit this. "
                "run: see kairix/core/connectors/dead_letter.py for the canonical store."
            )
        with emit_for(ctx.source_name, ctx.item_id, self._stage.name, db=self._db) as emit:
            try:
                outcome = self._stage.process(ctx)
            except Exception as exc:  # NOSONAR S5754 — runner contract: absorb + dead-letter, never raise
                code = self._stage.classify_exception(exc)
                detail = {**_exception_detail(exc), "dead_lettered": True}
                self._dead_letter.record(ctx.source_name, ctx.item_id, f"{self._stage.name}: {exc}")
                _emit_outcome(emit, code, detail)
                return StageOutcome(code=code, output=None, detail=detail)
            _emit_outcome(emit, outcome.code, outcome.detail)
            return outcome

    def run_batch_critical(self, ctx: StageContext) -> StageOutcome:
        """Batch-critical failure semantics: emit then re-raise.

        Mirrors the silver / chunk_write / entity_buffer branches of
        :func:`ConnectorPipeline._process_item`. The emit captures the
        failure on the timeline so triage sees it; the re-raise lets
        the caller's outer try/except roll back the entire batch's
        SQLite transaction (chunks + cursor + bronze all rewind
        together — atomicity).
        """
        with emit_for(ctx.source_name, ctx.item_id, self._stage.name, db=self._db) as emit:
            try:
                outcome = self._stage.process(ctx)
            except Exception as exc:
                code = self._stage.classify_exception(exc)
                detail = {**_exception_detail(exc), "rolled_back": True}
                _emit_outcome(emit, code, detail)
                raise
            _emit_outcome(emit, outcome.code, outcome.detail)
            return outcome


__all__ = [
    "BatchTransactionStageRunner",
    "IsolatedStageRunner",
    "Stage",
    "StageOutcome",
]
