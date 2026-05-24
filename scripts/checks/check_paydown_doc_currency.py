"""KFEAT-018: grandfathering paydown doc snapshot currency gate.

`docs/architecture/grandfathering-paydown.md` is the source-of-truth for
which fitness-function baselines have entries and what shape the
paydown plan takes for each. Its `## State (as of <date>, post-<sha>)`
header advertises when the snapshot was taken. This check makes that
advertised date load-bearing.

Algorithm:

  1. Parse the snapshot header from
     ``docs/architecture/grandfathering-paydown.md`` —
     ``## State (as of YYYY-MM-DD, post-<sha>)``.
  2. Resolve the most recent release tag matching
     ``v[0-9]*.[0-9]*.[0-9]*`` (excluding alpha tags) via
     ``git for-each-ref``.
  3. If the snapshot date is within 7 days of the tag date, pass.
  4. Otherwise, look for an
     ``<!-- expected-out-of-date-until: YYYY-MM-DD -->`` comment
     anywhere in the doc. If present AND its date is in the future,
     pass with a notice.
  5. Otherwise, exit 1 with an F21-shape affordance.

Runs in ``release.yml`` before the tag-cut step (mirrors
``check_baseline_shrinking.py``'s placement). Intentionally does NOT
run per-commit — between commits within a release window the snapshot
doesn't need to be re-stamped.

Exit code: 0 when snapshot is fresh OR a forward-dated extension
comment is present; 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_REL_PATH = "docs/architecture/grandfathering-paydown.md"

# `## State (as of 2026-05-24, post-37964593)` — captures the date.
_SNAPSHOT_HEADER_RE = re.compile(
    r"^##\s+State\s+\(as of\s+(\d{4}-\d{2}-\d{2}),\s+post-[0-9a-f]+\)\s*$",
    re.MULTILINE,
)

# `<!-- expected-out-of-date-until: 2026-06-15 -->` — rationale escape.
_EXTENSION_RE = re.compile(
    r"<!--\s*expected-out-of-date-until:\s*(\d{4}-\d{2}-\d{2})\s*-->",
)

# Freshness window relative to the most recent release tag.
_FRESHNESS_WINDOW_DAYS = 7

# Release-tag pattern excluding alpha tags. We rely on `for-each-ref`
# sorted by tag creation date (descending) so the first match is the
# most recent stable release.
_RELEASE_TAG_GLOB = "refs/tags/v[0-9]*.[0-9]*.[0-9]*"
# Excludes alpha tags such as `v2026.5.23a1` — the digit-only segment
# guard means anything containing `a<n>` after the patch component is
# filtered post-hoc.
_RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(\.\d+)?$")


@dataclass(frozen=True)
class _Snapshot:
    """Parsed snapshot header."""

    snapshot_date: date


@dataclass(frozen=True)
class _ReleaseTag:
    """Resolved most-recent release tag + its commit date."""

    tag: str
    tag_date: date


REMEDIATION = """paydown doc currency: snapshot dated {snapshot_date}; most recent release tag was
{tag_date} ({tag_name}); difference {diff_days} days exceeds 7-day threshold.

fix: re-run `wc -l .architecture/baseline/*.txt` and refresh the State table in
     docs/architecture/grandfathering-paydown.md; bump the snapshot date to today.
next: bash scripts/safe-commit.sh "docs: refresh paydown plan snapshot"
run:  python3 scripts/checks/check_paydown_doc_currency.py  # to confirm green

Alternatively, if the staleness is deliberate and time-boxed, add a
comment of the form `<!-- expected-out-of-date-until: YYYY-MM-DD -->`
immediately below the snapshot header with a rationale, e.g.

  <!-- expected-out-of-date-until: 2026-06-15 -->
  Snapshot kept until KFEAT-019 paydown wave lands; refresh together
  with that PR.

See docs/features/KFEAT-018-paydown-doc-refresh/BRIEF.md for context."""


def _parse_snapshot(doc_text: str) -> _Snapshot | None:
    """Parse the ``## State (as of YYYY-MM-DD, post-<sha>)`` header.

    Returns None if the header is absent or malformed (caller treats as
    failure with a dedicated remediation).
    """
    match = _SNAPSHOT_HEADER_RE.search(doc_text)
    if match is None:
        return None
    try:
        snapshot_date = date.fromisoformat(match.group(1))
    except ValueError:
        return None
    return _Snapshot(snapshot_date=snapshot_date)


def _parse_extension(doc_text: str) -> date | None:
    """Parse the ``<!-- expected-out-of-date-until: ... -->`` escape comment."""
    match = _EXTENSION_RE.search(doc_text)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _resolve_most_recent_release_tag(repo_root: Path) -> _ReleaseTag | None:
    """Return the most recent stable release tag + its commit date.

    Lists tags matching ``v[0-9]*.[0-9]*.[0-9]*`` sorted by creator
    date descending, filters out alpha tags (``v...a<n>``), and resolves
    the first match's commit date via ``git log -1 --format=%cs``.
    Returns None if no release tag exists.
    """
    result = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short) %(creatordate:short)",
            _RELEASE_TAG_GLOB,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        tag_name, tag_date_str = parts
        if not _RELEASE_TAG_RE.match(tag_name):
            continue
        try:
            tag_date = date.fromisoformat(tag_date_str)
        except ValueError:
            continue
        return _ReleaseTag(tag=tag_name, tag_date=tag_date)
    return None


def check_currency(
    repo_root: Path,
    *,
    today: date | None = None,
) -> tuple[int, list[str]]:
    """Run the snapshot-currency check.

    Args:
        repo_root: repo root path.
        today: override the date used to compare extension comments
            against (test seam — not used for the snapshot↔tag delta,
            which is intentionally tag-relative).

    Returns:
        (exit_code, lines) — exit_code is 0 on pass, 1 on fail; lines
        is the list of output lines (so callers can print or capture).
    """
    if today is None:
        today = date.today()
    lines: list[str] = []

    doc_path = repo_root / DOC_REL_PATH
    if not doc_path.exists():
        lines.append(f"paydown doc currency: cannot find {DOC_REL_PATH} at {doc_path}.")
        lines.append("")
        lines.append("fix: confirm the repo layout — this check only runs from a kairix checkout.")
        lines.append("next: bash scripts/safe-commit.sh")
        lines.append("run:  python3 scripts/checks/check_paydown_doc_currency.py")
        return 1, lines

    doc_text = doc_path.read_text(encoding="utf-8")

    snapshot = _parse_snapshot(doc_text)
    if snapshot is None:
        lines.append(
            f"paydown doc currency: cannot parse '## State (as of YYYY-MM-DD, post-<sha>)' header in {DOC_REL_PATH}."
        )
        lines.append("")
        lines.append(
            "fix: ensure the doc contains exactly one header matching '## State (as of YYYY-MM-DD, post-<sha>)'."
        )
        lines.append('next: bash scripts/safe-commit.sh "docs: refresh paydown plan snapshot"')
        lines.append("run:  python3 scripts/checks/check_paydown_doc_currency.py")
        return 1, lines

    tag = _resolve_most_recent_release_tag(repo_root)
    if tag is None:
        lines.append(
            "paydown doc currency: no release tag matching 'v[0-9]*.[0-9]*.[0-9]*' "
            "found in this repo; skipping freshness comparison."
        )
        return 0, lines

    diff_days = abs((snapshot.snapshot_date - tag.tag_date).days)
    lines.append(
        f"paydown doc currency: snapshot {snapshot.snapshot_date} vs most recent "
        f"release tag {tag.tag} dated {tag.tag_date} (delta {diff_days} days)."
    )

    if diff_days <= _FRESHNESS_WINDOW_DAYS:
        lines.append(f"paydown doc currency: within {_FRESHNESS_WINDOW_DAYS}-day window. OK.")
        return 0, lines

    # Outside the window — check for an extension comment.
    extension_until = _parse_extension(doc_text)
    if extension_until is not None and extension_until >= today:
        lines.append(
            f"paydown doc currency: snapshot is stale ({diff_days} days) but doc "
            f"carries 'expected-out-of-date-until: {extension_until}' (today: {today}). OK."
        )
        return 0, lines

    if extension_until is not None and extension_until < today:
        lines.append(
            f"paydown doc currency: snapshot is stale ({diff_days} days) and the "
            f"extension comment 'expected-out-of-date-until: {extension_until}' "
            f"expired on {today}."
        )
        lines.append("")

    lines.append("")
    lines.append(
        REMEDIATION.format(
            snapshot_date=snapshot.snapshot_date,
            tag_date=tag.tag_date,
            tag_name=tag.tag,
            diff_days=diff_days,
        )
    )
    return 1, lines


def main() -> int:
    exit_code, lines = check_currency(REPO_ROOT)
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
