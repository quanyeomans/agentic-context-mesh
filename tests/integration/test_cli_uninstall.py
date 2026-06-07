"""F30 outcome tests — ``kairix uninstall`` subprocess CLI surface (Plan 1 task 8).

Asserts the subprocess binary surface end-to-end:

  * ``kairix uninstall --user --json`` against an existing layout
    removes the systemd unit + config + cache, KEEPS the data dir
    (default ``--keep-data``), and emits a JSON envelope on stdout.
  * ``kairix uninstall --user --no-keep-data --json`` also removes
    the data dir and records it in ``removed``.
  * ``kairix uninstall --user --json`` against a clean tree exits 0
    and reports empty ``removed`` / ``kept`` lists (idempotent).
  * ``kairix uninstall --system`` from a non-root shell exits 1 with
    an actionable affordance.

F2-clean: only POSIX-spec XDG env vars + ``HOME`` are set on the
subprocess; no ``KAIRIX_*`` env manipulation, no ``monkeypatch``.

Pre-seeding the layout manually (rather than invoking ``kairix init``
first) keeps the uninstall tests independent of the systemd user-bus
availability that gates the install tests.

Sabotage-proofs (executed):
  * Mutated ``installer.uninstall`` to skip the ``shutil.rmtree(cache)``
    call → ``test_uninstall_user_mode_removes_layout_keeps_data``
    assertion on ``not cache_dir.exists()`` flips red. Restored.
  * Mutated ``uninstall_cli._resolve_mode`` to return ``Mode.user``
    from the ``--system`` branch when not root → the
    ``test_uninstall_system_mode_refuses_when_not_root`` assertion on
    ``returncode == 1`` flips red. Restored.
  * Mutated ``installer.uninstall`` to delete the data dir even when
    ``keep_data=True`` → ``test_uninstall_user_mode_removes_layout_keeps_data``
    assertion on ``data_dir.exists()`` flips red. Restored.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    """Build an env that redirects every per-mode resolver into ``tmp_path``.

    Same shape as the init-test helper — kept inline to avoid a
    cross-test import dependency.
    """
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["XDG_RUNTIME_DIR"] = str(tmp_path / "runtime")
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


def _seed_user_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Pre-create the four per-mode user dirs + config file + systemd unit.

    Mirrors what ``kairix init --user`` would lay down, but does it
    directly so the uninstall test does not depend on the systemd
    user-bus availability that gates the install path.

    Returns ``(config_dir, data_dir, cache_dir, systemd_unit_path)``
    so the assertions can probe each layer's post-uninstall state.
    """
    config_dir = tmp_path / "config" / "kairix"
    data_dir = tmp_path / "data" / "kairix"
    cache_dir = tmp_path / "cache" / "kairix"
    runtime_dir = tmp_path / "runtime" / "kairix" / "secrets"
    systemd_dir = tmp_path / ".config" / "systemd" / "user"

    for d in (config_dir, data_dir, cache_dir, runtime_dir, systemd_dir):
        d.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "kairix.config.yaml"
    config_file.write_text("# placeholder\n")
    systemd_unit = systemd_dir / "kairix.service"
    systemd_unit.write_text("[Unit]\nDescription=placeholder\n")

    # Seed a marker file inside the data dir so the keep-data assertion
    # has something concrete to confirm post-uninstall.
    (data_dir / "marker.txt").write_text("operator data\n")

    return config_dir, data_dir, cache_dir, systemd_unit


def test_uninstall_user_mode_removes_layout_keeps_data(tmp_path: Path) -> None:
    """Default uninstall removes systemd / config / cache; KEEPS the data dir."""
    config_dir, data_dir, cache_dir, systemd_unit = _seed_user_layout(tmp_path)
    env = _subprocess_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "uninstall", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    out = json.loads(result.stdout)
    assert out["mode"] == "user"
    assert out["keep_data"] is True

    # Removed surfaces: systemd unit, config file, cache dir.
    removed = set(out["removed"])
    assert str(systemd_unit) in removed, f"systemd unit not in removed list: {removed}"
    assert str(config_dir / "kairix.config.yaml") in removed, f"config file not in removed: {removed}"
    assert str(cache_dir) in removed, f"cache dir not in removed: {removed}"

    # Kept surfaces: data dir.
    assert str(data_dir) in set(out["kept"]), f"data dir not in kept list: {out['kept']}"

    # And the actual filesystem reflects the envelope.
    assert not systemd_unit.exists(), "systemd unit still on disk after uninstall"
    assert not (config_dir / "kairix.config.yaml").exists(), "config file still on disk"
    assert not cache_dir.exists(), "cache dir still on disk"
    assert data_dir.exists(), "data dir wrongly removed despite default --keep-data"
    assert (data_dir / "marker.txt").exists(), "operator data file inside data dir was wrongly removed"


def test_uninstall_user_mode_no_keep_data_removes_data_too(tmp_path: Path) -> None:
    """``--no-keep-data`` also deletes the data dir and records it in ``removed``."""
    _config_dir, data_dir, _cache_dir, _systemd_unit = _seed_user_layout(tmp_path)
    env = _subprocess_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "uninstall", "--user", "--no-keep-data", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    out = json.loads(result.stdout)
    assert out["keep_data"] is False
    assert str(data_dir) in set(out["removed"]), f"data dir not in removed list: {out['removed']}"
    assert out["kept"] == [], f"kept list should be empty with --no-keep-data; got {out['kept']}"
    assert not data_dir.exists(), "data dir still on disk despite --no-keep-data"


def test_uninstall_idempotent_on_clean_tree(tmp_path: Path) -> None:
    """``kairix uninstall`` against an absent layout exits 0 with empty removed/kept lists."""
    env = _subprocess_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "uninstall", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    out = json.loads(result.stdout)
    assert out["removed"] == [], f"clean-tree uninstall should report no removals; got {out['removed']}"
    assert out["kept"] == [], f"clean-tree uninstall should report no kept paths; got {out['kept']}"


def test_uninstall_system_mode_refuses_when_not_root() -> None:
    """``kairix uninstall --system`` from a non-root shell exits 1 with an actionable affordance."""
    if os.geteuid() == 0:
        pytest.skip(
            "test runs as non-root by design; fix: re-run the suite under an unprivileged user "
            "to exercise the system-mode permission gate."
        )

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "uninstall", "--system"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1, f"expected exit 1, got {result.returncode}; stdout={result.stdout}"
    assert "system-mode uninstall requires root" in result.stderr, f"affordance missing from stderr: {result.stderr!r}"
