"""E2E composed install-path test — F48 (Plan 1 task 9).

Exercises the full composed production path of the kairix self-installer:

  subprocess kairix CLI
    -> kairix.cli dispatch
    -> kairix.install.init_cli / uninstall_cli argparse
    -> kairix.install.installer orchestrator
    -> real dir creation under XDG-redirected tmp_path
    -> real config template rendering + write
    -> real systemd unit rendering + (best-effort) install_unit
    -> real verify() walk against the on-disk layout

No fakes. No monkeypatching. Drives the binary surface with subprocess
and an explicit ``env=`` dict that redirects every XDG resolver -- plus
``HOME`` (the systemd target-dir resolver uses ``Path.home()``, not
``XDG_CONFIG_HOME``) -- into ``tmp_path``, so the test never touches
the operator's real ``~/.config`` / ``~/.local`` / ``~/.cache`` tree.

F48: file exists, carries ``@pytest.mark.e2e``, runs in CI Stage 4.5
under ``pytest -m e2e``, exercises composed production code with no
substitution at any seam.

F2-clean: only POSIX-spec XDG names + ``HOME`` are placed on the
subprocess env. No ``KAIRIX_*`` env vars are set; provider-credential
``KAIRIX_*`` vars are *popped* so any subprocess credential read takes
the deterministic offline branch.

The user-mode install tests skip gracefully when ``systemctl --user``
cannot reach a live logind session bus -- macOS dev boxes lack
``systemctl`` entirely; headless CI runners have the binary but no user
bus, and the install layer unconditionally invokes ``systemctl --user
daemon-reload`` + ``enable``. The ``--system`` refusal test runs
unconditionally because it short-circuits before any systemctl call.

Sabotage-proofs:

  * ``test_kairix_init_system_refuses_when_not_root`` (executed on dev
    macOS; runs unconditionally everywhere): mutated
    ``kairix.install.init_cli._resolve_mode`` to return ``Mode.user``
    from the ``--system`` branch when not root -> the
    ``returncode == 1`` + "system-mode install requires root in stderr"
    assertions flip red. Restored.
  * ``test_kairix_init_user_lays_down_full_install_tree`` and
    ``test_kairix_uninstall_default_keeps_data``: cannot be sabotage-
    executed on a dev macOS box (both skip without a systemctl --user
    bus). Pinning targets for CI Stage 4.5 (Linux runner with a live
    user systemd session):
      - mutate ``kairix.install.installer.install`` to skip the dir-
        creation step -> the post-install ``is_dir()`` assertion in
        test_kairix_init_user_lays_down_full_install_tree flips red.
      - mutate ``kairix.install.installer.uninstall`` to call
        ``shutil.rmtree(data)`` regardless of the ``keep_data`` flag
        -> the post-uninstall ``marker.exists()`` assertion in
        test_kairix_uninstall_default_keeps_data flips red.
    Underlying behaviour these tests probe is already sabotage-proven
    in tests/integration/test_cli_init.py +
    tests/integration/test_cli_uninstall.py (which use the same
    composed code path); the e2e tests here are the F48 composed-
    path proof at CI Stage 4.5.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _systemctl_user_bus_available() -> bool:
    """Return True when ``systemctl --user`` can talk to a live user bus.

    macOS lacks ``systemctl`` entirely; GitHub Actions ubuntu-latest has
    the binary but typically no logind session, so ``systemctl --user
    daemon-reload`` exits nonzero with "Failed to connect to bus" on
    stderr. Either failure means the install path (which unconditionally
    invokes ``systemctl --user daemon-reload`` + ``enable``) cannot
    complete -- so the user-mode install tests skip rather than fail.
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
    if "Failed to connect" in result.stderr:
        return False
    # ``is-system-running`` returns 0 for "running" and 1 for "degraded"
    # but both mean a reachable bus; only a missing bus is a hard skip.
    return True


_SKIP_REASON_NO_USER_BUS = (
    "systemctl --user bus not reachable (macOS dev box or CI runner without logind session); "
    "fix: run on a host with a live user systemd session, or rely on tests/install/* "
    "+ tests/integration/test_cli_init.py unit/integration coverage."
)


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    """Build an env that redirects every per-mode resolver into ``tmp_path``.

    Mirrors the canonical helper from
    ``tests/integration/test_cli_init.py`` so the e2e and integration
    layers exercise the same redirect shape. Kept inline (not imported
    cross-test) to avoid a tests/integration -> tests/e2e dependency.

    Redirects:
      * ``XDG_CONFIG_HOME`` -> ``tmp_path/config``  (config_dir resolver)
      * ``XDG_DATA_HOME``   -> ``tmp_path/data``    (data_dir resolver)
      * ``XDG_CACHE_HOME``  -> ``tmp_path/cache``   (cache_dir resolver)
      * ``XDG_RUNTIME_DIR`` -> ``tmp_path/runtime`` (runtime_secrets_dir)
      * ``HOME``            -> ``tmp_path``         (Path.home() in
                                                    systemd._default_target_dir)

    Pops ``KAIRIX_LLM_API_KEY`` / ``KAIRIX_AZURE_API_KEY`` so the
    subprocess takes deterministic offline branches anywhere the
    install path touches secrets.
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


def test_kairix_init_user_lays_down_full_install_tree(tmp_path: Path) -> None:
    """``kairix init --user --json`` composes the full install layout end-to-end.

    Runs the real CLI subprocess (no fakes, no patching) against an
    XDG-redirected ``tmp_path`` and asserts every layer the installer
    is supposed to lay down:

      1. FHS/XDG dir tree exists under the redirected roots.
      2. Default ``kairix.config.yaml`` was rendered + written.
      3. ``kairix init verify --user --json`` reports ``ok: true``.
      4. Re-running ``kairix init`` is idempotent -- every dir step
         comes back as ``"existing"`` (or ``"mode-adjusted"`` if the
         operator happens to have drifted the perms between runs).
    """
    if not _systemctl_user_bus_available():
        pytest.skip(_SKIP_REASON_NO_USER_BUS)
    env = _subprocess_env(tmp_path)

    # 1. Run kairix init --user -- the full composed install path.
    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    report = json.loads(result.stdout)
    assert report["mode"] == "user", f"expected mode=user, got: {report}"

    # 2. The FHS/XDG dir tree must be on disk under the redirected roots.
    assert (tmp_path / "config" / "kairix").is_dir(), "XDG_CONFIG_HOME/kairix not created"
    assert (tmp_path / "data" / "kairix").is_dir(), "XDG_DATA_HOME/kairix not created"
    assert (tmp_path / "cache" / "kairix").is_dir(), "XDG_CACHE_HOME/kairix not created"
    assert (tmp_path / "config" / "kairix" / "kairix.config.yaml").exists(), (
        "default config file not rendered under XDG_CONFIG_HOME/kairix"
    )

    # 3. kairix init verify --user --json -- the composed verify walk
    #    against the layout we just laid down should report ok=True.
    verify = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "verify", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert verify.returncode == 0, f"verify failed: {verify.stderr}\nstdout: {verify.stdout}"
    verify_report = json.loads(verify.stdout)
    assert verify_report["ok"] is True, f"verify ok=False; envelope: {verify_report}"
    assert verify_report["mode"] == "user"

    # 4. Re-run kairix init -- idempotent. Every dir-step action must be
    #    one of the no-mutation outcomes ("existing") or the benign
    #    "mode-adjusted" (drifted perms re-set, no creation). A "created"
    #    on the second run would mean the first run didn't actually
    #    persist the dir, i.e. non-idempotent.
    rerun = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert rerun.returncode == 0, f"rerun stderr: {rerun.stderr}"
    rerun_report = json.loads(rerun.stdout)
    for d in rerun_report.get("dirs", []):
        assert d.get("action") in ("existing", "mode-adjusted"), f"non-idempotent dir step: {d}"


def test_kairix_init_system_refuses_when_not_root() -> None:
    """``kairix init --system`` from a non-root shell exits 1 with actionable stderr.

    Runs the real CLI subprocess and asserts the permission-check short-
    circuit fires before any filesystem mutation. F30: outcome assertion
    on returncode AND on the actionable affordance in stderr.
    """
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


def test_kairix_uninstall_default_keeps_data(tmp_path: Path) -> None:
    """``kairix init --user`` then ``kairix uninstall --user`` keeps the data dir.

    End-to-end: install via the real CLI, write an operator-state marker
    inside the data dir, run uninstall (with the default keep-data
    behaviour -- no flag required), and assert the marker survives.
    The default of ``keep_data=True`` is the load-bearing safety
    behaviour -- an accidental ``kairix uninstall`` MUST NOT delete the
    operator's SQLite index, vector index, or document state.
    """
    if not _systemctl_user_bus_available():
        pytest.skip(_SKIP_REASON_NO_USER_BUS)
    env = _subprocess_env(tmp_path)

    install = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "init", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert install.returncode == 0, f"install setup failed: {install.stderr}"

    # Plant an operator-state marker file inside the data dir.
    marker = tmp_path / "data" / "kairix" / "index.sqlite"
    marker.write_bytes(b"operator data")

    # Uninstall with no --no-keep-data flag -- default behaviour keeps data.
    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "uninstall", "--user", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, f"uninstall failed: {result.stderr}\nstdout: {result.stdout}"
    out = json.loads(result.stdout)
    assert out["keep_data"] is True, f"expected default keep_data=True; envelope: {out}"
    assert str(tmp_path / "data" / "kairix") in set(out["kept"]), f"data dir not in kept list: {out['kept']}"

    # The actual filesystem reflects the envelope -- marker survives.
    assert marker.exists(), "default uninstall (keep_data=True) wrongly removed operator data"
