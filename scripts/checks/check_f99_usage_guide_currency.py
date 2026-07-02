"""F99: the bundled agent usage guide stays in sync with the tool registry.

Agents self-train from the bundled usage guide
(``kairix/agents/usage_guide/data/agent-usage-guide.md``) — it is the one
document a fresh agent reads to learn what kairix can do. When a capability
ships in the registry but never lands in the guide, agents never discover it.
That is exactly how ``expand`` fell out of the guide after it shipped: the
catalogue advertised it, the CLI + MCP surfaces exposed it, but the guide
never mentioned it, so no self-training agent reached for it.

This rule is the catalogue-currency lock that closes the drift class. The
canonical tool registry is ``tool_capabilities()`` in
``kairix/agents/mcp/server.py`` (each ``_cap(...)`` row = one registered
capability with its MCP tool name + CLI invocation). Every registered
capability MUST be discoverable in the bundled guide, proven structurally by
one of its invocation tokens appearing in the guide text:

  * its CLI invocation (``kairix <subcommand>``), OR
  * its MCP tool name (``tool_<mcp_tool>``), OR
  * for an operator-only escalation capability, ``tool_<escalate_via>``.

Detection (AST walk over server.py + a substring scan of the guide):

  1. Resolve the module-level tool-name string constants in server.py
     (``CONTRADICT_TOOL_NAME`` etc.) so ``_cap(name=CONTRADICT_TOOL_NAME)``
     rows resolve to their literal value.
  2. Walk the module for every ``_cap(...)`` catalogue row (they live in the
     module-level ``CAPABILITIES_CATALOG`` tuple that ``tool_capabilities()``
     projects) and read its ``name`` / ``mcp_tool`` / ``cli`` / ``escalate_via``.
  3. For each capability not on the deliberate exclusion allowlist, assert at
     least one invocation token appears in the bundled guide.

Intentionally NOT caught (precision over recall):
  * WHERE in the guide a capability appears, or HOW well it is described —
    the rule proves discoverability (the token is present), not prose
    quality. The BDD/outcome tests prove the foregrounded patterns read well.
  * A capability whose ``cli`` is a bare ``python -c '...'`` snippet is matched
    on its ``tool_<mcp_tool>`` / ``tool_<escalate_via>`` token only — the
    python snippet is not a distinctive guide token, so it is ignored to keep
    false positives at zero.
  * The exclusion allowlist (``_NOT_ADVERTISED_IN_GUIDE``) carries capabilities
    that are deliberately NOT advertised to agents — currently the flag-gated
    recommender, whose ``recommender`` feature flag defaults OFF, so surfacing
    it in the agent guide would read as a live capability when it returns a
    disabled envelope. Re-include it when the flag defaults ON.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tc_fitness import REPO_ROOT, gate

MCP_SERVER_FILE = REPO_ROOT / "kairix" / "agents" / "mcp" / "server.py"
GUIDE_FILE = REPO_ROOT / "kairix" / "agents" / "usage_guide" / "data" / "agent-usage-guide.md"

# The builder call whose rows are the canonical tool registry. Post-PLA-317
# the ``_cap(...)`` rows live in the module-level ``CAPABILITIES_CATALOG`` tuple
# (``tool_capabilities()`` now just projects them), so the walk scans the whole
# module for ``_cap(...)`` calls rather than a single function body — server.py
# uses ``_cap`` only to build catalogue rows, so this stays precise.
_CAP_BUILDER = "_cap"

# Capabilities deliberately kept OUT of the agent usage guide. A name here is
# NOT required to appear in the guide.
#
# ``recommend`` (the ``recommend_capabilities`` MCP tool + ``kairix recommend``
# CLI) is gated behind the ``recommender`` feature flag, which defaults OFF
# (kairix/core/features/registry.py). While OFF both surfaces return a
# "not enabled on this deployment" envelope, so advertising it in the guide an
# agent self-trains from would read as a live capability. Remove this entry —
# and add a guide row — when the recommender flag defaults ON.
_NOT_ADVERTISED_IN_GUIDE: frozenset[str] = frozenset({"recommend"})

REMEDIATION = """A registered capability is missing from the bundled agent
usage guide. Agents self-train from
kairix/agents/usage_guide/data/agent-usage-guide.md — a capability the guide
never names is a capability agents never discover (this is how `expand` fell
out after it shipped).

fix: add the capability to the guide so ONE of its invocation tokens appears
in the text — its CLI invocation `kairix <subcommand>`, its MCP tool name
`tool_<mcp_tool>`, or (operator-only) `tool_<escalate_via>`. The
"Capabilities — which surface to use" table is the index; add a row there.

next: re-run `python3 scripts/checks/check_f99_usage_guide_currency.py`
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "docs(guide): surface <capability> affordance"

Pass example:
  # agent-usage-guide.md — the registered `expand` capability is discoverable
  | `tool_expand` / `kairix expand` | pull the chunks around a search hit | both |

Forbidden example:
  # kairix/agents/mcp/server.py registers _cap(name="expand", mcp_tool="expand", ...)
  # but agent-usage-guide.md never mentions `tool_expand` or `kairix expand`
  # — F99 fires because a self-training agent can't discover the capability.

If a capability is deliberately not advertised to agents (e.g. a flag-gated
surface that returns a disabled envelope by default), add its name to
_NOT_ADVERTISED_IN_GUIDE in this file with a one-line rationale comment."""


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Return ``{NAME: "literal"}`` for module-level ``NAME = "str"`` assigns.

    Lets ``_cap(name=CONTRADICT_TOOL_NAME, ...)`` rows resolve the constant to
    its string value without importing the (heavyweight) server module.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _resolve(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    """Resolve a ``_cap`` keyword value to a string, or ``None``.

    Handles the two shapes the catalogue uses: a string literal, and a Name
    reference to a module-level string constant. ``None`` (the operator-only
    ``mcp_tool=None`` marker) and anything unresolvable return ``None``.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _catalogue_capabilities(tree: ast.Module, constants: dict[str, str]) -> list[dict[str, str | None]]:
    """Return one dict per ``_cap(...)`` catalogue row in the module.

    The rows live in the module-level ``CAPABILITIES_CATALOG`` tuple
    (``tool_capabilities()`` projects them), so the whole module is walked for
    ``_cap(...)`` calls. ``_cap`` is the catalogue-row builder only, so no
    non-catalogue call is matched.
    """
    caps: list[dict[str, str | None]] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == _CAP_BUILDER):
            continue
        row: dict[str, str | None] = {}
        for kw in call.keywords:
            if kw.arg in ("name", "mcp_tool", "cli", "escalate_via"):
                row[kw.arg] = _resolve(kw.value, constants)
        if row.get("name"):
            caps.append(row)
    return caps


def _invocation_tokens(cap: dict[str, str | None]) -> list[str]:
    """Return the distinctive guide tokens that prove a capability is present.

    A ``kairix <subcommand>`` CLI string, the ``tool_<mcp_tool>`` MCP name, and
    the operator-only ``tool_<escalate_via>`` name are all distinctive enough
    to scan for. A ``python -c '...'`` CLI is not, so it is not a token.
    """
    tokens: list[str] = []
    cli = cap.get("cli")
    if cli and cli.startswith("kairix "):
        tokens.append(cli)
    mcp_tool = cap.get("mcp_tool")
    if mcp_tool:
        tokens.append(f"tool_{mcp_tool}")
    escalate_via = cap.get("escalate_via")
    if escalate_via:
        tokens.append(f"tool_{escalate_via}")
    return tokens


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Return ``<guide>::<capability>`` synthetic paths for capabilities the
    bundled guide fails to surface.

    A missing / unparseable server module or guide file is itself a
    violation — the currency contract names surfaces that must exist.
    """
    server_file = repo_root / "kairix" / "agents" / "mcp" / "server.py"
    guide_file = repo_root / "kairix" / "agents" / "usage_guide" / "data" / "agent-usage-guide.md"
    guide_rel = guide_file.relative_to(repo_root)

    if not server_file.is_file() or not guide_file.is_file():
        return {Path(f"{guide_rel}::<registry-or-guide-missing>")}

    try:
        tree = ast.parse(server_file.read_text(encoding="utf-8"), filename=str(server_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return {Path(f"{guide_rel}::<registry-unparseable>")}

    guide_text = guide_file.read_text(encoding="utf-8")
    constants = _module_string_constants(tree)
    caps = _catalogue_capabilities(tree, constants)

    violations: set[Path] = set()
    for cap in caps:
        name = cap.get("name")
        if not name or name in _NOT_ADVERTISED_IN_GUIDE:
            continue
        tokens = _invocation_tokens(cap)
        if tokens and not any(tok in guide_text for tok in tokens):
            violations.add(Path(f"{guide_rel}::{name}"))
    return violations


def main() -> int:
    return gate("f99", collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
