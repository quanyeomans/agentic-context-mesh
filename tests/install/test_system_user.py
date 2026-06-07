"""Unit tests for :mod:`kairix.install.system_user` (Plan 1 task 4).

Discipline:

* All tests carry ``@pytest.mark.unit`` (F8).
* No ``@patch`` / ``monkeypatch.setattr`` on ``kairix.*`` internals
  (F1) — only ``pwd.getpwnam`` and ``os.geteuid`` are monkeypatched
  and both are POSIX stdlib boundary calls (the F1 detector exempts
  ``os`` / ``pwd`` roots — only ``kairix.*`` targets fire the gate).
* The subprocess seam is injected via :class:`SystemUserDeps`, not
  monkeypatched — that's the F6-clean shape the production code
  exposes (no ``*_fn=None`` test-only kwarg).
* Every test below has a sabotage-proof noted in the docstring
  describing the production mutation that flips it red.
"""

from __future__ import annotations

import pwd
import subprocess
from collections import namedtuple
from typing import Any

import pytest

from kairix.install.system_user import (
    KAIRIX_GROUP,
    KAIRIX_USER,
    SystemUserDeps,
    SystemUserResult,
    ensure_kairix_system_user,
)

# pwd.struct_passwd is a C extension; we fake it with a namedtuple that
# has the same attribute surface we read (pw_uid).
_FakePw = namedtuple("_FakePw", ["pw_name", "pw_uid", "pw_gid"])
_FakeGr = namedtuple("_FakeGr", ["gr_name", "gr_gid", "gr_mem"])


def _force_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the test process is root for ``ensure_kairix_system_user``."""
    monkeypatch.setattr("os.geteuid", lambda: 0)


@pytest.mark.unit
def test_ensure_returns_existing_when_user_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the kairix user already exists, no subprocess fires; action=existing.

    Sabotage-proof: change the ``action="existing"`` literal in
    :func:`ensure_kairix_system_user` to ``"created"`` and this test
    fails on the ``assert result.action == "existing"`` line. Also,
    removing the ``return`` in the ``try`` block (so creation runs
    anyway) makes ``runner_calls`` non-empty and fails the call-count
    assertion.
    """
    _force_root(monkeypatch)
    monkeypatch.setattr(
        "pwd.getpwnam",
        lambda name: _FakePw(pw_name=name, pw_uid=991, pw_gid=991),
    )
    monkeypatch.setattr(
        "grp.getgrnam",
        lambda name: _FakeGr(gr_name=name, gr_gid=991, gr_mem=[]),
    )

    runner_calls: list[list[str]] = []

    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        runner_calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    result = ensure_kairix_system_user(deps=SystemUserDeps(subprocess_runner=fake_runner))

    assert isinstance(result, SystemUserResult)
    assert result.action == "existing"
    assert result.uid == 991
    assert result.gid == 991
    # No useradd / groupadd should have run on the idempotent path.
    assert runner_calls == [], f"Expected zero subprocess calls when user pre-exists, got {runner_calls!r}"


@pytest.mark.unit
def test_ensure_raises_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-root invocation raises PermissionError with an actionable message.

    Sabotage-proof: delete the ``if os.geteuid() != 0: raise ...`` guard
    and this test fails because the call no longer raises (it falls
    through to ``pwd.getpwnam`` which raises KeyError — different
    exception type). Inverting the guard to ``== 0`` makes it raise
    when we DO have root, which also flips this test red because the
    geteuid we wired returns 1000.
    """
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    # No need to wire pwd/subprocess — we should never reach them.

    with pytest.raises(PermissionError) as excinfo:
        ensure_kairix_system_user()

    msg = str(excinfo.value)
    # Actionable affordance per F21: fix: / run: markers.
    assert "requires root" in msg
    assert "--user" in msg
    assert "sudo kairix init --system" in msg


@pytest.mark.unit
def test_ensure_creates_when_absent_via_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent user → groupadd + useradd fire in the right order; action=created.

    Sabotage-proof: swap the order of the two ``_run`` calls in
    production (useradd before groupadd) and the ``argv_seen[0][0] ==
    "groupadd"`` assertion fails. Drop one of the two calls and the
    ``len(argv_seen) == 2`` assertion fails. Remove the
    ``--no-create-home`` flag from the useradd argv and the
    membership assertion on ``"--no-create-home"`` flips red.
    """
    _force_root(monkeypatch)

    # Trace through the production flow:
    #   try: pwd.getpwnam(KAIRIX_USER)   <-- call #1 raises KeyError
    #        grp.getgrnam(...)            <-- never reached on this branch
    #   except KeyError: pass
    #   ... groupadd + useradd via subprocess ...
    #   pwd.getpwnam(KAIRIX_USER)         <-- call #2 (post-create) -> succeeds
    #   grp.getgrnam(KAIRIX_GROUP)        <-- call #1 (post-create) -> succeeds
    # So pwd is called twice (raise then succeed); grp is called exactly once.
    pwd_call_count = {"n": 0}

    def fake_getpwnam(name: str) -> _FakePw:
        pwd_call_count["n"] += 1
        if pwd_call_count["n"] == 1:
            raise KeyError(name)
        return _FakePw(pw_name=name, pw_uid=992, pw_gid=992)

    def fake_getgrnam(name: str) -> _FakeGr:
        return _FakeGr(gr_name=name, gr_gid=992, gr_mem=[])

    monkeypatch.setattr(pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr("grp.getgrnam", fake_getgrnam)

    argv_seen: list[list[str]] = []
    kwargs_seen: list[dict[str, Any]] = []

    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        argv_seen.append(list(argv))
        kwargs_seen.append(dict(kwargs))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    result = ensure_kairix_system_user(deps=SystemUserDeps(subprocess_runner=fake_runner))

    assert isinstance(result, SystemUserResult)
    assert result.action == "created"
    assert result.uid == 992
    assert result.gid == 992

    # Exactly two subprocess calls: groupadd, then useradd.
    assert len(argv_seen) == 2, f"Expected exactly two subprocess calls (groupadd + useradd), got {argv_seen!r}"
    assert argv_seen[0][0] == "groupadd", f"groupadd must run first (so useradd --gid resolves); got {argv_seen[0]!r}"
    assert "--system" in argv_seen[0]
    assert KAIRIX_GROUP in argv_seen[0]

    assert argv_seen[1][0] == "useradd"
    assert "--system" in argv_seen[1]
    assert "--no-create-home" in argv_seen[1]
    assert "/usr/sbin/nologin" in argv_seen[1]
    assert "/var/lib/kairix" in argv_seen[1]
    assert KAIRIX_USER in argv_seen[1]

    # Both calls use check=True + capture_output=True so failures bubble up
    # with stderr captured.
    for kw in kwargs_seen:
        assert kw.get("check") is True
        assert kw.get("capture_output") is True
