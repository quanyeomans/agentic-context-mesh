"""Sonar new-code parity check — pull the gate's set of net-new findings.

CI's SonarCloud gate fires on smells / hotspots introduced in the current
new-code period (Sonar's "leak period"). This script reads the same set
locally so a developer or agent can fix the full batch in one commit
instead of discovering issues one-per-push.

Behaviour:

  - Queries the SonarCloud public API for the configured project key,
    branch, and new-code period. No token; the project is publicly
    readable. Anonymous queries are rate-limited per IP but fine for a
    pre-commit cadence.
  - Prints one row per finding: ``RULE  SEVERITY  FILE:LINE  MESSAGE``.
  - With ``--json``, prints a machine-readable list suitable for piping
    into an agent batch.
  - Exits 1 if any net-new finding remains; 0 otherwise.
  - Honours ``KAIRIX_SKIP_SONAR_PARITY=1`` to skip during focused
    refactor series (CI still enforces).

Rule-key → local-fix recipe map lives in
``docs/architecture/local-first-feedback-loops.md``. When a new Sonar
rule appears, add a recipe row in that doc in the same commit that adds
its detection here.

Reads from environment (overridable):

  - ``SONAR_PROJECT_KEY`` — default ``three-cubes_kairix``
  - ``SONAR_BRANCH``      — default ``main``
  - ``SONAR_API_BASE``    — default ``https://sonarcloud.io/api``

Network failure (e.g. offline pre-commit) prints a warning and exits 0
so local development isn't blocked by SonarCloud availability. CI's
own ``1 · Quality gate`` job remains authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REMEDIATION = """sonar new-code parity: at least one net-new SonarCloud issue or hotspot remains.

fix: read the rule key + file:line printed below and apply the local recipe from
docs/architecture/local-first-feedback-loops.md §Rule map; if the rule has no
recipe yet, add one in the same commit that fixes the issue.

next: re-run `python3 scripts/checks/check_sonar_new_code.py` until it prints
`0 new-code issues, 0 new-code hotspots`.

run: bash scripts/safe-commit.sh "<message>"

KAIRIX_SKIP_SONAR_PARITY=1 skips this gate locally for a focused refactor
series; CI's `1 · Quality gate` job remains authoritative."""

DEFAULT_PROJECT_KEY = "three-cubes_kairix"
DEFAULT_BRANCH = "main"
DEFAULT_API_BASE = "https://sonarcloud.io/api"
HTTP_TIMEOUT_S = 10

# fix-recipe hints keyed by Sonar rule. Keep terse; the canonical recipe
# table is in docs/architecture/local-first-feedback-loops.md.
FIX_HINTS: dict[str, str] = {
    "python:S3776": "fix: extract the deepest nested construct into a helper; sabotage-prove it.",
    "python:S5886": (
        "fix: construct the dataclass explicitly; don't use dataclasses.replace "
        "on an annotated `-> SomeDataclass` return."
    ),
    "python:S5890": "fix: don't annotate the local assignment — construct the dataclass directly.",
    "python:S7504": "fix: drop the list() wrapper; the upstream is already iterable.",
    "python:S5869": "fix: collapse the regex character class (e.g. [a-z]+ not [a-z][a-z]*).",
    "python:S6353": "fix: simplify the regex character-class repetition.",
    "python:S6792": "fix: use PEP 695 `type` parameter syntax instead of TypeVar.",
    "python:S6796": "fix: use a PEP 695 generic parameter instead of declaring a TypeVar.",
    "python:S5727": "fix: remove the identity check — the type is already narrowed.",
    "python:S5754": "fix: re-raise with `raise ... from <cause>` or narrow the except.",
    "python:S1186": "fix: add a one-line `# Intentionally empty — <why>` or a docstring.",
    "python:S5655": "fix: match the call-site argument type to the function signature.",
    "python:S3358": "fix: extract the nested conditional into an independent statement.",
    "docker:S7031": "fix: merge consecutive RUN instructions with &&.",
}

DEFAULT_HINT = "fix: see docs/architecture/local-first-feedback-loops.md §Rule map; add a recipe row if missing."


def _api_get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"warn: SonarCloud API unreachable ({exc}); skipping local parity check.", file=sys.stderr)
        print("next: CI's `1 · Quality gate` remains authoritative.", file=sys.stderr)
        return None


def _fetch_new_code_issues(project_key: str, branch: str, api_base: str) -> list[dict] | None:
    issues: list[dict] = []
    page = 1
    page_size = 100
    while True:
        params = urllib.parse.urlencode(
            {
                "componentKeys": project_key,
                "branch": branch,
                "sinceLeakPeriod": "true",
                "resolved": "false",
                "ps": str(page_size),
                "p": str(page),
            }
        )
        data = _api_get(f"{api_base}/issues/search?{params}")
        if data is None:
            return None
        batch = data.get("issues", [])
        issues.extend(batch)
        total = data.get("total", 0)
        if page * page_size >= total or not batch:
            break
        page += 1
        if page > 10:
            break
    return issues


def _fetch_new_code_hotspots(project_key: str, branch: str, api_base: str) -> list[dict] | None:
    params = urllib.parse.urlencode(
        {
            "projectKey": project_key,
            "branch": branch,
            "status": "TO_REVIEW",
            "inNewCodePeriod": "true",
            "ps": "100",
        }
    )
    data = _api_get(f"{api_base}/hotspots/search?{params}")
    if data is None:
        return None
    return data.get("hotspots", [])


def _fmt_issue_row(issue: dict) -> str:
    rule = issue.get("rule", "?")
    severity = issue.get("severity", "?")
    component = issue.get("component", "?").split(":", 1)[-1]
    tr = issue.get("textRange") or {}
    line = tr.get("startLine", "?")
    msg = (issue.get("message") or "")[:140]
    hint = FIX_HINTS.get(rule, DEFAULT_HINT)
    return f"  {rule:18s}  {severity:9s}  {component}:{line}\n      {msg}\n      {hint}"


def _fmt_hotspot_row(h: dict) -> str:
    component = h.get("component", "?").split(":", 1)[-1]
    line = h.get("line", "?")
    msg = (h.get("message") or "")[:140]
    prob = h.get("vulnerabilityProbability", "?")
    hint = "fix: refactor the construct or review in SonarCloud and mark Safe with rationale."
    return f"  hotspot ({prob}): {component}:{line}\n      {msg}\n      {hint}"


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("KAIRIX_SKIP_SONAR_PARITY") == "1":
        print("sonar new-code parity skipped via KAIRIX_SKIP_SONAR_PARITY=1")
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit issues + hotspots as JSON")
    parser.add_argument("--project-key", default=os.environ.get("SONAR_PROJECT_KEY", DEFAULT_PROJECT_KEY))
    parser.add_argument("--branch", default=os.environ.get("SONAR_BRANCH", DEFAULT_BRANCH))
    parser.add_argument("--api-base", default=os.environ.get("SONAR_API_BASE", DEFAULT_API_BASE))
    args = parser.parse_args(argv)

    issues = _fetch_new_code_issues(args.project_key, args.branch, args.api_base)
    hotspots = _fetch_new_code_hotspots(args.project_key, args.branch, args.api_base)
    if issues is None or hotspots is None:
        return 0  # offline / unreachable; CI remains authoritative

    if args.json:
        json.dump({"issues": issues, "hotspots": hotspots}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if (issues or hotspots) else 0

    if not issues and not hotspots:
        print("sonar new-code parity: 0 new-code issues, 0 new-code hotspots.")
        return 0

    print(f"sonar new-code parity FAILED — {len(issues)} issue(s), {len(hotspots)} hotspot(s):")
    for issue in issues:
        print(_fmt_issue_row(issue))
    for h in hotspots:
        print(_fmt_hotspot_row(h))
    print()
    print("next: batch-fix all of the above locally; re-run safe-commit; push once.")
    print("run: python3 scripts/checks/check_sonar_new_code.py --json  # for agent dispatch")
    print("docs: docs/architecture/local-first-feedback-loops.md §Rule map")
    return 1


if __name__ == "__main__":
    sys.exit(main())
