"""High-level installer orchestration (Plan 1 task 7).

The user-facing ``kairix init`` CLI lands in Plan 1 task 8 and delegates
the entire install flow to :func:`install` below; the verifier behind
``kairix init verify`` delegates to :func:`verify`.

The installer is a thin orchestrator over four lower-level layers:

* :mod:`kairix.install.system_user` — system-user / group creation
  (system mode only).
* :mod:`kairix.install.dirs` — FHS / XDG directory layout.
* :func:`kairix.install.systemd.render_unit` — render the systemd unit
  template for the mode.
* :func:`kairix.install.systemd.install_unit` — write the unit file +
  ``systemctl daemon-reload`` + ``enable``.

Each layer is reachable through :class:`InstallerDeps` so unit tests
inject record-only fakes for every callable without monkeypatching
``kairix.*`` internals (F1-clean). This mirrors the F6-clean shape of
:class:`SystemUserDeps` + :class:`SystemdDeps`: production callers omit
``deps`` and get the real callables; tests pass ``InstallerDeps(...)``.

:func:`verify` walks the install layout and reports whether each
element is present + healthy. The CLI surface treats ``verify(...).ok``
as the exit-code switch.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kairix.install.dirs import (
    DirActionReport,
    ensure_dirs,
    specs_for,
)
from kairix.install.system_user import (
    SystemUserResult,
    ensure_kairix_system_user,
)
from kairix.install.systemd import (
    SystemdDeps,
    install_unit,
    render_unit,
)
from kairix.paths import Mode, config_dir

_logger = logging.getLogger("kairix.install.installer")

# Centralised file-name constants. Each appears in installer.py +
# verify() + _ensure_default_config(), i.e. ≥3 times — F17 (S1192)
# hygiene says hoist to module-level rather than repeat the literal.
_CONFIG_FILENAME = "kairix.config.yaml"
_CONFIG_TEMPLATE_NAME = "kairix.config.yaml.j2"
_UNIT_FILENAME = "kairix.service"
_TEMPLATES_PACKAGE_DIR = "templates"

# Default kairix binary location when ``shutil.which`` returns ``None``
# (e.g. inside a constrained test process where ``PATH`` doesn't include
# the venv's ``bin/``). Matches the FHS default for ``pip install`` on a
# standard Linux host.
_DEFAULT_KAIRIX_BIN = "/usr/local/bin/kairix"


# Callable aliases for the InstallerDeps fields. Match the public shape
# of each underlying entry point so the production defaults plug in
# without any wrapper. _DirCreator takes (specs, *, strict=...) — the
# strict keyword is the container-mode best-effort seam (#469).
_UserCreator = Callable[..., SystemUserResult]
_DirCreator = Callable[..., list[DirActionReport]]
_UnitRenderer = Callable[..., str]
_UnitInstaller = Callable[..., dict[str, str]]


@dataclass
class InstallerDeps:
    """Injectable dependencies for :func:`install` + :func:`verify`.

    F6-clean: every field has a ``default_factory`` (or ``None`` default)
    so production callers either omit the argument entirely or construct
    ``InstallerDeps()`` and get the real callables; tests pass
    ``InstallerDeps(user_creator=fake, dir_creator=fake, ...)`` to
    record-only fakes.

    Fields:
      * ``user_creator`` — the system-user-creation callable. Production
        default is :func:`ensure_kairix_system_user`. Called only in
        system mode; user mode skips it entirely.
      * ``dir_creator`` — the dir-layout callable. Production default is
        :func:`ensure_dirs`.
      * ``unit_renderer`` — renders the systemd unit content. Production
        default is :func:`render_unit`.
      * ``unit_installer`` — writes the rendered unit + reloads systemd.
        Production default is :func:`install_unit`.
      * ``kairix_bin`` — absolute path to the ``kairix`` executable to
        bake into the systemd unit. ``None`` (production default) means
        resolve via ``shutil.which("kairix")`` falling back to
        ``/usr/local/bin/kairix``. Tests pin this so the rendered unit
        is deterministic without depending on the test process ``PATH``.
      * ``config_target_dir`` — override the directory the default
        ``kairix.config.yaml`` is written to. ``None`` (production
        default) means use ``config_dir(mode)``. Tests pin to
        ``tmp_path`` so the assertion runs against a real file without
        touching ``/etc/`` or ``$HOME``.
      * ``systemd_target_dir`` — forwarded into the :class:`SystemdDeps`
        the orchestrator constructs. Tests use ``tmp_path`` here so the
        rendered unit file lands in a controlled location.
    """

    user_creator: _UserCreator = field(default_factory=lambda: ensure_kairix_system_user)
    dir_creator: _DirCreator = field(default_factory=lambda: ensure_dirs)
    unit_renderer: _UnitRenderer = field(default_factory=lambda: render_unit)
    unit_installer: _UnitInstaller = field(default_factory=lambda: install_unit)
    kairix_bin: str | None = None
    config_target_dir: Path | None = None
    systemd_target_dir: Path | None = None


@dataclass(frozen=True)
class InstallReport:
    """Outcome of :func:`install`.

    Fields:
      * ``mode`` — the resolved mode value (``"system"`` / ``"user"`` /
        ``"container"``) the install ran in.
      * ``user`` — ``None`` in user mode (no system-user creation).
        In system mode, a dict with the action / uid / gid fields from
        :class:`SystemUserResult`.
      * ``dirs`` — one :class:`DirActionReport` per spec in the order
        :func:`specs_for` returns.
      * ``config`` — dict with ``path`` (absolute path of the written
        config file) + ``action`` (``"created"`` / ``"existing"``).
      * ``systemd`` — dict with ``path`` (absolute path of the unit
        file) + ``mode`` (the mode value the unit was installed for).
        In container mode no unit is installed (s6 supervises the
        service, #469) and the dict reads
        ``{"action": "skipped-container", "mode": "container"}``.
    """

    mode: str
    user: dict[str, int | str] | None
    dirs: list[DirActionReport]
    config: dict[str, str]
    systemd: dict[str, str]


@dataclass(frozen=True)
class DirVerifyReport:
    """Per-directory verify result returned inside :class:`VerifyReport`."""

    path: str
    present: bool
    mode_correct: bool


@dataclass(frozen=True)
class VerifyReport:
    """Outcome of :func:`verify`.

    The CLI surface (Plan 1 task 8) maps ``ok`` to the process exit
    code: ``0`` when ``ok is True``, ``1`` otherwise.

    Fields:
      * ``mode`` — the mode value verify ran against.
      * ``user_ok`` — ``True`` when the kairix system user exists
        (system mode) or always ``True`` in user mode (no system-user
        layer to verify).
      * ``dirs_ok`` — one :class:`DirVerifyReport` per directory in the
        order :func:`specs_for` returns.
      * ``config_ok`` — ``True`` when the default config file exists.
      * ``systemd_ok`` — ``True`` when the unit file exists at the
        expected location for the mode. Always ``True`` in container
        mode — no unit is installed there (s6 supervises the service,
        #469), so its absence is healthy.
      * ``ok`` — AND of every per-layer flag above. The single boolean
        the CLI surface reads.
    """

    mode: str
    user_ok: bool
    dirs_ok: list[DirVerifyReport]
    config_ok: bool
    systemd_ok: bool
    ok: bool


def install(*, mode: Mode, deps: InstallerDeps | None = None) -> InstallReport:
    """Run every install layer for ``mode``. Idempotent.

    The four layers fire in dependency order:

    1. System-user creation (system mode only) — every subsequent
       chown call needs the resolved uid / gid.
    2. Directory layout — needs the uid / gid from step 1 (system
       mode) or the invoking user (user mode).
    3. Default config file — written under :func:`config_dir` (or the
       :attr:`InstallerDeps.config_target_dir` override). Idempotent:
       a pre-existing file is left untouched and reported as
       ``"existing"``.
    4. systemd unit — render + write + reload + enable. Skipped in
       container mode (#469): the image has no systemd (s6 supervises
       the service) and the Dockerfile already laid the tree down, so
       ``kairix init`` acts as a verifier there; the report records
       the deliberate skip.

    Returns an :class:`InstallReport` capturing the per-layer outcome.
    """
    deps = deps or InstallerDeps()
    user_result, uid, gid = _resolve_user_and_uid_gid(mode, deps)

    specs = specs_for(mode, uid=uid, gid=gid)
    # Container mode runs dir creation best-effort: bind-mounted paths
    # (e.g. /run/secrets with the documented kairix.env mount) are owned
    # by the container runtime, so a denied mkdir/chmod is recorded in
    # the report instead of killing first boot (#469).
    dirs_result = deps.dir_creator(specs, strict=mode != Mode.container)

    config_result = _ensure_default_config(mode, deps=deps)
    if mode == Mode.container:
        # No systemd inside the container — s6 supervises the kairix
        # process. Record the skip so `kairix init --json` shows the
        # decision (#469).
        systemd_result = {"action": "skipped-container", "mode": mode.value}
    else:
        systemd_result = _install_systemd(mode, deps=deps)

    return InstallReport(
        mode=mode.value,
        user=_user_result_as_dict(user_result),
        dirs=dirs_result,
        config=config_result,
        systemd=systemd_result,
    )


def verify(*, mode: Mode, deps: InstallerDeps | None = None) -> VerifyReport:
    """Walk the install layout for ``mode`` and report element health.

    Every check is read-only — :func:`verify` never mutates the
    filesystem. Tests can therefore call ``verify(mode=Mode.user)``
    against a simulated install rooted in ``tmp_path`` via XDG env
    vars without leaving artefacts behind.

    Returns a :class:`VerifyReport` whose ``ok`` field is the AND of
    every per-layer flag — the single boolean the CLI maps to the
    process exit code.
    """
    deps = deps or InstallerDeps()

    user_ok = _verify_user(mode)

    dirs_ok: list[DirVerifyReport] = []
    # ``-1`` uid / gid is fine for verify — :func:`specs_for` consumes
    # them only when building DirSpecs we'd hand to ``ensure_dirs``;
    # verify only reads the paths + expected mode bits off the spec.
    uid, gid = _resolve_uid_gid_for_verify(mode)
    for spec in specs_for(mode, uid=uid, gid=gid):
        present = spec.path.exists()
        mode_correct = present and (spec.path.stat().st_mode & 0o7777 == spec.mode_octal)
        dirs_ok.append(DirVerifyReport(path=str(spec.path), present=present, mode_correct=mode_correct))

    config_path = _config_target_dir(mode, deps) / _CONFIG_FILENAME
    config_ok = config_path.exists()

    # Container mode installs no systemd unit (install() skips it — s6
    # supervises the service, #469), so the absent unit is healthy.
    unit_path = _systemd_target_dir(mode, deps) / _UNIT_FILENAME
    systemd_ok = mode == Mode.container or unit_path.exists()

    overall_ok = user_ok and all(d.present and d.mode_correct for d in dirs_ok) and config_ok and systemd_ok

    return VerifyReport(
        mode=mode.value,
        user_ok=user_ok,
        dirs_ok=dirs_ok,
        config_ok=config_ok,
        systemd_ok=systemd_ok,
        ok=overall_ok,
    )


def _resolve_user_and_uid_gid(mode: Mode, deps: InstallerDeps) -> tuple[SystemUserResult | None, int, int]:
    """Pick uid / gid from the kairix system user (system) or the caller (user)."""
    if mode == Mode.system:
        user_result = deps.user_creator()
        return user_result, user_result.uid, user_result.gid
    if mode == Mode.user:
        return None, os.getuid(), os.getgid()
    # Mode.container: the container image's entrypoint already owns the
    # FHS tree; the installer treats container mode like system mode for
    # the dir-layout step but skips the system-user creation. Caller
    # typically wouldn't invoke install() in container mode (the image
    # build does), but we resolve symmetrically.
    return None, os.getuid(), os.getgid()


def _resolve_uid_gid_for_verify(_mode: Mode) -> tuple[int, int]:
    """Pick uid / gid for the verify-only spec walk.

    Verify never invokes ``user_creator`` (which would mutate the
    system) — for every mode we use the live process uid / gid, which
    is enough to materialise the spec list. The spec list is then
    consumed for its ``.path`` and ``.mode_octal`` fields only; the
    uid / gid values themselves are never checked against the
    filesystem in verify, so the process uid is a safe stand-in.
    """
    return os.getuid(), os.getgid()


def _user_result_as_dict(
    user_result: SystemUserResult | None,
) -> dict[str, int | str] | None:
    """Flatten the SystemUserResult into the report's dict shape."""
    if user_result is None:
        return None
    return {
        "action": user_result.action,
        "uid": user_result.uid,
        "gid": user_result.gid,
    }


def _ensure_default_config(mode: Mode, *, deps: InstallerDeps) -> dict[str, str]:
    """Write the default ``kairix.config.yaml`` if it doesn't yet exist.

    Idempotent: a pre-existing file is left untouched and the report
    records ``action="existing"``. This is the right shape for the
    operator overlay — once they've edited the file, re-running
    ``kairix init`` must not stomp their changes.
    """
    target_dir = _config_target_dir(mode, deps)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _CONFIG_FILENAME

    if target.exists():
        return {"path": str(target), "action": "existing"}

    content = _render_config_template(mode)
    target.write_text(content)
    target.chmod(0o644)
    _logger.info("kairix_default_config_written path=%s", target)
    return {"path": str(target), "action": "created"}


def _render_config_template(mode: Mode) -> str:
    """Render ``kairix.config.yaml.j2`` for ``mode``.

    Kept separate from :func:`_ensure_default_config` so the I/O layer
    can be unit-tested independently if a future change needs to
    customise the rendering path (e.g. operator-supplied overlay).
    """
    # Local import keeps the jinja2 Environment construction lazy — the
    # systemd module already pays the same import cost when its
    # template loader fires, so we don't duplicate the env at module
    # level here.
    from jinja2 import Environment, PackageLoader, select_autoescape

    env = Environment(
        loader=PackageLoader("kairix.install", _TEMPLATES_PACKAGE_DIR),
        # Autoescape empty: YAML is plain text, not HTML / XML.
        autoescape=select_autoescape([]),
    )
    tpl = env.get_template(_CONFIG_TEMPLATE_NAME)
    return tpl.render(mode=mode.value)


def _install_systemd(mode: Mode, *, deps: InstallerDeps) -> dict[str, str]:
    """Render + install the systemd unit using the injected callables."""
    kairix_bin = deps.kairix_bin or shutil.which("kairix") or _DEFAULT_KAIRIX_BIN
    cfg = _config_target_dir(mode, deps) / _CONFIG_FILENAME
    content = deps.unit_renderer(mode, kairix_bin=kairix_bin, config_path=cfg)
    systemd_deps = SystemdDeps(target_dir=deps.systemd_target_dir) if deps.systemd_target_dir else None
    return deps.unit_installer(mode, content=content, deps=systemd_deps)


def _config_target_dir(mode: Mode, deps: InstallerDeps) -> Path:
    """Resolve where the default config file lives for ``mode``."""
    return deps.config_target_dir or config_dir(mode)


def _systemd_target_dir(mode: Mode, deps: InstallerDeps) -> Path:
    """Resolve where the systemd unit lives for ``mode``.

    Mirrors :func:`kairix.install.systemd._default_target_dir`'s
    behaviour so verify checks against the same path install writes to.
    User mode honours ``XDG_CONFIG_HOME`` per the XDG base-dir spec —
    must match the install-side resolver or verify can't find units
    that install just wrote.
    """
    if deps.systemd_target_dir is not None:
        return deps.systemd_target_dir
    if mode == Mode.system:
        return Path("/etc/systemd/system")
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "systemd" / "user"


def _verify_user(mode: Mode) -> bool:
    """Verify the kairix system user exists (system mode) or pass through.

    Verify never *creates* the user — it only reads ``pwd.getpwnam``
    to confirm presence. User mode has no system-user concept so the
    check is a vacuous True.

    Container mode is treated like system mode — the image build
    owns user creation, so the runtime verifier still confirms the
    user exists.
    """
    if mode == Mode.user:
        return True
    # System / container — confirm the kairix user is resolvable via pwd.
    # Importing locally so module import doesn't bind ``pwd`` (the F1
    # detector doesn't fire on stdlib, but local scoping keeps the
    # cross-module surface tight).
    import pwd

    try:
        pwd.getpwnam("kairix")
    except KeyError:
        return False
    return True


@dataclass(frozen=True)
class UninstallReport:
    """Outcome of :func:`uninstall`.

    Fields:
      * ``mode`` — the resolved mode value the uninstall ran in.
      * ``removed`` — list of absolute paths the uninstaller deleted on
        this run. Pre-existing absent paths are NOT recorded (idempotent
        — a re-run reports an empty list, not an error).
      * ``kept`` — list of absolute paths the uninstaller deliberately
        left in place (e.g. data dir when ``keep_data=True``).
      * ``keep_data`` — pass-through of the operator's
        ``--keep-data`` choice, so the JSON envelope records intent.
    """

    mode: str
    removed: list[str]
    kept: list[str]
    keep_data: bool


def uninstall(
    *,
    mode: Mode,
    keep_data: bool = True,
    deps: InstallerDeps | None = None,
) -> UninstallReport:
    """Remove the kairix install layout. Idempotent; non-destructive by default.

    Removes (in order):

    1. The systemd unit file at the per-mode location.
    2. The default ``kairix.config.yaml`` file (config dir itself stays
       in case the operator stored other files there).
    3. The cache dir (always — caches are regen-able).
    4. The data dir — only when ``keep_data=False``. Default is to KEEP
       data so operator state survives the uninstall.

    Never removes the system user / group: that's a destructive op an
    operator does deliberately via ``userdel kairix`` once they're sure
    no other state depends on it.

    Args:
      mode: the install mode whose layout to remove.
      keep_data: when True (default), leave ``data_dir(mode)`` intact;
        when False, remove it as well. Cache dir is always removed.
      deps: same shape as :func:`install` so test paths can override
        ``systemd_target_dir`` and ``config_target_dir``.

    Returns an :class:`UninstallReport` describing what was removed +
    what was kept on this run.
    """
    deps = deps or InstallerDeps()
    removed: list[str] = []
    kept: list[str] = []

    unit_path = _systemd_target_dir(mode, deps) / _UNIT_FILENAME
    if unit_path.exists():
        unit_path.unlink()
        removed.append(str(unit_path))

    config_path = _config_target_dir(mode, deps) / _CONFIG_FILENAME
    if config_path.exists():
        config_path.unlink()
        removed.append(str(config_path))

    from kairix.paths import cache_dir, data_dir

    cache = cache_dir(mode)
    if cache.exists():
        shutil.rmtree(cache)
        removed.append(str(cache))

    data = data_dir(mode)
    if keep_data:
        if data.exists():
            kept.append(str(data))
    elif data.exists():
        shutil.rmtree(data)
        removed.append(str(data))

    return UninstallReport(
        mode=mode.value,
        removed=removed,
        kept=kept,
        keep_data=keep_data,
    )
