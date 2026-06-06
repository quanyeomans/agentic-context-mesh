"""F53: operator surface for feature flags exists.

Operations affordance — flags are useless if the operator can't see what
is enabled. F53 enforces:

  1. ``kairix/cli.py:COMMANDS`` has a ``"features"`` entry.
  2. ``kairix/agents/mcp/server.py`` has a function named
     ``tool_features_status`` decorated by ``@server.tool()``.
  3. Neither surface appears in F30's grandfather list as missing an
     outcome test (i.e. both have outcome tests).

Binary presence check, no per-file baseline. Vacuous-green when
``kairix/core/features`` is not importable (PR-2 may not have landed).

Per F21, REMEDIATION carries ``fix:`` / ``next:`` / ``run:`` markers.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CLI_REL_PATH = Path("kairix/cli.py")
MCP_REL_PATH = Path("kairix/agents/mcp/server.py")
F30_BASELINE_REL = Path(".architecture/baseline/f30-operator-outcome-tests-files.txt")

REMEDIATION = """F53: operator surface missing for feature flags.
fix: ensure kairix/cli.py:COMMANDS includes a 'features' entry (CLI
     subcommand) AND kairix/agents/mcp/server.py has a function
     'tool_features_status' decorated with @server.tool(). Both must
     also have F30-compliant outcome tests (NOT appear in the F30
     baseline as missing an outcome test).
next: see docs/architecture/feature-flag-architecture.md §3.5 (operator
      surface) + §6 (F53 mechanics).
run: bash scripts/checks/check-f53-features-status-surface.sh

Pass example:
  # kairix/cli.py
  COMMANDS: dict[str, tuple[...]] = {
      "features": (run_features_status, "show feature flag state"),
      ...
  }
  # kairix/agents/mcp/server.py
  @server.tool()
  def tool_features_status() -> dict[str, Any]:
      return features_status().to_envelope()

Forbidden example:
  # kairix/cli.py COMMANDS has no 'features' entry; operators can only
  # read flag state by grep-ing the registry source. MCP also missing
  # tool_features_status — agents have no programmatic surface either."""


def _commands_has_features(cli_path: Path) -> bool:
    """Return True if kairix/cli.py COMMANDS dict has a 'features' key."""
    try:
        tree = ast.parse(cli_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        target_value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "COMMANDS":
            target_value = node.value
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in node.targets):
            target_value = node.value
        if target_value is None or not isinstance(target_value, ast.Dict):
            continue
        for key in target_value.keys:
            if isinstance(key, ast.Constant) and key.value == "features":
                return True
    return False


def _is_server_tool_decorator(dec: ast.expr) -> bool:
    """Return True if ``dec`` is ``@server.tool()`` or ``@server.tool``."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "server"
    )


def _mcp_has_tool_features_status(mcp_path: Path) -> bool:
    """Return True if server.py declares ``tool_features_status`` under @server.tool()."""
    try:
        tree = ast.parse(mcp_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != "tool_features_status":
            continue
        if any(_is_server_tool_decorator(d) for d in node.decorator_list):
            return True
    return False


def _f30_baseline_entries() -> set[str]:
    """Return the set of file/path entries in the F30 baseline."""
    path = REPO_ROOT / F30_BASELINE_REL
    if not path.exists():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def _features_surfaces_have_outcome_tests() -> bool:
    """Return True if neither the CLI 'features' subcommand nor the MCP
    'features_status' tool appears in the F30 baseline as missing an
    outcome test.

    F30 anchors CLI-subcommand violations at the implementation file and
    MCP-tool violations at ``kairix/agents/mcp/server.py/@tool:<name>``.
    """
    baseline = _f30_baseline_entries()
    # CLI features subcommand — most-likely module path under kairix/core/features/cli.py
    forbidden_substrings = (
        "kairix/core/features/cli.py",
        "kairix/agents/mcp/server.py/@tool:features_status",
    )
    return not any(any(sub in entry for sub in forbidden_substrings) for entry in baseline)


def _features_module_available() -> bool:
    """Return True if kairix.core.features is importable (PR-2 has landed).

    Defensive import to detect PR-2 readiness; gate stays vacuous-green
    if absent.
    """
    try:
        import kairix.core.features  # noqa: F401 — presence-probe import, no symbol use
    except ImportError:
        return False
    return True


def main() -> int:
    """Return 0 when clean / vacuous-green; 1 when surfaces are missing."""
    if not _features_module_available():
        print("ok [arch:f53-features-status-surface] — kairix.core.features absent; vacuous-green.")
        return 0

    cli_path = REPO_ROOT / CLI_REL_PATH
    mcp_path = REPO_ROOT / MCP_REL_PATH
    if not cli_path.exists() or not mcp_path.exists():
        print("ok [arch:f53-features-status-surface] — cli.py or server.py absent; vacuous-green.")
        return 0

    findings: list[str] = []
    if not _commands_has_features(cli_path):
        findings.append("kairix/cli.py:COMMANDS missing 'features' entry")
    if not _mcp_has_tool_features_status(mcp_path):
        findings.append("kairix/agents/mcp/server.py missing @server.tool() tool_features_status")
    if not _features_surfaces_have_outcome_tests():
        findings.append(
            "F30 baseline lists 'features' CLI or 'features_status' MCP tool as "
            "missing an outcome test — add the outcome test and remove the baseline entry"
        )

    if not findings:
        print("ok [arch:f53-features-status-surface] — clean.")
        return 0

    print("FAIL [arch:f53-features-status-surface] — operator surface incomplete:")
    for finding in findings:
        print(f"  {finding}")
    print()
    print(REMEDIATION)
    return 1


if __name__ == "__main__":
    sys.exit(main())
