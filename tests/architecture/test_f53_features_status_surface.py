"""Unit tests for F53 (``scripts/checks/check_f53_features_status_surface.py``).

F53 enforces that the operator surface for feature flags exists:

  1. ``kairix/cli.py:COMMANDS`` has a ``"features"`` entry.
  2. ``kairix/agents/mcp/server.py`` has ``@server.tool()``
     ``tool_features_status``.
  3. Neither appears in the F30 baseline as missing an outcome test.

These tests exercise the AST-presence helpers against synthetic source
files. Vacuous-green when ``kairix.core.features`` is not importable.

Each test carries an inline sabotage-proof.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f53_features_status_surface.py"


def _load_detector() -> object:
    """Load the F53 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f53_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f53_detector"] = module
    spec.loader.exec_module(module)
    return module


def test_commands_has_features_returns_true_when_present(tmp_path: Path) -> None:
    """COMMANDS containing a 'features' key is the positive contract.

    Sabotage proof: remove the key-name check in ``_commands_has_features``
    and this assertion would flip to False.
    """
    detector = _load_detector()
    cli = tmp_path / "cli.py"
    cli.write_text(
        """COMMANDS: dict[str, tuple[str, str, bool]] = {
    "features": ("kairix.core.features.cli", "main", True),
    "search": ("kairix.core.search.cli", "main", True),
}
""",
        encoding="utf-8",
    )
    assert detector._commands_has_features(cli) is True  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_commands_missing_features_returns_false(tmp_path: Path) -> None:
    """COMMANDS without 'features' is the negative contract.

    Sabotage proof: change the equality from ``"features"`` to ``"any"``
    and this assertion flips.
    """
    detector = _load_detector()
    cli = tmp_path / "cli.py"
    cli.write_text(
        """COMMANDS: dict[str, tuple[str, str, bool]] = {
    "search": ("kairix.core.search.cli", "main", True),
}
""",
        encoding="utf-8",
    )
    assert detector._commands_has_features(cli) is False  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_mcp_has_tool_features_status_returns_true(tmp_path: Path) -> None:
    """An @server.tool()-decorated tool_features_status function passes.

    Sabotage proof: change the decorator check to require ``@app.tool``
    instead of ``@server.tool`` and this assertion flips to False.
    """
    detector = _load_detector()
    mcp = tmp_path / "server.py"
    mcp.write_text(
        """server = FastMCP("kairix")

@server.tool()
def tool_features_status() -> dict:
    return {"flags": []}
""",
        encoding="utf-8",
    )
    assert detector._mcp_has_tool_features_status(mcp) is True  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_mcp_missing_tool_features_status_returns_false(tmp_path: Path) -> None:
    """A different decorated tool does NOT satisfy F53.

    Sabotage proof: weaken the function-name check from
    ``tool_features_status`` to any name starting with ``tool_`` and
    this assertion flips.
    """
    detector = _load_detector()
    mcp = tmp_path / "server.py"
    mcp.write_text(
        """server = FastMCP("kairix")

@server.tool()
def tool_search(q: str) -> dict:
    return {"hits": []}
""",
        encoding="utf-8",
    )
    assert detector._mcp_has_tool_features_status(mcp) is False  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_remediation_carries_action_markers() -> None:
    """F53's REMEDIATION must carry F21 ``fix:`` / ``next:`` / ``run:`` markers."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem


def test_vacuous_green_when_module_absent() -> None:
    """The detector returns 0 when kairix.core.features is absent.

    PR-2 may not be landed yet; F53 must not block the gate then.
    """
    detector = _load_detector()
    # In this worktree, kairix.core.features is absent; verifies the
    # short-circuit branch.
    if not detector._features_module_available():  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
        assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
