"""F30 outcome test — ``kairix wikilinks`` subprocess surface.

Pays down ``kairix/knowledge/wikilinks/cli.py`` from the F30 baseline.

The wikilinks CLI exposes ``inject`` (writes to .md files; needs the
entity registry), ``audit`` (reads vault docs; surfaces broken-link
and unlinked-mention counts), and ``status`` (reads the module-level
log + last-run marker).

``audit`` is the natural subprocess outcome path: it walks the
configured ``document_root``, queries the entity loader (graceful-
degrades to empty when Neo4j is absent), and prints a structured
markdown report.

Production change (additive):
- Added a global ``--document-root PATH`` flag stripped from argv
  before subcommand dispatch — matches the canonical pattern from
  ``kairix store crawl --document-root``. When supplied (and no
  in-process ``paths=`` kwarg was injected), it replaces the
  ``document_root`` component of the resolved ``KairixPaths``.

Boundary chain exercised:

  subprocess([kairix, wikilinks, audit, --document-root <tmp>])
    → kairix/knowledge/wikilinks/cli.py:main
    → _extract_document_root_flag pops the flag
    → _replace_document_root replaces paths.document_root
    → _audit_cmd → weekly_report walks tmp_path
    → report written to <tmp>/04-Agent-Knowledge/shared/wikilink-audit-report.md
    → CLI exits 0

The error-path test invokes an unknown subcommand — the CLI prints
"Unknown wikilinks subcommand:" on stderr and exits 1.

F2-clean: no ``KAIRIX_*`` env mutation in the subprocess invocation.

Sabotage-proof anchor: mutating ``_replace_document_root`` to return
``paths`` unchanged makes the audit walk the production document root
instead of the tmp dir → the "Report saved to" line points at the
wrong path → the assertion that the report file lives under tmp_path
fails. Verified locally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_minimal_vault(root: Path) -> None:
    """Lay out the minimum structure ``weekly_report`` walks.

    The audit reads the document_root tree and writes the report under
    ``<root>/04-Agent-Knowledge/shared/wikilink-audit-report.md``. One
    placeholder doc keeps the structure realistic.
    """
    shared_dir = root / "04-Agent-Knowledge" / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (root / "00-Home").mkdir(parents=True, exist_ok=True)
    (root / "00-Home" / "README.md").write_text("# Home\n", encoding="utf-8")


def test_wikilinks_audit_subprocess_report_outcome(tmp_path: Path) -> None:
    """Drive ``kairix wikilinks audit --document-root <tmp>``.

    Asserts on the markdown report the operator sees on stdout plus
    the report file written into the tmp document root — the binary
    surface must respect the new ``--document-root`` flag.
    """
    _seed_minimal_vault(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "wikilinks",
            "audit",
            "--document-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"wikilinks audit exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    stdout = proc.stdout
    assert "# Wikilink Audit Report" in stdout, f"audit header missing: {stdout!r}"
    assert "## Entity Ontology" in stdout, f"entity ontology section missing: {stdout!r}"
    assert "## Broken Links" in stdout, f"broken-links section missing: {stdout!r}"
    assert "## Unlinked Mentions" in stdout, f"unlinked-mentions section missing: {stdout!r}"

    # The flag must drive the report path into the tmp vault, not the
    # operator's real document_root.
    expected_report = tmp_path / "04-Agent-Knowledge" / "shared" / "wikilink-audit-report.md"
    assert expected_report.exists(), (
        f"audit report not written to tmp document root. Expected: {expected_report}, stdout: {stdout!r}"
    )


def test_wikilinks_unknown_subcommand_subprocess_error(tmp_path: Path) -> None:
    """An unknown wikilinks subcommand must surface a structured error
    on stderr and exit non-zero.

    Closes the binary-surface error path the unit tests cover only in-
    process.
    """
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "wikilinks",
            "nonexistent-subcommand",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, (
        f"wikilinks unknown subcommand expected exit 1, got {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "Unknown wikilinks subcommand" in proc.stderr, (
        f"expected unknown-subcommand error on stderr, got: {proc.stderr!r}"
    )
