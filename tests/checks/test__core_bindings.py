"""Contract tests for the CORE-check binding table (``scripts/checks/_core_bindings.py``).

The binding table is SINGLE-SOURCED from ``pyproject.toml``'s
``[tool.tc_fitness.core_checks.<module>]`` tables — the engine reads those at gate
time, so deriving ``CORE_BINDINGS`` from the same file makes the two physically
incapable of drifting (SGO-202 recommendation 1). These tests pin that contract:

1. The loader reads the config blocks out of a given ``pyproject.toml``.
2. ``CORE_BINDINGS`` *is* the repo's ``[tool.tc_fitness.core_checks]`` table
   (the single-source guard that replaces the old hand-mirror equality test).
3. Every binding key has a ``core:<module>`` catalogue row.
4. Every binding key resolves to a real ``tc_fitness.core_checks`` module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import tomllib

_CHECKS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "checks"
sys.path.insert(0, str(_CHECKS_DIR))

from _core_bindings import CORE_BINDINGS  # noqa: E402
from _rule_catalogue import ALL_ENTRIES  # noqa: E402

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_core_bindings_are_loaded_from_pyproject(tmp_path: Path) -> None:
    """The loader parses ``[tool.tc_fitness.core_checks.<x>]`` out of a pyproject.

    Point the loader at a crafted ``pyproject.toml`` fragment and assert it
    returns exactly the ``core_checks`` table it declares — proving the binding
    table is DERIVED from that file, not hand-mirrored beside it.
    """
    from _core_bindings import _load_core_bindings

    fragment = (
        '[tool.tc_fitness.core_checks.sample_check]\nroots = ["kairix"]\nextensions = [".py"]\nname = "sample-check"\n'
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(fragment, encoding="utf-8")

    loaded = _load_core_bindings(pyproject)

    assert loaded == {
        "sample_check": {
            "roots": ["kairix"],
            "extensions": [".py"],
            "name": "sample-check",
        }
    }


def test_core_bindings_is_the_pyproject_core_checks() -> None:
    """Single-source guard: ``CORE_BINDINGS`` IS the repo's pyproject table.

    Replaces the retired ``test_pyproject_core_check_blocks_mirror_the_python_source``:
    there is no longer a second hand-authored source to compare against, so the
    only meaningful invariant is that the module exposes the pyproject blocks
    verbatim.
    """
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_blocks = data["tool"]["tc_fitness"]["core_checks"]

    assert CORE_BINDINGS == pyproject_blocks


def test_every_core_binding_has_a_catalogue_row() -> None:
    """Each bound module is dispatched by a ``core:<module>`` catalogue row."""
    catalogue_core = {
        entry.check.split("core:", 1)[1] for entry in ALL_ENTRIES if getattr(entry, "check", "").startswith("core:")
    }
    missing = sorted(set(CORE_BINDINGS) - catalogue_core)

    assert not missing, f"bindings without a catalogue row: {missing}"


def test_every_core_binding_resolves_to_a_real_core_module() -> None:
    """Each bound key names a real ``tc_fitness.core_checks`` module."""
    import tc_fitness.core_checks as core_checks

    core_dir = Path(os.path.dirname(core_checks.__file__))
    available = {p.stem for p in core_dir.glob("*.py") if not p.stem.startswith("__")}
    missing = sorted(k for k in CORE_BINDINGS if k not in available)

    assert not missing, f"bindings with no core_checks module: {missing}"
