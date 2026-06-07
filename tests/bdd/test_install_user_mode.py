"""pytest-bdd loader for ``install_user_mode.feature``.

Task 3 of Plan 1 (kairix self-installer) — wires the user-mode install
feature file into the pytest-bdd collector. Each Scenario gets one
``@scenario`` declaration here; the step bodies will live in
``tests/bdd/steps/install_steps.py`` (Task 10 of the same plan).

All 3 scenarios are tagged ``@future`` in the .feature file and skipped
here via ``@pytest.mark.skip`` because the implementation slices
(Tasks 4-8: system_user, dirs, systemd, installer, CLI) have not yet
landed. The skip rationale points back at Plan 1 so a developer
running the suite sees why each test is dormant.

This module follows the ``tests/bdd/test_benchmark_unified_contract.py``
pattern — one test function per scenario, body populated by ``@scenario``
from the .feature file.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "install_user_mode.feature")


# ---------------------------------------------------------------------------
# Scenario 1 — first run lays down user-mode install
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@scenario(FEATURE, "First run lays down user-mode install")
def test_first_run_lays_down_user_mode_install():
    """Body populated by @scenario from the .feature file (skipped — Plan 1 future)."""


# ---------------------------------------------------------------------------
# Scenario 2 — user-mode refuses to install global systemd unit
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@scenario(FEATURE, "User-mode refuses to install global systemd unit")
def test_user_mode_refuses_to_install_global_systemd_unit():
    """Body populated by @scenario from the .feature file (skipped — Plan 1 future)."""


# ---------------------------------------------------------------------------
# Scenario 3 — system and user installs can coexist
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@pytest.mark.skip(reason="future — Plan 1; impl lands in subsequent tasks")
@scenario(FEATURE, "System and user installs can coexist on the same host")
def test_system_and_user_installs_can_coexist_on_the_same_host():
    """Body populated by @scenario from the .feature file (skipped — Plan 1 future)."""
