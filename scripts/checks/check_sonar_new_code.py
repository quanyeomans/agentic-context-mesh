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

fix: read each `<rule>  <severity>  <file>:<line>` row below; apply the
positive-pattern recipe shown on the line directly underneath it (or in
docs/architecture/local-first-feedback-loops.md §Rule map for the canonical
shape). Recipes are copy-paste-adapt — the agent reads the recipe and writes
that exact shape into the named file.

Pattern: for each row,
  1. read the recipe (the `fix: ...` line directly under the row),
  2. apply it at the printed `file:line`,
  3. re-run this script until 0/0,
  4. commit once via safe-commit.sh.

next: `python3 scripts/checks/check_sonar_new_code.py` — repeat until clean.
run: bash scripts/safe-commit.sh "<message>"

If a rule has no recipe row in the §Rule map, add the recipe in the same
commit that fixes the issue. KAIRIX_SKIP_SONAR_PARITY=1 skips this gate
locally for a focused refactor series; CI's `1 · Quality gate` remains
authoritative."""

DEFAULT_PROJECT_KEY = "three-cubes_kairix"
DEFAULT_BRANCH = "main"
DEFAULT_API_BASE = "https://sonarcloud.io/api"
HTTP_TIMEOUT_S = 10

# fix-recipe hints keyed by Sonar rule. Keep terse; the canonical recipe
# table is in docs/architecture/local-first-feedback-loops.md.
# Each hint shows the positive pattern as a code shape, so the agent
# reading the failure can apply it directly without re-deriving the
# pattern from a description. Canonical references in
# docs/architecture/local-first-feedback-loops.md §Rule map.
FIX_HINTS: dict[str, str] = {
    "python:S3776": (
        "fix: hoist the inner-most for/if block into a `_helper(...)` function; "
        "call it from the outer loop. Example: `_mark_existing_vec_hit` in "
        "`kairix/core/search/rrf.py` (replaced a 3-deep nested for/if/if)."
    ),
    "python:S5886": (
        "fix: `return SomeDataclass(field=new_value, other=x.other, third=x.third)` "
        "— construct explicitly with field-by-field copy. Example: "
        "`_replace_document_root` in `kairix/knowledge/wikilinks/cli.py`."
    ),
    "python:S5890": (
        "fix: drop the local annotation and construct the dataclass directly: "
        "`return SomeDataclass(field=v, other=x.other, ...)`."
    ),
    "python:S7504": "fix: `for x in seq:` — drop the list() wrapper around an already-iterable seq.",
    "python:S5869": "fix: `[a-z]+` — collapse `[a-z][a-z]*` and remove any duplicate class members.",
    "python:S6353": "fix: `[a-z]+` — replace `[a-z][a-z]*` style repetition with the `+` quantifier.",
    "python:S6792": "fix: `class C[T]:` — use PEP 695 generic syntax instead of `TypeVar('T'); class C(Generic[T])`.",
    "python:S6796": "fix: `def f[T](x: T) -> T:` — declare type parameters PEP 695-style on the def.",
    "python:S5727": "fix: remove the redundant check; use the variable directly (the type is already narrowed).",
    "python:S5754": "fix: `raise NewError(...) from exc` — chain the cause instead of bare-re-raising a broad except.",
    "python:S1186": "fix: `# Intentionally empty — <reason>` or a one-line docstring inside the body.",
    "python:S5655": "fix: convert the argument at the call site to the type the signature declares.",
    "python:S3358": (
        "fix: `tmp = X if cond else Y; result = f(tmp)` — hoist the inner ternary "
        "into a named statement before the outer use."
    ),
    "docker:S7031": "fix: `RUN a && b && c` — merge consecutive RUN instructions into one layer.",
}

DEFAULT_HINT = (
    "fix: see docs/architecture/local-first-feedback-loops.md §Rule map for a "
    "positive-pattern recipe; add a row there in the same commit that fixes the issue."
)


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
