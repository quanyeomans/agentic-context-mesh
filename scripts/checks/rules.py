#!/usr/bin/env python3
"""Paved-road query surface for the fitness catalogue (#499 Phase 2).

This is TOOLING under ``scripts/checks/`` — NOT a ``kairix.cli``
subcommand and NOT an MCP tool, so the capability-discipline rules
(F45 new-capability-BDD, F30 operator-outcome-test) do not apply. It
sits next to its siblings ``run_checks.py`` and
``generate_catalogue_docs.py``, which the F22 path-naming allow-list
already names; ``rules`` is added to that same allow-list.

Why it exists
-------------
The catalogue (:mod:`_rule_catalogue`) already answers "which rules
exist and what do they protect?". This surface answers the agent's
*forward* question — "I'm about to do task X; what pattern do I copy,
and which gates will judge me?" — BEFORE any code is written. It reads
the ``exemplar`` + ``task_type`` metadata each high-traffic RuleEntry
carries and turns it into three plain-text views:

* ``rules.py --task <task>`` — every rule tagged with that task, each
  with its F-id, one-line summary, the exemplar to copy, and the
  ``run:`` command that judges it.
* ``rules.py --rule <Fid>`` — one rule's summary, exemplar, task tags,
  and remediation (the ``run:`` command).
* ``rules.py --list-tasks`` — the closed task-type vocabulary with a
  per-task rule count.

Output is plain text in the F21 affordance style: every actionable
line is prefixed so an agent (or human) reading it knows the next move.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _rule_catalogue import ALL_ENTRIES, TASK_TYPES, RuleEntry
from run_checks import is_dispatchable


def _run_command(entry: RuleEntry) -> str:
    """The command an agent runs to make ``entry``'s gate judge their
    work. Every cataloged rule dispatches through the single runner
    (``run_checks.py --gate <id>``); proposed rules have no runnable
    check yet."""
    if not is_dispatchable(entry):
        return "(no runnable check yet — rule is proposed)"
    return f"python3 scripts/checks/run_checks.py --gate {entry.id}"


def _entries_for_task(task: str) -> list[RuleEntry]:
    """Every catalogue entry tagged with ``task``, in catalogue order."""
    return [e for e in ALL_ENTRIES if task in e.task_type]


def _entry_by_id(rule_id: str) -> RuleEntry | None:
    """The first catalogue entry whose id matches ``rule_id``
    (case-insensitive), or ``None``."""
    for entry in ALL_ENTRIES:
        if entry.id.lower() == rule_id.lower():
            return entry
    return None


def render_task(task: str) -> str:
    """The ``--task`` view: every rule governing ``task``, each with its
    F-id, summary, exemplar, and run command."""
    if task not in TASK_TYPES:
        valid = ", ".join(TASK_TYPES)
        return (
            f"unknown task-type: {task!r}\n"
            f"fix: pass one of the closed vocabulary — {valid}\n"
            f"run: python3 scripts/checks/rules.py --list-tasks"
        )
    entries = _entries_for_task(task)
    lines: list[str] = [f"=== Paved road for task: {task} ==="]
    if not entries:
        lines.append("(no rules tagged with this task yet)")
        return "\n".join(lines)
    lines.append(f"{len(entries)} rule(s) govern this task — follow each exemplar:")
    lines.append("")
    for entry in entries:
        lines.append(f"{entry.id}  {entry.summary}")
        exemplar = entry.exemplar if entry.exemplar else "(no curated exemplar — read the rule's remediation)"
        lines.append(f"  copy: {exemplar}")
        lines.append(f"  run:  {_run_command(entry)}")
        lines.append("")
    lines.append("next: read each `copy:` file, mirror its shape, then run each `run:` command.")
    return "\n".join(lines)


def render_rule(rule_id: str) -> str:
    """The ``--rule`` view: one rule's summary, exemplar, task tags, and
    remediation command."""
    entry = _entry_by_id(rule_id)
    if entry is None:
        return (
            f"unknown rule id: {rule_id!r}\n"
            f"fix: pass a real catalogue id (e.g. F46) — see scripts/checks/_rule_catalogue.py\n"
            f"run: python3 scripts/checks/rules.py --list-tasks"
        )
    lines: list[str] = [f"=== {entry.id} ==="]
    lines.append(f"summary:   {entry.summary}")
    exemplar = entry.exemplar if entry.exemplar else "(none — no curated exemplar for this rule)"
    lines.append(f"exemplar:  {exemplar}")
    tasks = ", ".join(entry.task_type) if entry.task_type else "(none)"
    lines.append(f"tasks:     {tasks}")
    lines.append(f"category:  {entry.category}")
    lines.append(f"status:    {entry.status}")
    lines.append("")
    if entry.exemplar:
        lines.append("fix:  copy the pattern in the exemplar file named above")
    else:
        lines.append("fix:  follow the rule summary above; this rule has no curated exemplar yet")
    lines.append("run:  " + _run_command(entry))
    return "\n".join(lines)


def render_list_tasks() -> str:
    """The ``--list-tasks`` view: the closed task vocabulary with a
    per-task rule count."""
    lines: list[str] = ["=== Paved-road task types (closed vocabulary) ==="]
    lines.append("Each task names a thing an agent BUILDS; the count is how many")
    lines.append("rules judge that task. Query one with --task <name>.")
    lines.append("")
    for task in TASK_TYPES:
        count = len(_entries_for_task(task))
        lines.append(f"  {task:24} {count} rule(s)")
    lines.append("")
    lines.append("run: python3 scripts/checks/rules.py --task <name>")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse the query mode and print the corresponding view."""
    parser = argparse.ArgumentParser(
        prog="rules.py",
        description="Paved-road query surface for the fitness catalogue (#499 Phase 2).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", metavar="TASK", help="list rules governing a task-type (e.g. adding-a-connector)")
    group.add_argument("--rule", metavar="ID", help="show one rule's summary, exemplar, tasks, remediation")
    group.add_argument("--list-tasks", action="store_true", help="list the task-type vocabulary with rule counts")
    args = parser.parse_args(argv)

    if args.list_tasks:
        print(render_list_tasks())
        return 0
    if args.task is not None:
        out = render_task(args.task)
        print(out)
        return 0 if not out.startswith("unknown task-type") else 2
    out = render_rule(args.rule)
    print(out)
    return 0 if not out.startswith("unknown rule id") else 2


if __name__ == "__main__":
    sys.exit(main())
