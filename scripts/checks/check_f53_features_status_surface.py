"""F53: operator surface for feature flags exists.

Operations affordance — flags are useless if the operator can't see what
is enabled. F53 enforces:

  1. ``kairix/cli.py:COMMANDS`` has a ``"features"`` entry.
  2. ``kairix/agents/mcp/server.py`` registers a ``features_status`` MCP
     tool — an ``@server.tool()``-decorated function whose name resolves
     to the ``features_status`` capability. Per the codebase convention
     (see ``worker_status`` / ``caches_status``), the registered inner
     function is named ``features_status`` and delegates to the
     module-level ``tool_features_status`` adapter; the bare
     ``tool_<name>`` form is also accepted for forward-compat.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_registry import registered_mcp_tool_names

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CLI_REL_PATH = Path("kairix/cli.py")
MCP_REL_PATH = Path("kairix/agents/mcp/server.py")
F30_BASELINE_REL = Path(".architecture/baseline/f30-operator-outcome-tests-files.txt")

REMEDIATION = """F53: operator surface missing for feature flags.
fix: ensure kairix/cli.py:COMMANDS includes a 'features' entry (CLI
     subcommand) AND kairix/agents/mcp/server.py registers a
     'features_status' MCP tool — an @server.tool() function named
     'features_status' (codebase convention; delegates to the
     module-level tool_features_status adapter). Both must also have
     F30-compliant outcome tests (NOT appear in the F30 baseline as
     missing an outcome test).
next: see docs/architecture/feature-flag-architecture.md §3.5 (operator
      surface) + §6 (F53 mechanics).
run: python3 scripts/checks/check_f53_features_status_surface.py

Pass example:
  # kairix/cli.py
  COMMANDS: dict[str, tuple[...]] = {
      "features": (run_features_status, "show feature flag state"),
      ...
  }
  # kairix/agents/mcp/server.py — registered inside build_server()
  @server.tool()
  @async_tool_handler
  def features_status() -> dict[str, Any]:
      return tool_features_status()

Forbidden example:
  # kairix/cli.py COMMANDS has no 'features' entry; operators can only
  # read flag state by grep-ing the registry source. MCP also has no
  # @server.tool() features_status — agents have no programmatic
  # surface either, so a `features_status` MCP call is tool-not-found."""


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


# The registered MCP tool name that resolves to this capability. Post-PLA-318
# registration is catalogue-driven — ``build_server`` registers one tool per
# ``CAPABILITIES_CATALOG`` row, so ``features_status`` is registered iff the
# catalogue declares a ``mcp_tool="features_status"`` row (see ``_mcp_registry``).
_FEATURES_TOOL_NAME = "features_status"


def _mcp_registers_features_status(mcp_path: Path) -> bool:
    """Return True if server.py registers a ``features_status`` MCP tool.

    Reads the catalogue-driven registration: ``features_status`` is the
    ``mcp_tool`` of a ``_cap(...)`` row that ``build_server`` walks and
    registers, so the presence of that catalogue row IS the runtime MCP tool
    an agent calls.
    """
    return _FEATURES_TOOL_NAME in registered_mcp_tool_names(mcp_path)


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
    if not _mcp_registers_features_status(mcp_path):
        findings.append("kairix/agents/mcp/server.py missing @server.tool() features_status")
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
