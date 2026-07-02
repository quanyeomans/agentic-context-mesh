"""Contract: the ``kairix`` CLI dispatch table derives from the catalogue.

``kairix.cli.COMMANDS`` is no longer a hand-maintained literal — it is DERIVED
at import from :data:`~kairix.agents.mcp.server.CAPABILITIES_CATALOG` plus an
explicit operator/infra allow-list (PLA-319). This pins two observable
consequences of that derivation through the public surface only:

- The retired ``vault`` backwards-compat alias is gone; ``store`` is the
  command. Reintroducing a ``vault`` handler makes the import-time drift guard
  in :func:`kairix.cli._derive_commands` raise (so the whole suite fails to
  import ``kairix.cli``), or — if it is also smuggled into the infra allow-list
  — trips :func:`test_vault_alias_is_gone` directly.
- Every agent-facing capability whose ``cli`` names a top-level ``kairix <sub>``
  command is dispatchable, so the CLI surface can't drift out of the catalogue.
  Drop a catalogue-backed subcommand from the wiring and
  :func:`test_agent_facing_catalogue_commands_are_dispatchable` fails.

Sabotage-proof: reintroduce ``"vault"`` in ``kairix/cli.py`` (either as a
``_CLI_HANDLERS`` row — import-time ``RuntimeError`` — or additionally in
``_INFRA_SUBCOMMANDS`` — this file's ``test_vault_alias_is_gone`` fails), and a
gate goes red. Restore and it is green again.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.server import CAPABILITIES_CATALOG
from kairix.cli import COMMANDS

pytestmark = pytest.mark.contract


def test_vault_alias_is_gone() -> None:
    """The ``vault`` alias was removed; ``store`` is the knowledge-store command."""
    assert "vault" not in COMMANDS, (
        "the retired `vault` backwards-compat alias reappeared in the CLI dispatch "
        "table — it must stay gone; use `store` instead (PLA-319)"
    )
    assert "store" in COMMANDS, "the `store` command must remain dispatchable"


def test_agent_facing_catalogue_commands_are_dispatchable() -> None:
    """Each agent-facing capability that names a ``kairix <sub>`` command is wired.

    ``COMMANDS`` derives from the catalogue, so an agent-callable capability
    (an ``mcp_tool`` row that is not an operator-only escalation stub) whose
    ``cli`` names a top-level command must be dispatchable — otherwise the CLI
    surface has silently drifted from the catalogue.
    """
    # The two catalogue rows whose ``cli`` is spelled differently from the
    # shipped command: ``facts about`` ships as the hyphenated ``facts-about``,
    # and ``capabilities`` is an MCP-only surface with no CLI command.
    known_spelling_exceptions = {"facts", "capabilities"}
    for cap in CAPABILITIES_CATALOG:
        if cap.mcp_tool is None or cap.escalate_via is not None:
            continue  # operator-only / escalation stub — not an agent CLI command
        if not cap.cli.startswith("kairix "):
            continue  # e.g. a `python -c '...'` probe stub — no kairix subcommand
        subcommand = cap.cli.split()[1]
        if subcommand in known_spelling_exceptions:
            continue
        assert subcommand in COMMANDS, (
            f"agent capability {cap.name!r} (cli {cap.cli!r}) has no dispatchable "
            f"`kairix {subcommand}` command — the CLI drifted from the catalogue"
        )
