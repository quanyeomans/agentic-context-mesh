"""Live remaining-warm estimate in the ColdStart envelope (#390).

Pre-#390 the envelope returned a static ``estimated_seconds_remaining = 8.0``
during the warm window. Real warm on a fresh container takes ~120s (the
vec_index cold-load alone is ~116s), so agents that back off 8s x N
retries thrash until warm completes — every retry returns the same
no-progress envelope.

These tests pin the live-estimate contract:

  1. At warm-start (elapsed ~= 0s, total = 120s), the envelope reports
     remaining in ``[119, 120]`` — within ms drift of the budget.
  2. As wall time advances (injected via ``time_source``), the envelope
     decrements toward zero so the agent sees progress.
  3. When no WarmProgress is registered (warm not started yet, or warm
     completed and was cleared), the envelope falls back to the historical
     static 8s value — backwards compatibility.

Sabotage-prove discipline (executed before commit, output captured in the
agent report): for each test the production code was mutated (e.g. hardcode
``estimated_seconds_remaining = 8.0`` in ``cold_start.py``; drop the
``time_source`` injection point in ``WarmProgress``), the test was re-run,
the failure was confirmed, then the production code was restored.
"""

from __future__ import annotations

import time

import pytest

from kairix.agents.mcp.cold_start import (
    DEFAULT_ESTIMATED_SECONDS,
    cold_start_envelope,
)
from kairix.platform.warm.state import WarmProgress

pytestmark = pytest.mark.unit


def test_envelope_shows_120s_remaining_at_warm_start() -> None:
    """At warm-start the envelope reports the full 120s budget remaining.

    Pins #390: pre-fix the envelope returned a static 8.0 here, causing
    agents to retry 15x before warm actually completed. Now the envelope
    surfaces the realistic ~120s budget.

    Sabotage-proof (executed before commit): replace the
    ``warm_progress.remaining_seconds()`` computation in
    ``cold_start.py`` with the static
    ``DEFAULT_ESTIMATED_SECONDS`` (8.0). Test fails with
    ``assert 119 <= 8.0 <= 120``. Restore the live computation; test passes.
    """
    progress = WarmProgress(started_at=time.time(), total_estimate_seconds=120.0)

    envelope = cold_start_envelope(tool_name="search", warm_progress=progress)

    remaining = envelope["estimated_seconds_remaining"]
    assert isinstance(remaining, (int, float)), f"estimated_seconds_remaining must be numeric; got {remaining!r}"
    assert 119.0 <= remaining <= 120.0, (
        f"at warm-start the envelope must report ~120s remaining (#390); "
        f"got {remaining!r}. If this is 8.0 the live-progress path is bypassed."
    )


def test_envelope_decrements_as_stages_complete() -> None:
    """Wall-clock advance shrinks ``estimated_seconds_remaining`` toward zero.

    The test injects ``time_source`` as a callable returning a virtual
    clock — F1-clean (no ``monkeypatch.setattr(time, "time", ...)``) and
    the WarmProgress dataclass exposes the seam as a constructor kwarg.

    Sabotage-proof (executed before commit): remove the
    ``time_source: Callable[[], float] = field(default=time.time)`` field
    from WarmProgress and replace ``self.time_source()`` with ``time.time()``.
    The test fails because the virtual clock can no longer be injected —
    the elapsed math reads wall time and reports the trivially-small
    elapsed of the test run itself. Restore the seam; test passes.
    """
    started = 1_000_000.0  # fixed virtual epoch — clock starts here
    advanced_60s = [started + 60.0]
    progress = WarmProgress(
        started_at=started,
        total_estimate_seconds=120.0,
        time_source=lambda: advanced_60s[0],
    )

    envelope = cold_start_envelope(tool_name="search", warm_progress=progress)

    remaining = envelope["estimated_seconds_remaining"]
    assert 59.0 <= remaining <= 61.0, (
        f"after 60s advance the envelope must report ~60s remaining; "
        f"got {remaining!r}. If this is 120 the time_source is not threaded; "
        f"if 8.0 the live-progress path is bypassed."
    )
    elapsed = envelope["elapsed_seconds"]
    assert 59.0 <= elapsed <= 61.0, f"elapsed_seconds must reflect the virtual clock; got {elapsed!r}"


def test_envelope_falls_back_to_static_when_warm_not_started() -> None:
    """When WarmProgress is None, the envelope keeps the static 8s value.

    Pins backwards compatibility: callers that pre-date #390 keep their
    existing behaviour. ``require_ready`` falls through this path when
    ``get_warm_progress()`` returns None — warm has not been registered.

    Sabotage-proof (executed before commit): change the
    ``warm_progress is not None`` guard in ``cold_start.py`` to ``is None``.
    The test fails because the envelope reports 0.0 (the empty WarmProgress
    case) instead of the historical 8.0. Restore the guard; test passes.
    """
    envelope = cold_start_envelope(tool_name="search", warm_progress=None)

    assert envelope["estimated_seconds_remaining"] == DEFAULT_ESTIMATED_SECONDS, (
        f"with warm_progress=None the envelope must keep the static "
        f"{DEFAULT_ESTIMATED_SECONDS}s fallback for backwards compatibility; "
        f"got {envelope['estimated_seconds_remaining']!r}."
    )
    # The elapsed_seconds key is only surfaced when WarmProgress is provided —
    # absence is the explicit fallback signal.
    assert "elapsed_seconds" not in envelope, (
        "elapsed_seconds must not appear in the fallback envelope; presence implies the live path ran."
    )
