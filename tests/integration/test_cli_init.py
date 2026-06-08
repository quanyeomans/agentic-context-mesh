"""F30 outcome tests — ``kairix init`` subprocess CLI surface (Plan 1 task 8).

Asserts the subprocess binary surface end-to-end:

  * ``kairix init --user --json`` lays down the user-mode install
    layout under XDG-redirected paths and emits a JSON envelope on
    stdout whose ``mode`` field reads ``"user"``.
  * ``kairix init --system`` from a non-root shell exits 1 and prints
    an actionable affordance to stderr (no layout mutation attempted).
  * ``kairix init verify --user --json`` against a pre-seeded layout
    emits ``"ok": true``.

F2-clean: the only env vars manipulated for the subprocess are
POSIX-spec XDG names and ``HOME`` (NOT ``KAIRIX_*``) so the per-mode
path resolvers + ``Path.home()`` (used by the systemd target dir
resolver) land under ``tmp_path``. No ``monkeypatch.setenv``; the
subprocess gets an explicit ``env=`` dict.

The two install-running tests skip when ``systemctl --user`` is
unreachable (no systemctl binary on macOS dev boxes; no logind
session on GitHub Actions runners) — the install layer calls
``systemctl --user daemon-reload`` + ``enable`` which exits nonzero
without a user systemd bus. The refuse-without-root test runs
unconditionally because it short-circuits before any systemctl call.

Sabotage-proofs (executed):
  * Mutated ``init_cli._resolve_mode`` to return ``Mode.user`` from
    the ``--system`` branch when not root → the
    ``test_init_system_mode_refuses_when_not_root`` assertion on
    ``returncode == 1`` flips red. Restored.
  * Mutated ``init_cli.main`` to print ``"not-json"`` instead of the
    json envelope under ``--json`` → ``json.loads(stdout)`` raises in
    ``test_init_user_mode_succeeds_and_emits_json_envelope``. Restored.
  * Mutated ``installer.verify`` to return ``ok=False`` unconditionally
    → ``test_init_verify_returns_ok_after_install`` ``out["ok"] is True``
    flips red. Restored.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _systemctl_user_bus_available() -> bool:
    """Return True when ``systemctl --user`` can talk to a live user bus.

    macOS dev boxes lack ``systemctl`` entirely; GitHub Actions
    ubuntu-latest has the binary but no logind session, so
    ``systemctl --user daemon-reload`` exits nonzero. Either failure
    means the install path (which unconditionally invokes
    ``systemctl --user daemon-reload`` + ``enable``) cannot complete.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    # ``is-system-running`` returns 0 for "running" and nonzero for
    # "degraded"/"offline"; for our purposes any reachable bus is OK,
    # so we treat exit codes 0 and 1 (degraded but reachable) as a
    # working bus. The diagnostic stderr "Failed to connect to bus" is
    # the case we want to skip on, and that returns a different code
    # (or stderr contains "Failed to connect").
    if "Failed to connect" in result.stderr:
        return False
    return True


_REQUIRES_USER_BUS = pytest.mark.skipif(
    not _systemctl_user_bus_available(),
    reason="systemctl --user bus not reachable (macOS dev box or CI runner without logind session); "
    "fix: run on a host with a live user systemd session, or rely on tests/install/* unit coverage.",
)


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    """Build an env that redirects every per-mode resolver into ``tmp_path``.

    The kairix install layer reads:
      * ``XDG_CONFIG_HOME`` → ``config_dir(Mode.user)``
      * ``XDG_DATA_HOME``   → ``data_dir(Mode.user)``
      * ``XDG_CACHE_HOME``  → ``cache_dir(Mode.user)``
      * ``XDG_RUNTIME_DIR`` → ``runtime_secrets_dir(Mode.user)``

    The systemd target dir uses ``Path.home()`` directly (not XDG), so
    we also redirect ``HOME`` to keep the rendered unit file under
    ``tmp_path/.config/systemd/user/`` rather than the developer's real
    ``~/.config/systemd/user/``.
    """
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["XDG_RUNTIME_DIR"] = str(tmp_path / "runtime")
    # Strip provider credentials so the subprocess takes deterministic
    # offline branches anywhere the install path touches secrets.
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


@_REQUIRES_USER_BUS
def test_init_user_mode_succeeds_and_emits_json_envelope(tmp_path: Path) -> None:
    """``kairix init --user --json`` lays down the user-mode layout and emits JSON."""
    env = _subprocess_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    out = json.loads(result.stdout)
    assert out["mode"] == "user", f"expected mode=user, got envelope: {out}"
    # XDG-rooted config dir must exist after install — the layer-1 dir
    # creation step is the durable proof the install actually ran.
    assert (tmp_path / "config" / "kairix").exists(), "config dir not created"
    # Default config file must be present at the per-mode location.
    assert (tmp_path / "config" / "kairix" / "kairix.config.yaml").exists(), "default config file not created"


def test_init_system_mode_refuses_when_not_root() -> None:
    """``kairix init --system`` from a non-root shell exits 1 with an actionable affordance."""
    if os.geteuid() == 0:
        pytest.skip(
            "test runs as non-root by design; fix: re-run the suite under an unprivileged user "
            "to exercise the system-mode permission gate."
        )

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--system"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1, f"expected exit 1, got {result.returncode}; stdout={result.stdout}"
    assert "system-mode install requires root" in result.stderr, f"affordance missing from stderr: {result.stderr!r}"


@_REQUIRES_USER_BUS
def test_init_verify_returns_ok_after_install(tmp_path: Path) -> None:
    """``kairix init verify --user --json`` against a healthy layout reports ok=true."""
    env = _subprocess_env(tmp_path)

    # Lay down the install first.
    install_result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--user"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert install_result.returncode == 0, f"setup install failed: {install_result.stderr}"

    # Now verify against the same layout.
    verify_result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "verify", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert verify_result.returncode == 0, f"verify failed: {verify_result.stderr}\nstdout: {verify_result.stdout}"
    out = json.loads(verify_result.stdout)
    assert out["ok"] is True, f"verify ok=False; envelope: {out}"
    assert out["mode"] == "user"
