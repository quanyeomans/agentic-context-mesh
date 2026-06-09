"""Warm-state tracking + cold-start envelope generation.

An agent that hits a not-yet-warmed kairix gets an immediate structured
response — never a silent 8s block, never an opaque error. The envelope
follows the F21 affordance pattern: every dead-end carries a marked next
step ('retry in N seconds; this is normal startup behaviour').

API:
    is_warm() -> bool          — has run_warm completed successfully?
    warm_status() -> dict      — full state for diagnostic envelope
    cold_start_envelope(tool)  — structured response for a cold-hit call
    trigger_background_warm()  — kick off warm in a daemon thread
    mark_warming() / mark_warm() — called by run_warm internally

Process-global state, threading.Lock-protected. Module-level state is the
right shape here because warm/cold is a process invariant, not a request
concern.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kairix.paths import warm_flag_path

logger = logging.getLogger(__name__)

# Cross-process readiness flag.
#
# The in-process ``_state`` dict below is only visible inside the MCP server
# process. The Docker healthcheck runs as a separate ``docker exec`` shell
# and can't read it directly. ``mark_warm`` writes the flag at
# ``warm_flag_path()`` once the search/embed pipeline is ready;
# ``kairix onboard ready`` reads the same path as the docker compose
# healthcheck signal so ``docker compose up --wait`` blocks until the
# first agent call will actually return real results.
#
# The env-read boundary lives in ``kairix/paths.py::warm_flag_path``
# (F4 — KAIRIX_* env reads stay centralised).


# Estimated wall time for a full warm-up: build_search_pipeline ~2.5s +
# probe_search ~4.5s + graph open ~0s = ~7s. We round up to 8 so the
# agent's "retry in ~N seconds" message slightly over-promises the wait
# rather than under-promises.
_ESTIMATED_WARM_SECONDS = 8.0


# Realistic full-warm budget on a fresh container. The vec_index cold-load
# alone is ~116s on production hardware (#390), so the static 8s estimate
# the in-process envelope returned during warm caused agents to back off
# 8s x N retries and thrash. WarmProgress carries the live elapsed/remaining
# so the envelope reports actual remaining time, defaulting to 120s before
# any stage has completed.
DEFAULT_WARM_TOTAL_ESTIMATE_SECONDS = 120.0


@dataclass(frozen=True)
class WarmProgress:
    """Live progress snapshot for an in-flight warm-up.

    Threaded into the ColdStart envelope so agents see a real remaining
    estimate (#390) rather than the static 8s that pre-dated this type.
    Frozen + explicit-typed so the shape is immutable per F42 spirit.

    Attributes:
        started_at: epoch seconds when warm-up began (typically ``time.time()``).
        total_estimate_seconds: realistic full-warm budget. Defaults to
            :data:`DEFAULT_WARM_TOTAL_ESTIMATE_SECONDS` (120s on fresh
            containers); callers tune via constructor kwarg.
        stages_completed: ordered tuple of stage names that have finished.
            Tuple (not list) so the dataclass stays frozen-hashable.
        time_source: injectable wall-clock so tests can advance virtual
            time without monkey-patching ``time.time``. Production callers
            leave it at the default ``time.time``.
    """

    started_at: float
    total_estimate_seconds: float = DEFAULT_WARM_TOTAL_ESTIMATE_SECONDS
    stages_completed: tuple[str, ...] = field(default_factory=tuple)
    time_source: Callable[[], float] = field(default=time.time)

    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since ``started_at``, never negative."""
        return max(0.0, self.time_source() - self.started_at)

    def remaining_seconds(self) -> float:
        """Estimated seconds left in warm-up.

        ``max(0, total - elapsed)`` per #390 — once the budget is blown
        the envelope reports 0 remaining, not a negative number.
        """
        return max(0.0, self.total_estimate_seconds - self.elapsed_seconds())

    def with_stage_completed(self, stage_name: str) -> WarmProgress:
        """Return a new WarmProgress with ``stage_name`` appended.

        Frozen-immutable update — caller swaps the holder reference rather
        than mutating in-place.
        """
        return WarmProgress(
            started_at=self.started_at,
            total_estimate_seconds=self.total_estimate_seconds,
            stages_completed=(*self.stages_completed, stage_name),
            time_source=self.time_source,
        )


# Module-level holder for the in-flight WarmProgress. None when no warm-up
# has been registered yet — the cold_start envelope falls back to its
# static 8s value in that case, preserving the historical behaviour
# (test 3 in the issue #390 brief).
_progress_lock = threading.Lock()
_warm_progress: WarmProgress | None = None


def set_warm_progress(progress: WarmProgress | None) -> None:
    """Register (or clear) the in-flight WarmProgress snapshot.

    The wiring site at :mod:`kairix.agents.mcp.cli` calls this once when
    warm-up starts (passing a fresh WarmProgress) and updates it via
    ``set_warm_progress(progress.with_stage_completed(name))`` from the
    runner's ``progress_callback``. Tests call it directly with a fixed
    ``time_source`` to drive deterministic remaining-seconds assertions.
    """
    global _warm_progress
    with _progress_lock:
        _warm_progress = progress


def get_warm_progress() -> WarmProgress | None:
    """Return the in-flight WarmProgress, or None when warm-up hasn't started."""
    with _progress_lock:
        return _warm_progress


# State-dict keys — extracted so the same string isn't repeated across
# every read/write site (F17).
_K_WARM = "warm"
_K_WARMING = "warming"
_K_STARTED_AT = "warm_started_at"
_K_COMPLETED_AT = "warm_completed_at"

# Envelope keys.
_K_ELAPSED = "elapsed_seconds"
_K_REMAINING = "estimated_seconds_remaining"


_lock = threading.Lock()
_state: dict[str, Any] = {
    _K_WARM: False,
    _K_WARMING: False,
    _K_STARTED_AT: 0.0,
    _K_COMPLETED_AT: 0.0,
}


def is_warm() -> bool:
    """True if a successful run_warm has completed in this process."""
    with _lock:
        return bool(_state[_K_WARM])


def is_warming() -> bool:
    """True if a warm-up is currently running in the background."""
    with _lock:
        return bool(_state[_K_WARMING])


def mark_warming() -> None:
    """Record that warm-up has started. Called by run_warm."""
    with _lock:
        _state[_K_WARMING] = True
        _state[_K_STARTED_AT] = time.time()


def mark_warm(*, flag_path: Any = None) -> None:
    """Record successful warm-up completion. Called by run_warm on ok=True.

    Also writes the cross-process flag so ``kairix onboard ready``
    (running as the docker healthcheck) returns 0.

    ``flag_path`` is the public DI seam — production callers leave it
    None and the default resolution via ``warm_flag_path()`` (env-aware
    per F4) fires. Integration tests pass an explicit ``tmp_path /
    "warm.flag"`` so the test asserts against its own per-test path
    without relying on ``monkeypatch.setenv("KAIRIX_WARM_FLAG_PATH",
    ...)`` (F2-clean).
    """
    with _lock:
        _state[_K_WARM] = True
        _state[_K_WARMING] = False
        _state[_K_COMPLETED_AT] = time.time()
    flag = flag_path if flag_path is not None else warm_flag_path()
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch(exist_ok=True)
    except OSError as exc:
        # Filesystem failure shouldn't prevent the in-process state from
        # being set. Operators see the degraded healthcheck as the signal.
        logger.warning("could not write warm flag at %s: %s", flag, exc)


def reset_warm_state() -> None:
    """Clear all state. Tests use this between cases."""
    with _lock:
        _state[_K_WARM] = False
        _state[_K_WARMING] = False
        _state[_K_STARTED_AT] = 0.0
        _state[_K_COMPLETED_AT] = 0.0
    set_warm_progress(None)
    try:
        warm_flag_path().unlink(missing_ok=True)
    except OSError:
        pass


def is_warm_persisted() -> bool:
    """Cross-process warm check — does the flag file exist?

    Used by ``kairix onboard ready`` (docker healthcheck) so the deploy
    wait gate can see the MCP server's warm state from outside its
    process. Independent of the in-process ``is_warm()`` so tests can
    drive each on its own.
    """
    return warm_flag_path().exists()


def is_warm_with_self_heal(*, flag_path: Any = None) -> bool:
    """In-process warm check with self-heal against the persisted flag.

    The 2026-06-07 dogfood-reported regression (#425) was a 13-hour
    period of ``app-kairix-1`` returning ColdStart envelopes long
    after a successful initial warm-up. Root-cause analysis pointed
    at in-process state diverging from the persisted flag — the flag
    file said warm, but the in-process ``_state[_K_WARM]`` had been
    cleared.

    This helper bridges the divergence:

      1. If in-process state says warm → return True (fast path).
      2. If in-process state says cold BUT the persisted flag exists,
         the process is in the regression state. Log a WARN with a
         full :func:`warm_status` snapshot (so the next occurrence
         lands a timestamped diagnostic), re-mark in-process warm via
         :func:`mark_warm`, then return True.
      3. If neither is warm → return False (genuine cold).

    The MCP ``warm_gate`` calls this on every request so the
    divergence is caught and healed at the request boundary.
    Operators see the WARN in container logs the moment the
    regression fires, instead of discovering it hours later via
    dogfood reports.
    """
    if is_warm():
        return True
    flag = flag_path if flag_path is not None else warm_flag_path()
    if not flag.exists():
        return False
    snapshot = warm_status()
    logger.warning(
        "warm-state divergence detected: persisted flag exists at %s but in-process "
        "state was cold. Re-marking warm and proceeding. snapshot=%s. "
        "This recovers from the #425 regression class without restarting the process; "
        "the next non-divergent tick will see a clean snapshot.",
        flag,
        snapshot,
    )
    mark_warm(flag_path=flag_path)
    return True


def warm_status() -> dict[str, Any]:
    """Diagnostic envelope for the current warm state.

    Used by tool_warm to report state, and by cold_start_envelope to
    compose the agent-facing response.
    """
    with _lock:
        now = time.time()
        elapsed = round(now - _state[_K_STARTED_AT], 1) if _state[_K_STARTED_AT] else 0.0
        estimated_remaining = max(0.0, round(_ESTIMATED_WARM_SECONDS - elapsed, 1)) if _state[_K_WARMING] else 0.0
        return {
            "warm": bool(_state[_K_WARM]),
            "warming": bool(_state[_K_WARMING]),
            _K_ELAPSED: elapsed,
            _K_REMAINING: estimated_remaining,
        }


def cold_start_envelope(tool_name: str) -> dict[str, Any]:
    """Structured response for an agent that hit a not-yet-warm kairix.

    The agent receives a marked next step ('retry in N seconds') instead
    of a slow first call that anchors 'kairix is flaky' in their memory.
    """
    status = warm_status()
    eta = status[_K_REMAINING] or _ESTIMATED_WARM_SECONDS
    state_label = "warming" if status["warming"] else "cold"
    guidance = (
        f"kairix is {state_label} (one-time cost per process). "
        f"Retry this call in ~{int(eta)} seconds. "
        "Subsequent calls in this process will be fast — the warm-up is amortised."
    )
    return {
        "error": "ColdStart",
        "tool": tool_name,
        "status": state_label,
        _K_ELAPSED: status[_K_ELAPSED],
        _K_REMAINING: eta,
        "guidance": guidance,
        "see_also": ["docs/runbooks/kairix-retrieval-health.md"],
    }


def _default_warm_runner() -> Any:
    """Production warm runner — defers the heavy import until call time."""
    from kairix.platform.warm.runner import run_warm

    return run_warm()


def trigger_background_warm(*, warm_runner: Any = None) -> None:
    """Start a warm-up in a background thread, if not already running.

    Idempotent: calling this when already warm or already warming is a
    no-op. The background thread is daemonised so an exit while warming
    doesn't block process shutdown.

    ``warm_runner`` is the public DI seam: tests pass a fake runner to
    drive the success / failure / exception paths without monkey-patching
    ``kairix.platform.warm.runner.run_warm``. Production callers leave
    it ``None``.
    """
    with _lock:
        if _state[_K_WARM] or _state[_K_WARMING]:
            return
        _state[_K_WARMING] = True
        _state[_K_STARTED_AT] = time.time()

    runner = warm_runner if warm_runner is not None else _default_warm_runner

    def _warm_target() -> None:
        try:
            result = runner()
            if result.ok:
                mark_warm()
            else:
                logger.warning("background warm-up returned ok=False; %d failure(s)", len(result.failures))
                # Stay in 'warming' state — next trigger_background_warm
                # call will retry after another cold-start envelope expires.
                with _lock:
                    _state[_K_WARMING] = False
        except Exception as exc:
            logger.warning("background warm-up raised: %s", exc, exc_info=True)
            with _lock:
                _state[_K_WARMING] = False

    thread = threading.Thread(target=_warm_target, daemon=True, name="kairix-background-warm")
    thread.start()
