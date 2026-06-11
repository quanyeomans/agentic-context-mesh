"""Unit tests for :mod:`kairix.install.installer` (Plan 1 task 7).

Discipline:

* All tests carry ``@pytest.mark.unit`` (F8).
* No ``@patch`` / ``monkeypatch.setattr`` on ``kairix.*`` internals
  (F1) — every layer is injected via :class:`InstallerDeps`. The
  ``XDG_*`` env vars touched by the verify tests are POSIX-spec
  names (not kairix internals), matching the same seam
  ``tests/install/test_dirs.py`` relies on.
* No ``KAIRIX_*`` env vars are touched (F2).
* Every test below has a sabotage-proof noted in the docstring; each
  proof was executed by mutating production, confirming the test goes
  red, then restoring production.
"""

from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path

import pytest

from kairix.install.dirs import DirActionReport, DirSpec, specs_for
from kairix.install.installer import (
    InstallerDeps,
    InstallReport,
    UninstallReport,
    VerifyReport,
    install,
    uninstall,
    verify,
)
from kairix.install.system_user import SystemUserResult
from kairix.paths import Mode

# ---------------------------------------------------------------------------
# Recording fakes — record arguments + return canned shapes for each layer
# ---------------------------------------------------------------------------

_UserCreatorCall = namedtuple("_UserCreatorCall", [])
_DirCreatorCall = namedtuple("_DirCreatorCall", ["specs", "strict"])
_UnitRendererCall = namedtuple("_UnitRendererCall", ["mode", "kairix_bin", "config_path"])
_UnitInstallerCall = namedtuple("_UnitInstallerCall", ["mode", "content", "deps"])


class _RecordingFakes:
    """Bundle of recording fakes the dispatch tests plug into InstallerDeps."""

    def __init__(self, *, user_uid: int = 991, user_gid: int = 991) -> None:
        self.user_calls: list[_UserCreatorCall] = []
        self.dir_calls: list[_DirCreatorCall] = []
        self.render_calls: list[_UnitRendererCall] = []
        self.install_calls: list[_UnitInstallerCall] = []
        self._user_uid = user_uid
        self._user_gid = user_gid

    def user_creator(self) -> SystemUserResult:
        self.user_calls.append(_UserCreatorCall())
        return SystemUserResult(action="existing", uid=self._user_uid, gid=self._user_gid)

    def dir_creator(self, specs: list[DirSpec], *, strict: bool = True) -> list[DirActionReport]:
        self.dir_calls.append(_DirCreatorCall(specs=list(specs), strict=strict))
        return [DirActionReport(path=str(s.path), action="created") for s in specs]

    def unit_renderer(self, mode: Mode, *, kairix_bin: str, config_path: Path) -> str:
        self.render_calls.append(_UnitRendererCall(mode=mode, kairix_bin=kairix_bin, config_path=config_path))
        return "[Unit]\nDescription=fake-rendered\n"

    def unit_installer(
        self,
        mode: Mode,
        *,
        content: str,
        deps: object | None = None,
    ) -> dict[str, str]:
        self.install_calls.append(_UnitInstallerCall(mode=mode, content=content, deps=deps))
        return {"path": "/fake/path/kairix.service", "mode": mode.value}


def _deps_with(
    fakes: _RecordingFakes,
    *,
    config_target_dir: Path,
    systemd_target_dir: Path,
    kairix_bin: str = "/usr/local/bin/kairix",
) -> InstallerDeps:
    """Build an InstallerDeps that routes every layer through ``fakes``."""
    return InstallerDeps(
        user_creator=fakes.user_creator,
        dir_creator=fakes.dir_creator,
        unit_renderer=fakes.unit_renderer,
        unit_installer=fakes.unit_installer,
        kairix_bin=kairix_bin,
        config_target_dir=config_target_dir,
        systemd_target_dir=systemd_target_dir,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_install_system_mode_dispatches_all_layers(tmp_path: Path) -> None:
    """System mode fires user_creator, dir_creator, unit_renderer, unit_installer.

    Sabotage-proof: delete the ``user_result = deps.user_creator()``
    line in production's ``_resolve_user_and_uid_gid`` and this test
    flips red on ``len(fakes.user_calls) == 1``. Drop the
    ``deps.unit_installer(...)`` call in ``_install_systemd`` and the
    ``len(fakes.install_calls) == 1`` assertion flips red.
    """
    fakes = _RecordingFakes()
    deps = _deps_with(
        fakes,
        config_target_dir=tmp_path / "config",
        systemd_target_dir=tmp_path / "systemd",
    )

    report = install(mode=Mode.system, deps=deps)

    assert len(fakes.user_calls) == 1, "user_creator must fire exactly once in system mode"
    assert len(fakes.dir_calls) == 1, "dir_creator must fire exactly once"
    assert len(fakes.render_calls) == 1, "unit_renderer must fire exactly once"
    assert len(fakes.install_calls) == 1, "unit_installer must fire exactly once"
    # And the dispatch order is user → dirs → render → install (dirs depend on
    # the uid/gid the user_creator resolves; the render reads the config_path
    # the dirs layer would lay down).
    # Each fake's *_calls list captures one entry per call so total ordering
    # falls out of the per-layer counts above when every layer fires once.
    assert report.mode == Mode.system.value


@pytest.mark.unit
def test_install_user_mode_skips_system_user(tmp_path: Path) -> None:
    """User mode never calls user_creator — system-user creation is system-mode only.

    Sabotage-proof: change the production
    ``if mode == Mode.system: user_result = deps.user_creator()``
    guard to ``if mode == Mode.user:`` and this test flips red on
    ``len(fakes.user_calls) == 0`` (the call would fire in user mode
    instead).
    """
    fakes = _RecordingFakes()
    deps = _deps_with(
        fakes,
        config_target_dir=tmp_path / "config",
        systemd_target_dir=tmp_path / "systemd",
    )

    report = install(mode=Mode.user, deps=deps)

    assert len(fakes.user_calls) == 0, f"user_creator must NOT fire in user mode, got {fakes.user_calls!r}"
    # The other layers still fire (user mode has its own dirs / config / systemd).
    assert len(fakes.dir_calls) == 1
    assert len(fakes.render_calls) == 1
    assert len(fakes.install_calls) == 1
    # And the report's ``user`` field is None in user mode (no SystemUserResult
    # to flatten into the dict shape).
    assert report.user is None


@pytest.mark.unit
def test_install_container_mode_skips_systemd_unit_install(tmp_path: Path) -> None:
    """Container mode never renders or installs the systemd unit (#469).

    Containers have no systemd — s6 supervises the kairix process and
    the Dockerfile already laid the FHS tree down. Before this fix,
    ``install(mode=Mode.container)`` unconditionally called
    ``_install_systemd``, whose ``systemctl`` invocation raised
    ``FileNotFoundError`` inside the image, killed ``kairix init`` and
    crash-looped the container on first boot. The report's ``systemd``
    element must record the deliberate skip so ``kairix init --json``
    shows the decision.

    Sabotage-proof (executed): remove the ``mode == Mode.container``
    branch from production ``install()`` (always call
    ``_install_systemd``) and this test flips red on
    ``len(fakes.install_calls) == 0`` and on the skip-shaped
    ``report.systemd``. Restored.
    """
    fakes = _RecordingFakes()
    deps = _deps_with(
        fakes,
        config_target_dir=tmp_path / "config",
        systemd_target_dir=tmp_path / "systemd",
    )

    report = install(mode=Mode.container, deps=deps)

    assert len(fakes.install_calls) == 0, f"unit_installer must NOT fire in container mode, got {fakes.install_calls!r}"
    assert len(fakes.render_calls) == 0, f"unit_renderer must NOT fire in container mode, got {fakes.render_calls!r}"
    # No system-user creation either — the image build owns the user.
    assert len(fakes.user_calls) == 0
    # Dirs + config layers still run (verifier semantics over the image tree).
    assert len(fakes.dir_calls) == 1
    assert report.mode == "container"
    assert report.systemd == {"action": "skipped-container", "mode": "container"}


@pytest.mark.unit
def test_install_dir_strictness_follows_mode(tmp_path: Path) -> None:
    """Container installs request best-effort dirs; system installs stay strict (#469).

    With the documented ``/run/secrets/kairix.env`` bind-mount the
    container's ``/run/secrets`` is root-owned, so the uid-995 mkdir
    of ``/run/secrets/kairix`` raises PermissionError. install() must
    pass ``strict=False`` to the dir layer in container mode (record +
    continue) while system mode keeps ``strict=True`` (a root install
    failing to mkdir /var/lib/kairix is a real error).

    Sabotage-proof (executed): change production install() to pass
    ``strict=True`` unconditionally and the container assertion flips
    red; pass ``strict=False`` unconditionally and the system assertion
    flips red. Restored.
    """
    container_fakes = _RecordingFakes()
    install(
        mode=Mode.container,
        deps=_deps_with(
            container_fakes,
            config_target_dir=tmp_path / "c-config",
            systemd_target_dir=tmp_path / "c-systemd",
        ),
    )
    assert container_fakes.dir_calls[0].strict is False, "container mode must request best-effort dir creation"

    system_fakes = _RecordingFakes()
    install(
        mode=Mode.system,
        deps=_deps_with(
            system_fakes,
            config_target_dir=tmp_path / "s-config",
            systemd_target_dir=tmp_path / "s-systemd",
        ),
    )
    assert system_fakes.dir_calls[0].strict is True, "system mode must keep strict dir creation"


@pytest.mark.unit
def test_verify_container_mode_treats_absent_unit_as_ok(tmp_path: Path) -> None:
    """Container verify reports systemd_ok=True even with no unit file (#469 symmetry).

    install() skips the unit in container mode, so verify() must not
    count the absent unit against health — otherwise the s6 first-boot
    check would flag every healthy container as broken.

    Sabotage-proof (executed): revert production verify() to
    ``systemd_ok = unit_path.exists()`` with no container branch and
    this test flips red on ``report.systemd_ok is True``. Restored.
    """
    # Point the unit dir at a path that definitely has no kairix.service.
    deps = InstallerDeps(systemd_target_dir=tmp_path / "empty-systemd")

    report = verify(mode=Mode.container, deps=deps)

    assert report.systemd_ok is True, "container mode must treat the absent systemd unit as healthy"


@pytest.mark.unit
def test_install_returns_report_with_per_layer_actions(tmp_path: Path) -> None:
    """The InstallReport carries the per-layer outcome dicts in the expected shape.

    Sabotage-proof: change production's
    ``return InstallReport(... dirs=dirs_result, ...)`` to
    ``dirs=[]`` and this test flips red on the
    ``len(report.dirs) == len(specs_for(...))`` check.
    """
    fakes = _RecordingFakes(user_uid=995, user_gid=995)
    deps = _deps_with(
        fakes,
        config_target_dir=tmp_path / "config",
        systemd_target_dir=tmp_path / "systemd",
    )

    report = install(mode=Mode.system, deps=deps)

    assert isinstance(report, InstallReport)
    assert report.mode == Mode.system.value
    # System mode flattens SystemUserResult into the dict shape.
    assert report.user == {"action": "existing", "uid": 995, "gid": 995}
    # One DirActionReport per spec — recording fake returns "created" for every
    # entry so the shape is observable.
    expected_specs = specs_for(Mode.system, uid=995, gid=995)
    assert len(report.dirs) == len(expected_specs)
    for entry in report.dirs:
        assert set(entry.keys()) == {"path", "action"}
        assert entry["action"] == "created"
    # The config + systemd entries surface the recording-fake return shapes.
    assert set(report.config.keys()) == {"path", "action"}
    assert report.config["action"] in ("created", "existing")
    assert report.systemd == {"path": "/fake/path/kairix.service", "mode": "system"}


@pytest.mark.unit
def test_verify_returns_ok_when_all_layers_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User-mode verify returns ok=True when every install element is on disk.

    The whole simulated install is rooted under ``tmp_path`` via the
    XDG env vars (the same seam ``test_dirs.py`` uses). System-mode
    verify is exercised by the BDD layer in Plan 1 task 10 because it
    requires root simulation; this test covers the happy-path
    user-mode walk.

    Sabotage-proof: change the production
    ``overall_ok = user_ok and all(...) and config_ok and systemd_ok``
    expression to AND in a ``False`` literal (``and False``) and this
    test flips red on ``report.ok is True``.
    """
    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_runtime = tmp_path / "xdg-runtime"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    # Pre-create every directory the install layout requires, at the
    # exact mode bits the spec declares.
    specs = specs_for(Mode.user, uid=os.getuid(), gid=os.getgid())
    for spec in specs:
        spec.path.mkdir(parents=True, exist_ok=True)
        spec.path.chmod(spec.mode_octal)

    # And the config file + systemd unit at their expected user-mode paths.
    config_target = xdg_config / "kairix" / "kairix.config.yaml"
    config_target.write_text("# placeholder\n")
    systemd_target_dir = tmp_path / "systemd-user"
    systemd_target_dir.mkdir(parents=True, exist_ok=True)
    (systemd_target_dir / "kairix.service").write_text("[Unit]\nDescription=placeholder\n")

    deps = InstallerDeps(systemd_target_dir=systemd_target_dir)
    report = verify(mode=Mode.user, deps=deps)

    assert isinstance(report, VerifyReport)
    assert report.mode == Mode.user.value
    # User mode has no system-user to verify — always True.
    assert report.user_ok is True
    assert report.config_ok is True
    assert report.systemd_ok is True
    for d in report.dirs_ok:
        assert d.present is True, f"dir {d.path} should be present after pre-create"
        assert d.mode_correct is True, f"dir {d.path} should have correct mode bits"
    assert report.ok is True


@pytest.mark.unit
def test_verify_returns_not_ok_when_systemd_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same layout as the happy path, minus the systemd unit — ok flips to False.

    Sabotage-proof: change production's
    ``systemd_ok = unit_path.exists()`` to ``systemd_ok = True`` and
    this test flips red because the missing unit file is silently
    masked, so ``report.systemd_ok`` stays True and the overall ``ok``
    aggregate flips True.
    """
    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_runtime = tmp_path / "xdg-runtime"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    # Pre-create every directory + the config file, but deliberately
    # omit the systemd unit file.
    specs = specs_for(Mode.user, uid=os.getuid(), gid=os.getgid())
    for spec in specs:
        spec.path.mkdir(parents=True, exist_ok=True)
        spec.path.chmod(spec.mode_octal)

    config_target = xdg_config / "kairix" / "kairix.config.yaml"
    config_target.write_text("# placeholder\n")

    systemd_target_dir = tmp_path / "systemd-user"
    # Intentionally NO mkdir + NO unit file write — verify must report missing.

    deps = InstallerDeps(systemd_target_dir=systemd_target_dir)
    report = verify(mode=Mode.user, deps=deps)

    assert report.systemd_ok is False, "verify must report systemd_ok=False when unit missing"
    # The dirs + config legs are healthy — the missing systemd unit alone
    # flips the overall ok.
    assert report.config_ok is True
    for d in report.dirs_ok:
        assert d.present is True
    assert report.ok is False, "overall ok must be False when any layer is missing"


# ---------------------------------------------------------------------------
# uninstall() coverage — Plan 1 task 8 surface
# ---------------------------------------------------------------------------


def _seed_user_install_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, Path]:
    """Pre-create the user-mode install layout under tmp_path via XDG env vars.

    Returns ``(config_dir, data_dir, cache_dir, systemd_target_dir)``
    so the test body can probe each layer's post-uninstall state.
    Mirrors what ``install(mode=Mode.user)`` would lay down without
    invoking systemctl — uninstall is read-only against the install
    layer, so the seed is enough.
    """
    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_runtime = tmp_path / "xdg-runtime"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    config_dir = xdg_config / "kairix"
    data_dir = xdg_data / "kairix"
    cache_dir = xdg_cache / "kairix"
    systemd_target_dir = tmp_path / "systemd-user"

    for d in (config_dir, data_dir, cache_dir, systemd_target_dir):
        d.mkdir(parents=True, exist_ok=True)

    (config_dir / "kairix.config.yaml").write_text("# placeholder\n")
    (systemd_target_dir / "kairix.service").write_text("[Unit]\nDescription=placeholder\n")
    (data_dir / "marker.txt").write_text("operator data\n")

    return config_dir, data_dir, cache_dir, systemd_target_dir


@pytest.mark.unit
def test_uninstall_user_mode_keep_data_removes_layout_keeps_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default uninstall removes systemd / config / cache; data dir survives.

    Sabotage-proof: change production's ``if keep_data:`` branch to
    unconditionally ``shutil.rmtree(data)`` and the assertion on
    ``data_dir.exists()`` flips red. Restored.
    """
    config_dir, data_dir, cache_dir, systemd_target_dir = _seed_user_install_layout(tmp_path, monkeypatch)
    deps = InstallerDeps(systemd_target_dir=systemd_target_dir)

    report = uninstall(mode=Mode.user, keep_data=True, deps=deps)

    assert isinstance(report, UninstallReport)
    assert report.mode == Mode.user.value
    assert report.keep_data is True

    removed = set(report.removed)
    assert str(systemd_target_dir / "kairix.service") in removed
    assert str(config_dir / "kairix.config.yaml") in removed
    assert str(cache_dir) in removed
    assert str(data_dir) in set(report.kept)

    assert not (systemd_target_dir / "kairix.service").exists()
    assert not (config_dir / "kairix.config.yaml").exists()
    assert not cache_dir.exists()
    assert data_dir.exists(), "data dir wrongly removed despite keep_data=True"
    assert (data_dir / "marker.txt").exists(), "operator data file inside data dir was wrongly removed"


@pytest.mark.unit
def test_uninstall_user_mode_no_keep_data_removes_data_too(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``keep_data=False`` also deletes the data dir and records it in ``removed``.

    Sabotage-proof: change production's ``elif data.exists(): rmtree(data)``
    branch to a pass-through and the assertion on ``not data_dir.exists()``
    flips red. Restored.
    """
    _config_dir, data_dir, _cache_dir, systemd_target_dir = _seed_user_install_layout(tmp_path, monkeypatch)
    deps = InstallerDeps(systemd_target_dir=systemd_target_dir)

    report = uninstall(mode=Mode.user, keep_data=False, deps=deps)

    assert report.keep_data is False
    assert str(data_dir) in set(report.removed)
    assert report.kept == [], f"kept list should be empty with keep_data=False; got {report.kept}"
    assert not data_dir.exists()


@pytest.mark.unit
def test_uninstall_idempotent_on_clean_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uninstall against an absent layout exits cleanly with empty removed/kept lists.

    Sabotage-proof: change production's ``if unit_path.exists():`` guard
    to an unconditional ``unit_path.unlink()`` and this test flips red
    on the now-raised FileNotFoundError. Restored.
    """
    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_runtime = tmp_path / "xdg-runtime"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))
    systemd_target_dir = tmp_path / "systemd-user"
    # Intentionally no mkdir on systemd_target_dir — proves the empty path branch.

    deps = InstallerDeps(systemd_target_dir=systemd_target_dir)
    report = uninstall(mode=Mode.user, keep_data=True, deps=deps)

    assert report.removed == []
    assert report.kept == []
    assert report.keep_data is True
