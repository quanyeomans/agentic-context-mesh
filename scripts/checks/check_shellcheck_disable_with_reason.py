"""F33: ``# shellcheck disable=<rule>`` directives require a rationale.

Shell counterpart to F3 (which covers Python ``# noqa``, ``# type: ignore``,
``# nosec``, ``# NOSONAR``, and ``# pragma: no cover``). Cross-pollinated
from tc-agent-zone's ``shellcheck_disable_with_reason.py``.

Why this rule exists:

- A bare ``# shellcheck disable=SC2034`` is a silent override — six months
  later nobody knows whether the disable is still load-bearing or whether
  the underlying warning has become a real bug.
- An inline rationale (or one on the immediately preceding ``#`` comment
  line) documents WHY the rule doesn't apply, so the next reader can
  decide whether to keep, remove, or rewrite the disable.

What counts as a rationale:

- A free-text comment longer than ~10 chars on the same line, after the
  directive: ``# shellcheck disable=SC2034  # safe -- array used in subshell``
- The immediately preceding non-blank line is a ``#``-comment with a
  non-trivial body (≥ ~10 chars and not just a copy of the directive).
- A canonical marker prefix on either line: ``# why:``, ``# fix:``,
  ``# next:``, ``# run:``, ``# rationale:``, ``# reason:``, ``# because:``.

Scope:

- Every tracked file whose name ends in ``.sh`` OR whose first line is a
  ``#!`` shebang naming ``bash`` or ``sh``.
- Files inside ``.architecture/baseline/``, ``reference-library/``, and
  ``benchmark-results/`` are exempt.
- The detector and its test (which embed example disables in docstrings)
  are self-exempt.

Baseline at ``.architecture/baseline/shellcheck-disable-with-reason-files.txt``
grandfathers any pre-existing offenders so the rule lands without forcing
a sweep. Net-new violations block at safe-commit and CI.

Failure output follows F21: leads with the fix, includes ``run:`` for
re-running the gate, and shows a Pass/Forbidden example.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / ".architecture" / "baseline" / "shellcheck-disable-with-reason-files.txt"

# Matches: ``# shellcheck disable=SC2034`` and ``# shellcheck disable=SC2034,SC2046``.
# Captures everything after the directive on the same line so the caller
# can inspect for an inline rationale.
DISABLE_RE = re.compile(r"#\s*shellcheck\s+disable=(?P<rules>[A-Za-z0-9,]+)(?P<trailing>.*)$")

# Canonical marker prefixes that count as rationale even when the trailing
# text is short. Mirrors F21's ``fix:`` / ``next:`` / ``run:`` set, plus
# the F3-style explanatory prefixes that humans tend to reach for.
RATIONALE_MARKERS: tuple[str, ...] = (
    "fix:",
    "next:",
    "run:",
    "why:",
    "rationale:",
    "reason:",
    "because:",
)

# Minimum length of a free-text rationale (in chars after stripping the
# leading ``#`` and whitespace). Below this we treat the comment as a
# stub (e.g. ``# ok``) that doesn't actually justify the disable.
MIN_RATIONALE_LEN = 10

EXEMPT_PREFIX = (
    ".architecture/baseline/",
    "reference-library/",
    "benchmark-results/",
)
# The detector and its test embed example disable lines in docstrings;
# self-exempt so the rule doesn't flag its own dogfood examples.
EXEMPT_FILES = frozenset(
    {
        "scripts/checks/check_shellcheck_disable_with_reason.py",
        "tests/checks/test_shellcheck_disable_with_reason.py",
    }
)

# Shebangs that mark a file as a shell script (no ``.sh`` extension).
_SHEBANG_RE = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?(?:ba)?sh\b")

REMEDIATION = """Refactor to add a rationale to each shellcheck disable -- to pass.

fix: add an inline comment after the directive that explains WHY the
rule doesn't apply (everything after the directive counts; an em-dash +
one-line justification is the canonical shape), OR put the rationale on
the immediately preceding ``#`` comment line. Markers ``fix:``,
``next:``, ``run:``, ``why:``, ``rationale:``, ``reason:``, ``because:``
are recognised as canonical rationale prefixes.
next: re-run ``python3 scripts/checks/check_shellcheck_disable_with_reason.py``
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "chore(<area>): document shellcheck disable rationale"

Pass:
  # safe -- sourced path is computed from a controlled config var
  # shellcheck disable=SC1090
  . "$SECRETS_FILE"

  # shellcheck disable=SC2034  # exported via process substitution below

Forbidden:
  # shellcheck disable=SC1090
  . "$SECRETS_FILE"
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
    if rel in EXEMPT_FILES:
        return True
    return any(rel.startswith(p) for p in EXEMPT_PREFIX)


def _is_shell_file(path: Path, rel: str) -> bool:
    """A file qualifies for F33 scanning if its name ends in ``.sh`` OR
    its first line is a recognised shell shebang."""
    if rel.endswith(".sh"):
        return True
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            first = fh.readline()
    except OSError:
        return False
    return bool(_SHEBANG_RE.match(first))


def _is_rationale_comment(line: str) -> bool:
    """True iff ``line`` (a previously-stripped string) is a ``#``-comment
    whose body is either a canonical marker or long enough to count as a
    free-text justification.
    """
    if not line.startswith("#"):
        return False
    # A shebang line (``#!/usr/bin/env bash`` etc.) is not a rationale.
    if line.startswith("#!"):
        return False
    body = line.lstrip("#").strip()
    if not body:
        return False
    lowered = body.lower()
    if any(marker in lowered for marker in RATIONALE_MARKERS):
        return True
    # The body must not itself be a shellcheck disable copy-paste.
    if "shellcheck" in lowered and "disable" in lowered:
        return False
    return len(body) >= MIN_RATIONALE_LEN


def _trailing_has_rationale(trailing: str) -> bool:
    """Inspect everything on the same line after ``disable=<rules>`` for
    an inline rationale. Whitespace and a comma-list continuation don't
    count; only a ``#``-led comment or a non-whitespace tail does.
    """
    tail = trailing.strip()
    if not tail:
        return False
    # ``# rationale here`` — strip the leading ``#`` and treat as a body.
    if tail.startswith("#"):
        return _is_rationale_comment(tail)
    # Anything else on the line after the directive is unusual but
    # counts as rationale if it's substantive (e.g. an em-dash and a
    # sentence). We're deliberately generous here — the failure mode
    # we want to catch is the bare directive with NOTHING after it.
    lowered = tail.lower()
    if any(marker in lowered for marker in RATIONALE_MARKERS):
        return True
    return len(tail) >= MIN_RATIONALE_LEN


def _scan_file(path: Path, rel: str) -> list[str]:
    """Return one ``<rel>:<lineno>`` string per disable directive that
    lacks a rationale on the same line or on the preceding ``#`` line.

    Build the list via comprehension (no ``.append``) so the F21
    actionable-feedback detector doesn't treat per-line location strings
    as remediation text -- the agent-actionable message lives in the
    ``REMEDIATION`` constant above, not on each violation line.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    lines = text.splitlines()
    return [
        f"{rel}:{idx + 1}: shellcheck disable without rationale"
        for idx, line in enumerate(lines)
        if (m := DISABLE_RE.search(line)) is not None
        and not _trailing_has_rationale(m.group("trailing"))
        and not _preceding_line_has_rationale(lines, idx)
    ]


def _preceding_line_has_rationale(lines: list[str], idx: int) -> bool:
    """Walk backward from ``idx`` to the previous non-blank line; if that
    line is a ``#``-comment with a substantive body, accept it as the
    rationale for the disable on ``idx``.
    """
    j = idx - 1
    while j >= 0:
        prev = lines[j].strip()
        if prev:
            break
        j -= 1
    if j < 0:
        return False
    return _is_rationale_comment(lines[j].strip())


def main() -> int:
    try:
        files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    except subprocess.CalledProcessError:
        print("FAIL shellcheck_disable_with_reason: could not enumerate tracked files", file=sys.stderr)
        return 1

    baseline = _load_baseline()
    net_new: list[str] = []
    matched_baseline_files: set[str] = set()
    shell_file_count = 0

    for rel in files:
        if _is_exempt_path(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        if not _is_shell_file(path, rel):
            continue
        shell_file_count += 1
        hits = _scan_file(path, rel)
        if not hits:
            continue
        if rel in baseline:
            matched_baseline_files.add(rel)
            continue
        net_new.extend(hits)

    if net_new:
        print("FAIL F33 shellcheck_disable_with_reason: net-new violations", file=sys.stderr)
        for v in net_new:
            print(f"  {v}", file=sys.stderr)
        print("", file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    stale = baseline - matched_baseline_files
    if stale:
        # Baseline shrinks when a grandfathered file is cleaned up — keep
        # the baseline file truthful by failing on stale entries.
        print(
            "FAIL F33 shellcheck_disable_with_reason: baseline has stale "
            "entries (file no longer offends or no longer exists)",
            file=sys.stderr,
        )
        for s in sorted(stale):
            print(f"  remove from baseline: {s}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            f"fix: remove the listed lines from {BASELINE_FILE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        print('run: bash scripts/safe-commit.sh "chore(baseline): shrink F33"', file=sys.stderr)
        return 1

    print(f"PASS F33 shellcheck_disable_with_reason ({shell_file_count} shell files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
