"""Bindings for tests/bdd/features/install_system_mode.feature.

All 5 scenarios @future — skipped until the installer impl lands in Plan 1
Tasks 4-8. Pinning the contract early so the feature file is operator-readable
without breaking CI.
"""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "First run creates user, dirs, config, systemd unit",
)
def test_first_run_creates_install() -> None:
    """First-run install creates user, dirs, config, systemd unit."""


@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "Re-running is a no-op",
)
def test_rerun_is_noop() -> None:
    """Re-running kairix init --system is idempotent."""


@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "Refusing to run as non-root with --system",
)
def test_refuses_non_root_system_mode() -> None:
    """--system as non-root fails with actionable error."""


@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "`kairix init verify` reports install health",
)
def test_verify_reports_install_health() -> None:
    """verify subcommand reports every install element OK."""


@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "`kairix uninstall --system` removes everything except data",
)
def test_uninstall_keeps_data() -> None:
    """uninstall --keep-data preserves /var/lib/kairix/index.sqlite."""
