"""F30 outcome test — ``kairix config validate`` subprocess surface.

Wave 0 paydown for ``kairix/core/search/config_validator.py``. The
validator is a CLI entry point operators invoke pre-deploy / in CI:
``kairix config validate <path>`` parses the YAML, reports any
structural issues, and exits non-zero on errors. This test asserts on
the operator-visible stdout content (the success line, the per-error
bullets) — not just on the exit code.

F2-clean by construction: the config path is passed as an explicit
positional argument, so no ``KAIRIX_*`` env-var mutation is needed.

Boundary chain exercised:

  subprocess([kairix, config, validate, <path>])
    → kairix/core/search/config_validator.py:main
    → yaml.safe_load(<path>)
    → validate_config(data)
    → print("OK: ...") | print("Found N validation error(s)...") + bullets
    → sys.exit(0|1)

Sabotage-proof anchor: deleting the "Found {len(errors)} validation
error(s)" print in ``main`` (or weakening the error-list iteration to
print nothing) makes the error-path test fail on the stderr/stdout
assertion — the contract that operators read a per-error line for
each issue. Tested locally.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.integration


def _write_config(root: Path, body: str) -> Path:
    path = root / "kairix.config.yaml"
    path.write_text(dedent(body), encoding="utf-8")
    return path


def test_config_validate_subprocess_reports_ok_on_valid_yaml(tmp_path: Path) -> None:
    """Valid YAML → exit 0 + ``OK: <path> is valid.`` on stdout."""
    config_path = _write_config(
        tmp_path,
        """
        collections:
          shared:
            - name: docs
              path: docs
            - name: research
              path: research
          agent_pattern: "{agent}-memory"
        agents:
          - name: agent-alpha
            write_path: agents/agent-alpha
          - name: agent-beta
            write_path: agents/agent-beta
        """,
    )

    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "config", "validate", str(config_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"config validate exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "OK" in proc.stdout, f"expected OK marker in stdout: {proc.stdout!r}"
    assert str(config_path) in proc.stdout, f"expected path in stdout: {proc.stdout!r}"

    assert elapsed_ms < 5000.0, f"config validate subprocess took {elapsed_ms:.1f}ms (threshold 5000ms)"


def test_config_validate_subprocess_reports_errors_on_typo_overrides(tmp_path: Path) -> None:
    """A config with a typo'd retrieval override key → exit 1 + each error on stdout.

    Mirrors the common operator mistake (``rrfk`` vs ``rrf_k``). The
    validator must surface the per-error bullet, not just exit non-zero.
    """
    config_path = _write_config(
        tmp_path,
        """
        collections:
          shared:
            - name: docs
              path: docs
              retrieval:
                rrfk: 30
                bm25_limmit: 12
        """,
    )

    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "config", "validate", str(config_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, (
        f"expected exit 1 on invalid config; got {proc.returncode}\n--- stdout ---\n{proc.stdout}"
    )
    assert "validation error" in proc.stdout, f"expected validation-error header in stdout: {proc.stdout!r}"
    assert "unknown retrieval override key" in proc.stdout, f"expected per-error bullet in stdout: {proc.stdout!r}"
    assert "rrfk" in proc.stdout, f"expected typo'd key in stdout: {proc.stdout!r}"
