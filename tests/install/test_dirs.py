"""Unit tests for :mod:`kairix.install.dirs` (Plan 1 task 5).

Discipline:

* All tests carry ``@pytest.mark.unit`` (F8).
* No ``@patch`` / ``monkeypatch.setattr`` on ``kairix.*`` internals
  (F1). XDG env vars are POSIX-spec environment names, not kairix
  internals — ``monkeypatch.setenv("XDG_CONFIG_HOME", ...)`` is the
  documented test seam :mod:`kairix.paths` itself uses.
* No ``KAIRIX_*`` env vars are touched (F2).
* Every test below has a sabotage-proof recorded in the commit body
  (executed mutate → fail → restore, not a comment-only claim).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kairix.install.dirs import DirSpec, ensure_dirs, specs_for
from kairix.paths import Mode


@pytest.mark.unit
def test_specs_for_system_mode_returns_etc_var_paths() -> None:
    """System-mode lays the FHS tree under /etc + /var with kairix ownership."""
    specs = specs_for(Mode.system, uid=991, gid=991)

    paths_by_str = {str(s.path): s for s in specs}
    assert "/etc/kairix" in paths_by_str
    assert "/var/lib/kairix" in paths_by_str
    assert "/var/cache/kairix" in paths_by_str
    assert "/run/secrets/kairix" in paths_by_str

    # /etc/kairix is root-owned admin config (the kairix runtime reads,
    # never writes).
    etc = paths_by_str["/etc/kairix"]
    assert etc.owner_uid == 0
    assert etc.owner_gid == 0
    assert etc.mode_octal == 0o755

    # /var/lib/kairix is the runtime's state dir — owned by the kairix
    # system user.
    var_lib = paths_by_str["/var/lib/kairix"]
    assert var_lib.owner_uid == 991
    assert var_lib.owner_gid == 991
    assert var_lib.mode_octal == 0o755

    # /var/cache/kairix mirrors /var/lib but is regen-able.
    var_cache = paths_by_str["/var/cache/kairix"]
    assert var_cache.owner_uid == 991
    assert var_cache.owner_gid == 991
    assert var_cache.mode_octal == 0o755

    # /run/secrets/kairix is the tmpfs secret store: root writes, the
    # kairix group reads. Mode 0750 enforces the group-only read.
    secrets = paths_by_str["/run/secrets/kairix"]
    assert secrets.owner_uid == 0
    assert secrets.owner_gid == 991
    assert secrets.mode_octal == 0o750


@pytest.mark.unit
def test_specs_for_user_mode_returns_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User-mode lays the XDG tree under the operator's XDG bases."""
    xdg_config = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_runtime = tmp_path / "xdg-runtime"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    uid = 4242
    gid = 4242
    specs = specs_for(Mode.user, uid=uid, gid=gid)

    # Every spec must sit under the XDG bases we set; none may leak
    # into the FHS /etc + /var tree.
    assert len(specs) == 4
    for spec in specs:
        assert tmp_path in spec.path.parents, f"User-mode spec {spec.path!s} escaped the XDG tmp_path tree"
        # XDG user-private mode.
        assert spec.mode_octal == 0o700
        # Every dir owned by the invoking user (no root-owned dirs in
        # the user-mode tree).
        assert spec.owner_uid == uid
        assert spec.owner_gid == gid

    paths_by_str = {str(s.path) for s in specs}
    assert str(xdg_config / "kairix") in paths_by_str
    assert str(xdg_data / "kairix") in paths_by_str
    assert str(xdg_cache / "kairix") in paths_by_str
    assert str(xdg_runtime / "kairix" / "secrets") in paths_by_str


@pytest.mark.unit
def test_ensure_dirs_creates_missing(tmp_path: Path) -> None:
    """Missing dirs are mkdir'd; action recorded as 'created'."""
    target_a = tmp_path / "a" / "kairix-config"
    target_b = tmp_path / "b" / "kairix-data"
    assert not target_a.exists()
    assert not target_b.exists()

    specs = [
        DirSpec(target_a, 0o700, os.getuid(), os.getgid()),
        DirSpec(target_b, 0o700, os.getuid(), os.getgid()),
    ]

    results = ensure_dirs(specs)

    assert target_a.is_dir()
    assert target_b.is_dir()
    actions = {r["path"]: r["action"] for r in results}
    assert actions[str(target_a)] == "created"
    assert actions[str(target_b)] == "created"
    # And the chmod step converged the mode to the spec value, regardless
    # of what umask left mkdir at.
    assert target_a.stat().st_mode & 0o7777 == 0o700
    assert target_b.stat().st_mode & 0o7777 == 0o700


@pytest.mark.unit
def test_ensure_dirs_idempotent(tmp_path: Path) -> None:
    """Running ensure_dirs twice leaves the second pass reporting 'existing'."""
    target = tmp_path / "kairix-cache"
    spec = DirSpec(target, 0o700, os.getuid(), os.getgid())

    first = ensure_dirs([spec])
    second = ensure_dirs([spec])

    assert first[0]["action"] == "created"
    assert second[0]["action"] == "existing"
    # Mode unchanged across runs.
    assert target.stat().st_mode & 0o7777 == 0o700


@pytest.mark.unit
def test_ensure_dirs_best_effort_records_perms_unmanaged_and_continues(tmp_path: Path) -> None:
    """``strict=False`` turns a denied mkdir into action='perms-unmanaged' (#469).

    Container first boot: with the documented ``/run/secrets/kairix.env``
    bind-mount, ``/run/secrets`` is root-owned, so the uid-995 mkdir of
    ``/run/secrets/kairix`` raises PermissionError and killed
    ``kairix init`` → s6 crash-loop. In best-effort mode the failure is
    recorded in the report (the operator still sees the path) and the
    walk continues to the remaining specs.

    Simulated with a 0o555 parent dir under tmp_path — same EACCES
    shape as the root-owned ``/run/secrets`` mount.

    Sabotage-proof (executed): revert production ``ensure_dirs`` to the
    pre-#469 body (no strict seam, bare mkdir/chmod) and this test
    errors with the escaped PermissionError (or TypeError on the
    missing ``strict`` kwarg). Restored.
    """
    if os.geteuid() == 0:
        pytest.skip(
            "permission denial cannot be simulated as root (mkdir succeeds despite 0o555); "
            "fix: run the suite as an unprivileged user to exercise the best-effort branch."
        )

    locked_parent = tmp_path / "locked"
    locked_parent.mkdir()
    locked_parent.chmod(0o555)
    denied = locked_parent / "kairix"
    later_target = tmp_path / "writable" / "kairix"

    specs = [
        DirSpec(denied, 0o750, os.getuid(), os.getgid()),
        DirSpec(later_target, 0o700, os.getuid(), os.getgid()),
    ]
    try:
        results = ensure_dirs(specs, strict=False)
    finally:
        # Restore write perms so pytest's tmp_path cleanup never trips.
        locked_parent.chmod(0o755)

    actions = {r["path"]: r["action"] for r in results}
    # The denied path is surfaced in the report — operators can see it.
    assert actions[str(denied)] == "perms-unmanaged"
    assert not denied.exists()
    # The walk continued past the denied entry: later specs still land.
    assert actions[str(later_target)] == "created"
    assert later_target.is_dir()


@pytest.mark.unit
def test_ensure_dirs_strict_default_raises_on_permission_error(tmp_path: Path) -> None:
    """Default (strict) behaviour still raises — system/user installs must fail loudly.

    The #469 best-effort tolerance is container-only; a system-mode
    install that cannot mkdir /var/lib/kairix is a real error the
    operator must see as a nonzero exit, not a soft report entry.

    Sabotage-proof (executed): change production ``_ensure_one_dir`` to
    swallow PermissionError regardless of ``strict`` and this test
    flips red on the missing raise. Restored.
    """
    if os.geteuid() == 0:
        pytest.skip(
            "permission denial cannot be simulated as root (mkdir succeeds despite 0o555); "
            "fix: run the suite as an unprivileged user to exercise the strict branch."
        )

    locked_parent = tmp_path / "locked-strict"
    locked_parent.mkdir()
    locked_parent.chmod(0o555)
    denied = locked_parent / "kairix"

    try:
        with pytest.raises(PermissionError):
            ensure_dirs([DirSpec(denied, 0o750, os.getuid(), os.getgid())])
    finally:
        locked_parent.chmod(0o755)


@pytest.mark.unit
def test_ensure_dirs_adjusts_wrong_mode(tmp_path: Path) -> None:
    """A pre-existing dir whose mode drifted gets chmod'd; action='mode-adjusted'.

    Distinct from 'created' (path absent → mkdir) and 'existing'
    (path present, mode matches). 'mode-adjusted' is the operator's
    signal that they manually chmod'd the dir between installer runs
    and the installer corrected the drift.
    """
    target = tmp_path / "kairix-drifted"
    target.mkdir()
    target.chmod(0o755)
    assert target.stat().st_mode & 0o7777 == 0o755

    spec = DirSpec(target, 0o700, os.getuid(), os.getgid())
    results = ensure_dirs([spec])

    assert results[0]["action"] == "mode-adjusted"
    assert target.stat().st_mode & 0o7777 == 0o700
