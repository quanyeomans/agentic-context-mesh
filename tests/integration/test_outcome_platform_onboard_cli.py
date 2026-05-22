"""F30 outcome test — ``kairix onboard`` subprocess surface.

Wave 0 paydown (kairix-pro-platform#59), Group E. Closes the F30 gap on
``kairix/platform/onboard/cli.py``: existing unit tests in
``tests/onboard/test_cli.py`` drive ``main()`` in-process via the
``pkg_root`` / ``document_root_override_fn`` DI seams; this test adds
the F30-required subprocess outcome assertion.

Subcommand targeted: ``onboard guide`` — it already exposes
``--document-root`` and gains ``--guide-src`` here so the subprocess
binary can be driven against a tmp-path placeholder without monkey-
patching ``kairix.__file__`` and without setting any ``KAIRIX_*`` env
vars (F2-clean by construction).

Boundary chain exercised:

  subprocess([kairix, onboard, guide,
              --document-root <tmp>,
              --guide-src <tmp>/guide.md,
              --dry-run])
    → kairix/cli.py dispatch
    → kairix/platform/onboard/cli.py:main
    → cmd_guide → _resolve_doc_root + _resolve_guide_src
    → dry-run path → stdout banner ("Would install...")

Sabotage-proof (executed locally):
    Mutated ``if explicit:`` in ``_resolve_guide_src`` to ``if False:``
    — the CLI then falls through to the installed-package probe, which
    in this worktree resolves to ``<repo>/kairix/docs/agent-usage-guide.md``
    (does not exist) → error message + exit 1 → happy-path test fails
    on ``returncode == 0`` and on the missing "Source: <tmp>" line.
    Restored after observing the failure.

Latency baseline (2024 M-series Mac): subprocess wall ~800ms cold;
threshold 5000ms for CI variance + slower hardware.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_guide_source(tmp_path: Path) -> Path:
    """Write a minimal markdown placeholder the CLI accepts as the guide source.

    The CLI's ``_resolve_guide_src`` only checks ``Path.exists()`` —
    content is copied verbatim to the destination on a non-dry-run.
    The dry-run path doesn't read it; existence alone is the gate.
    """
    guide = tmp_path / "agent-usage-guide.md"
    guide.write_text("# agent usage guide placeholder\n", encoding="utf-8")
    return guide


def test_onboard_guide_subprocess_dry_run_emits_source_and_dest(tmp_path: Path) -> None:
    """Drive the real ``kairix onboard guide`` binary surface against a tmp vault.

    Asserts on the dry-run banner content the operator consumes —
    NOT on returncode alone, NOT on internal fake call-counts. F30
    contract: subprocess + stdout assertion.
    """
    guide = _seed_guide_source(tmp_path)
    doc_root = tmp_path / "vault"
    doc_root.mkdir()

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "onboard",
            "guide",
            "--document-root",
            str(doc_root),
            "--guide-src",
            str(guide),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"onboard guide exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    stdout = proc.stdout
    assert "Would install agent usage guide" in stdout, f"banner missing: {stdout!r}"
    assert f"Source: {guide}" in stdout, f"source line missing: {stdout!r}"
    assert "Dest:" in stdout, f"dest line missing: {stdout!r}"
    # Destination should be inside the tmp document root, not the host's real vault.
    assert str(doc_root) in stdout, f"dest does not land under tmp doc_root: {stdout!r}"

    assert elapsed_ms < 5000.0, f"onboard guide subprocess took {elapsed_ms:.1f}ms (threshold 5000ms)"


def test_onboard_guide_subprocess_exits_non_zero_on_missing_document_root(tmp_path: Path) -> None:
    """Pointing ``--document-root`` at a non-existent directory must
    surface a non-zero exit + an operator-actionable error message on
    stderr. Closes the binary-surface error path the unit tests cover
    only in-process."""
    guide = _seed_guide_source(tmp_path)
    bogus_root = tmp_path / "does-not-exist"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "onboard",
            "guide",
            "--document-root",
            str(bogus_root),
            "--guide-src",
            str(guide),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "document root does not exist" in proc.stderr.lower(), f"stderr missing error message: {proc.stderr!r}"
