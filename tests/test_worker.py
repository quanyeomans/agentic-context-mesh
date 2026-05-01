"""Tests for kairix.worker — scheduled background task runner.

Covers:
  - _run_embed catches exceptions without crashing
  - _run_entity_seed catches exceptions without crashing
  - _run_health_check catches exceptions without crashing
  - _run_embed calls embed_main on success
  - _run_entity_seed calls store_main on success
  - _run_health_check counts results on success
  - Shutdown signal (SIGTERM / SIGINT) sets running=False and exits the loop
  - Main loop scheduling logic
"""

from __future__ import annotations

import signal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _run_embed() tests
# ---------------------------------------------------------------------------


def test_run_embed_catches_exceptions() -> None:
    """_run_embed should catch exceptions and not re-raise."""
    from kairix.worker import _run_embed

    with patch("kairix.core.embed.cli.main", side_effect=RuntimeError("embed failed")):
        _run_embed()  # should not raise


def test_run_embed_catches_import_error() -> None:
    """_run_embed should catch ImportError without crashing."""
    from kairix.worker import _run_embed

    with patch("kairix.core.embed.cli.main", side_effect=ImportError("no module")):
        _run_embed()


def test_run_embed_calls_embed_main() -> None:
    """_run_embed should call embed CLI main."""
    from kairix.worker import _run_embed

    with patch("kairix.core.embed.cli.main") as mock:
        _run_embed()
    mock.assert_called_once_with()


# ---------------------------------------------------------------------------
# _run_entity_seed() tests
# ---------------------------------------------------------------------------


def test_run_entity_seed_catches_exceptions() -> None:
    """_run_entity_seed should catch exceptions and not re-raise."""
    from kairix.worker import _run_entity_seed

    with patch("kairix.knowledge.store.cli.main", side_effect=RuntimeError("store crawl failed")):
        _run_entity_seed()  # should not raise


def test_run_entity_seed_catches_import_error() -> None:
    """_run_entity_seed should catch ImportError without crashing."""
    from kairix.worker import _run_entity_seed

    with patch("kairix.knowledge.store.cli.main", side_effect=ImportError("no module")):
        _run_entity_seed()


def test_run_entity_seed_calls_store_main() -> None:
    """_run_entity_seed should call store CLI main with crawl args."""
    from kairix.worker import _run_entity_seed

    with patch("kairix.knowledge.store.cli.main") as mock:
        _run_entity_seed()
    mock.assert_called_once()
    args = mock.call_args[0][0]
    assert args[0] == "crawl"


# ---------------------------------------------------------------------------
# _run_health_check() tests
# ---------------------------------------------------------------------------


def test_run_health_check_catches_exceptions() -> None:
    """_run_health_check should catch exceptions and not re-raise."""
    from kairix.worker import _run_health_check

    with patch("kairix.platform.onboard.check.run_all_checks", side_effect=RuntimeError("check failed")):
        _run_health_check()  # should not raise


def test_run_health_check_catches_import_error() -> None:
    """_run_health_check should catch ImportError without crashing."""
    from kairix.worker import _run_health_check

    with patch("kairix.platform.onboard.check.run_all_checks", side_effect=ImportError("no module")):
        _run_health_check()


def test_run_health_check_counts_results() -> None:
    """_run_health_check should count passed/total results."""
    from kairix.worker import _run_health_check

    ok_result = MagicMock(ok=True)
    fail_result = MagicMock(ok=False)
    with patch("kairix.platform.onboard.check.run_all_checks", return_value=[ok_result, fail_result, ok_result]):
        _run_health_check()  # should not raise


# ---------------------------------------------------------------------------
# Module-level checks
# ---------------------------------------------------------------------------


def test_worker_has_required_imports() -> None:
    """Worker module should have os and Path available (regression test)."""
    from kairix import worker

    # These are imported at module level — verify they exist
    assert hasattr(worker, "os")
    assert hasattr(worker, "Path")


def test_worker_constants() -> None:
    """Worker module should define scheduling interval constants."""
    from kairix.worker import EMBED_INTERVAL, ENTITY_SEED_INTERVAL, HEALTH_CHECK_INTERVAL

    assert EMBED_INTERVAL == 3600
    assert ENTITY_SEED_INTERVAL == 86400
    assert HEALTH_CHECK_INTERVAL == 21600


# ---------------------------------------------------------------------------
# Shutdown signal tests
# ---------------------------------------------------------------------------


def test_shutdown_handler_sets_running_false() -> None:
    """The _shutdown signal handler should set running=False via nonlocal."""
    # We test the signal-handler wiring by calling main() and having
    # the patched _run_embed trigger a shutdown via os.kill(SIGTERM).
    import os

    from kairix import worker

    call_count = 0

    def embed_then_signal() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Send SIGTERM to our own process — signal handler should fire
            os.kill(os.getpid(), signal.SIGTERM)

    with (
        patch.object(worker, "_run_embed", side_effect=embed_then_signal),
        patch.object(worker, "_run_entity_seed"),
        patch.object(worker, "_run_health_check"),
        patch.object(worker.time, "sleep"),
        patch.object(worker, "EMBED_INTERVAL", 999999),
        patch.object(worker, "ENTITY_SEED_INTERVAL", 999999),
        patch.object(worker, "HEALTH_CHECK_INTERVAL", 999999),
    ):
        # main() should return after SIGTERM is handled
        worker.main()

    assert call_count >= 1, "embed was never called"


def test_main_loop_runs_embed_on_interval() -> None:
    """Main loop should run embed when interval has elapsed."""
    import os

    from kairix import worker

    call_count = 0

    def embed_counter() -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            os.kill(os.getpid(), signal.SIGTERM)

    entity_called = False

    def entity_then_noop() -> None:
        nonlocal entity_called
        entity_called = True

    health_called = False

    def health_then_noop() -> None:
        nonlocal health_called
        health_called = True

    with (
        patch.object(worker, "_run_embed", side_effect=embed_counter),
        patch.object(worker, "_run_entity_seed", side_effect=entity_then_noop),
        patch.object(worker, "_run_health_check", side_effect=health_then_noop),
        patch.object(worker.time, "sleep"),
        # Set all intervals to 0 so every task fires on every loop iteration
        patch.object(worker, "EMBED_INTERVAL", 0),
        patch.object(worker, "ENTITY_SEED_INTERVAL", 0),
        patch.object(worker, "HEALTH_CHECK_INTERVAL", 0),
    ):
        worker.main()

    # embed is called once on startup + once in the loop = at least 2
    assert call_count >= 2
    assert entity_called, "entity seed should have been called"
    assert health_called, "health check should have been called"


def test_shutdown_handler_via_sigint() -> None:
    """SIGINT should also trigger graceful shutdown."""
    import os

    from kairix import worker

    call_count = 0

    def embed_then_sigint() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            os.kill(os.getpid(), signal.SIGINT)

    with (
        patch.object(worker, "_run_embed", side_effect=embed_then_sigint),
        patch.object(worker, "_run_entity_seed"),
        patch.object(worker, "_run_health_check"),
        patch.object(worker.time, "sleep"),
        patch.object(worker, "EMBED_INTERVAL", 999999),
        patch.object(worker, "ENTITY_SEED_INTERVAL", 999999),
        patch.object(worker, "HEALTH_CHECK_INTERVAL", 999999),
    ):
        worker.main()

    assert call_count >= 1
