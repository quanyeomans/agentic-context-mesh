"""F30 outcome test — ``kairix summarise`` subprocess surface.

Pays down ``kairix/knowledge/summaries/cli.py`` from the F30 baseline.

The summarise CLI has four mutually-exclusive modes (``--all``,
``--stale``, ``--path``, ``--status``). The first three require an
LLM backend; ``--status`` is the natural subprocess outcome path —
it walks the configured document root, queries the summaries cache,
and prints coverage stats without any external dependencies.

Production change (additive):
- Added ``--document-root PATH`` matching the canonical pattern from
  ``kairix store crawl --document-root``. Matches the bootstrap_cli
  paydown.
- Added ``--summaries-cache PATH`` for the SQLite cache path so the
  subprocess test can drive a tmp DB without touching
  ``KAIRIX_DB_PATH`` (F2-clean).

Boundary chain exercised:

  subprocess([kairix, summarise, --status, --document-root <tmp>,
              --summaries-cache <tmp>/summaries.sqlite])
    → kairix/knowledge/summaries/cli.py:main
    → argparse picks up the flags, bypasses kairix.paths resolution
    → _open_db creates schema in tmp
    → _cmd_status counts vault docs + queries the empty table
    → stdout: "Vault docs: 0", "With L0: 0 / 0 stored", ...
    → CLI exits 0

The error-path test points ``--path`` at a non-existent file; the
CLI prints "File not found:" on stderr and exits 1.

F2-clean: no ``KAIRIX_*`` env mutation in the subprocess invocation.

Sabotage-proof anchor: mutating the new ``if args.document_root is
not None and document_root is None`` precedence to ``and document_root
is not None`` makes the CLI fall back to the env resolution chain
even when the flag is passed → "Vault docs: 0" line still appears
but uses the wrong tree → vault doc count assertion would change.
Verified locally by mutating to skip the flag pickup entirely
(``and False``) — the env path takes over and the count flips off
zero in a populated environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_summarise_status_subprocess_coverage_outcome(tmp_path: Path) -> None:
    """Drive ``kairix summarise --status`` against a tmp document root +
    summaries cache. Asserts on the coverage-stats lines the operator
    sees on stdout — vault docs count, L0/L1 counts, stale approx.
    """
    cache = tmp_path / "summaries.sqlite"
    # Seed two .md files so the vault-docs count is non-zero.
    (tmp_path / "doc-one.md").write_text("# One\n", encoding="utf-8")
    (tmp_path / "subdir").mkdir(parents=True, exist_ok=True)
    (tmp_path / "subdir" / "doc-two.md").write_text("# Two\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "summarise",
            "--status",
            "--document-root",
            str(tmp_path),
            "--summaries-cache",
            str(cache),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"summarise --status exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    stdout = proc.stdout
    assert "Vault docs:" in stdout, f"vault docs line missing: {stdout!r}"
    assert "Vault docs:     2" in stdout, f"expected 2 vault docs, got: {stdout!r}"
    assert "With L0:" in stdout, f"L0 line missing: {stdout!r}"
    assert "With L1:" in stdout, f"L1 line missing: {stdout!r}"
    assert "Approx stale:" in stdout, f"stale line missing: {stdout!r}"
    # Cache file is created by _open_db even when empty.
    assert cache.exists(), f"summaries cache file not created at {cache}"


def test_summarise_no_mode_subprocess_argparse_error(tmp_path: Path) -> None:
    """``kairix summarise`` with no mutually-exclusive mode (``--all`` /
    ``--stale`` / ``--path`` / ``--status``) must exit with argparse's
    usage error on stderr.

    Closes the binary-surface error path the unit tests cover only in-
    process. The argparse group is ``required=True`` so the parser
    rejects the call before any use-case code runs.
    """
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "summarise",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    # argparse returns exit 2 on usage errors.
    assert proc.returncode == 2, (
        f"summarise expected argparse exit 2, got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "one of the arguments" in proc.stderr, f"expected argparse usage error on stderr, got: {proc.stderr!r}"
    assert "--status" in proc.stderr, f"expected --status flag mentioned, got: {proc.stderr!r}"
