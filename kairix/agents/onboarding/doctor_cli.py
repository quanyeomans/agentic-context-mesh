"""argparse subcommand bodies for ``kairix doctor agent`` (PR 1.5 / #420).

Top-level ``kairix doctor`` dispatcher with one subcommand today
(``agent``) — the shape leaves room for ``doctor connector``,
``doctor index`` etc. without restructuring the CLI tree.

Routing:

* ``--all`` runs :func:`doctor_check_all` (bulk)
* ``--name NAME`` runs :func:`doctor_check_agent` (single)
* Neither flag → defaults to ``--all``
* ``--json`` emits the canonical envelope
* ``--yaml`` emits the YAML rendering of the same envelope
* Default mode emits a human-readable summary report

Exit codes:

* ``0`` when overall is ``"ok"`` OR ``"warn"`` (warnings do not break CI)
* ``1`` when overall is ``"error"``

Production callers wire this through :mod:`kairix.cli`'s top-level
``doctor`` command — see ``COMMANDS`` in that module. Standalone
``main()`` is preserved as a test entry point.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from kairix.agents.onboarding.doctor import (
    AgentHealth,
    DoctorReport,
    SurfaceHealth,
    doctor_check_agent,
    doctor_check_all,
)

# F17 — argparse action keyword repeated across boolean-flag declarations.
_STORE_TRUE = "store_true"


def add_doctor_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the ``doctor agent`` subparser onto an existing add_subparsers."""
    parser = sub.add_parser(
        "agent",
        help="Re-validate every configured agent scope against disk state.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        dest="all",
        action=_STORE_TRUE,
        default=False,
        help="Validate every configured agent (default when neither --all nor --name set).",
    )
    group.add_argument(
        "--name",
        dest="name",
        default=None,
        help="Validate a single named agent.",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default="",
        help="Path to kairix.config.yaml carrying the agents: block.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        default=False,
        help="Emit the doctor result as a JSON envelope.",
    )
    parser.add_argument(
        "--yaml",
        dest="as_yaml",
        action=_STORE_TRUE,
        default=False,
        help="Emit the doctor result as YAML.",
    )


def _surface_health_to_envelope(sh: SurfaceHealth) -> dict[str, object]:
    """Render one :class:`SurfaceHealth` as the canonical envelope dict."""
    return {
        "path": str(sh.path),
        "label": sh.label,
        "exists": sh.exists,
        "writable": sh.writable,
        "file_count": sh.file_count,
        "most_recent_mtime": sh.most_recent_mtime,
        "issues": list(sh.issues),
    }


def agent_health_to_envelope(health: AgentHealth) -> dict[str, object]:
    """Render one :class:`AgentHealth` as the canonical envelope dict.

    Used by the CLI ``--json`` path AND the MCP ``tool_doctor_*`` tools
    so CLI and MCP return byte-identical envelopes for the same input.
    """
    return {
        "name": health.name,
        "harness": health.harness,
        "surfaces": [_surface_health_to_envelope(s) for s in health.surfaces],
        "overall": health.overall,
        "issues": list(health.issues),
    }


def report_to_envelope(report: DoctorReport) -> dict[str, object]:
    """Render the bulk :class:`DoctorReport` as the canonical envelope dict."""
    return {
        "agents": [agent_health_to_envelope(a) for a in report.agents],
        "overall": report.overall,
        "summary_text": report.summary_text,
        "error": "",
    }


def _load_config_from_path(config_path: str) -> dict[str, object]:
    """Read a yaml config file and return its parsed dict.

    Returns ``{}`` when ``config_path`` is empty (the operator passed
    no --config) or when the file does not exist — the doctor handles
    that as "no agents configured".
    """
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _render_text_report(report: DoctorReport) -> str:
    """Human-readable validation report for stdout (default mode)."""
    lines = [f"kairix doctor agent — {report.summary_text}", "─" * 50]
    if not report.agents:
        lines.append("(no agents configured)")
    for agent in report.agents:
        lines.append(f"  {agent.name}  harness={agent.harness}  overall={agent.overall}")
        for issue in agent.issues:
            lines.append(f"    ! {issue}")
        for sh in agent.surfaces:
            lines.append(
                f"    surface[{sh.label}] path={sh.path} exists={sh.exists} "
                f"writable={sh.writable} file_count={sh.file_count}",
            )
            for issue in sh.issues:
                lines.append(f"      ! {issue}")
    lines.append("─" * 50)
    lines.append(f"overall={report.overall}")
    return "\n".join(lines) + "\n"


def _exit_code_for(overall: str) -> int:
    """Map an overall label to a process exit code."""
    return 1 if overall == "error" else 0


def _emit_bulk(report: DoctorReport, *, as_json: bool, as_yaml: bool) -> int:
    """Emit the bulk doctor report in the operator's chosen mode."""
    envelope = report_to_envelope(report)
    if as_json:
        print(json.dumps(envelope, indent=2))
    elif as_yaml:
        print(yaml.safe_dump(envelope, sort_keys=False), end="")
    else:
        print(_render_text_report(report), end="")
    return _exit_code_for(report.overall)


def _emit_single(
    health: AgentHealth,
    *,
    as_json: bool,
    as_yaml: bool,
) -> int:
    """Emit the single-agent doctor health in the operator's chosen mode."""
    envelope: dict[str, Any] = {"agent": agent_health_to_envelope(health), "error": ""}
    if as_json:
        print(json.dumps(envelope, indent=2))
    elif as_yaml:
        print(yaml.safe_dump(envelope, sort_keys=False), end="")
    else:
        # Reuse the bulk text renderer by wrapping the single health.
        wrapped = DoctorReport(
            agents=(health,),
            overall=health.overall,
            summary_text=f"1 agent checked — overall={health.overall}",
        )
        print(_render_text_report(wrapped), end="")
    return _exit_code_for(health.overall)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Execute ``kairix doctor agent``.

    Dispatches to either :func:`doctor_check_all` or
    :func:`doctor_check_agent` based on the --all / --name flags.
    """
    config = _load_config_from_path(getattr(args, "config", "") or "")

    name: str | None = getattr(args, "name", None)
    if name:
        health = doctor_check_agent(name, config=config)
        return _emit_single(
            health,
            as_json=args.as_json,
            as_yaml=args.as_yaml,
        )

    # Default to --all when neither --all nor --name was set.
    report = doctor_check_all(config=config)
    return _emit_bulk(
        report,
        as_json=args.as_json,
        as_yaml=args.as_yaml,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m kairix.agents.onboarding.doctor_cli``
    and ``kairix doctor ...``.

    Returns the exit code (0 = ok / warn, 1 = error) rather than
    calling ``sys.exit`` so tests can drive ``main(...)`` and assert
    on the return value without catching SystemExit. The package-
    level entry point in :mod:`kairix.cli` translates this int into
    the process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="kairix doctor",
        description="Re-validate configured agent scopes against disk state.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    add_doctor_parser(sub)
    args = parser.parse_args(argv)
    if args.subcommand == "agent":
        return cmd_doctor(args)
    return 2


# Re-export for the F1 ban — tests construct argparse.Namespace
# directly rather than monkey-patching parser internals; expose the
# helpers so the unit tests cover the production path.
__all__ = [
    "add_doctor_parser",
    "agent_health_to_envelope",
    "cmd_doctor",
    "main",
    "report_to_envelope",
]

# Internal helper re-exported for tests of the SurfaceHealth dataclass
# round-trip — used by tests/unit/test_onboarding/test_doctor_cli.py.
_surface_health_to_envelope_for_tests = _surface_health_to_envelope
