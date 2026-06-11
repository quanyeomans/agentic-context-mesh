"""FHS / XDG directory tree creation for the kairix self-installer (Plan 1 task 5).

Lays down the per-mode directory tree that :mod:`kairix.paths` resolves
against. Idempotent: the second (and Nth) ``kairix init`` run reports
``"existing"`` for every untouched directory and ``"mode-adjusted"`` for
any directory whose perms drifted since the last run.

Two layers:

* :func:`specs_for` — declarative. Returns the list of
  :class:`DirSpec` entries the installer must create for the given
  :class:`~kairix.paths.Mode`. Pure, no IO; safe to call inside
  ``--dry-run`` reporting.
* :func:`ensure_dirs` — imperative. Walks the spec list, creates
  missing dirs, adjusts modes that drifted, and best-effort sets
  ownership via :func:`os.chown`. Tolerates :class:`PermissionError`
  on the chown so a non-root operator running ``--user`` against a
  layout under their HOME does not crash on the rare path that lies
  outside their owned tree (e.g. ``$XDG_RUNTIME_DIR`` on a shared
  host) — the operator is told via the install report and resolves
  out-of-band. With ``strict=False`` (container installs, #469) the
  mkdir/chmod pair gets the same best-effort treatment: bind-mounted
  paths like ``/run/secrets`` are owned by the container runtime, so
  a denied mkdir is recorded as ``"perms-unmanaged"`` instead of
  killing first boot.

The system-mode spec mirrors the FHS / Debian convention used by
``apt install`` for daemon packages:

  * ``/etc/kairix`` owned ``root:root`` mode 0755 (admin-edits config)
  * ``/var/lib/kairix`` owned ``kairix:kairix`` mode 0755 (state)
  * ``/var/cache/kairix`` owned ``kairix:kairix`` mode 0755 (regen-able)
  * ``/run/secrets/kairix`` owned ``root:kairix`` mode 0750 (tmpfs)

The user-mode spec uses the XDG base-dir contract — every directory
owned by the invoking user, mode 0700 (private by default).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from kairix.paths import Mode, cache_dir, config_dir, data_dir, runtime_secrets_dir

_logger = logging.getLogger("kairix.install.dirs")

# Mode-octal constants used by both system + user spec branches. Centralised
# so any future hardening (e.g. tighter 0750 on /var/lib/kairix) flips one
# literal rather than four.
_MODE_PRIVATE = 0o700  # XDG-style user-private
_MODE_FHS_WORLD_READ = 0o755  # FHS daemon-state directories
_MODE_FHS_GROUP_READ = 0o750  # FHS runtime secrets (root:kairix)


@dataclass(frozen=True)
class DirSpec:
    """Declarative spec for one directory in the install tree.

    Fields:
      * ``path`` — absolute filesystem path the installer will create.
      * ``mode_octal`` — POSIX permission bits the installer enforces
        (e.g. ``0o755`` for ``/var/lib/kairix``, ``0o700`` for XDG dirs).
      * ``owner_uid`` — uid the directory should be owned by. ``0`` for
        root-owned system dirs; the invoking user's uid for XDG dirs.
      * ``owner_gid`` — matching gid.
    """

    path: Path
    mode_octal: int
    owner_uid: int
    owner_gid: int


class DirActionReport(TypedDict):
    """One entry in the :func:`ensure_dirs` return list."""

    path: str
    action: str  # "created" | "existing" | "mode-adjusted" | "perms-unmanaged"


def specs_for(mode: Mode, *, uid: int, gid: int) -> list[DirSpec]:
    """Return the directory specs the installer should lay down for ``mode``.

    Args:
        mode: The :class:`~kairix.paths.Mode` to materialise. The
            ``container`` mode shares the system layout (the container
            image owns ``/etc`` / ``/var``), so callers wanting a
            container install pass ``Mode.system`` here.
        uid: The uid to use for directories the kairix runtime owns. For
            ``Mode.system`` this is the resolved ``kairix`` system user
            uid (from :func:`kairix.install.system_user.ensure_kairix_system_user`).
            For ``Mode.user`` this is the invoking user's uid
            (``os.getuid()``).
        gid: Matching gid.

    Returns:
        Ordered list of :class:`DirSpec` entries. The order is stable
        across calls so the install report reads top-to-bottom in the
        same sequence on every run (config → data → cache → secrets).

    Per-mode shape:

    * ``Mode.user`` — four XDG dirs, all owned by the invoking user,
      mode 0700 (XDG-spec default for user-private state).
    * ``Mode.system`` — four FHS dirs:

      * ``/etc/kairix`` owned ``root:root`` (admin-edited config tree).
      * ``/var/lib/kairix`` + ``/var/cache/kairix`` owned ``kairix:kairix``.
      * ``/run/secrets/kairix`` owned ``root:kairix`` mode 0750 (tmpfs
        secret store the systemd unit binds in; root writes, the
        kairix runtime group reads).
    """
    if mode == Mode.user:
        return [
            DirSpec(config_dir(Mode.user), _MODE_PRIVATE, uid, gid),
            DirSpec(data_dir(Mode.user), _MODE_PRIVATE, uid, gid),
            DirSpec(cache_dir(Mode.user), _MODE_PRIVATE, uid, gid),
            DirSpec(runtime_secrets_dir(Mode.user), _MODE_PRIVATE, uid, gid),
        ]
    # Mode.system (and container, which callers map onto system here)
    return [
        DirSpec(config_dir(Mode.system), _MODE_FHS_WORLD_READ, 0, 0),
        DirSpec(data_dir(Mode.system), _MODE_FHS_WORLD_READ, uid, gid),
        DirSpec(cache_dir(Mode.system), _MODE_FHS_WORLD_READ, uid, gid),
        DirSpec(runtime_secrets_dir(Mode.system), _MODE_FHS_GROUP_READ, 0, gid),
    ]


def ensure_dirs(specs: list[DirSpec], *, strict: bool = True) -> list[DirActionReport]:
    """Create the directories in ``specs`` idempotently.

    For each :class:`DirSpec` (in order):

    1. If the path does not exist, ``mkdir(parents=True, exist_ok=False)``.
       Action recorded as ``"created"``.
    2. If the path exists, action starts as ``"existing"``.
    3. If the current mode bits (low 12) differ from ``spec.mode_octal``,
       ``chmod`` to the desired mode. If the entry was already
       ``"existing"`` (i.e. the operator left a dir with drifted perms
       between runs), the action upgrades to ``"mode-adjusted"``.
       A just-created directory always passes through chmod (since
       ``mkdir`` honours umask, not the spec) but keeps action
       ``"created"`` — the more informative shape.
    4. Best-effort ``os.chown`` to ``(owner_uid, owner_gid)``.
       :class:`PermissionError` is swallowed: under ``Mode.user`` a
       handful of XDG paths can sit on shared mounts the operator
       cannot chown; the operator resolves these out-of-band by
       reading the install report.

    Args:
        specs: ordered directory specs to lay down.
        strict: when ``True`` (default — system / user installs), a
            :class:`PermissionError` from the mkdir/chmod pair
            propagates so the install fails loudly. When ``False``
            (container installs, #469), the failure is recorded as
            ``"perms-unmanaged"`` — bind-mounted paths such as
            ``/run/secrets`` are owned by the container runtime, the
            report still surfaces the path, and the walk continues.

    Returns:
        One :class:`DirActionReport` per input spec, same order. The
        ``action`` field is one of ``"created"`` / ``"existing"`` /
        ``"mode-adjusted"`` / ``"perms-unmanaged"`` (the last only
        with ``strict=False``).
    """
    return [_ensure_one_dir(spec, strict=strict) for spec in specs]


def _ensure_one_dir(spec: DirSpec, *, strict: bool) -> DirActionReport:
    """Lay down a single :class:`DirSpec`; see :func:`ensure_dirs` for the contract."""
    try:
        action = _create_and_align_mode(spec)
    except PermissionError:
        if strict:
            raise
        _logger.warning(
            "kairix install: mkdir/chmod denied for %s (runtime-owned mount?) — "
            "recorded action=perms-unmanaged and continuing. fix: lay the "
            "directory down in the image build or mount it writable, then "
            "re-run `kairix init`.",
            spec.path,
        )
        return {"path": str(spec.path), "action": "perms-unmanaged"}
    try:
        os.chown(spec.path, spec.owner_uid, spec.owner_gid)
    except PermissionError:
        # User-mode + path not under the invoking user's owned tree
        # (rare XDG_RUNTIME_DIR on shared hosts). The install report
        # surfaces the path; operator fixes out-of-band.
        pass
    return {"path": str(spec.path), "action": action}


def _create_and_align_mode(spec: DirSpec) -> str:
    """mkdir-if-missing + chmod-if-drifted for one spec; returns the action label."""
    action = "existing"
    if not spec.path.exists():
        spec.path.mkdir(parents=True, exist_ok=False)
        action = "created"
    if spec.path.stat().st_mode & 0o7777 != spec.mode_octal:
        spec.path.chmod(spec.mode_octal)
        if action == "existing":
            action = "mode-adjusted"
    return action
