"""kairix uninstall -- remove the kairix install layout (Plan 1 task 8).

Non-destructive by default: removes the systemd unit + config file +
cache dir, but KEEPS the data dir (operator state) so an accidental
``kairix uninstall`` does not erase the SQLite index, vector index, or
ingested document store.

To wipe data too, pass ``--no-keep-data`` (deliberately verbose so a
shell mistake on the flag does not delete state).

Mode resolution mirrors ``kairix init``:

  - ``--system`` — operate on the system layout (requires root).
  - ``--user`` — operate on the per-user layout.
  - (auto-detect) — picks system if running as root, user otherwise.

The system user / group itself is never removed: that's a destructive
op an operator does deliberately via ``userdel kairix`` once they're
sure no other state depends on it.

Tests of the underlying behaviour live in ``tests/install/`` (added
alongside subsequent uninstall hardening); this module's own coverage
lives in ``tests/integration/test_cli_uninstall.py`` (F30 outcome
tests against the subprocess CLI surface).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any

from kairix.install.installer import uninstall
from kairix.paths import Mode

# F17 (S1192): the argparse ``action`` kwarg literal appears on every
# bool flag the parser declares (--system / --user / --json), so hoist
# to a module-level constant rather than repeat the 10-char literal.
_ARGPARSE_STORE_TRUE = "store_true"


def main(argv: list[str] | None = None) -> int:
    """Argparse entry point for ``kairix uninstall``.

    Returns the process exit code:
      * ``0`` — uninstall succeeded (or was a no-op).
      * ``1`` — permission-check failure (e.g. ``--system`` without root).
      * ``2`` — invalid CLI args.
    """
    parser = argparse.ArgumentParser(prog="kairix uninstall")
    parser.add_argument(
        "--system",
        action=_ARGPARSE_STORE_TRUE,
        help="uninstall the system-wide layout (requires root)",
    )
    parser.add_argument("--user", action=_ARGPARSE_STORE_TRUE, help="uninstall the per-user layout")
    parser.add_argument(
        "--no-keep-data",
        dest="keep_data",
        action="store_false",
        default=True,
        help="also delete the data dir (SQLite index, vector index, documents); default keeps data",
    )
    parser.add_argument("--json", action=_ARGPARSE_STORE_TRUE, help="emit machine-readable JSON report")
    args = parser.parse_args(argv)

    if args.system and args.user:
        print("--system and --user are mutually exclusive", file=sys.stderr)
        return 2

    mode = _resolve_mode(args)
    if mode is None:
        return 1

    report = uninstall(mode=mode, keep_data=args.keep_data)
    if args.json:
        print(json.dumps(_to_jsonable(report), default=str, indent=2))
    else:
        _print_human(report)
    return 0


def _resolve_mode(args: argparse.Namespace) -> Mode | None:
    """Resolve the operator-requested mode + permission check.

    Returns ``None`` (and prints an actionable affordance to stderr)
    when the request is incompatible with the running euid.
    """
    if args.system:
        if os.geteuid() != 0:
            print(
                "system-mode uninstall requires root; re-run with sudo OR pass --user",
                file=sys.stderr,
            )
            return None
        return Mode.system
    if args.user:
        if os.geteuid() == 0:
            print(
                "user-mode uninstall as root targets /root/.config; pass --system to remove the system install instead",
                file=sys.stderr,
            )
            return None
        return Mode.user
    return Mode.detect()


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / dicts / lists into JSON-safe shapes."""
    # ``is_dataclass`` returns True for both classes and instances; narrow
    # to instance-only here so ``asdict`` (which rejects raw types) is
    # safe to call. Matching ``not isinstance(obj, type)`` is the
    # canonical narrowing per mypy's NewType / dataclass docs.
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    return obj


def _print_human(report: Any) -> None:
    """Render the uninstall report as plain text for an interactive operator."""
    print(f"kairix uninstall -- mode={report.mode} keep_data={report.keep_data}")
    for path in report.removed:
        print(f"  removed: {path}")
    for path in report.kept:
        print(f"  kept: {path}")
    if not report.removed and not report.kept:
        print("  (nothing to do -- install layout was already absent)")


if __name__ == "__main__":
    sys.exit(main())
