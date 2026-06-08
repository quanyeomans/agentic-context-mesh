"""Bindings for tests/bdd/features/install_system_mode.feature.

Plan 1 task 10 — all 5 scenarios are @current, driven by the step
impls in :mod:`tests.bdd.steps.install_steps`. Each scenario carries
runtime gates (NOT static ``@pytest.mark.skip``) so dev boxes without
root + a live systemd user bus skip with fix-style affordances while
root-capable CI / Linux hosts run the full path. See the install_steps
module docstring for the gate-by-gate rationale.
"""

from __future__ import annotations

import pytest
from pytest_bdd import scenario

# Importing the step module here is belt-and-braces over the
# pytest_plugins registration in ``tests/conftest.py``: pytest-bdd's
# scenario decorator only resolves step phrases that have been imported
# into the live process. The conftest-level pytest_plugins entry handles
# this for the suite-wide run; the explicit import documents the
# coupling at the binding file too.
import tests.bdd.steps.install_steps  # noqa: F401 — registers @given/@when/@then via import side effect


@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "First run creates user, dirs, config, systemd unit",
)
def test_first_run_creates_install() -> None:
    """First-run install creates user, dirs, config, systemd unit."""


@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "Re-running is a no-op",
)
def test_rerun_is_noop() -> None:
    """Re-running kairix init --system is idempotent."""


@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "Refusing to run as non-root with --system",
)
def test_refuses_non_root_system_mode() -> None:
    """--system as non-root fails with actionable error."""


@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "`kairix init verify` reports install health",
)
def test_verify_reports_install_health() -> None:
    """verify subcommand reports every install element OK."""


@pytest.mark.bdd
@scenario(
    "features/install_system_mode.feature",
    "`kairix uninstall --system` removes everything except data",
)
def test_uninstall_keeps_data() -> None:
    """uninstall --keep-data preserves /var/lib/kairix/index.sqlite."""
