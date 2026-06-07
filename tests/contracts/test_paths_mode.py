"""Contract tests for the Plan 1 ``Mode`` enum + per-mode resolvers.

Pins the install-mode boundary that the kairix self-installer (``kairix
init``) consumes:

- ``Mode`` enum has exactly three members (``system`` / ``user`` /
  ``container``) with stable string values matching the resolver
  dispatch table.
- ``Mode.detect()`` resolution order is ``KAIRIX_CONTAINER`` → ``geteuid
  == 0`` → user. ``KAIRIX_CONTAINER`` is the only Dockerfile-controlled
  signal; ``geteuid`` is the only host-state read.
- Per-mode resolvers (``config_dir``, ``data_dir``) return the FHS/XDG
  paths declared in the Plan 1 path-resolution table — independent of
  any env override (mode-arg form short-circuits env overrides so the
  contract holds under any test environment).

Sabotage proofs for each test are documented in the commit body.

``Mode.detect`` accepts an explicit ``env: Mapping[str, str]`` kwarg as
its F2-clean test seam (mirrors the ``env=`` / ``environ=`` kwarg
pattern used by :func:`kairix.paths.is_docker_env`, :func:`mcp_endpoint`,
:func:`log_queries_enabled`). The tests below pass an explicit dict
instead of mutating ``os.environ`` so they never trip the F2 gate.

``monkeypatch.setenv("XDG_*", ...)`` is F2-clean (the AST detector only
flags ``KAIRIX_*`` first-arg literals). ``monkeypatch.setattr(
"os.geteuid", ...)`` is F1-clean (``os.geteuid`` is the POSIX boundary
``Mode.detect`` reads, not a kairix internal).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.contract
def test_mode_enum_has_three_members() -> None:
    """Mode enum carries exactly the three members the installer dispatches on.

    The string values double as on-disk markers (e.g. systemd unit
    ``mode=system`` / ``mode=user``), so the values are part of the
    contract — not just the names.
    """
    from kairix.paths import Mode

    assert Mode.system.value == "system"
    assert Mode.user.value == "user"
    assert Mode.container.value == "container"
    # And only these three. A fourth would break the dispatch tables in
    # the installer + this resolver suite.
    assert {m.value for m in Mode} == {"system", "user", "container"}


@pytest.mark.contract
def test_mode_detect_returns_container_when_kairix_container_set() -> None:
    """``KAIRIX_CONTAINER=1`` (set by the Dockerfile) → container mode.

    Driven through the explicit ``env=`` kwarg seam — production
    callers leave it ``None`` and the live ``os.environ`` is read at
    the F4 boundary inside ``paths.py``.
    """
    from kairix.paths import Mode

    assert Mode.detect(env={"KAIRIX_CONTAINER": "1"}) == Mode.container


@pytest.mark.contract
def test_mode_detect_returns_system_when_running_as_root_no_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root + no container signal → system mode.

    ``env={}`` carries no ``KAIRIX_CONTAINER`` so the second rule
    (``geteuid == 0``) decides.
    """
    monkeypatch.setattr("os.geteuid", lambda: 0)
    from kairix.paths import Mode

    assert Mode.detect(env={}) == Mode.system


@pytest.mark.contract
def test_mode_detect_returns_user_when_non_root_no_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """non-root + no container signal → user mode."""
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    from kairix.paths import Mode

    assert Mode.detect(env={}) == Mode.user


@pytest.mark.contract
def test_config_dir_resolves_per_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_dir(Mode)`` returns the FHS/XDG path per mode.

    System + container → ``/etc/kairix`` (root-owned).
    User → ``$XDG_CONFIG_HOME/kairix`` with ``~/.config/kairix`` fallback.
    """
    from kairix.paths import Mode, config_dir

    assert config_dir(Mode.system) == Path("/etc/kairix")
    assert config_dir(Mode.container) == Path("/etc/kairix")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-config")
    assert config_dir(Mode.user) == Path("/tmp/xdg-config/kairix")


@pytest.mark.contract
def test_data_dir_resolves_per_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """``data_dir(Mode)`` returns the FHS/XDG path per mode.

    System + container → ``/var/lib/kairix``.
    User → ``$XDG_DATA_HOME/kairix`` with ``~/.local/share/kairix`` fallback.
    """
    from kairix.paths import Mode, data_dir

    assert data_dir(Mode.system) == Path("/var/lib/kairix")
    assert data_dir(Mode.container) == Path("/var/lib/kairix")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
    assert data_dir(Mode.user) == Path("/tmp/xdg-data/kairix")


@pytest.mark.contract
def test_cache_dir_resolves_per_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cache_dir(Mode)`` returns the FHS/XDG path per mode."""
    from kairix.paths import Mode, cache_dir

    assert cache_dir(Mode.system) == Path("/var/cache/kairix")
    assert cache_dir(Mode.container) == Path("/var/cache/kairix")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-cache")
    assert cache_dir(Mode.user) == Path("/tmp/xdg-cache/kairix")


@pytest.mark.contract
def test_runtime_secrets_dir_resolves_per_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``runtime_secrets_dir(Mode)`` returns the FHS/XDG path per mode.

    System + container → ``/run/secrets/kairix``.
    User → ``$XDG_RUNTIME_DIR/kairix/secrets`` with ``/tmp/kairix/secrets``
    fallback.
    """
    from kairix.paths import Mode, runtime_secrets_dir

    assert runtime_secrets_dir(Mode.system) == Path("/run/secrets/kairix")
    assert runtime_secrets_dir(Mode.container) == Path("/run/secrets/kairix")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/xdg-runtime")
    assert runtime_secrets_dir(Mode.user) == Path("/tmp/xdg-runtime/kairix/secrets")
