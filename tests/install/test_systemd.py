"""Unit tests for :mod:`kairix.install.systemd` (Plan 1 task 6).

Discipline:

* All tests carry ``@pytest.mark.unit`` (F8).
* No ``@patch`` / ``monkeypatch.setattr`` on ``kairix.*`` internals
  (F1) — the subprocess seam and the target dir are both injected via
  :class:`SystemdDeps`, matching the F6-clean shape used by
  :mod:`kairix.install.system_user`.
* Every test below has a sabotage-proof noted in the docstring
  describing the production mutation that flips it red. Each sabotage
  proof was executed by mutating production, confirming the test goes
  red, then restoring production.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from kairix.install.systemd import SystemdDeps, install_unit, render_unit
from kairix.paths import Mode


def _fake_runner_factory() -> tuple[list[tuple[list[str], dict[str, Any]]], Any]:
    """Return a ``(calls, runner)`` pair. The runner records argv + kwargs."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    return calls, fake_runner


@pytest.mark.unit
def test_render_unit_system_mode_includes_user_kairix() -> None:
    """System mode renders ``User=kairix`` so systemd runs the unit as that user.

    Sabotage-proof: change the production conditional from
    ``"User=kairix" if mode == Mode.system else ""`` to ``""`` and the
    ``"User=kairix" in content`` assertion flips red because the
    rendered template no longer emits the directive on the system
    branch.
    """
    content = render_unit(
        Mode.system,
        kairix_bin="/usr/local/bin/kairix",
        config_path=Path("/etc/kairix/kairix.config.yaml"),
    )

    assert "User=kairix" in content
    assert "mode=system" in content
    # System mode targets multi-user.target so the unit comes up at boot.
    assert "WantedBy=multi-user.target" in content


@pytest.mark.unit
def test_render_unit_user_mode_no_user_directive(tmp_path: Path) -> None:
    """User mode renders no ``User=`` directive — systemd inherits the invoking user.

    Sabotage-proof: invert the production conditional to
    ``"User=kairix" if mode == Mode.user else ""`` and the
    ``"User=" not in content`` assertion flips red because the rendered
    template now emits the directive on the user branch.
    """
    content = render_unit(
        Mode.user,
        kairix_bin=str(tmp_path / ".local" / "bin" / "kairix"),
        config_path=tmp_path / ".config" / "kairix" / "kairix.config.yaml",
    )

    # The literal "User=" must not appear anywhere in user-mode output.
    # If a future template change adds e.g. a comment containing "User=",
    # that's a semantic regression and this test should be updated
    # deliberately — not silently relaxed.
    assert "User=" not in content
    assert "mode=user" in content
    # User mode targets default.target so the unit comes up at user login.
    assert "WantedBy=default.target" in content


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode,kairix_bin_rel",
    [
        (Mode.system, "usr/local/bin/kairix"),
        (Mode.user, ".local/bin/kairix"),
        (Mode.system, "opt/some-tools/bin/kairix"),
    ],
)
def test_render_unit_includes_kairix_bin_path(mode: Mode, kairix_bin_rel: str, tmp_path: Path) -> None:
    """``ExecStart=`` substitutes the literal ``kairix_bin`` argument.

    Sabotage-proof: change the template's ``ExecStart={{ kairix_bin }}``
    to a hardcoded ``ExecStart=/usr/local/bin/kairix`` and the
    parametrised cases with other paths flip red because the substring
    no longer appears in the rendered output.
    """
    # tmp_path roots every parametrised case so we never hardcode a
    # ``/home/...`` literal (F31). The rendered output is then asserted
    # against the same dynamic path the caller passed in.
    kairix_bin = str(tmp_path / kairix_bin_rel)
    content = render_unit(
        mode,
        kairix_bin=kairix_bin,
        config_path=tmp_path / "kairix.config.yaml",
    )

    assert f"ExecStart={kairix_bin} mcp serve --transport http" in content


@pytest.mark.unit
def test_install_unit_system_writes_to_injected_target_dir(tmp_path: Path) -> None:
    """System install writes ``kairix.service`` under the injected target_dir.

    The production default would write to ``/etc/systemd/system/``;
    tests inject ``target_dir=tmp_path`` so the assertion runs against
    a real file without touching ``/etc/``.

    Sabotage-proof: change the production
    ``target_dir = deps.target_dir or _default_target_dir(mode)`` to
    ``target_dir = _default_target_dir(mode)`` (ignoring the override)
    and this test flips red because the file lands under
    ``/etc/systemd/system/`` instead of ``tmp_path``.
    """
    _calls, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner, target_dir=tmp_path)
    content = "[Unit]\nDescription=test\n"

    result = install_unit(Mode.system, content=content, deps=deps)

    target = tmp_path / "kairix.service"
    assert target.exists(), f"Expected unit file at {target}, dir contents: {list(tmp_path.iterdir())}"
    assert target.read_text() == content
    # Mode bits — 0o644 — F50/security baseline.
    assert target.stat().st_mode & 0o777 == 0o644
    assert result == {"path": str(target), "mode": "system", "systemctl_enabled": "true"}


@pytest.mark.unit
def test_install_unit_user_writes_to_injected_target_dir(tmp_path: Path) -> None:
    """User install writes ``kairix.service`` under the injected target_dir.

    Production default would write to
    ``~/.config/systemd/user/kairix.service``; the test injects
    ``target_dir=tmp_path`` so we stay off of ``$HOME``.

    Sabotage-proof: same as the system variant — drop the
    ``deps.target_dir or`` branch and this test flips red.
    """
    _calls, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner, target_dir=tmp_path)
    content = "[Unit]\nDescription=test-user\n"

    result = install_unit(Mode.user, content=content, deps=deps)

    target = tmp_path / "kairix.service"
    assert target.exists()
    assert target.read_text() == content
    assert result == {"path": str(target), "mode": "user", "systemctl_enabled": "true"}


@pytest.mark.unit
def test_install_unit_invokes_daemon_reload_and_enable_system(tmp_path: Path) -> None:
    """System install runs ``systemctl daemon-reload`` then ``systemctl enable``.

    Sabotage-proof: swap the order of the two ``_run`` calls in
    ``install_unit`` (enable before daemon-reload) and the
    ``calls[0][0]`` argv check on ``"daemon-reload"`` flips red. Drop
    one of the two calls and the ``len(calls) == 2`` assertion flips
    red. Strip the ``--user`` arm from ``_systemctl_argv_for(Mode.user)``
    by always returning ``["systemctl"]`` — the user-mode test in the
    next case catches that mutation.
    """
    calls, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner, target_dir=tmp_path)

    install_unit(Mode.system, content="x", deps=deps)

    assert len(calls) == 2, f"Expected exactly two systemctl invocations, got {calls!r}"
    first_argv, first_kwargs = calls[0]
    second_argv, second_kwargs = calls[1]

    assert first_argv == ["systemctl", "daemon-reload"]
    assert second_argv == ["systemctl", "enable", "kairix.service"]
    # check=True + capture_output=True so failures bubble up with stderr.
    for kwargs in (first_kwargs, second_kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True


@pytest.mark.unit
def test_install_unit_invokes_daemon_reload_and_enable_user(tmp_path: Path) -> None:
    """User install prefixes systemctl with ``--user`` for both invocations.

    Sabotage-proof: change ``_systemctl_argv_for`` to always return
    ``["systemctl"]`` (drop the ``--user`` branch) and the argv
    assertions here flip red because ``--user`` no longer appears.
    """
    calls, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner, target_dir=tmp_path)

    install_unit(Mode.user, content="x", deps=deps)

    assert len(calls) == 2
    assert calls[0][0] == ["systemctl", "--user", "daemon-reload"]
    assert calls[1][0] == ["systemctl", "--user", "enable", "kairix.service"]


@pytest.mark.unit
def test_install_unit_creates_parent_dirs(tmp_path: Path) -> None:
    """Target dir is created if it doesn't exist (user install on a fresh host).

    Sabotage-proof: change ``target_dir.mkdir(parents=True, exist_ok=True)``
    to ``target_dir.mkdir(parents=False, exist_ok=True)`` and this test
    flips red on the nested ``a/b/c`` path because the intermediate
    dirs don't yet exist.
    """
    _calls, fake_runner = _fake_runner_factory()
    nested = tmp_path / "a" / "b" / "c"
    deps = SystemdDeps(subprocess_runner=fake_runner, target_dir=nested)

    install_unit(Mode.user, content="x", deps=deps)

    assert (nested / "kairix.service").exists()


@pytest.mark.unit
def test_install_unit_user_mode_default_target_honours_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_unit's default target (no deps.target_dir) honours XDG_CONFIG_HOME.

    Load-bearing: kairix systemd unit lands where systemd's user
    manager looks for it. systemd reads ``$XDG_CONFIG_HOME/systemd/user/``
    per spec; install_unit must write to the same path or
    ``systemctl --user enable`` fails to find the unit (caught in CI on
    PR #445 before this fix).

    Sabotage-proof: drop the XDG_CONFIG_HOME read in the default-target
    resolver — the unit lands under HOME instead and this test fails.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    _, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner)

    install_unit(Mode.user, content="[Unit]\n", deps=deps)

    expected = tmp_path / "xdg-config" / "systemd" / "user" / "kairix.service"
    assert expected.exists(), f"unit not written to XDG path; expected {expected}"


@pytest.mark.unit
def test_install_unit_user_mode_falls_back_to_home_when_xdg_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When XDG_CONFIG_HOME is unset, install_unit writes under HOME/.config.

    Per XDG base-dir spec, ``~/.config`` is the documented fallback. Set
    HOME=tmp_path so the test verifies the fallback without touching the
    developer's real home.

    Sabotage-proof: drop the ``if xdg_config else Path.home() / ".config"``
    fallback — XDG_CONFIG_HOME being unset would produce a broken base
    and this test fails.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _, fake_runner = _fake_runner_factory()
    deps = SystemdDeps(subprocess_runner=fake_runner)

    install_unit(Mode.user, content="[Unit]\n", deps=deps)

    expected = tmp_path / ".config" / "systemd" / "user" / "kairix.service"
    assert expected.exists(), f"unit not written to HOME fallback; expected {expected}"
