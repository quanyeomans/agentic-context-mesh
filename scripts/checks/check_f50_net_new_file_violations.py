"""F50 detector — net-new files cannot accrete fitness-function debt.

Closes the loophole that per-file shrink-only baselines leave open: a
brand-new file under ``kairix/**`` can land with arbitrary F-rule
violations because the baseline doesn't yet know the file exists.
Identified by the 2026-05-22 tc-agent-zone cross-repo audit.

This check fires at pre-commit + CI Stage 0. For every file added in the
current commit (or in HEAD's diff against the previous commit, depending
on invocation), it asserts the file is not currently grandfathered in
any of the per-file F-rule baselines. New files must land clean.

Pre-existing files already in baselines are unaffected — F49 governs
the shrinking schedule. F50 only blocks NEW additions.

Determination of "added":

  * Staged-diff mode (default, pre-commit): ``git diff --cached --name-only --diff-filter=A``.
  * Full-tree mode (CI / explicit): every file in any baseline that
    didn't exist at the most recent tagged release (``git ls-tree
    <prev-tag>:<path>``). Lets CI catch a release-PR that introduces
    new violations even if pre-commit was skipped locally.

Per F21, the failure text carries a ``fix:``/``next:``/``run:``
trailer pointing at the canonical paydown patterns.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASELINE_DIR = Path(".architecture/baseline")

REMEDIATION = """F50: net-new file(s) appear in one or more grandfathered F-rule baselines.
A new file must land clean — the per-file shrink-only baseline policy
(F49) governs paydown of pre-existing entries, not accretion via fresh
additions.

fix: address the underlying F-rule violation(s) in the new file before
     committing. The owning baseline file in .architecture/baseline/
     names which rule fired (e.g. f30-... for outcome tests, f47-...
     for direct *Pipeline construction in tests/integration/).
next: see docs/architecture/test-discipline-hardening.md §5 (canonical
      paydown patterns) for the same shape applied to F30. Same approach
      works for any per-file baseline.
run: bash scripts/checks/check-f50-net-new-file-violations.sh
"""

# ---------------------------------------------------------------------------
# Baseline parsing
# ---------------------------------------------------------------------------


def _read_baseline(baseline_path: Path) -> set[str]:
    """Return the set of non-comment, non-blank file-path entries.

    Skips header lines (``#`` prefix) and empty lines. Trailing whitespace
    on entries is stripped so ``"kairix/foo.py "`` and ``"kairix/foo.py"``
    are treated as the same file (defends against editor-introduced
    trailing whitespace in baseline files).
    """
    if not baseline_path.exists():
        return set()
    entries: set[str] = set()
    for line in baseline_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def _load_all_baselines() -> dict[str, set[str]]:
    """Return ``{baseline_filename: {file_paths...}}`` for every .txt baseline.

    Walks ``.architecture/baseline/*-files.txt`` (F22-conformant
    naming). The map keys are filenames (e.g. ``f30-operator-outcome-tests-files.txt``);
    values are the parsed grandfathered file paths.
    """
    out: dict[str, set[str]] = {}
    if not BASELINE_DIR.is_dir():
        return out
    for path in sorted(BASELINE_DIR.glob("*-files.txt")):
        out[path.name] = _read_baseline(path)
    return out


# ---------------------------------------------------------------------------
# Added-file resolution
# ---------------------------------------------------------------------------


def _staged_added_files() -> list[str]:
    """Return paths added in the current staged diff (``--diff-filter=A``).

    Empty list when no staged changes (pre-commit-hook no-op case).
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _added_since_last_tag() -> list[str]:
    """Return paths that exist at HEAD but did not exist at the prior tag.

    Used by CI to catch full-PR or full-branch additions; pre-commit
    only sees the staged diff for the in-flight commit. If there's no
    prior tag (first release), returns the empty list (nothing to
    compare against).
    """
    prev_tag_result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*.[0-9]*.[0-9]*", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if prev_tag_result.returncode != 0:
        return []
    prev_tag = prev_tag_result.stdout.strip()
    if not prev_tag:
        return []

    diff_result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{prev_tag}..HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if diff_result.returncode != 0:
        return []
    return [line for line in diff_result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def find_net_new_violations(
    added_files: Iterable[str],
    baselines: dict[str, set[str]],
) -> dict[str, list[str]]:
    """Return ``{baseline_filename: [violating_added_paths]}`` for hits.

    Empty dict when no added file appears in any baseline.
    """
    added_set = set(added_files)
    out: dict[str, list[str]] = {}
    for baseline_name, baseline_files in baselines.items():
        hits = sorted(added_set & baseline_files)
        if hits:
            out[baseline_name] = hits
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 when clean, 1 when violations are found."""
    parser = argparse.ArgumentParser(
        prog="check_f50_net_new_file_violations",
        description="F50: net-new files cannot appear in any F-rule baseline.",
    )
    parser.add_argument(
        "--mode",
        choices=("staged", "full-tree"),
        default="staged",
        help="staged: pre-commit hook mode (default); full-tree: CI mode comparing against previous tag.",
    )
    args = parser.parse_args(argv)

    added = _staged_added_files() if args.mode == "staged" else _added_since_last_tag()
    if not added:
        return 0

    baselines = _load_all_baselines()
    violations = find_net_new_violations(added, baselines)
    if not violations:
        return 0

    print("F50: net-new file(s) appear in grandfathered F-rule baseline(s):", file=sys.stderr)
    for baseline_name in sorted(violations):
        print(f"  baseline {baseline_name}:", file=sys.stderr)
        for path in violations[baseline_name]:
            print(f"    {path}", file=sys.stderr)
    print("", file=sys.stderr)
    print(REMEDIATION, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
