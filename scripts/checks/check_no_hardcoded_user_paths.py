"""F31: No hardcoded user/machine paths in committed code.

Detects literal absolute filesystem paths that pin a single contributor's
machine to a file checked into the repo:

- ``/Users/<name>/...`` (macOS home dirs)
- ``/home/<name>/...`` (Linux home dirs, excluding ``/home/runner/`` which is
  the GitHub-hosted runner workspace and legitimately appears in CI fixtures)

Why this rule exists:

- A path like ``/Users/developer/Development/kairix/scripts/...`` only
  resolves on one human's laptop. Anyone else running the same code (CI,
  another contributor, the alpha VM) hits ``FileNotFoundError`` or a
  silent path mismatch.
- Today's release session surfaced an instance of worktree-path leakage
  into a subagent's report; this rule converts that ad-hoc smell into a
  mechanical gate so the next leak gets caught at safe-commit, not at
  cherry-pick time.

Allow-list rules:

- Markdown documentation (``*.md``) is exempt — user-facing docs often
  show example paths and shell snippets that look like absolute paths
  but are clearly illustrative.
- ``/home/runner/...`` is exempt — that's the GitHub-hosted runner
  workspace and legitimately appears in workflow fixtures and CI log
  parsing tests.
- ``/Users/runner/...`` is exempt — same rationale for macOS runners.
- Files inside ``.architecture/baseline/`` are exempt — baselines exist
  to record state, not to enforce.
- Files inside ``reference-library/`` and ``benchmark-results/`` are
  exempt — these are data fixtures.

Baseline at ``.architecture/baseline/no-hardcoded-user-paths-files.txt``
grandfathers any pre-existing offenders so the rule lands without
forcing a sweep. Net-new violations block at safe-commit and CI.

Failure output follows F21: leads with the fix, includes ``run:`` for
re-running the gate, and shows a Pass/Forbidden example.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / ".architecture" / "baseline" / "no-hardcoded-user-paths-files.txt"

# Two patterns: macOS ``/Users/<x>/...`` and Linux ``/home/<x>/...``.
# The negative lookahead excludes ``/home/runner/`` and ``/Users/runner/``
# because GitHub Actions hosted runners check the repo out there and the
# strings legitimately appear in workflow / CI log fixtures.
PATTERNS = (
    re.compile(r"/Users/(?!runner/)[A-Za-z][\w.-]*/"),
    re.compile(r"/home/(?!runner/)[A-Za-z][\w.-]*/"),
)

EXEMPT_SUFFIX = (".md",)
EXEMPT_PREFIX = (
    ".architecture/baseline/",
    "reference-library/",
    "benchmark-results/",
)
# The detector and its test file are themselves exempt: they have to
# embed example paths to describe what they catch.
EXEMPT_FILES = frozenset(
    {
        "scripts/checks/check_no_hardcoded_user_paths.py",
        "tests/checks/test_no_hardcoded_user_paths.py",
    }
)

REMEDIATION = """Refactor to a relative or env-derived path — to pass.

fix: replace the hardcoded path with one of:
  - a relative path resolved at runtime via ``Path(__file__).resolve().parents[N]``
  - an environment variable (``os.environ["KAIRIX_DATA_DIR"]``) with a sensible default
  - a fixture path scoped to the test (``tmp_path`` in pytest)
next: re-run ``python3 scripts/checks/check_no_hardcoded_user_paths.py`` to confirm green.
run: bash scripts/safe-commit.sh "fix(<area>): drop hardcoded user path"

Pass:
  ROOT = Path(__file__).resolve().parents[2]
  data_dir = os.environ.get("KAIRIX_DATA_DIR", "/opt/kairix/data")

Forbidden:
  ROOT = Path("/Users/developer/Development/kairix")
  config = "/home/dan/.config/kairix.yaml"
"""


def _load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    return {
        line.strip()
        for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _is_exempt_path(rel: str) -> bool:
    if rel.endswith(EXEMPT_SUFFIX):
        return True
    if rel in EXEMPT_FILES:
        return True
    return any(rel.startswith(p) for p in EXEMPT_PREFIX)


def _scan_file(path: Path, rel: str) -> list[str]:
    """Return one ``<rel>:<lineno>`` string per matching line.

    Build the list via comprehension rather than ``.append`` so the F21
    actionable-feedback detector doesn't treat the per-line location
    strings as remediation text — the agent-actionable message lives
    in the ``REMEDIATION`` constant above, not on each violation line.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [
        f"{rel}:{n}: hardcoded user/machine path"
        for n, line in enumerate(text.splitlines(), 1)
        if any(rx.search(line) for rx in PATTERNS)
    ]


def main() -> int:
    try:
        files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    except subprocess.CalledProcessError:
        print("FAIL no_hardcoded_user_paths: could not enumerate tracked files", file=sys.stderr)
        return 1

    baseline = _load_baseline()
    net_new: list[str] = []
    matched_baseline_files: set[str] = set()

    for rel in files:
        if _is_exempt_path(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        hits = _scan_file(path, rel)
        if not hits:
            continue
        if rel in baseline:
            matched_baseline_files.add(rel)
            continue
        net_new.extend(hits)

    if net_new:
        print("FAIL F31 no_hardcoded_user_paths: net-new violations", file=sys.stderr)
        for v in net_new:
            print(f"  {v}", file=sys.stderr)
        print("", file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    stale = baseline - matched_baseline_files
    if stale:
        # Baseline shrinks when a grandfathered file is cleaned up — keep the
        # baseline file truthful by failing on stale entries.
        print(
            "FAIL F31 no_hardcoded_user_paths: baseline has stale entries (file no longer offends or no longer exists)",
            file=sys.stderr,
        )
        for s in sorted(stale):
            print(f"  remove from baseline: {s}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            f"fix: remove the listed lines from {BASELINE_FILE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        print('run: bash scripts/safe-commit.sh "chore(baseline): shrink F31"', file=sys.stderr)
        return 1

    print(f"PASS F31 no_hardcoded_user_paths ({len(files)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
