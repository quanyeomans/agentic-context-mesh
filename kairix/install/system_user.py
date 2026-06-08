"""Idempotent system-user creation for kairix install (Plan 1 task 4).

Creates a system user ``kairix`` (uid auto-assigned in the 990-999 range
by default) + matching group. Idempotent: if the user already exists,
report ``action="existing"`` and return their existing uid/gid.

Only callable when ``geteuid() == 0`` (i.e. root). Raises
:class:`PermissionError` otherwise; the caller is responsible for the
user-mode fallback (``kairix init --user``).

Subprocess execution is injected via :class:`SystemUserDeps` so unit
tests can run without actually shelling out to ``useradd`` /
``groupadd``. This is the F6-clean DI seam (no ``*_fn=None`` test-only
kwarg on the public callable) — production callers construct
``SystemUserDeps()`` (or omit it entirely) and get the real
``subprocess.run``; tests pass a fake ``subprocess_runner``.
"""

from __future__ import annotations

import grp
import logging
import os
import pwd
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

_logger = logging.getLogger("kairix.install.system_user")

KAIRIX_USER = "kairix"
KAIRIX_GROUP = "kairix"

# ``Callable`` alias matching ``subprocess.run``'s shape for the args we use.
# We only call it with a positional argv list plus ``check=`` /
# ``capture_output=`` kwargs, so the signature is intentionally permissive.
SubprocessRunner = Callable[..., "subprocess.CompletedProcess[bytes]"]


@dataclass(frozen=True)
class SystemUserResult:
    """Outcome of :func:`ensure_kairix_system_user`.

    Fields:
      * ``action`` — ``"created"`` on first run, ``"existing"`` on idempotent re-run.
      * ``uid`` — resolved system uid of the kairix user (from ``pwd.getpwnam``).
      * ``gid`` — resolved system gid of the kairix group (from ``grp.getgrnam``).
    """

    action: str
    uid: int
    gid: int


@dataclass
class SystemUserDeps:
    """Injectable dependencies for :func:`ensure_kairix_system_user`.

    F6-clean: the only field has a ``default_factory`` so production
    callers either omit the argument entirely or construct
    ``SystemUserDeps()`` and get the real ``subprocess.run``; tests
    pass ``SystemUserDeps(subprocess_runner=fake)``.
    """

    subprocess_runner: SubprocessRunner = field(default_factory=lambda: subprocess.run)


def ensure_kairix_system_user(*, deps: SystemUserDeps | None = None) -> SystemUserResult:
    """Ensure the ``kairix`` system user + group exist. Idempotent.

    Returns a :class:`SystemUserResult` describing whether the user was
    created on this call or already present.

    Raises:
        PermissionError: when called without root privileges.
    """
    if os.geteuid() != 0:
        raise PermissionError(
            "ensure_kairix_system_user requires root. "
            "fix: re-run with sudo for system-mode install, "
            "or pass --user for a per-user install. "
            "run: sudo kairix init --system"
        )

    deps = deps or SystemUserDeps()

    # Already exists? Look up both the user and the group; either missing
    # means the install is incomplete and we fall through to creation.
    try:
        pw = pwd.getpwnam(KAIRIX_USER)
        gr = grp.getgrnam(KAIRIX_GROUP)
        return SystemUserResult(action="existing", uid=pw.pw_uid, gid=gr.gr_gid)
    except KeyError:
        pass

    # Group first — useradd's --gid resolves against the existing group.
    _run(deps.subprocess_runner, ["groupadd", "--system", KAIRIX_GROUP])
    _run(
        deps.subprocess_runner,
        [
            "useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            "--gid",
            KAIRIX_GROUP,
            "--home-dir",
            "/var/lib/kairix",
            KAIRIX_USER,
        ],
    )

    pw = pwd.getpwnam(KAIRIX_USER)
    gr = grp.getgrnam(KAIRIX_GROUP)
    _logger.info("kairix_system_user_created uid=%d gid=%d", pw.pw_uid, gr.gr_gid)
    return SystemUserResult(action="created", uid=pw.pw_uid, gid=gr.gr_gid)


def _run(runner: SubprocessRunner, argv: Sequence[str]) -> None:
    """Invoke ``runner`` with the standard install-time args.

    Centralised so both ``groupadd`` and ``useradd`` get the same
    ``check=True`` + ``capture_output=True`` discipline without
    duplicating the literal kwargs at every call site (S1192).
    """
    runner(list(argv), check=True, capture_output=True)
