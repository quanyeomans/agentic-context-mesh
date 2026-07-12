"""Unit tests for F93 — the CI fan-in parity rule.

F93 proves ``.github/workflows/ci.yml`` is internally honest: every job
either sits in the ``Quality gate`` aggregator's transitive ``needs:`` closure
(so its failure blocks the merge) or carries a
``# fan-in: informational`` marker. A job that is neither is a dangling
job — it can run, fail, and the PR still merges green.

These tests drive the public ``collect_violations(repo_root=...)``
surface against synthetic workflow files in a tmp dir, plus a sanity
assertion that the real shipped ci.yml is clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "checks"))
import check_f93_ci_fanin_parity as f93

pytestmark = pytest.mark.unit


def _write_ci(repo_root: Path, text: str) -> None:
    """Write ``text`` to ``<repo_root>/.github/workflows/ci.yml``."""
    workflow = repo_root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(text, encoding="utf-8")


# A minimal but realistic workflow: a `changes` root, one stage, and the
# `check` aggregator (name "Quality gate") fanning both in. `gated_stage` is in
# the closure; the aggregator gates by definition.
_GATED_ONLY = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  changes:
    name: "Detect changes"
    runs-on: ubuntu-latest
  gated_stage:
    name: "Stage X"
    runs-on: ubuntu-latest
    needs: [changes]
  check:
    name: "Quality gate"
    needs:
      - changes
      - gated_stage
"""


def test_job_in_closure_passes(tmp_path: Path) -> None:
    """A job reachable from the Quality-gate aggregator's needs: closure is
    gated — no violation."""
    _write_ci(tmp_path, _GATED_ONLY)
    assert f93.collect_violations(repo_root=tmp_path) == set()


# Same workflow plus a `dangling` job the aggregator never lists and that
# carries no informational marker — the failure class F93 exists to catch.
_WITH_DANGLING = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  changes:
    name: "Detect changes"
    runs-on: ubuntu-latest
  gated_stage:
    name: "Stage X"
    runs-on: ubuntu-latest
    needs: [changes]
  dangling:
    name: "Dangling stage"
    runs-on: ubuntu-latest
    needs: [changes]
  check:
    name: "Quality gate"
    needs:
      - changes
      - gated_stage
"""


def test_dangling_job_fails(tmp_path: Path) -> None:
    """A job outside the closure with no informational marker is a
    violation — a green merge could ship with it failing."""
    _write_ci(tmp_path, _WITH_DANGLING)
    violations = f93.collect_violations(repo_root=tmp_path)
    assert violations == {Path(".github/workflows/ci.yml::dangling-not-in-Quality-gate-fanin")}


# The same dangling job, now annotated with the informational marker in
# the comment block directly above its key — the sanctioned escape hatch.
_WITH_DANGLING_MARKED = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  changes:
    name: "Detect changes"
    runs-on: ubuntu-latest
  gated_stage:
    name: "Stage X"
    runs-on: ubuntu-latest
    needs: [changes]
  # fan-in: informational — advisory job; posts a PR comment, never blocks.
  dangling:
    name: "Dangling stage"
    runs-on: ubuntu-latest
    needs: [changes]
  check:
    name: "Quality gate"
    needs:
      - changes
      - gated_stage
"""


def test_informational_marker_passes(tmp_path: Path) -> None:
    """The same dangling job marked ``# fan-in: informational`` passes —
    the file now SAYS the job is legitimately non-gating."""
    _write_ci(tmp_path, _WITH_DANGLING_MARKED)
    assert f93.collect_violations(repo_root=tmp_path) == set()


def test_transitive_closure_is_followed(tmp_path: Path) -> None:
    """A job two hops from the aggregator (aggregator → mid → leaf) is
    gated — the closure is transitive, not just direct dependencies."""
    workflow = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  leaf:
    name: "Leaf stage"
    runs-on: ubuntu-latest
  mid:
    name: "Mid stage"
    runs-on: ubuntu-latest
    needs: [leaf]
  check:
    name: "Quality gate"
    needs:
      - mid
"""
    _write_ci(tmp_path, workflow)
    # `leaf` is only reachable through `mid`; if the walk were not
    # transitive it would be flagged as dangling.
    assert f93.collect_violations(repo_root=tmp_path) == set()


def test_scalar_needs_form_is_understood(tmp_path: Path) -> None:
    """GitHub accepts scalar ``needs: changes`` as well as the list form;
    a job depended on via the scalar form is still in the closure."""
    workflow = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  changes:
    name: "Detect changes"
    runs-on: ubuntu-latest
  check:
    name: "Quality gate"
    needs: changes
"""
    _write_ci(tmp_path, workflow)
    assert f93.collect_violations(repo_root=tmp_path) == set()


def test_missing_aggregator_is_flagged(tmp_path: Path) -> None:
    """No job named ``Quality gate`` means the required status context has no
    producer — the whole fan-in premise is broken; F93 surfaces it."""
    workflow = """\
name: "1 · Quality gate"
on:
  pull_request:
jobs:
  changes:
    name: "Detect changes"
    runs-on: ubuntu-latest
  build:
    name: "Build"
    runs-on: ubuntu-latest
    needs: [changes]
"""
    _write_ci(tmp_path, workflow)
    violations = f93.collect_violations(repo_root=tmp_path)
    assert violations == {Path(".github/workflows/ci.yml::no-aggregator-named-Quality-gate")}


def test_missing_workflow_is_a_no_op(tmp_path: Path) -> None:
    """No ci.yml at all → nothing this rule governs (F81 owns workflow
    presence). F93 must not crash or false-positive on an empty tree."""
    assert f93.collect_violations(repo_root=tmp_path) == set()


def test_real_ci_yml_is_clean() -> None:
    """The shipped ci.yml must pass F93 — every job is either gated or
    explicitly marked informational. Pins the production invariant."""
    assert f93.collect_violations() == set()
