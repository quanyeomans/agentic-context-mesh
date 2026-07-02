"""Shared AST helper — the registered MCP tool set, read from the catalogue.

Post-PLA-318 the FastMCP registration is catalogue-driven: ``build_server`` in
``kairix/agents/mcp/server.py`` registers exactly one tool per
``CAPABILITIES_CATALOG`` row (keyed by the row's ``mcp_tool`` for agent-callable
tools, or its ``escalate_via`` name for operator-only stubs) instead of ~37
hand-written ``@server.tool`` defs. The fitness checks that need "the set of
registered MCP tool names" (F30, F45, F46) read it from the ``_cap(...)`` rows
here — the same AST source F99 uses — so a tool is discovered by its catalogue
row, not a decorator that no longer exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

# The catalogue-row builder. ``_cap`` is used only to build catalogue rows in
# server.py, so matching every ``_cap(...)`` call is precise.
_CAP_BUILDER = "_cap"


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Return ``{NAME: "literal"}`` for module-level ``NAME = "str"`` assigns.

    Lets ``_cap(mcp_tool=CONTRADICT_TOOL_NAME, ...)`` rows resolve the constant
    to its string value without importing the (heavyweight) server module.
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


def registered_mcp_tool_names_from_source(source: str) -> set[str]:
    """Return every registered MCP tool name declared in server.py source.

    A registered tool is the ``mcp_tool`` (agent-callable) OR ``escalate_via``
    (operator-only stub) of a ``_cap(...)`` catalogue row — exactly the set of
    names FastMCP registers when ``build_server`` walks the catalogue.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    constants = _module_string_constants(tree)
    names: set[str] = set()
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == _CAP_BUILDER):
            continue
        for kw in call.keywords:
            if kw.arg in ("mcp_tool", "escalate_via"):
                resolved = _resolve(kw.value, constants)
                if resolved:
                    names.add(resolved)
    return names


def registered_mcp_tool_names(server_path: Path) -> set[str]:
    """Return the registered MCP tool names from the server module at ``server_path``.

    Missing / unreadable server module → empty set (the caller treats that as
    "no tools", matching the pre-catalogue AST helpers' fail-soft behaviour).
    """
    try:
        source = server_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return registered_mcp_tool_names_from_source(source)


def afforded_cli_commands_from_source(source: str) -> set[str]:
    """Return the top-level CLI commands the catalogue affords an MCP tool for.

    A ``_cap(cli="kairix <cmd> ...", mcp_tool=... | escalate_via=...)`` row
    declares that ``kairix <cmd>`` has an MCP affordance — including where the
    tool name differs from the command (``kairix remember`` maps to the
    ``memory_write`` tool). Only ``kairix ...`` cli strings map to a CLI
    command; ``python -c ...`` entries carry no top-level command and are
    ignored. This is the authoritative CLI↔MCP mapping the affordance gate
    reads instead of assuming a name-matching ``tool_<command>``.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    constants = _module_string_constants(tree)
    commands: set[str] = set()
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == _CAP_BUILDER):
            continue
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        has_affordance = bool(_resolve(kwargs.get("mcp_tool"), constants)) or bool(
            _resolve(kwargs.get("escalate_via"), constants)
        )
        if not has_affordance:
            continue
        cli = _resolve(kwargs.get("cli"), constants)
        if cli and cli.startswith("kairix "):
            commands.add(cli.split()[1])
    return commands


def afforded_cli_commands(server_path: Path) -> set[str]:
    """Return the top-level CLI commands afforded an MCP tool by the catalogue.

    Missing / unreadable server module → empty set (fail-soft).
    """
    try:
        source = server_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return afforded_cli_commands_from_source(source)
