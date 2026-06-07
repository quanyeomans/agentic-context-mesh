"""Output formatting for proposed scopes (PR 1.4 / #420).

Three operator-facing surfaces: paste-ready yaml, the human-readable
text summary, and the per-scope validation report. The CLI picks one
based on the operator's flag (``--yaml`` / default / per-tool body).

YAML is rendered via :func:`yaml.safe_dump` so the round-trip
through ``yaml.safe_load`` is structurally stable — operators can
copy the block straight into ``kairix.config.yaml`` and the loader
parses it unchanged.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import yaml

from kairix.agents.onboarding.scanner import ProposedScope


def _scope_to_yaml_block(scope: ProposedScope) -> dict[str, Any]:
    """Render one :class:`ProposedScope` to the dict shape kairix's
    config loader expects under ``agents.<name>``.
    """
    return {
        "harness": scope.harness,
        "surfaces": [
            {
                "path": str(s.path),
                "glob": s.glob,
                "label": s.label,
            }
            for s in scope.surfaces
        ],
    }


def _format_mtime(mtime: float | None) -> str:
    """Render an mtime as a YYYY-MM-DD ISO date, or ``"never"`` when
    no .md files were found in the scope."""
    if mtime is None:
        return "never"
    return _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc).date().isoformat()


def render_scopes_as_yaml(scopes: tuple[ProposedScope, ...]) -> str:
    """Render proposed scopes as a paste-ready ``kairix.config.yaml``
    ``agents:`` block.

    Each scope is prefixed by a one-line comment carrying
    ``confidence=...``, ``file_count=...``, and the most-recent .md
    date. Operators read the comment, then keep or edit the block
    underneath.

    An empty proposal tuple still produces a structurally valid
    ``agents: {}`` mapping so the round-trip through
    :func:`yaml.safe_load` returns a dict, not ``None``.
    """
    if not scopes:
        return yaml.safe_dump({"agents": {}}, sort_keys=False)

    out_lines: list[str] = ["agents:"]
    for scope in scopes:
        comment = (
            f"  # confidence={scope.confidence} "
            f"file_count={scope.file_count} "
            f"most_recent={_format_mtime(scope.most_recent_mtime)}"
        )
        out_lines.append(comment)
        block = {scope.name: _scope_to_yaml_block(scope)}
        block_text = yaml.safe_dump(block, sort_keys=False, indent=2)
        # Indent the rendered block under "agents:" — two spaces, the
        # canonical indentation kairix.config.yaml uses.
        for line in block_text.splitlines():
            out_lines.append(f"  {line}")
    out_lines.append("")
    return "\n".join(out_lines)


def render_scopes_as_text(scopes: tuple[ProposedScope, ...]) -> str:
    """Render proposed scopes as a human-readable table for stdout.

    Operators read this for a quick overview before deciding whether
    to re-run with ``--yaml`` and paste the block into the config.
    """
    if not scopes:
        return "no agents found under the configured memory root\n"
    lines = ["Proposed agent scopes:"]
    for scope in scopes:
        lines.append(
            f"  {scope.name}  harness={scope.harness}  "
            f"confidence={scope.confidence}  "
            f"file_count={scope.file_count}  "
            f"most_recent={_format_mtime(scope.most_recent_mtime)}",
        )
    return "\n".join(lines) + "\n"


def render_validation_report(scopes: tuple[ProposedScope, ...]) -> str:
    """Render the per-scope validation report.

    Per-agent file count + most-recent .md date so the operator can
    see at a glance whether the scope is real and active.
    """
    if not scopes:
        return "no agents found — re-run with --memory-root if the path was wrong\n"
    lines = ["Validation report:"]
    for scope in scopes:
        lines.append(
            f"  {scope.name}: file_count={scope.file_count} "
            f"most_recent={_format_mtime(scope.most_recent_mtime)} "
            f"harness={scope.harness} confidence={scope.confidence}",
        )
    return "\n".join(lines) + "\n"
