"""pytest-bdd loader for ``install_user_mode.feature``.

Plan 1 task 10 — all 3 scenarios are @current, driven by the step
impls in :mod:`tests.bdd.steps.install_steps`. Runtime gates inside
the step impls skip scenarios that need a live ``systemctl --user``
bus (macOS dev boxes, CI runners without logind) with fix-style
affordances; the binding file itself carries no static skips.

This module follows the ``tests/bdd/test_benchmark_unified_contract.py``
pattern — one test function per scenario, body populated by ``@scenario``
from the .feature file.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

# Belt-and-braces import so the @scenario decorator can resolve the step
# phrases even when the binding file is collected before the conftest
# pytest_plugins entry registers the steps. The pytest_plugins entry in
# tests/conftest.py is the suite-wide source of truth; this import
# documents the coupling at the binding-file level too.
import tests.bdd.steps.install_steps  # noqa: F401 — registers @given/@when/@then via import side effect

FEATURE = str(Path(__file__).parent / "features" / "install_user_mode.feature")


# ---------------------------------------------------------------------------
# Scenario 1 — first run lays down user-mode install
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "First run lays down user-mode install")
def test_first_run_lays_down_user_mode_install():
    """User-mode install lays down config / data / systemd unit under tmp XDG root."""


# ---------------------------------------------------------------------------
# Scenario 2 — user-mode refuses to install global systemd unit
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "User-mode refuses to install global systemd unit")
def test_user_mode_refuses_to_install_global_systemd_unit():
    """User-mode install never writes /etc/systemd/system/kairix.service."""


# ---------------------------------------------------------------------------
# Scenario 3 — system and user installs can coexist
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "System and user installs can coexist on the same host")
def test_system_and_user_installs_can_coexist_on_the_same_host():
    """System-mode install + user-mode install land in distinct trees, both intact."""
