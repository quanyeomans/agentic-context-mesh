"""Tests for kairix.worker — scheduled background task runner."""

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_run_embed_catches_exceptions() -> None:
    """_run_embed should catch exceptions and not re-raise."""
    from kairix.worker import _run_embed

    with patch("kairix.embed.cli.main", side_effect=RuntimeError("embed failed")):
        _run_embed()  # should not raise


@pytest.mark.unit
def test_run_entity_seed_catches_exceptions() -> None:
    """_run_entity_seed should catch exceptions and not re-raise."""
    from kairix.worker import _run_entity_seed

    with patch("kairix.store.cli.main", side_effect=RuntimeError("store crawl failed")):
        _run_entity_seed()  # should not raise


@pytest.mark.unit
def test_run_health_check_catches_exceptions() -> None:
    """_run_health_check should catch exceptions and not re-raise."""
    from kairix.worker import _run_health_check

    with patch("kairix.onboard.check.run_all_checks", side_effect=RuntimeError("check failed")):
        _run_health_check()  # should not raise


@pytest.mark.unit
def test_run_embed_calls_embed_main() -> None:
    """_run_embed should call embed CLI main."""
    from kairix.worker import _run_embed

    with patch("kairix.embed.cli.main") as mock:
        _run_embed()
    mock.assert_called_once_with([])


@pytest.mark.unit
def test_worker_has_required_imports() -> None:
    """Worker module should have os and Path available (regression test)."""
    from kairix import worker

    # These are imported at module level — verify they exist
    assert hasattr(worker, "os")
    assert hasattr(worker, "Path")
