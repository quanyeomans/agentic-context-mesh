"""CORE-check binding config — DERIVED from ``[tool.tc_fitness.core_checks.<module>]``.

``CORE_BINDINGS`` is the per-CORE-check config the catalogue rows and the
equivalence harness read. It is **single-sourced** from ``pyproject.toml``'s
``[tool.tc_fitness.core_checks.*]`` tables — the same blocks the tc-fitness engine
reads at gate time (via ``load_core_check_configs``). Loading it from that one file
(rather than hand-mirroring the literals here) makes the Python-side view and the
engine-side config physically incapable of drifting: editing the pyproject block is
the only edit, and it updates both (SGO-202 recommendation 1).

Two binding provenances live in the pyproject table. MIGRATED bindings replaced a
retired local reimplementation and were gated on equivalence — same raw
``collect_violations()`` set on kairix's tree AND positive/negative differential
fixture parity. ``no_logging_secrets`` is the canonical KEPT-LOCAL case (the CORE
detector diverged, so it stays local, unbound). NET-NEW bindings
(``pattern_chokepoint``, ``integrity_state_predicate``) have no local predecessor;
they are forward regression guards authored directly in tc-fitness and gated on zero
violations + zero false-positives over kairix's current tree. This module holds no
config literals of its own — the CODEOWNERS-gated pyproject block is the source, so
an agent cannot self-exempt by editing the Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

# Repo root is two levels up from ``scripts/checks/_core_bindings.py``.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_core_bindings(pyproject_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the ``[tool.tc_fitness.core_checks]`` table from ``pyproject.toml``.

    Parses the given ``pyproject.toml`` (defaulting to the repo's, resolved
    relative to this module so the path is correct from any cwd) and returns its
    ``core_checks`` sub-table — a mapping of CORE module name to the config block
    the engine injects via ``build(config, repo_root=...)``. A pyproject with no
    ``[tool.tc_fitness.core_checks]`` table yields an empty mapping.
    """
    path = pyproject_path if pyproject_path is not None else _REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data.get("tool", {}).get("tc_fitness", {}).get("core_checks", {})


#: Each key is the CORE module name (the part after ``core:``); the value is the
#: config block the engine injects. Single-sourced from ``pyproject.toml`` — see the
#: module docstring.
CORE_BINDINGS: dict[str, dict[str, Any]] = _load_core_bindings()
