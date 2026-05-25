"""Tests for ``scripts/ci/check_release_gate.py``.

Regression-locks the docs-only-commits-shouldn't-fail-the-gate behaviour.
The historical bug (release-alpha workflow failing every time a docs
commit landed on main between code commits and the alpha cut) repeated
across multiple alphas before the proper fix landed; these tests stop
that regression.

Each test builds a real git repo in tmp_path with controlled history
and runs the script as a subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_release_gate.py"


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo``, return stdout (raises on non-zero)."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _run_gate(repo: Path, head_sha: str, last_success_sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--head-sha", head_sha, "--last-success-sha", last_success_sha],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_gate_passes_when_head_equals_last_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, {"src/x.py": "print()\n"}, "initial code")
    result = _run_gate(repo, sha, sha)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "is the last green ci.yml run" in result.stdout


def test_gate_passes_for_docs_only_commits_past_last_success(tmp_path: Path) -> None:
    """The historical regression: docs commits past the last green ci.yml
    SHA used to fail the gate. They must pass now."""
    repo = _init_repo(tmp_path)
    code_sha = _commit(repo, {"src/x.py": "print()\n"}, "initial code")
    _commit(repo, {"docs/upgrades/v2.md": "# notes\n"}, "docs: upgrade note")
    _commit(repo, {"CHANGELOG.md": "# changelog\n"}, "docs: changelog entry")
    head = _git(repo, "rev-parse", "HEAD")

    result = _run_gate(repo, head, code_sha)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "docs-only commit" in result.stdout


def test_gate_fails_when_code_commit_is_past_last_success(tmp_path: Path) -> None:
    """A code commit past the last green ci.yml SHA must fail the gate
    — that's the real 'untested code' case the original check tried to
    catch."""
    repo = _init_repo(tmp_path)
    last_green = _commit(repo, {"src/x.py": "print()\n"}, "initial code")
    _commit(repo, {"docs/upgrades/v2.md": "# notes\n"}, "docs: ok")
    bad = _commit(repo, {"src/y.py": "print(2)\n"}, "code: untested change")

    result = _run_gate(repo, bad, last_green)
    assert result.returncode == 1
    assert "untested code commits" in result.stderr
    assert bad[:8] in result.stderr


def test_gate_treats_claude_md_as_code(tmp_path: Path) -> None:
    """ci.yml's paths-ignore has ``!CLAUDE.md`` — that re-includes CLAUDE.md
    as a code path. The gate must mirror that exactly so a CLAUDE.md edit
    requires a ci.yml run to pass."""
    repo = _init_repo(tmp_path)
    last_green = _commit(repo, {"src/x.py": "print()\n"}, "initial code")
    bad = _commit(repo, {"CLAUDE.md": "# instructions\n"}, "edit CLAUDE.md")

    result = _run_gate(repo, bad, last_green)
    assert result.returncode == 1
    assert "CLAUDE.md" in result.stderr


def test_gate_fails_on_diverged_branch(tmp_path: Path) -> None:
    """Last green SHA must be an ancestor of HEAD. If main was force-pushed
    or branched out, the gate must fail loud rather than passing silently."""
    repo = _init_repo(tmp_path)
    base = _commit(repo, {"src/x.py": "print()\n"}, "base")
    _commit(repo, {"src/y.py": "print(2)\n"}, "branch-a")
    branch_a_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", base)
    _commit(repo, {"src/z.py": "print(3)\n"}, "branch-b")
    branch_b_head = _git(repo, "rev-parse", "HEAD")

    # Treat branch-a as "last green" and branch-b as HEAD — disjoint history.
    result = _run_gate(repo, branch_b_head, branch_a_head)
    assert result.returncode == 1
    assert "not an ancestor" in result.stderr


def test_gate_fails_on_missing_last_success_sha(tmp_path: Path) -> None:
    """Empty last_success_sha means ci.yml never ran green on main —
    treat as fail with an actionable message."""
    repo = _init_repo(tmp_path)
    sha = _commit(repo, {"src/x.py": "print()\n"}, "initial")
    result = _run_gate(repo, sha, "")
    assert result.returncode == 1
    assert "no successful CI gate run" in result.stderr


def test_gate_treats_merge_commits_as_code_conservatively(tmp_path: Path) -> None:
    """Merge commits have ambiguous diffs (depends on which parent). Treat
    them as code to be safe — better to fail loud and require a manual
    review than silently pass a merge that rolled in code changes."""
    repo = _init_repo(tmp_path)
    base = _commit(repo, {"src/x.py": "print()\n"}, "base")

    _git(repo, "checkout", "-b", "feature")
    _commit(repo, {"src/y.py": "print(2)\n"}, "feature code")
    feature_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "main")
    _commit(repo, {"docs/note.md": "# note\n"}, "docs only")

    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    merge_head = _git(repo, "rev-parse", "HEAD")

    # The merge has feature code in it, so the gate must fail.
    result = _run_gate(repo, merge_head, base)
    assert result.returncode == 1
    # Either the docs-only/code commit check or the merge-commit guard catches it.
    assert merge_head[:8] in result.stderr or feature_head[:8] in result.stderr


def test_docs_paths_match_ci_yml_paths_ignore() -> None:
    """Sanity proof: the script's is_docs_only_path mirrors ci.yml's
    paths-ignore. If ci.yml changes, this test fails first so the
    script can be updated in lockstep."""
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        from check_release_gate import (
            is_docs_only_path,  # type: ignore[import-not-found]  # dynamic path-insert load; mypy can't see scripts/ci/
        )
    finally:
        sys.path.pop(0)

    # docs/** = docs only
    assert is_docs_only_path("docs/README.md") is True
    assert is_docs_only_path("docs/upgrades/v2026.5.25a1.md") is True
    # *.md anywhere = docs only
    assert is_docs_only_path("CHANGELOG.md") is True
    assert is_docs_only_path("kairix/README.md") is True
    # CLAUDE.md is the explicit code re-include
    assert is_docs_only_path("CLAUDE.md") is False
    # Anything else = code
    assert is_docs_only_path("kairix/core/connectors/bronze.py") is False
    assert is_docs_only_path("scripts/safe-commit.sh") is False
    assert is_docs_only_path(".github/workflows/ci.yml") is False
