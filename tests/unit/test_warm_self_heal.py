"""Unit tests for ``is_warm_with_self_heal`` (#425).

The 2026-06-07 dogfood report described a 13-hour period of
``app-kairix-1`` returning ColdStart envelopes long after a successful
initial warm-up. Hypothesis: the in-process warm state had diverged
from the persisted flag — flag file exists (healthcheck reads warm),
but the MCP gate's in-memory check read cold and produced the
ColdStart envelope on every request.

These tests pin the self-heal contract:

* in-process warm → fast-path True, no flag read needed
* in-process cold + flag file exists → log a WARN with a warm_status
  snapshot, re-mark in-process warm, return True (the regression
  signature is logged so the next occurrence is debuggable in
  seconds rather than 13 hours)
* in-process cold + flag file missing → return False (genuine cold)

F1/F2-clean: drives ``is_warm_with_self_heal`` via the public
``flag_path`` kwarg seam — no monkey-patching, no env-var manipulation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kairix.platform.warm.state import (
    is_warm_with_self_heal,
    mark_warm,
    reset_warm_state,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_warm_state(tmp_path: Path) -> None:
    """Reset in-process warm state before every test and clear the
    default flag path on exit so a leaking flag from one case doesn't
    contaminate the next.

    Yields nothing — the fixture is autouse for setup + teardown."""
    reset_warm_state()
    yield
    reset_warm_state()


def test_in_process_warm_returns_true_without_flag_read(tmp_path: Path) -> None:
    """When ``is_warm()`` is True the helper short-circuits — no flag
    file is consulted. Locks the fast-path contract for the steady-state
    request flow (every MCP request hits this branch under normal
    operation)."""
    explicit_flag = tmp_path / "warm.flag"
    mark_warm(flag_path=explicit_flag)
    assert is_warm_with_self_heal(flag_path=explicit_flag) is True


def test_cold_and_no_flag_returns_false(tmp_path: Path) -> None:
    """Genuine cold (in-process cold + flag file missing) → False.

    Locks the contract that the helper doesn't manufacture a warm
    state out of nothing."""
    flag = tmp_path / "warm.flag"
    assert not flag.exists()
    assert is_warm_with_self_heal(flag_path=flag) is False


def test_in_process_cold_but_flag_exists_self_heals_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The core #425 regression: persisted flag says warm, in-process
    state says cold. The helper:
      1. Detects the divergence
      2. Logs a WARN with the warm_status snapshot
      3. Re-marks in-process warm
      4. Returns True

    Sabotage-proof: drop the ``mark_warm(flag_path=flag_path)`` call
    in the self-heal branch and the next call's in-process check
    still reads cold — the regression is not actually fixed, just
    masked.
    """
    flag = tmp_path / "warm.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    with caplog.at_level(logging.WARNING, logger="kairix.platform.warm.state"):
        result = is_warm_with_self_heal(flag_path=flag)
    assert result is True
    # Snapshot WARN landed.
    msgs = [r.getMessage() for r in caplog.records]
    assert any("warm-state divergence detected" in m for m in msgs), f"expected divergence WARN; got messages={msgs!r}"
    # A second call now hits the fast path — in-process state was
    # re-marked, so no second WARN.
    caplog.clear()
    second = is_warm_with_self_heal(flag_path=flag)
    assert second is True
    second_msgs = [r.getMessage() for r in caplog.records]
    assert not any("warm-state divergence detected" in m for m in second_msgs), (
        "self-heal should re-mark in-process warm so subsequent calls take the fast path"
    )


def test_divergence_log_includes_warm_status_snapshot(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The divergence WARN includes the warm_status() snapshot so the
    operator can read elapsed/remaining/warming counters at the moment
    of the regression."""
    flag = tmp_path / "warm.flag"
    flag.touch()
    with caplog.at_level(logging.WARNING, logger="kairix.platform.warm.state"):
        is_warm_with_self_heal(flag_path=flag)
    relevant = [r.getMessage() for r in caplog.records if "warm-state divergence" in r.getMessage()]
    assert relevant, "expected the divergence WARN to land"
    # ``warm_status`` returns a dict with at minimum a ``warm`` key; the
    # log must include it so the operator can read it without grepping
    # extra state.
    assert "'warm'" in relevant[0] or "warm:" in relevant[0]


# Note: production default-flag-path resolution (`warm_flag_path()` →
# `KAIRIX_WARM_FLAG_PATH`) is covered by `tests/test_paths.py`; this
# file's self-heal tests pin the divergence-detection contract via the
# public `flag_path` kwarg seam (F2-clean — no env-var monkeypatch).
