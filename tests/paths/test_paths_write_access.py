"""Unit tests for the write-access probe helpers in ``kairix.paths`` (PLA-259).

:func:`kairix.paths.probe_write_access` is the shared "can kairix actually
write here?" check that the ``doctor`` preflight and the ``remember`` write
path both rely on; :func:`kairix.paths.write_access_fix_hint` renders the
matching F21 ``fix:`` line by errno so both surfaces speak the same
remediation language.

F2-clean: every path is a ``tmp_path``; no env vars. ``:ro``/permission
behaviour is simulated with a ``0o500`` directory and skips gracefully on
hosts (e.g. CI run as root) where mode bits do not block the write — the
``/run/secrets`` lesson in CLAUDE.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kairix.paths import WriteAccessProbe, probe_write_access, write_access_fix_hint

pytestmark = pytest.mark.unit


def test_probe_reports_writable_for_a_normal_dir(tmp_path: Path) -> None:
    """A writable dir probes writable with no errno and leaves no debris.

    Sabotage-proof (executed): made ``probe_write_access`` return
    ``writable=False`` unconditionally → the ``writable is True`` assertion
    failed; restored.
    """
    target = tmp_path / "memory"
    probe = probe_write_access(target)

    assert isinstance(probe, WriteAccessProbe)
    assert probe.writable is True
    assert probe.errno_name == ""
    assert probe.reason == ""
    # The probe file must be cleaned up — no orphan probe artefacts.
    assert list(target.iterdir()) == []


def test_probe_creates_the_dir_when_create_true(tmp_path: Path) -> None:
    """``create=True`` mkdirs the (possibly nested) target before probing.

    Sabotage-proof (executed): dropped the ``target.mkdir`` call in the
    ``create`` branch → the touch raised FileNotFoundError and the
    ``writable`` assertion failed; restored. Also (executed): flipped
    ``parents=True`` to False → the nested mkdir raised and writable went
    False; restored.
    """
    target = tmp_path / "a" / "b" / "memory"
    assert not target.exists()

    probe = probe_write_access(target, create=True)

    assert probe.writable is True
    assert target.is_dir()


def test_probe_create_true_on_existing_dir_stays_writable(tmp_path: Path) -> None:
    """``create=True`` on an ALREADY-existing dir is a no-op mkdir and still
    probes writable — pins ``exist_ok=True`` (PLA-259).

    Sabotage-proof (executed): flipped ``exist_ok=True`` to False in the
    ``mkdir`` call → mkdir on the existing dir raised FileExistsError and
    writable went False; restored.
    """
    target = tmp_path / "already-here"
    target.mkdir()

    probe = probe_write_access(target, create=True)

    assert probe.writable is True
    assert probe.errno_name == ""


def test_probe_missing_dir_with_create_false_reports_enoent(tmp_path: Path) -> None:
    """``create=False`` must NOT create a missing dir — it reports ENOENT.

    The doctor preflight is a non-mutating validator; it must not silently
    materialise directories while checking them.

    Sabotage-proof (executed): made the ``create=False`` branch fall through
    to ``mkdir`` → the dir was created and the ``not exists`` assertion
    failed; restored.
    """
    target = tmp_path / "ghost"

    probe = probe_write_access(target, create=False)

    assert probe.writable is False
    assert probe.errno_name == "ENOENT"
    assert not target.exists()


def test_probe_mkdir_failure_surfaces_the_errno(tmp_path: Path) -> None:
    """When ``mkdir`` fails (parent is a file) the errno is reported, not raised.

    Sabotage-proof (executed): removed the ``except OSError`` around the
    ``mkdir`` → the NotADirectoryError propagated and the test errored;
    restored.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")

    probe = probe_write_access(blocker / "child", create=True)

    assert probe.writable is False
    assert probe.errno_name == "ENOTDIR"  # pins ``exc.errno or 0`` -> errorcode mapping
    # reason is the OS strerror, NOT ``str(exc)`` (which embeds "[Errno N] ...").
    # Pins the ``exc.strerror or str(exc)`` fallback ordering.
    assert probe.reason != ""
    assert not probe.reason.startswith("[Errno")


def test_probe_unwritable_existing_dir_reports_not_writable(tmp_path: Path) -> None:
    """An existing dir with no write permission probes not-writable + errno.

    Sabotage-proof (executed): removed the ``except OSError`` around the
    ``probe_file.touch()`` → the PermissionError propagated and the test
    errored; restored.
    """
    if os.geteuid() == 0:
        pytest.skip("permission denial cannot be simulated as root (touch succeeds despite 0o500)")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)  # r-x: readable + listable, but not writable
    try:
        probe = probe_write_access(locked, create=False)
        if probe.writable:
            pytest.skip("filesystem ignores mode bits (write succeeded despite 0o500)")
        assert probe.writable is False
        assert probe.errno_name in {"EACCES", "EPERM", "EROFS"}
        assert probe.reason != ""
        # No probe file leaked into the (now restored) directory.
    finally:
        locked.chmod(0o700)
    assert list(locked.iterdir()) == []


def test_fix_hint_names_read_only_mount_for_erofs() -> None:
    """The EROFS hint points at the read-only-mount remediation.

    Sabotage-proof (executed): returned the generic hint for the EROFS
    branch → the ``read-only`` substring assertion failed; restored.
    """
    hint = write_access_fix_hint("EROFS")

    assert hint.startswith("fix:")
    assert "read-only" in hint


def test_fix_hint_names_ownership_for_eacces() -> None:
    """The EACCES hint points at the ownership / chmod remediation.

    Sabotage-proof (executed): merged EACCES into the generic branch → the
    ``chown`` substring assertion failed; restored.
    """
    hint = write_access_fix_hint("EACCES")

    assert hint.startswith("fix:")
    assert "chown" in hint or "write access" in hint


def test_fix_hint_falls_back_to_generic_for_other_errno() -> None:
    """An unmapped errno still yields an actionable ``fix:`` line.

    Sabotage-proof (executed): made the fallback return an empty string →
    the ``startswith('fix:')`` assertion failed; restored.
    """
    hint = write_access_fix_hint("ENOTDIR")

    assert hint.startswith("fix:")
    assert "write" in hint
