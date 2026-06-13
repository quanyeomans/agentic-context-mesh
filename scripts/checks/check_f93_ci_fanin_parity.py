"""F93: every CI job either gates the merge or is explicitly informational.

Motivation (EPIC #499 Phase 2 — the fan-in parity class)
--------------------------------------------------------
``main`` branch protection requires exactly one status context from this
workflow: **"CI gate"** (the terminal ``check`` job, whose ``needs:``
fan-in aggregates every blocking stage). A green merge is therefore only
as safe as that fan-in is COMPLETE. If a job is defined in ``ci.yml`` but
is NOT reachable from the ``CI gate`` aggregator's transitive ``needs:``
closure, its failure does not block the merge — a red job ships
silently. The class this prevents: someone adds a new stage (a license
scan, a second security job, a schema-drift gate), wires its triggers,
but forgets to add it to the ``check`` job's ``needs:`` list. CI shows it
running and failing, branch protection waves the PR through anyway,
because "CI gate" went green without ever waiting on the new job.

What F93 asserts
----------------
Parse ``.github/workflows/ci.yml``. Identify the aggregator job — the one
whose ``name:`` is ``CI gate``. Build the transitive ``needs:`` closure
rooted at that aggregator. Then assert: every job defined in the workflow
is EITHER

  * in that closure (so its failure blocks the gate), OR
  * the aggregator itself (the closure root), OR
  * carries an explicit ``# fan-in: informational — <reason>`` marker
    comment in the lines immediately preceding its ``<job-id>:`` key.

A job that is neither in the closure nor marked informational is a
FAILURE: its failure cannot block a merge, yet nothing in the file says
that is intentional.

The informational marker convention
-----------------------------------
``# fan-in: informational — <reason>``  on its own comment line in the
block of comments directly above the job key. The reason is mandatory
free text (F21 affordance: state WHY the job is legitimately non-gating —
"advisory", "publishes artefacts", "posts a PR comment"). A bare
``# fan-in: informational`` with no reason still satisfies the marker
presence, but reviewers should push back on a missing reason.

Intentionally NOT caught (precision over recall — a detector agents
distrust is worse than no detector):

  * **Whether a job SHOULD gate.** F93 cannot know intent. It only
    proves the file is INTERNALLY HONEST: every non-gating job SAYS it is
    non-gating. Promoting an informational job into the gate is a human
    decision (add it to the aggregator's ``needs:`` and drop the marker).
  * **Workflows other than ci.yml.** Sibling workflows
    (fresh-install-smoke, soak-suite, release) have their own gating
    story and are not aggregated by this ``CI gate`` job; F93 scopes to
    the single workflow that produces the required context.
  * **The aggregator's own pass/fail logic.** Whether the ``check`` job's
    shell correctly fails on a failed dependency is F83's / the job's own
    concern; F93 only checks the ``needs:`` graph shape.
  * **``if:`` conditions on closure membership.** A job in the closure
    that is path-filtered (``if: needs.changes.outputs.python``) still
    counts as gating — when it runs and fails, the aggregator sees the
    failure. Skipped is not failed.

Binary structural check, no per-file baseline (same shape as F81's
fresh-install-smoke presence gate and F92's catalogue currency): the
fan-in graph is either complete or it is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from tc_fitness import REPO_ROOT, gate

WORKFLOW_REL = Path(".github/workflows/ci.yml")

# The job whose name produces the required "CI gate" status context.
AGGREGATOR_NAME = "CI gate"

# The marker that declares a job legitimately outside the gate fan-in.
INFORMATIONAL_MARKER = "# fan-in: informational"

REMEDIATION = """F93: a CI job is not reachable from the "CI gate" aggregator and is
not marked informational — a green merge could ship with that job
failing.

Branch protection on main requires exactly the "CI gate" status context
(the terminal `check` job). Its `needs:` fan-in is what makes that one
green light mean "every blocking stage passed". A job outside that
transitive closure does NOT block the merge: it can run, fail, and the
PR still merges green.

fix: decide whether the dangling job SHOULD gate the merge —
  * If yes (it is a real blocking stage): add the job's id to the
    `check` (CI gate) job's `needs:` list. Add it to the aggregator's
    result-evaluation loop too so a failure actually fails the gate.
  * If no (it is advisory — publishes artefacts, posts a PR comment,
    races a webhook): add a marker comment on the lines directly above
    the job's `<id>:` key:
        # fan-in: informational — <why this job is legitimately non-gating>
next: re-run python3 scripts/checks/check_f93_ci_fanin_parity.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh "ci: wire <job> into the CI-gate fan-in (or mark it informational)"

Pass example: .github/workflows/ci.yml
  # fan-in: informational — SonarCloud is advisory; not a required status
  # context, and its webhook check can race this job (#269).
  sonarcloud:
    name: "SonarCloud analysis"
    needs: [changes, unit-and-type]
  ...
  check:
    name: "CI gate"
    needs:
      - changes
      - unit-and-type
      - security        # every blocking stage is listed here
      - docker

Forbidden example:
  license-scan:          # NEW blocking job, runs + fails on a bad licence
    name: "License scan"
    needs: [changes]
  check:
    name: "CI gate"
    needs:
      - changes          # license-scan NOT listed, NOT marked informational
      - docker           # → a bad licence merges green."""


def _load_jobs(workflow_text: str) -> dict[str, dict]:
    """Parse the workflow and return its ``jobs`` mapping ({} on failure).

    PyYAML is the canonical parser already used across the checks tree.
    A malformed workflow yields ``{}`` — the caller treats "no jobs" as
    "nothing to assert" rather than crashing the gate.
    """
    try:
        data = yaml.safe_load(workflow_text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    jobs = data.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _needs_of(job_spec: object) -> list[str]:
    """Normalise a job's ``needs:`` to a list of job-id strings.

    GitHub Actions accepts both scalar (``needs: changes``) and sequence
    (``needs: [a, b]``) forms. Anything else yields an empty list.
    """
    if not isinstance(job_spec, dict):
        return []
    needs = job_spec.get("needs")
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, list):
        return [n for n in needs if isinstance(n, str)]
    return []


def _find_aggregator(jobs: dict[str, dict]) -> str | None:
    """Return the job id whose ``name:`` is the aggregator name, else None."""
    for job_id, spec in jobs.items():
        if isinstance(spec, dict) and spec.get("name") == AGGREGATOR_NAME:
            return job_id
    return None


def _closure(jobs: dict[str, dict], root: str) -> set[str]:
    """Transitive ``needs:`` closure rooted at ``root`` (root excluded).

    Walks the dependency graph breadth-first. A ``needs:`` reference to a
    job that does not exist is ignored (GitHub would error on it; the
    graph walk simply has nothing to descend into).
    """
    seen: set[str] = set()
    frontier = list(_needs_of(jobs.get(root)))
    while frontier:
        node = frontier.pop()
        if node in seen or node not in jobs:
            continue
        seen.add(node)
        frontier.extend(_needs_of(jobs.get(node)))
    return seen


def _jobs_marked_informational(workflow_text: str, job_ids: set[str]) -> set[str]:
    """Return the set of job ids carrying an informational marker.

    A job ``<id>:`` is marked when the contiguous block of comment lines
    immediately preceding its key line contains
    ``# fan-in: informational``. We scan the raw text (PyYAML discards
    comments) and match a top-level ``<id>:`` key at two-space indent —
    the indentation every job key sits at under ``jobs:``.
    """
    lines = workflow_text.splitlines()
    marked: set[str] = set()
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        # Job keys live at exactly two-space indent under `jobs:` and end
        # in a colon with no inline value (e.g. "  sonarcloud:").
        if not (raw.startswith("  ") and raw[2:3] != " "):
            continue
        if not stripped.endswith(":"):
            continue
        job_id = stripped[:-1].strip()
        if job_id not in job_ids:
            continue
        # Walk upward over the contiguous comment block above the key.
        cursor = idx - 1
        while cursor >= 0:
            above = lines[cursor].strip()
            if above.startswith("#"):
                if INFORMATIONAL_MARKER in lines[cursor]:
                    marked.add(job_id)
                    break
                cursor -= 1
                continue
            break  # first non-comment line ends the block
    return marked


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Return a synthetic repo-relative path per dangling (un-gated,
    un-marked) job. Empty set means the fan-in is honest.

    The synthetic entries name the workflow + offending job so the
    failure output reads as an inventory (mirroring F81's
    ``<workflow>::<problem>`` convention).
    """
    workflow = repo_root / WORKFLOW_REL
    if not workflow.is_file():
        # No ci.yml → nothing this rule governs. F81 owns presence of the
        # smoke workflow; F93 owns the SHAPE of ci.yml when it exists.
        return set()

    text = workflow.read_text(encoding="utf-8")
    jobs = _load_jobs(text)
    if not jobs:
        return set()

    aggregator = _find_aggregator(jobs)
    if aggregator is None:
        # The required "CI gate" context has no producing job — the whole
        # fan-in premise is broken. Surface it as a single violation.
        return {Path(f"{WORKFLOW_REL}::no-aggregator-named-{AGGREGATOR_NAME.replace(' ', '-')}")}

    gated = _closure(jobs, aggregator)
    informational = _jobs_marked_informational(text, set(jobs))

    violations: set[Path] = set()
    for job_id in jobs:
        if job_id == aggregator:
            continue  # the closure root gates by definition
        if job_id in gated:
            continue
        if job_id in informational:
            continue
        violations.add(Path(f"{WORKFLOW_REL}::{job_id}-not-in-CI-gate-fanin"))
    return violations


def main() -> int:
    return gate("f93-ci-fanin-parity", collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
