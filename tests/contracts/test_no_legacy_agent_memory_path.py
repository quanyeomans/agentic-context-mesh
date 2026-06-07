"""Regression net for PR 1.2 — the legacy ``agent_memory_path`` /
``set_agent_memory_root_override`` / ``KAIRIX_AGENT_MEMORY_ROOT`` surface is
deleted.

Pre-PR-1.2 the four production callsites that needed an agent's memory
directory resolved via ``kairix.paths.agent_memory_path(<agent>)``, which
hardcoded the ``<root>/<agent>/memory`` convention. PR 1.2 swaps every
callsite onto :func:`kairix.core.agents.scope.get_agent_scope` and deletes
the legacy helpers entirely so a future contributor cannot reintroduce them.

Two structural assertions:
  * the symbols cannot be imported from ``kairix.paths``;
  * a tree-grep over ``kairix/`` shows zero non-test references to the
    symbol names — so a quietly re-added helper would still fail the gate.

Sabotage-proof (executed): re-added ``def agent_memory_path(agent): pass``
to ``kairix/paths.py`` → ``test_agent_memory_path_is_not_importable`` failed
on the ``ImportError`` assertion; ``test_no_production_references_to_legacy_helpers``
failed on the grep count assertion; restored.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


_REPO_ROOT = Path(__file__).resolve().parents[2]
_KAIRIX_TREE = _REPO_ROOT / "kairix"

# Symbols whose presence in any non-test ``kairix/`` file means the legacy
# ``/memory`` subdir convention has been reintroduced.
_LEGACY_SYMBOLS = (
    "agent_memory_path",
    "set_agent_memory_root_override",
    "KAIRIX_AGENT_MEMORY_ROOT",
)


def test_agent_memory_path_is_not_importable() -> None:
    """``from kairix.paths import agent_memory_path`` must raise ImportError.

    Mirrors the F5 spirit — the legacy boundary helper is gone, and the
    helper that replaced it (``get_agent_scope``) lives at
    ``kairix.core.agents.scope``, not on ``kairix.paths``.
    """
    with pytest.raises(ImportError):
        from kairix.paths import (
            agent_memory_path,  # type: ignore[attr-defined]  # noqa: F401 — proving the import fails
        )

    with pytest.raises(ImportError):
        from kairix.paths import (
            set_agent_memory_root_override,  # type: ignore[attr-defined]  # noqa: F401 — proving the import fails
        )


def test_no_production_references_to_legacy_helpers() -> None:
    """Grep ``kairix/`` for the legacy symbol names — zero hits required.

    A future contributor might re-add ``def agent_memory_path(...)`` to
    paths.py or call ``os.environ.get("KAIRIX_AGENT_MEMORY_ROOT")`` in
    a new module; this test catches both without depending on a runtime
    import path.
    """
    proc = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            "-E",
            "|".join(_LEGACY_SYMBOLS),
            "--",
            "kairix/",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    # git grep exits 1 when no matches found — the success case here.
    hits = [line for line in proc.stdout.splitlines() if line.strip()]
    assert hits == [], (
        "PR 1.2 regression: legacy agent_memory_path / KAIRIX_AGENT_MEMORY_ROOT "
        "references reintroduced in production code:\n  "
        + "\n  ".join(hits)
        + "\n\nFix: route through kairix.core.agents.scope.get_agent_scope() instead."
    )


def test_kairix_paths_module_does_not_define_legacy_symbols() -> None:
    """Belt-and-braces: even if the symbols were re-exported elsewhere, the
    canonical ``kairix.paths`` module must not carry them.

    Uses subprocess so the test is hermetic — running the assertion in the
    same interpreter as a previous test that already imported ``kairix.paths``
    would see a cached module object.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import kairix.paths as p; "
            "names = [n for n in "
            f"{list(_LEGACY_SYMBOLS)!r}"
            " if hasattr(p, n)]; "
            "print(','.join(names))",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
    present = [n for n in proc.stdout.strip().split(",") if n]
    assert present == [], (
        f"PR 1.2 regression: ``kairix.paths`` still exposes legacy symbols: {present}. "
        "Delete them and route callers through kairix.core.agents.scope."
    )
