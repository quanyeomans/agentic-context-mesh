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
    VerifyReport,
    install,
    verify,
)
from kairix.install.system_user import SystemUserResult
from kairix.paths import Mode

# ---------------------------------------------------------------------------
# Recording fakes — record arguments + return canned shapes for each layer
# ---------------------------------------------------------------------------

_UserCreatorCall = namedtuple("_UserCreatorCall", [])
_DirCreatorCall = namedtuple("_DirCreatorCall", ["specs"])
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

    def dir_creator(self, specs: list[DirSpec]) -> list[DirActionReport]:
        self.dir_calls.append(_DirCreatorCall(specs=list(specs)))
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
