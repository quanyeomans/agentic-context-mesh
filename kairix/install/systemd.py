"""Render and install the kairix.service systemd unit (Plan 1 task 6).

Two public entry points:

* :func:`render_unit` — turn ``Mode`` + binary path + config path into a
  ready-to-write unit file string. Pure: no I/O, no subprocess.
* :func:`install_unit` — write the rendered content to the right systemd
  location for the mode (``/etc/systemd/system/`` for ``system``,
  ``~/.config/systemd/user/`` for ``user``), then ``systemctl
  daemon-reload`` + ``systemctl enable``.

The subprocess seam and the destination directory are both injected via
:class:`SystemdDeps` so unit tests run without actually shelling out to
systemctl AND without writing under the real ``/etc/`` or ``$HOME``. This
mirrors :class:`kairix.install.system_user.SystemUserDeps`'s F6-clean
shape (no ``*_fn=None`` test-only kwarg on the public callable):
production callers omit ``deps`` and get real ``subprocess.run`` + the
real FHS / XDG locations; tests pass
``SystemdDeps(subprocess_runner=fake, target_dir=tmp_path)``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from kairix.paths import Mode

# Autoescape is intentionally empty: systemd unit files are plain text
# (ini-style key=value), not HTML / XML, so escaping ``&``/``<``/``>``
# would corrupt the rendered unit (e.g. an ``ExecStart=`` path containing
# ``&`` would become ``&amp;``). ``select_autoescape([])`` is the
# F3-acceptable way to satisfy ruff S701 (the auto-escape-on-extension
# helper) for a non-HTML target.
_env = Environment(
    loader=PackageLoader("kairix.install", "templates"),
    autoescape=select_autoescape([]),
)

# ``subprocess.run``-shaped runner. Production passes through the real
# ``subprocess.run``; tests pass a fake that records argv + kwargs.
SubprocessRunner = Callable[..., "subprocess.CompletedProcess[bytes]"]

_UNIT_FILENAME = "kairix.service"


@dataclass
class SystemdDeps:
    """Injectable dependencies for :func:`install_unit`.

    F6-clean: both fields have ``default_factory`` so production callers
    either omit the argument entirely or construct ``SystemdDeps()`` and
    get the real ``subprocess.run`` + ``None`` target_dir (meaning "use
    the FHS / XDG default for the mode"). Tests pass
    ``SystemdDeps(subprocess_runner=fake_runner, target_dir=tmp_path)``.

    Fields:
      * ``subprocess_runner`` — callable matching ``subprocess.run``'s shape.
      * ``target_dir`` — if set, write the unit file under this directory
        instead of the FHS / XDG default. Tests use ``tmp_path`` here so
        the assertion runs against a real file on disk without touching
        ``/etc/`` or ``$HOME``.
    """

    subprocess_runner: SubprocessRunner = field(default_factory=lambda: subprocess.run)
    target_dir: Path | None = None


def render_unit(mode: Mode, *, kairix_bin: str, config_path: Path) -> str:
    """Render ``kairix.service.j2`` for the given mode.

    Pure function: no I/O, no subprocess. The ``user_directive`` is
    ``"User=kairix"`` in system mode and the empty string in user mode
    (a user-mode systemd unit must NOT declare ``User=`` — it inherits
    the invoking user).
    """
    tpl = _env.get_template("kairix.service.j2")
    return tpl.render(
        mode=mode.value,
        kairix_bin=kairix_bin,
        config_path=str(config_path),
        user_directive="User=kairix" if mode == Mode.system else "",
    )


def install_unit(
    mode: Mode,
    *,
    content: str,
    deps: SystemdDeps | None = None,
) -> dict[str, str]:
    """Write the unit file + invoke ``systemctl daemon-reload`` + ``enable``.

    Returns a small dict describing the action: the resolved target
    path and the mode that was installed. Idempotent at the file level
    (``write_text`` overwrites); idempotent at the systemctl level
    because ``enable`` is itself idempotent.
    """
    deps = deps or SystemdDeps()
    target_dir = deps.target_dir or _default_target_dir(mode)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _UNIT_FILENAME
    target.write_text(content)
    target.chmod(0o644)

    systemctl_argv = _systemctl_argv_for(mode)
    _run(deps.subprocess_runner, [*systemctl_argv, "daemon-reload"])
    _run(deps.subprocess_runner, [*systemctl_argv, "enable", _UNIT_FILENAME])

    return {"path": str(target), "mode": mode.value}


def _default_target_dir(mode: Mode) -> Path:
    """Resolve the canonical systemd unit directory for the mode."""
    if mode == Mode.system:
        return Path("/etc/systemd/system")
    # user + container fall through to the per-user systemd location.
    # Container mode doesn't run systemd, but install_unit isn't called
    # there in practice; the resolver still has to return *something*.
    return Path.home() / ".config" / "systemd" / "user"


def _systemctl_argv_for(mode: Mode) -> list[str]:
    """Return the systemctl prefix list: ``["systemctl"]`` or ``["systemctl", "--user"]``."""
    if mode == Mode.system:
        return ["systemctl"]
    return ["systemctl", "--user"]


def _run(runner: SubprocessRunner, argv: Sequence[str]) -> None:
    """Invoke ``runner`` with the standard install-time args.

    Centralised so both ``daemon-reload`` and ``enable`` get the same
    ``check=True`` + ``capture_output=True`` discipline without
    duplicating the literal kwargs at every call site (S1192).
    """
    runner(list(argv), check=True, capture_output=True)
