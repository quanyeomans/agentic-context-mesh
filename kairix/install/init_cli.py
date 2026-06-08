"""kairix init — self-install CLI subcommand (Plan 1 task 8).

Lays down the FHS/XDG dir tree + systemd unit + config template for
the kairix knowledge service. Three operating modes:

  - ``--system`` — system-wide install (requires root); creates kairix
    user + ``/etc/kairix`` + ``/var/lib/kairix`` + systemd unit.
  - ``--user`` — per-user install (no root needed); installs to
    ``~/.config/kairix`` + ``~/.local/share/kairix`` + user-mode systemd unit.
  - (auto-detect) — picks system if running as root, user otherwise.

Idempotent: re-running is a no-op (existing files retained).

The ``verify`` subaction reports install health (every layer's status).

The CLI is a thin argparse wrapper over
:func:`kairix.install.installer.install` and
:func:`kairix.install.installer.verify`. Tests of the underlying
behaviour live in ``tests/install/test_installer.py``; this module's
own coverage lives in
``tests/integration/test_cli_init.py`` (F30 outcome tests against the
subprocess CLI surface).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any

from kairix.install.installer import install, verify
from kairix.paths import Mode

# F17 (S1192): the argparse ``action`` kwarg literal appears on every
# bool flag the parser declares (--system / --user / --json), so hoist
# to a module-level constant rather than repeat the 10-char literal.
_ARGPARSE_STORE_TRUE = "store_true"


def main(argv: list[str] | None = None) -> int:
    """Argparse entry point for ``kairix init``.

    Returns the process exit code:
      * ``0`` — install / verify succeeded.
      * ``1`` — install / verify failed (e.g. system-mode without root,
        or verify reported any layer missing).
      * ``2`` — invalid CLI args (mutually exclusive ``--system`` / ``--user``).
    """
    parser = argparse.ArgumentParser(prog="kairix init")
    parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "verify"],
        help="install (default) or verify",
    )
    parser.add_argument("--system", action=_ARGPARSE_STORE_TRUE, help="system-wide install (requires root)")
    parser.add_argument("--user", action=_ARGPARSE_STORE_TRUE, help="per-user install (no root needed)")
    parser.add_argument("--json", action=_ARGPARSE_STORE_TRUE, help="emit machine-readable JSON report")
    args = parser.parse_args(argv)

    if args.system and args.user:
        print("--system and --user are mutually exclusive", file=sys.stderr)
        return 2

    mode = _resolve_mode(args)
    if mode is None:
        # Permission-check failure already printed an actionable message.
        return 1

    if args.action == "verify":
        report = verify(mode=mode)
        if args.json:
            print(json.dumps(_to_jsonable(report), default=str, indent=2))
        else:
            _print_verify_human(report)
        return 0 if report.ok else 1

    install_report = install(mode=mode)
    if args.json:
        print(json.dumps(_to_jsonable(install_report), default=str, indent=2))
    else:
        _print_install_human(install_report)
    return 0


def _resolve_mode(args: argparse.Namespace) -> Mode | None:
    """Resolve the operator-requested install mode + permission check.

    Returns ``None`` (and prints an actionable affordance to stderr)
    when the request is incompatible with the running euid (e.g.
    ``--system`` requested from a non-root shell).
    """
    if args.system:
        if os.geteuid() != 0:
            print(
                "system-mode install requires root; re-run with sudo OR pass --user",
                file=sys.stderr,
            )
            return None
        return Mode.system
    if args.user:
        if os.geteuid() == 0:
            print(
                "user-mode install as root puts kairix under /root/.config; pass --system for a system install instead",
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


def _print_install_human(report: Any) -> None:
    """Render the install report as plain text for an interactive operator."""
    print(f"kairix install -- mode={report.mode}")
    if report.user:
        print(f"  user: uid={report.user.get('uid')} gid={report.user.get('gid')} action={report.user.get('action')}")
    for d in report.dirs:
        print(f"  dir: {d.get('path')} action={d.get('action')}")
    print(f"  config: {report.config.get('path')} action={report.config.get('action')}")
    print(f"  systemd: {report.systemd.get('path')} mode={report.systemd.get('mode')}")


def _print_verify_human(report: Any) -> None:
    """Render the verify report as plain text for an interactive operator."""
    print(f"kairix verify -- mode={report.mode} ok={report.ok}")
    print(f"  user_ok: {report.user_ok}")
    for d in report.dirs_ok:
        print(f"  dir: {d.path} present={d.present} mode_correct={d.mode_correct}")
    print(f"  config_ok: {report.config_ok}")
    print(f"  systemd_ok: {report.systemd_ok}")


if __name__ == "__main__":
    sys.exit(main())
