"""Release-alpha gate: verify HEAD is releasable.

Replaces the brittle ``LAST_SUCCESS_SHA == HEAD_SHA`` check in
``.github/workflows/release-alpha.yml``. That check failed every time
a docs-only commit landed between the last code change and the alpha
cut, because ci.yml's ``paths-ignore`` skips docs commits — so
LAST_SUCCESS_SHA never advances to a docs-only HEAD, and the strict-
equality gate fails.

"Releasable" means "mergeable" — the signal is the ``CI gate`` fan-in
**check-run** conclusion on main HEAD, not the umbrella ci.yml *run*
conclusion. An informational job (SonarCloud, Publish to PyPI, ...)
failing flips the whole-run conclusion to ``failure`` even when the
merge-blocking ``CI gate`` aggregator is green; keying off the run
conclusion mis-blocks a perfectly releasable HEAD (#548). The workflow
resolves HEAD's ``CI gate`` check-run conclusion and passes it in via
``--head-gate-conclusion``; when it is ``success`` the gate short-
circuits to pass immediately, before any git-walking.

Fallback (docs-only-gap walk): when HEAD has no ``CI gate`` check-run
at all — a pure docs-only commit ci.yml's ``paths-ignore`` skipped —
``--head-gate-conclusion`` is empty and the gate instead checks that
**every commit since the last commit whose ``CI gate`` check-run was
green is docs-only** (i.e., would have been skipped by ci.yml itself).
HEAD == LAST_SUCCESS_SHA stays the happy path; HEAD ahead by docs-only
commits is also green; HEAD ahead by even one code commit is still a
fail (that's the real "untested code" case).

Mirrors ci.yml's paths-ignore exactly:
  - ``docs/**``
  - ``**/*.md``
  - ``!CLAUDE.md`` (override: CLAUDE.md is treated as code)

Run from the workflow:
    python3 scripts/ci/check_release_gate.py \\
        --head-sha "$HEAD_SHA" \\
        --last-success-sha "$LAST_SUCCESS_SHA" \\
        --head-gate-conclusion "$HEAD_GATE"

Exit codes:
  0 — gate passes (HEAD's CI gate check-run is green, HEAD == last
      commit whose CI gate was green, or only docs-only commits past it)
  1 — gate fails (code commits past the last commit whose CI gate
      check-run was green)
  2 — usage / unrecoverable error
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import PurePosixPath


def is_docs_only_path(path: str) -> bool:
    """Return True when ``path`` would be filtered by ci.yml's paths-ignore.

    Mirrors the YAML:
      paths-ignore:
        - "docs/**"
        - "**/*.md"
        - "!CLAUDE.md"
    """
    if path == "CLAUDE.md":
        return False  # explicit re-include — treated as code
    p = PurePosixPath(path)
    if p.parts and p.parts[0] == "docs":
        return True
    if path.endswith(".md"):
        return True
    return False


def commit_is_docs_only(sha: str) -> tuple[bool, list[str]]:
    """Return (is_docs_only, code_files_touched) for ``sha``.

    A merge commit (multiple parents) is conservatively treated as a
    code commit — its diff is ambiguous and we'd rather fail loud than
    miss a code change rolled in via merge.
    """
    parent_count = subprocess.run(
        ["git", "rev-list", "--no-walk", "--count", "--parents", sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if len(parent_count.split()) > 2:
        return False, ["<merge commit>"]
    changed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    code_files = [f for f in changed if f and not is_docs_only_path(f)]
    return (not code_files, code_files)


def commits_in_range(last_success_sha: str, head_sha: str) -> list[str]:
    """Return commits between ``last_success_sha`` (exclusive) and
    ``head_sha`` (inclusive) in chronological order."""
    result = subprocess.run(
        ["git", "rev-list", "--reverse", f"{last_success_sha}..{head_sha}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--head-sha", required=True, help="The SHA being released (github.sha)")
    parser.add_argument(
        "--last-success-sha",
        required=True,
        help="Most recent SHA whose `CI gate` check-run was green on main",
    )
    parser.add_argument(
        "--head-gate-conclusion",
        default="",
        help=(
            "HEAD's own `CI gate` check-run conclusion, resolved by the workflow "
            "(e.g. 'success'). When 'success' the gate short-circuits to pass — "
            "the mergeable signal overrides the git-walk. Empty/missing means HEAD "
            "had no `CI gate` check-run (a docs-only commit ci.yml skipped), so the "
            "docs-only-gap walk against --last-success-sha runs instead."
        ),
    )
    args = parser.parse_args()

    head_sha = args.head_sha
    last_success_sha = args.last_success_sha

    # #548: "releasable == mergeable". When the workflow has already resolved
    # HEAD's own `CI gate` check-run to success, that IS the gate — short-
    # circuit before any git-walking so an informational job (SonarCloud,
    # Publish to PyPI) failing the umbrella run can't mis-block the release.
    if args.head_gate_conclusion == "success":
        print(f"CI gate check-run on HEAD ({head_sha}) is green — releasable")
        return 0

    if not last_success_sha:
        print("::error::no successful CI gate run found on main", file=sys.stderr)
        print("::error::fix: push code to main, wait for ci.yml to go green, then re-run", file=sys.stderr)
        return 1

    if head_sha == last_success_sha:
        print(f"CI gate verified — HEAD ({head_sha}) is the last green ci.yml run")
        return 0

    # Verify last_success_sha is an ancestor of head_sha. If it isn't, main
    # has diverged in some unexpected way (force-push, branch swap, ...)
    # and we should fail loud rather than silently let the gate pass.
    is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", last_success_sha, head_sha],
        capture_output=True,
        text=True,
    )
    if is_ancestor.returncode != 0:
        print(
            f"::error::last green SHA ({last_success_sha}) is not an ancestor of HEAD ({head_sha})",
            file=sys.stderr,
        )
        print(
            "::error::fix: main has diverged unexpectedly (force-push?); investigate before releasing",
            file=sys.stderr,
        )
        return 1

    gap = commits_in_range(last_success_sha, head_sha)
    if not gap:
        # Can happen if last_success_sha is descendant of head_sha somehow;
        # treat as no-gap and pass since we already verified ancestry above.
        print(f"CI gate verified — HEAD ({head_sha}) at or behind last green ({last_success_sha})")
        return 0

    bad_commits: list[tuple[str, list[str]]] = []
    for sha in gap:
        docs_only, code_files = commit_is_docs_only(sha)
        if not docs_only:
            bad_commits.append((sha, code_files))

    if not bad_commits:
        print(
            f"CI gate verified — HEAD ({head_sha}) is last green ({last_success_sha}) "
            f"plus {len(gap)} docs-only commit(s) (would be skipped by ci.yml paths-ignore)"
        )
        return 0

    print(
        f"::error::main HEAD ({head_sha}) has untested code commits past last green ci.yml run",
        file=sys.stderr,
    )
    print(f"::error::last green SHA: {last_success_sha}", file=sys.stderr)
    for sha, files in bad_commits:
        preview = ", ".join(files[:3]) + ("..." if len(files) > 3 else "")
        print(f"::error::  - {sha[:8]} touched code: {preview}", file=sys.stderr)
    print(
        "::error::fix: wait for ci.yml to complete on HEAD (or push docs-only follow-ups), then re-run",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
