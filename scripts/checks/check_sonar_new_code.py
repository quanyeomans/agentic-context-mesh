"""Sonar per-file count ratchet — deterministic parity against a committed baseline.

CI's SonarCloud gate fires on smells / bugs / vulnerabilities / security
hotspots in the codebase. This script makes that gate reproducible locally
*deterministically*: it compares the project's CURRENT per-file open-issue
counts (read live from SonarCloud's public API) against a COMMITTED baseline
snapshot, and fails any file whose count EXCEEDS its baseline.

Why a committed ratchet (not the live "leak period")
----------------------------------------------------
The previous gate queried Sonar's live new-code "leak period" on ``main``.
That period MUTATES as commits land, so the gate was non-deterministic — two
runs minutes apart could disagree — which made a routine bypass attractive.
The committed baseline removes the flakiness: the pass/fail verdict depends on
the committed snapshot in ``.architecture/baseline/sonar-per-file*.json``, not
on a moving target. There is no skip flag any more because the gate is now
deterministic — there is nothing flaky left to skip.

Two baselines, two policies
---------------------------
  - ``sonar-per-file.json`` — code smells / bugs / vulnerabilities, keyed by
    repo-relative path -> open-issue count. Grandfathers main's existing debt.
  - ``sonar-per-file-hotspots.json`` — security hotspots, split out with a
    STRICTER policy so they ratchet independently (hotspots are
    security-critical; a smell regression must never mask a hotspot
    regression).

A file ABSENT from a baseline defaults to ``0`` -> any open issue/hotspot on a
net-new (or previously-clean) file fails the gate.

Behaviour
---------
  - Default (no flag): focus on the WORKING SET — the files changed in this
    change (staged + unstaged + untracked vs the merge-base with ``main``),
    mirroring how ``scripts/safe-commit.sh`` defines "this change". Only those
    files are gated. This keeps the local loop fast and scoped.
  - ``--all``: explicit full-repo view — every file's current-vs-baseline,
    regardless of what changed. Use this to audit the whole snapshot.
  - ``--capture``: regenerate BOTH baseline JSON files from live Sonar, then
    exit. Run this after main is re-scanned to re-grandfather the current
    state. Handles pagination and anonymous rate-limits gracefully.
  - ``--json``: machine-readable regressions list for agent batches.
  - Exits 1 if any gated file exceeds its baseline; 0 otherwise.

Network failure (e.g. offline pre-commit) prints a warning and exits 0 so
local development isn't blocked by SonarCloud availability. This is the ONLY
remaining "skip" path and it only fires when SonarCloud is genuinely
unreachable — it is not a routine bypass. CI's own ``1 · Quality gate`` job
remains authoritative.

Rule-key -> local-fix recipe map lives in
``docs/architecture/local-first-feedback-loops.md``. When a new Sonar rule
appears, add a recipe row in that doc in the same commit that adds its
detection here.

Reads from environment (overridable):

  - ``SONAR_PROJECT_KEY`` — default ``three-cubes_kairix``
  - ``SONAR_BRANCH``      — default ``main``
  - ``SONAR_API_BASE``    — default ``https://sonarcloud.io/api``
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from os import environ
from pathlib import Path

REMEDIATION = """sonar per-file ratchet: at least one file exceeds its committed baseline.

fix: each `<file>: <current> > <baseline> (+<delta>)` row below is a file whose
open SonarCloud issue (or hotspot) count rose above the committed grandfathered
baseline. Open the file, read the new finding(s) in SonarCloud or via
`python3 scripts/checks/check_sonar_new_code.py --all --json`, and apply the
positive-pattern recipe in docs/architecture/local-first-feedback-loops.md
§Rule map. The ratchet only allows the count to STAY or FALL, never rise.

Pattern: for each row,
  1. find the new finding(s) on that file in SonarCloud,
  2. apply the §Rule-map recipe at the printed file,
  3. re-run this script until clean,
  4. commit once via safe-commit.sh.

next: `python3 scripts/checks/check_sonar_new_code.py --all` — repeat until clean.
run: bash scripts/safe-commit.sh "<message>"

The baseline is committed at .architecture/baseline/sonar-per-file.json (smells
/ bugs / vulnerabilities) and .architecture/baseline/sonar-per-file-hotspots.json
(security hotspots, stricter). After a fix lands on main and SonarCloud
re-scans, regenerate both with
`python3 scripts/checks/check_sonar_new_code.py --capture` to re-grandfather
the lowered counts. The gate is deterministic — there is no skip flag.

Pass example:
  $ python3 scripts/checks/check_sonar_new_code.py --all
  ok [sonar-per-file] — 0 file(s) over baseline (issues), 0 over (hotspots).

Forbidden example:
  # row printed by this script but not addressed in the next commit
  kairix/core/search/rrf.py: 2 > 0 (+2)   issues
  # ... and the developer pushes anyway because "CI is the slow path"."""

DEFAULT_PROJECT_KEY = "three-cubes_kairix"
DEFAULT_BRANCH = "main"
DEFAULT_API_BASE = "https://sonarcloud.io/api"
HTTP_TIMEOUT_S = 10
_PAGE_CEILING = 40  # hard pagination ceiling — anonymous rate-limit safety
_INTER_PAGE_DELAY_S = 0.2  # courtesy delay against anonymous rate limits

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_DIR = _REPO_ROOT / ".architecture" / "baseline"
ISSUES_BASELINE_PATH = _BASELINE_DIR / "sonar-per-file.json"
HOTSPOTS_BASELINE_PATH = _BASELINE_DIR / "sonar-per-file-hotspots.json"


class SonarUnreachableError(Exception):
    """Raised when the SonarCloud public API cannot be reached.

    Callers translate this into the offline-tolerant warn+exit-0 path — the
    ONLY non-failure escape, and only when Sonar is genuinely down.
    """


def _api_get(url: str) -> dict:
    """GET a SonarCloud public-API URL anonymously and parse JSON.

    Raises ``SonarUnreachableError`` on any network-level failure so the
    caller can distinguish "Sonar is down" (warn + exit 0) from "Sonar
    answered and a file regressed" (fail).
    """
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SonarUnreachableError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise SonarUnreachableError(f"unexpected non-object response from {url}")
    return parsed


def _component_to_path(component: str) -> str:
    """``three-cubes_kairix:kairix/x.py`` -> ``kairix/x.py`` (repo-relative)."""
    return component.split(":", 1)[-1]


def fetch_issue_counts(project_key: str, branch: str, api_base: str) -> dict[str, int]:
    """Current open (unresolved) issue counts per repo-relative file.

    Counts code smells / bugs / vulnerabilities — NOT security hotspots
    (those have their own endpoint + stricter baseline). Paginates the
    anonymous public API with a small inter-page courtesy delay.
    """
    counts: dict[str, int] = {}
    page = 1
    page_size = 500
    while True:
        params = urllib.parse.urlencode(
            {
                "componentKeys": project_key,
                "branch": branch,
                "resolved": "false",
                "ps": str(page_size),
                "p": str(page),
            }
        )
        data = _api_get(f"{api_base}/issues/search?{params}")
        batch = data.get("issues", [])
        for issue in batch:
            path = _component_to_path(issue.get("component", "?"))
            counts[path] = counts.get(path, 0) + 1
        total = data.get("total", 0)
        if page * page_size >= total or not batch:
            break
        page += 1
        if page > _PAGE_CEILING:
            break
        time.sleep(_INTER_PAGE_DELAY_S)
    return counts


def fetch_hotspot_counts(project_key: str, branch: str, api_base: str) -> dict[str, int]:
    """Current TO_REVIEW security-hotspot counts per repo-relative file."""
    counts: dict[str, int] = {}
    page = 1
    page_size = 500
    while True:
        params = urllib.parse.urlencode(
            {
                "projectKey": project_key,
                "branch": branch,
                "status": "TO_REVIEW",
                "ps": str(page_size),
                "p": str(page),
            }
        )
        data = _api_get(f"{api_base}/hotspots/search?{params}")
        batch = data.get("hotspots", [])
        for h in batch:
            path = _component_to_path(h.get("component", "?"))
            counts[path] = counts.get(path, 0) + 1
        total = data.get("paging", {}).get("total", 0)
        if page * page_size >= total or not batch:
            break
        page += 1
        if page > _PAGE_CEILING:
            break
        time.sleep(_INTER_PAGE_DELAY_S)
    return counts


def load_baseline(path: Path) -> dict[str, int]:
    """Load a committed per-file baseline; ``{}`` if the file is absent.

    The JSON has a ``"_meta"`` provenance header and a ``"files"`` mapping;
    a missing file (first run before capture) is treated as an all-zero
    baseline so every issue fails — the strictest safe default.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    files = raw.get("files", {})
    return {str(k): int(v) for k, v in files.items()}


def compute_regressions(
    current: dict[str, int],
    baseline: dict[str, int],
    files_in_scope: set[str] | None,
) -> list[tuple[str, int, int]]:
    """Files whose current count exceeds baseline (default 0).

    Returns ``[(path, current, baseline), ...]`` sorted by path. When
    ``files_in_scope`` is given (working-set mode), only those files are
    considered; ``None`` means full-repo (``--all``).
    """
    regressions: list[tuple[str, int, int]] = []
    for path, cur in current.items():
        if files_in_scope is not None and path not in files_in_scope:
            continue
        base = baseline.get(path, 0)
        if cur > base:
            regressions.append((path, cur, base))
    return sorted(regressions)


def _git_lines(args: list[str]) -> list[str]:
    """Run a read-only git command, return non-empty stdout lines (or [])."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def working_set_files() -> set[str]:
    """Repo-relative paths changed in THIS change — mirror safe-commit's set.

    Union of:
      - diff vs the merge-base with ``origin/main`` (or ``main`` if the
        remote ref is absent),
      - unstaged + staged working-tree diffs,
      - untracked files.

    Falls back to the plain working-tree diff if no merge-base is resolvable
    (e.g. shallow checkout). Empty set means nothing changed.
    """
    base_ref = None
    for ref in ("origin/main", "main"):
        mb = _git_lines(["merge-base", "HEAD", ref])
        if mb:
            base_ref = mb[0]
            break
    files: set[str] = set()
    if base_ref:
        files.update(_git_lines(["diff", "--name-only", base_ref]))
    else:
        files.update(_git_lines(["diff", "--name-only", "HEAD"]))
    files.update(_git_lines(["diff", "--name-only"]))  # unstaged
    files.update(_git_lines(["diff", "--name-only", "--cached"]))  # staged
    files.update(_git_lines(["ls-files", "--others", "--exclude-standard"]))  # untracked
    return {f for f in files if f}


def _meta_header(project_key: str, branch: str, kind: str) -> dict:
    """Provenance header recording how/when the snapshot was captured."""
    return {
        "_meta": {
            "project_key": project_key,
            "branch": branch,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "snapshot": (
                f"ALL current open {kind} per file (unresolved), NOT the mutating "
                "new-code leak period, so this baseline is stable/deterministic."
            ),
            "generator": "scripts/checks/check_sonar_new_code.py --capture",
        }
    }


def _write_baseline(path: Path, header: dict, counts: dict[str, int]) -> None:
    doc = {**header, "files": dict(sorted(counts.items()))}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def capture_baselines(project_key: str, branch: str, api_base: str) -> int:
    """Regenerate both baseline JSON files from live Sonar.

    Returns 0 on success; warns + returns 0 if Sonar is unreachable (so a
    capture run never wedges an offline developer).
    """
    try:
        issue_counts = fetch_issue_counts(project_key, branch, api_base)
        hotspot_counts = fetch_hotspot_counts(project_key, branch, api_base)
    except SonarUnreachableError as exc:
        print(f"warn: SonarCloud API unreachable ({exc}); cannot capture baseline.", file=sys.stderr)
        print("next: re-run when SonarCloud is reachable.", file=sys.stderr)
        return 0

    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    _write_baseline(
        ISSUES_BASELINE_PATH,
        _meta_header(project_key, branch, "issues (smells/bugs/vulnerabilities)"),
        issue_counts,
    )
    _write_baseline(
        HOTSPOTS_BASELINE_PATH,
        _meta_header(project_key, branch, "security hotspots"),
        hotspot_counts,
    )

    issues_rel = ISSUES_BASELINE_PATH.relative_to(_REPO_ROOT)
    hotspots_rel = HOTSPOTS_BASELINE_PATH.relative_to(_REPO_ROOT)
    print(f"captured {len(issue_counts)} file(s) / {sum(issue_counts.values())} issue(s) -> {issues_rel}")
    print(f"captured {len(hotspot_counts)} file(s) / {sum(hotspot_counts.values())} hotspot(s) -> {hotspots_rel}")
    return 0


def evaluate(
    issue_counts: dict[str, int],
    hotspot_counts: dict[str, int],
    issue_baseline: dict[str, int],
    hotspot_baseline: dict[str, int],
    files_in_scope: set[str] | None,
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Pure verdict core: ``(issue_regressions, hotspot_regressions)``.

    Split so tests can inject fake count maps + baselines and assert the
    ratchet verdict without touching the network.
    """
    issue_regressions = compute_regressions(issue_counts, issue_baseline, files_in_scope)
    hotspot_regressions = compute_regressions(hotspot_counts, hotspot_baseline, files_in_scope)
    return issue_regressions, hotspot_regressions


def _print_regressions(
    issue_regressions: list[tuple[str, int, int]],
    hotspot_regressions: list[tuple[str, int, int]],
) -> None:
    print(
        f"sonar per-file ratchet FAILED — {len(issue_regressions)} file(s) over the issues "
        f"baseline, {len(hotspot_regressions)} over the hotspots baseline:"
    )
    for path, cur, base in issue_regressions:
        print(f"  {path}: {cur} > {base} (+{cur - base})   issues")
    for path, cur, base in hotspot_regressions:
        print(f"  {path}: {cur} > {base} (+{cur - base})   hotspots")
    print()
    print(REMEDIATION)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit regressions as JSON")
    parser.add_argument(
        "--all",
        action="store_true",
        help="full-repo view (every file current-vs-baseline); default focuses on the working set",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="regenerate both committed baselines from live Sonar, then exit",
    )
    parser.add_argument("--project-key", default=environ.get("SONAR_PROJECT_KEY", DEFAULT_PROJECT_KEY))
    parser.add_argument("--branch", default=environ.get("SONAR_BRANCH", DEFAULT_BRANCH))
    parser.add_argument("--api-base", default=environ.get("SONAR_API_BASE", DEFAULT_API_BASE))
    args = parser.parse_args(argv)

    if args.capture:
        return capture_baselines(args.project_key, args.branch, args.api_base)

    try:
        issue_counts = fetch_issue_counts(args.project_key, args.branch, args.api_base)
        hotspot_counts = fetch_hotspot_counts(args.project_key, args.branch, args.api_base)
    except SonarUnreachableError as exc:
        # The ONLY remaining skip path — fires only when Sonar is genuinely
        # down, never as a routine bypass. CI's quality gate stays authoritative.
        print(f"warn: SonarCloud API unreachable ({exc}); skipping local parity check.", file=sys.stderr)
        print("next: CI's `1 · Quality gate` remains authoritative.", file=sys.stderr)
        return 0

    issue_baseline = load_baseline(ISSUES_BASELINE_PATH)
    hotspot_baseline = load_baseline(HOTSPOTS_BASELINE_PATH)
    files_in_scope = None if args.all else working_set_files()

    issue_regressions, hotspot_regressions = evaluate(
        issue_counts,
        hotspot_counts,
        issue_baseline,
        hotspot_baseline,
        files_in_scope,
    )

    if args.json:
        json.dump(
            {
                "scope": "all" if args.all else "working-set",
                "issue_regressions": [{"file": p, "current": c, "baseline": b} for p, c, b in issue_regressions],
                "hotspot_regressions": [{"file": p, "current": c, "baseline": b} for p, c, b in hotspot_regressions],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 1 if (issue_regressions or hotspot_regressions) else 0

    if not issue_regressions and not hotspot_regressions:
        scope = "full-repo" if args.all else "working-set"
        print(
            f"sonar per-file ratchet: 0 file(s) over baseline ({scope} scope) — "
            "issues and hotspots within committed grandfathered counts."
        )
        return 0

    _print_regressions(issue_regressions, hotspot_regressions)
    return 1


if __name__ == "__main__":
    sys.exit(main())
