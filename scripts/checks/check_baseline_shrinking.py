"""F49: Test-discipline baselines shrink per release.

Each release tag (matching ``v[0-9]*.[0-9]*.[0-9]*``) must reduce each of
the following baseline files by at least one entry compared to the
previous tagged release, OR keep all three at zero:

    - .architecture/baseline/f30-operator-outcome-tests-files.txt
    - .architecture/baseline/F46-files.txt
    - .architecture/baseline/F47-files.txt

The check runs at release time (in ``.github/workflows/release.yml``)
BEFORE the tag is cut. It does NOT run per-commit — between commits
within a release window the baselines don't change, so it would always
pass.

Algorithm:

  1. Resolve the previous release tag via ``git describe --tags --abbrev=0
     --match 'v[0-9]*.[0-9]*.[0-9]*' HEAD^``. If no prior tag exists
     (first release), exit 0 with a "first release" notice.
  2. For each of the three baseline files, count non-comment non-blank
     lines at HEAD and at the previous tag (via
     ``git show <prev-tag>:<path>``). If the baseline didn't exist at
     the prev tag (e.g. F46/F47 introduced post-tag), treat as count 0.
  3. Apply the shrink rule per file:
        HEAD > prev      → FAIL (baseline grew)
        HEAD == prev > 0 → FAIL (didn't shrink)
        HEAD == 0        → OK (zero stays zero)
        HEAD < prev      → OK (shrunk)
  4. On failure: emit action-marked text per F21 listing the specific
     baseline(s) that didn't shrink AND a diff-style summary of which
     entries are still in HEAD but were also in prev (i.e. entries that
     should have been paid down).

Exit code: 0 when all baselines shrunk or stayed at zero; 1 otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The three baseline files governed by F49.
F49_BASELINE_PATHS: tuple[str, ...] = (
    ".architecture/baseline/f30-operator-outcome-tests-files.txt",
    ".architecture/baseline/F46-files.txt",
    ".architecture/baseline/F47-files.txt",
)

REMEDIATION = """F49: one or more test-discipline baselines did not shrink
since the previous release.

fix: pay down at least one entry in each listed baseline before tagging
the release. The canonical paydown patterns live in
docs/architecture/test-discipline-hardening.md §5 (F30 paydown plan) —
add an outcome test that asserts on captured stdout/stderr/envelope
content, then remove the corresponding entry from the baseline file in
the same commit. F46/F47 paydown: refactor a BDD step impl /
integration test to construct its pipeline via
kairix.core.factory.build_* with paths=FakePaths(...), then remove the
file from its baseline.

next: re-run ``bash scripts/checks/check-baseline-shrinking.sh`` from
the repo root to confirm the gate goes green; then re-run the release
workflow.

run: bash scripts/checks/check-baseline-shrinking.sh

Pass example:
  # release tag v2026.5.18 -> v2026.5.19
  # Before: .architecture/baseline/f30-operator-outcome-tests-files.txt has 14 entries.
  # After:  .architecture/baseline/f30-operator-outcome-tests-files.txt has 13 entries
  #         (one entry removed in the same commit that adds the outcome
  #         test that asserts on stdout/stderr/envelope for that command).
  $ diff -u v2026.5.18:.../f30-...-files.txt HEAD:.../f30-...-files.txt
  -kairix/cli/embed.py

Forbidden example:
  # release tag v2026.5.18 -> v2026.5.19
  # f30 baseline count unchanged at 14; no outcome test landed in this
  # cycle. F49 fires — release is blocked until at least one entry is
  # paid down (or the rule is explicitly granted a skip in the release PR
  # body with rationale).

See also: docs/architecture/test-discipline-hardening.md §3 (F49)."""


def _count_non_comment_lines(text: str) -> int:
    """Return the count of non-blank non-comment lines in ``text``."""
    return sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def _non_comment_entries(text: str) -> set[str]:
    """Return the set of non-blank non-comment line entries (stripped)."""
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}


def _read_head_baseline(repo_root: Path, rel_path: str) -> str:
    """Read the baseline file at HEAD (working tree). Returns "" if absent."""
    path = repo_root / rel_path
    if not path.exists():
        return ""
    return path.read_text()


def _read_prev_tag_baseline(
    repo_root: Path,
    rel_path: str,
    prev_tag: str,
) -> str:
    """Read the baseline file content at ``prev_tag`` via ``git show``.

    Returns the file content if it existed at that tag; "" if the file
    did not exist (the baseline may have been introduced post-tag).
    """
    result = subprocess.run(
        ["git", "show", f"{prev_tag}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # File did not exist at that tag — treat as count 0.
        return ""
    return result.stdout


def _resolve_previous_tag(repo_root: Path) -> str | None:
    """Resolve the most recent release tag prior to HEAD.

    Returns the tag string, or None if HEAD has no prior release tag in
    its ancestry (first release).
    """
    # ``HEAD^`` is the parent of HEAD; describe-from-there gives us the
    # most recent tag strictly older than HEAD's tip. We accept any tag
    # matching the release pattern (alpha tags like v2026.5.19a1 use a
    # different pattern and are excluded by the --match glob).
    result = subprocess.run(
        [
            "git",
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            "v[0-9]*.[0-9]*.[0-9]*",
            "HEAD^",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def check_baselines(
    repo_root: Path,
    prev_tag: str | None = None,
) -> tuple[int, list[str]]:
    """Run the F49 check.

    Args:
        repo_root: repo root path.
        prev_tag: previous release tag to compare against. If None,
            auto-resolves via ``git describe``.

    Returns:
        (exit_code, lines) — exit_code is 0 on pass, 1 on fail; lines is
        the list of output lines (so callers can print or capture).
    """
    lines: list[str] = []

    if prev_tag is None:
        prev_tag = _resolve_previous_tag(repo_root)

    if prev_tag is None:
        lines.append(
            "F49: no previous release tag found in HEAD's ancestry (first release). Skipping baseline-shrink check."
        )
        return 0, lines

    lines.append(f"F49: comparing baselines at HEAD vs previous release tag {prev_tag}.")

    failures: list[tuple[str, int, int, set[str]]] = []
    for rel_path in F49_BASELINE_PATHS:
        head_text = _read_head_baseline(repo_root, rel_path)
        prev_text = _read_prev_tag_baseline(repo_root, rel_path, prev_tag)

        head_count = _count_non_comment_lines(head_text)
        prev_count = _count_non_comment_lines(prev_text)
        head_entries = _non_comment_entries(head_text)
        prev_entries = _non_comment_entries(prev_text)

        lines.append(f"  {rel_path}: prev={prev_count} head={head_count}")

        if head_count > prev_count:
            # Grew — fail.
            still_present = head_entries & prev_entries
            failures.append((rel_path, prev_count, head_count, still_present))
        elif head_count == prev_count and head_count > 0:
            # Equal and non-zero — fail (didn't shrink).
            still_present = head_entries & prev_entries
            failures.append((rel_path, prev_count, head_count, still_present))
        elif head_count == 0:
            # Zero stays zero (or shrunk to zero) — OK.
            continue
        else:
            # head < prev — shrunk — OK.
            continue

    if not failures:
        lines.append("F49: all governed baselines shrunk (or stayed at zero). OK.")
        return 0, lines

    lines.append("")
    for rel_path, prev_count, head_count, still_present in failures:
        if head_count > prev_count:
            verb = f"grew from {prev_count} to {head_count}"
        else:
            verb = f"did not shrink (stayed at {head_count})"
        lines.append(f"F49: baseline {rel_path} {verb} since {prev_tag}.")
        if still_present:
            lines.append("  entries still present in HEAD that were also in the previous tag (paydown candidates):")
            for entry in sorted(still_present):
                lines.append(f"    - {entry}")
        lines.append("")

    lines.append(REMEDIATION)
    return 1, lines


def main() -> int:
    exit_code, lines = check_baselines(REPO_ROOT)
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
