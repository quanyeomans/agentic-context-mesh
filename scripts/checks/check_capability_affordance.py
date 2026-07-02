"""F25: Every CLI subcommand has an MCP affordance (binding OR escalation stub).

Operationally-relevant kairix capabilities have one Python implementation with
two bindings: CLI and MCP. The MCP binding is either:

  1. A real exposure — `tool_<command>` invokes the same Python API the CLI
     uses, with safe defaults (e.g. read-only, bounded runtime).

  OR

  2. An escalation stub — `tool_<command>` returns a structured
     `OperatorOnlyCapability` envelope naming the exact CLI command for an
     agent to surface to its admin. The envelope payload looks like:

         {
           "error": "OperatorOnlyCapability",
           "capability": "<name>",
           "operator_command": "kairix <command> ...",
           ...
         }

This gate enforces that every entry in `kairix/cli.py`'s `COMMANDS` dispatch
has a matching `tool_<command>` function defined in `kairix/agents/mcp/server.py`,
EXCEPT for the explicit allowlist of commands that have no agent use case at
all (interactive setup wizards, mcp server-side commands).

Detection (AST walk over server.py + dispatch dict from cli.py):

  1. Read `COMMANDS` dict from kairix/cli.py via AST parse.
  2. Walk `kairix/agents/mcp/server.py` for `def tool_<name>` functions.
  3. For every CLI command not in the allowlist, assert a matching
     `tool_<name>` exists.

A missing tool function is the violation — adding either a real binding
(call the underlying Python API) or a stub (return _operator_only_envelope)
closes it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _mcp_registry import afforded_cli_commands
from tc_fitness import REPO_ROOT, gate

CLI_FILE = REPO_ROOT / "kairix" / "cli.py"
MCP_SERVER_FILE = REPO_ROOT / "kairix" / "agents" / "mcp" / "server.py"
# Post-PLA-318 the ``tool_<name>`` adapter bodies live in per-domain modules
# under ``kairix/agents/mcp/tools/`` (server.py re-exports them); scan that tree
# alongside server.py so the affordance gate finds each capability's adapter.
MCP_TOOLS_DIR = REPO_ROOT / "kairix" / "agents" / "mcp" / "tools"

# Commands that legitimately have no MCP equivalent — never agent-invokable
# even via an escalation stub. The setup wizard and config validator are
# interactive operator tools; mcp itself is the protocol the agent uses to
# talk to kairix, so a "tool_mcp" would be circular.
_NO_MCP_AFFORDANCE_REQUIRED: frozenset[str] = frozenset(
    {
        "setup",
        "config",
        "mcp",
        "bootstrap",
        "research",
        "summarise",
        "classify",
        "wikilinks",
        "curator",
        "timeline",
        "reference-library",
        # eval is an operator-side benchmark harness — load-generating against
        # a synthetic corpus, not an agent-callable retrieval surface.
        "eval",
        "worker",
        "usage-guide",
        "contradict",
        "brief",
        "prep",
        "search",
        "entity",
        # ingest-chat mutates the document store + fact store from a JSONL
        # transcript supplied by the operator; not safe to expose to agents
        # even as an escalation stub (the operator runs it from the host).
        "ingest-chat",
        # secrets is pre-deploy operator-only — the verify subcommand IS
        # exposed via tool_secrets_verify (kairix/agents/mcp/secrets_status.py)
        # so agents can answer "is auth healthy?" without docker exec access.
        # The CLI surface stays operator-only because the verify table is
        # most useful during pre-deploy wiring (after the operator has set
        # KV secrets); agents querying the deployed instance just need the
        # status envelope, not the CLI wrapper.
        "secrets",
        # connect is operator-only — opens a browser, captures OAuth2 tokens via
        # the consent flow, and writes them to the operator's chosen store
        # (file / Azure KV / stdout). Agents cannot complete a browser-based
        # consent flow (no $DISPLAY, no human in the loop) and the capability
        # writes credential material — categorically not safe to expose even
        # as an escalation stub.
        "connect",
        # mcp-calls + caches are local-only diagnostic CLIs over the in-process
        # mcp_call_log SQLite table and in-memory cache stats. Operators run
        # them from a shell to inspect tooling health; agents have no use case
        # (the data lives only on the box where the kairix CLI runs, and the
        # MCP server itself is the thing being observed — exposing the
        # observability table over MCP would be circular).
        "mcp-calls",
        "caches",
        # slo is an operator/engineering measurement harness (PLA-256) — it
        # runs every most-used command repeatedly to capture cold/warm
        # latency, fact-recall quality, and breadcrumb completeness against a
        # synthetic (or the operator's real) corpus. Load-generating and
        # diagnostic, same shape as `eval`; not an agent-callable retrieval
        # surface, so no MCP affordance is required.
        "slo",
        # init + uninstall are operator-only self-installer entry points
        # (Plan 1 task 8). They mutate the FHS / XDG filesystem layout,
        # create the kairix system user (system mode), and write the
        # systemd unit — all on the host where the kairix CLI runs.
        # Categorically not safe for agents: an agent issuing
        # `kairix uninstall --no-keep-data` over MCP would erase the
        # operator's data dir, and a system-mode init requires real root
        # (no MCP escalation path is meaningful). Operators run these
        # interactively from a shell.
        "init",
        "uninstall",
    }
)

REMEDIATION = """Every CLI subcommand needs an MCP affordance — either a real
binding (tool_<command> calls the same Python API the CLI uses with safe
defaults) or an escalation stub (tool_<command> returns an
OperatorOnlyCapability envelope with the exact CLI string for the agent's
admin to run).

fix: add a `tool_<command>` function to kairix/agents/mcp/server.py.

  Real binding for read-only / fast / safe-for-agent capabilities:

    def tool_<command>(...) -> dict[str, Any]:
        from kairix.<module> import <python_api>
        return <python_api>(...).to_envelope()

  Escalation stub for load-generating / mutating / long-running operations:

    def tool_<command>(...) -> dict[str, Any]:
        return _operator_only_envelope(
            capability="<command>",
            operator_command="kairix <command> ...",
            reason="<why agents must escalate>",
            expected_runtime_seconds=<int>,
            see_also=[_RETRIEVAL_RUNBOOK],
        )

  Then register it via @server.tool() in build_server().

next: re-run `python3 scripts/checks/check_capability_affordance.py`
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "feat(mcp): add tool_<command> affordance"

See docs/architecture/operational-tests-design.md for the full design
and the per-capability binding decision matrix.

If the command legitimately has no agent use case (interactive wizard,
protocol-level dispatch like `mcp` itself), add it to
_NO_MCP_AFFORDANCE_REQUIRED in this file with a one-line rationale comment.

Pass example:
  # kairix/agents/mcp/server.py — read-only `kairix status` binding
  @server.tool()
  def tool_status() -> dict[str, Any]:
      from kairix.cli.status import run_status
      return run_status(paths=resolve_paths()).to_envelope()

Forbidden example:
  # kairix/cli.py adds COMMANDS["reindex"] = (...)
  # but kairix/agents/mcp/server.py has no tool_reindex AND
  # _NO_MCP_AFFORDANCE_REQUIRED does not list "reindex" — F-rule fires
  # because agents have no path to invoke the capability or escalate."""


def _read_cli_commands() -> set[str]:
    """Return the set of command keys from `kairix/cli.py`'s dispatch wiring.

    Post-PLA-319 the wiring literal is ``_CLI_HANDLERS`` and ``COMMANDS`` is
    DERIVED from it + the catalogue (no longer a dict literal); both names are
    accepted so the affordance gate reads the shipped subcommand set correctly.
    """
    tree = ast.parse(CLI_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in ("COMMANDS", "_CLI_HANDLERS")
            and isinstance(node.value, ast.Dict)
        ):
            keys: set[str] = set()
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
            return keys
    return set()


def _tool_basenames_in_source(source: str) -> set[str]:
    """Return the `tool_<name>` function basenames defined in one module ``source``."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {
        node.name.removeprefix("tool_")
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("tool_")
    }


def _read_mcp_tool_functions() -> set[str]:
    """Return the set of `tool_<...>` function basenames across the MCP surface.

    A CLI top-level command satisfies the gate when at least one tool
    function name starts with `tool_<command>` (with `-` normalised to `_`).
    e.g. `kairix soak run` is satisfied by `tool_soak_run`;
    `kairix store crawl` by `tool_store_crawl`.

    Post-PLA-318 the ``tool_<name>`` adapter bodies live in the per-domain
    modules under ``kairix/agents/mcp/tools/`` (server.py re-exports them for
    direct-call tests), so the scan covers that tree plus server.py.
    """
    names: set[str] = set()
    if MCP_SERVER_FILE.is_file():
        names |= _tool_basenames_in_source(MCP_SERVER_FILE.read_text(encoding="utf-8"))
    if MCP_TOOLS_DIR.is_dir():
        for module in sorted(MCP_TOOLS_DIR.glob("*.py")):
            names |= _tool_basenames_in_source(module.read_text(encoding="utf-8"))
    return names


def main() -> int:
    cli_commands = _read_cli_commands()
    tool_names = _read_mcp_tool_functions()
    # The catalogue's authoritative CLI↔MCP mapping — covers commands whose MCP
    # tool name differs from the command (``kairix remember`` → ``memory_write``).
    catalogue_afforded = afforded_cli_commands(MCP_SERVER_FILE)
    missing: set[Path] = set()
    for cmd in sorted(cli_commands):
        if cmd in _NO_MCP_AFFORDANCE_REQUIRED:
            continue
        if cmd in catalogue_afforded:
            continue
        normalised = cmd.replace("-", "_")
        # Tool name either matches exactly OR starts with the command prefix
        # (e.g. tool_soak_run satisfies the `soak` command, tool_store_crawl
        # satisfies the `store` command).
        if any(name == normalised or name.startswith(f"{normalised}_") for name in tool_names):
            continue
        missing.add(Path(f"kairix/cli.py::COMMANDS[{cmd!r}]"))

    return gate("capability-affordance", missing, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
