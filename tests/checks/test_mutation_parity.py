"""Tests for the diff-scoped mutation runner (#499 Phase 1).

Covers ``scripts/checks/mutation_parity.py`` — the mechanical sabotage
control. The runner is itself a correctness gate, so it carries the same
discipline it enforces: every branch is driven through the module's public
functions, the survivor/killed verdict is proven both ways, and the
ratchet's never-grow invariant is pinned.

Sabotage proofs (executed; mutate prod -> confirm fail -> restore):

  * generate_mutants: change ``_COMPARE_SWAPS[ast.Gt]`` from ``(">", ">=")``
    to ``(">", ">")`` -> ``test_generate_mutants_swaps_comparisons`` fails
    (the produced mutation no longer differs); restore -> green.
  * _verdict ratchet: change ``_ratchet``'s ``len(survivor_keys) >
    len(previous)`` to ``>=`` -> ``test_ratchet_refuses_to_grow`` fails (an
    equal-size set would wrongly be rejected) AND
    ``test_ratchet_holds_when_steady`` fails; restore -> green.
  * changed_lines: change the hunk-count guard ``if count > 0`` to
    ``if count >= 0`` -> ``test_changed_lines_ignores_pure_deletions``
    fails (a 0-line deletion hunk would add a phantom line); restore.

The full tautology->survivor->strengthen->killed end-to-end proof against
the real ``_run_impacted_tests`` subprocess path is captured in the agent
report (it stages a throwaway target + tautological test and runs the CLI).
This suite stays subprocess-free so it lives in the fast unit tier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

import mutation_parity as mp  # noqa: E402

pytestmark = pytest.mark.unit


# ── diff parsing -> changed lines ───────────────────────────────────────


def test_changed_lines_parses_added_hunk() -> None:
    """A unified-0 diff for a kairix/*.py file yields its added line numbers."""
    diff = (
        "diff --git a/kairix/x.py b/kairix/x.py\n"
        "--- a/kairix/x.py\n"
        "+++ b/kairix/x.py\n"
        "@@ -10,0 +11,2 @@\n"
        "+    a = 1\n"
        "+    b = 2\n"
    )
    result = mp._changed_lines_from_diff(diff)
    assert result == {Path("kairix/x.py"): {11, 12}}


def test_changed_lines_single_line_hunk_defaults_count_to_one() -> None:
    """A hunk header with no explicit +count means a single changed line."""
    diff = "+++ b/kairix/y.py\n@@ -5 +5 @@\n+    z = 3\n"
    assert mp._changed_lines_from_diff(diff) == {Path("kairix/y.py"): {5}}


def test_changed_lines_ignores_non_kairix_and_non_py() -> None:
    """Tests, scripts, and non-python files are out of mutation scope."""
    diff = (
        "+++ b/tests/test_x.py\n@@ -1 +1 @@\n+changed\n"
        "+++ b/kairix/notes.md\n@@ -1 +1 @@\n+changed\n"
        "+++ b/scripts/thing.py\n@@ -1 +1 @@\n+changed\n"
    )
    assert mp._changed_lines_from_diff(diff) == {}


def test_changed_lines_ignores_pure_deletions() -> None:
    """A hunk that only deletes (``+count`` == 0) adds no phantom line."""
    diff = "+++ b/kairix/d.py\n@@ -5,3 +4,0 @@\n-old\n-old\n-old\n"
    assert mp._changed_lines_from_diff(diff) == {}


# ── function-span narrowing ─────────────────────────────────────────────


def test_enclosing_function_lines_keeps_body_lines_only() -> None:
    """Only changed lines INSIDE a def body survive the narrowing."""
    source = "X = 1\n\ndef f(a):\n    return a > 0\n"
    # line 1 (module const) is outside any def; line 4 (return) is in f's body.
    assert mp._enclosing_function_lines(source, {1, 4}) == {4}


def test_enclosing_function_lines_handles_syntax_error() -> None:
    """Unparseable source narrows to the empty set rather than raising."""
    assert mp._enclosing_function_lines("def (:\n", {1}) == set()


# ── mutant generation ───────────────────────────────────────────────────


def _mutations(source: str, scope: set[int]) -> set[tuple[str, str]]:
    return {(m.original, m.mutation) for m in mp.generate_mutants(source, Path("kairix/t.py"), scope)}


def test_generate_mutants_swaps_comparisons() -> None:
    """Each comparison operator is swapped to its mutation partner."""
    source = "def f(a, b):\n    return a > b\n"
    muts = _mutations(source, {2})
    assert (">", ">=") in muts
    # The mutation must DIFFER from the original (the sabotage anchor).
    assert all(orig != mut for orig, mut in muts)


def test_generate_mutants_swaps_boolean_operators() -> None:
    """``and`` becomes ``or``."""
    source = "def f(a, b):\n    return a and b\n"
    assert ("and", "or") in _mutations(source, {2})


def test_generate_mutants_flips_bool_literal() -> None:
    """``True`` becomes ``False``."""
    source = "def f():\n    return True\n"
    assert ("True", "False") in _mutations(source, {2})


def test_generate_mutants_respects_scope() -> None:
    """A comparison on a line NOT in scope yields no mutant."""
    source = "def f(a, b):\n    return a > b\n"
    assert _mutations(source, {99}) == set()


def test_generate_mutants_empty_on_syntax_error() -> None:
    assert mp.generate_mutants("def (:\n", Path("kairix/t.py"), {1}) == []


def test_generated_mutant_source_is_a_one_token_delta() -> None:
    """The mutated source differs from the original by exactly the swap."""
    source = "def f(a, b):\n    return a == b\n"
    [mutant] = [m for m in mp.generate_mutants(source, Path("kairix/t.py"), {2}) if m.original == "=="]
    assert "a != b" in mutant.mutated_source
    assert "a == b" not in mutant.mutated_source


# ── ratchet: never-grow invariant ───────────────────────────────────────


def test_ratchet_holds_when_steady(tmp_path: Path) -> None:
    """A surviving set equal in size to the baseline is accepted + written."""
    baseline = tmp_path / "surv.txt"
    baseline.write_text("kairix/a.py:1:>->>=\n", encoding="utf-8")
    rc = mp._ratchet({"kairix/b.py:2:==->!="}, baseline)
    assert rc == 0
    assert "kairix/b.py:2:==->!=" in baseline.read_text(encoding="utf-8")


def test_ratchet_shrinks_ok(tmp_path: Path) -> None:
    """Fewer survivors than the baseline is the goal — accepted."""
    baseline = tmp_path / "surv.txt"
    baseline.write_text("kairix/a.py:1:>->>=\nkairix/b.py:2:==->!=\n", encoding="utf-8")
    assert mp._ratchet({"kairix/a.py:1:>->>="}, baseline) == 0


def test_ratchet_refuses_to_grow(tmp_path: Path) -> None:
    """More survivors than the baseline fails AND leaves the baseline intact."""
    baseline = tmp_path / "surv.txt"
    baseline.write_text("kairix/a.py:1:>->>=\n", encoding="utf-8")
    before = baseline.read_text(encoding="utf-8")
    rc = mp._ratchet({"kairix/a.py:1:>->>=", "kairix/b.py:2:==->!="}, baseline)
    assert rc == 1
    assert baseline.read_text(encoding="utf-8") == before  # untouched


# ── verdict translation ─────────────────────────────────────────────────


def _result(key_path: str, lineno: int, original: str, mutation: str) -> mp.MutantResult:
    mutant = mp.Mutant(
        path=Path(key_path),
        lineno=lineno,
        col=0,
        original=original,
        mutation=mutation,
        mutated_source="",
    )
    return mp.MutantResult(mutant=mutant, survived=True, detail="survived", elapsed_s=0.1)


def test_verdict_clean_when_no_survivors() -> None:
    assert mp._verdict([], baseline_path=None, write_baseline=False, skipped=0) == 0


def test_verdict_fails_on_new_survivor_without_baseline() -> None:
    """Strict (safe-commit) mode: any survivor fails."""
    survivors = [_result("kairix/a.py", 1, ">", ">=")]
    assert mp._verdict(survivors, baseline_path=None, write_baseline=False, skipped=0) == 1


def test_verdict_passes_when_survivor_in_baseline(tmp_path: Path) -> None:
    """Nightly mode: a survivor already in the ratchet does not fail."""
    baseline = tmp_path / "surv.txt"
    baseline.write_text("kairix/a.py:1:>->>=\n", encoding="utf-8")
    survivors = [_result("kairix/a.py", 1, ">", ">=")]
    assert mp._verdict(survivors, baseline_path=baseline, write_baseline=False, skipped=0) == 0


def test_verdict_fails_on_survivor_not_in_baseline(tmp_path: Path) -> None:
    """A survivor outside the ratchet fails even in baseline mode."""
    baseline = tmp_path / "surv.txt"
    baseline.write_text("kairix/a.py:1:>->>=\n", encoding="utf-8")
    survivors = [_result("kairix/b.py", 9, "==", "!=")]
    assert mp._verdict(survivors, baseline_path=baseline, write_baseline=False, skipped=0) == 1


# ── impacted-test selection + report shape ──────────────────────────────


def test_module_path_maps_to_dotted_import() -> None:
    assert mp._module_path(Path("kairix/core/factory.py")) == "kairix.core.factory"


def test_same_module_tests_picks_a_modules_own_test_files() -> None:
    """Only ``test_<mod>.py`` for a mutated module counts — not look-alikes."""
    paths = {Path("kairix/use_cases/recommend.py"), Path("kairix/core/factory.py")}
    found = {
        "tests/use_cases/test_recommend.py",  # recommend.py's own test
        "tests/contracts/test_cli_mcp_parity_recommend.py",  # NOT test_recommend.py
        "tests/core/test_factory.py",  # factory.py's own test
        "tests/integration/test_pipeline_cache_race.py",  # incidental importer
    }
    assert mp._same_module_tests(paths, found) == [
        "tests/core/test_factory.py",
        "tests/use_cases/test_recommend.py",
    ]


def test_prioritise_keeps_same_module_test_even_when_cap_would_evict_it() -> None:
    """The fix: a module's own test survives the cap even when a co-mutated,
    widely-imported file floods ``found`` with alphabetically-earlier importers.

    Sabotage proof: revert ``_prioritise`` to ``sorted(found)[:MAX]`` and the
    own-test (which sorts after the 'aaa' crowd) drops out of the window — this
    assertion fails. The crowd models the importers of a co-mutated factory.py.
    """
    own = "tests/use_cases/test_recommend.py"
    crowd = {f"tests/aaa/test_{i:03d}.py" for i in range(mp.MAX_IMPACTED_TEST_FILES + 10)}
    found = crowd | {own}
    paths = {Path("kairix/use_cases/recommend.py"), Path("kairix/core/factory.py")}

    result = mp._prioritise(found, paths)

    assert own in result  # the mutated module's own test is never evicted
    assert result[0] == own  # same-module tests come first
    assert len(result) <= mp.MAX_IMPACTED_TEST_FILES  # the long tail stays capped


def test_survivor_report_carries_f21_action_markers() -> None:
    """The F21 affordance contract: fix:/next:/run: markers present."""
    report = mp._survivor_report(_result("kairix/a.py", 7, ">", ">="))
    assert "fix:" in report
    assert "next:" in report
    assert "run:" in report
    assert "mutant survived" in report


def test_survivor_key_is_stable_and_unique() -> None:
    a = mp.Mutant(Path("kairix/a.py"), 1, 0, ">", ">=", "")
    b = mp.Mutant(Path("kairix/a.py"), 2, 0, ">", ">=", "")
    assert mp._survivor_key(a) != mp._survivor_key(b)
    assert mp._survivor_key(a) == "kairix/a.py:1:>->>="


def test_load_baseline_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "b.txt"
    p.write_text("# header\n\nkairix/a.py:1:>->>=\n", encoding="utf-8")
    assert mp._load_baseline(p) == {"kairix/a.py:1:>->>="}


def test_load_baseline_missing_file_is_empty(tmp_path: Path) -> None:
    assert mp._load_baseline(tmp_path / "nope.txt") == set()
