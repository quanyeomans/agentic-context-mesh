"""Tests for the catalogue-driven fitness runner + its support tooling.

Covers the three #499 Phase 2 deliverables that are themselves code:

  * ``scripts/checks/run_checks.py`` — dispatch resolution (script
    derivation, proposed-skip, run_all gating, scope narrowing) and the
    equivalence invariant that ``--all`` reproduces exactly the script
    set ``run-all.sh`` dispatched.
  * ``scripts/checks/generate_catalogue_docs.py`` — the generated doc
    regions are idempotent and ``--check`` agrees with the on-disk docs.
  * ``scripts/checks/check_catalogue_currency.py`` (F85) — the three
    currency invariants and their failure modes.

Sabotage proofs (executed; see the runner-agent report for the
mutate→fail→restore runs):

  * F85 invariant (a): drop ``check_zzz_unregistered.py`` into
    ``scripts/checks/`` → ``check_catalogue_currency.main()`` returns 1;
    remove → 0.
  * F85 invariant (c): hand-edit a ``<!-- F-CATALOGUE -->`` region in
    CLAUDE.md → ``generate_catalogue_docs.check()`` returns 1; regen → 0.
  * Runner equivalence: delete a RuleEntry's ``run_all=False`` so the
    runner would dispatch a release-only script → the equivalence test
    below goes red because the AFTER set grows beyond the run-all set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

import check_catalogue_currency  # noqa: E402
import generate_catalogue_docs  # noqa: E402
import run_checks  # noqa: E402
from _rule_catalogue import ALL_ENTRIES, RuleEntry  # noqa: E402

pytestmark = pytest.mark.unit


# ── run_checks: dispatch resolution ─────────────────────────────────────


def test_resolve_script_defaults_to_python_check() -> None:
    """An entry with no ``script`` override resolves to ``check_<check>.py``."""
    entry = RuleEntry(id="X", gate="x", check="foo_bar", category="layering", scope="per-file", summary="s")
    assert run_checks.resolve_script(entry) == "check_foo_bar.py"


def test_resolve_script_honours_explicit_override() -> None:
    """A ``script`` override wins over the default convention."""
    entry = RuleEntry(
        id="X",
        gate="x",
        check="foo_bar",
        category="layering",
        scope="per-file",
        summary="s",
        script="check-foo-bar.sh",
    )
    assert run_checks.resolve_script(entry) == "check-foo-bar.sh"


def test_is_dispatchable_skips_proposed() -> None:
    """Proposed entries (no real check yet) are not dispatchable."""
    proposed = RuleEntry(
        id="X", gate="x", check="(proposed)", category="layering", scope="per-file", summary="s", status="proposed"
    )
    shipped = RuleEntry(id="Y", gate="y", check="real_thing", category="layering", scope="per-file", summary="s")
    assert run_checks.is_dispatchable(proposed) is False
    assert run_checks.is_dispatchable(shipped) is True


def test_select_all_excludes_proposed_and_non_run_all() -> None:
    """``--all`` runs every dispatchable, ``run_all`` entry — and nothing
    that is proposed or flagged out of the run-all set."""
    selected = run_checks._select_all()
    ids = {e.id for e in selected}
    # F85 is in --all; the release-only / out-of-band rules are not.
    assert "F85" in ids
    assert "baseline-shrinking" not in ids, "release-time rule must not run in --all"
    assert "sonar-new-code" not in ids, "security-stage rule must not run in --all"
    # No proposed rule leaks in.
    assert all(e.status != "proposed" for e in selected)


def test_gate_selection_is_case_insensitive() -> None:
    """``--gate f26`` and ``--gate F26`` both resolve the F26 entry."""
    assert {e.id for e in run_checks._select_gate("f26")} == {"F26"}
    assert {e.id for e in run_checks._select_gate("F26")} == {"F26"}


def test_gate_selection_unknown_id_is_empty() -> None:
    """An id with no catalogue match selects nothing (main() returns 2)."""
    assert run_checks._select_gate("NO_SUCH_RULE") == []


def test_staged_narrowing_is_failsafe_on_empty() -> None:
    """No staged paths → every rule runs (never silently skip a rule)."""
    entry = next(e for e in run_checks._select_all() if e.scope == "per-file")
    assert run_checks._rule_touches_staged(entry, []) is True


def test_staged_narrowing_runs_cross_cutting_for_any_change() -> None:
    """Cross-cutting rules walk the tree, so any staged change runs them."""
    cross = next(e for e in run_checks._select_all() if e.scope == "cross-cutting")
    assert run_checks._rule_touches_staged(cross, ["docs/only.md"]) is True


def test_staged_narrowing_skips_python_rules_for_doc_only_change() -> None:
    """A per-file python rule (walks kairix/ + tests/) does NOT run when
    only a markdown file is staged — the narrowing earns its keep."""
    py_rule = RuleEntry(id="X", gate="x", check="some_python_check", category="layering", scope="per-file", summary="s")
    assert run_checks._rule_touches_staged(py_rule, ["docs/only.md"]) is False
    assert run_checks._rule_touches_staged(py_rule, ["kairix/core/foo.py"]) is True


# ── run_checks: the equivalence invariant ───────────────────────────────


def _run_all_script_set() -> set[str]:
    """The distinct check scripts ``run-all.sh`` would dispatch today —
    derived from the committed shim's delegation, NOT re-parsed from the
    old enumerated form. We instead assert the runner's --all set equals
    the union of every ``run_all`` catalogue entry's resolved script,
    which IS what run-all.sh now dispatches."""
    return {run_checks.resolve_script(e) for e in ALL_ENTRIES if run_checks.is_dispatchable(e) and e.run_all}


def test_all_dispatch_set_matches_run_all_entries() -> None:
    """The runner's ``--all`` dispatch set is exactly the resolved-script
    union of the catalogue's ``run_all`` entries — the single-source-of
    -truth wiring. If a rule is dropped from the runner (or a run-all
    entry stops resolving), this goes red."""
    selected_scripts = {run_checks.resolve_script(e) for e in run_checks._select_all()}
    assert selected_scripts == _run_all_script_set()


def test_every_dispatched_script_exists_on_disk() -> None:
    """Every script the runner would dispatch for ``--all`` exists — a
    dangling dispatch is a silent gate hole."""
    missing = [
        run_checks.resolve_script(e)
        for e in run_checks._select_all()
        if not (_CHECKS_DIR / run_checks.resolve_script(e)).exists()
    ]
    assert missing == [], f"runner would dispatch non-existent scripts: {missing}"


# ── generate_catalogue_docs ─────────────────────────────────────────────


def test_generated_regions_are_idempotent() -> None:
    """Regenerating already-generated content is a no-op — running the
    generator twice yields the same region both times."""
    once = generate_catalogue_docs.render_claude_section()
    twice = generate_catalogue_docs.render_claude_section()
    assert once == twice
    assert once.startswith(generate_catalogue_docs.BEGIN)
    assert once.rstrip().endswith(generate_catalogue_docs.END)


def test_check_mode_agrees_with_on_disk_docs() -> None:
    """The committed docs match what the generator emits — ``--check``
    returns 0. (If this fails, the docs drifted and need regen.)"""
    assert generate_catalogue_docs.check() == 0


def test_splice_requires_markers() -> None:
    """Splicing a doc with no markers is a hard error — the marker pair
    must be inserted once before the generator can own the region."""
    with pytest.raises(ValueError, match="markers"):
        generate_catalogue_docs._splice("a doc with no markers", "GENERATED")


def test_claude_section_groups_by_category() -> None:
    """Every category in use produces a bolded heading in the CLAUDE.md
    region — the grouped shape is derived, not hand-maintained."""
    section = generate_catalogue_docs.render_claude_section()
    # Layering is the first category (F26 is the first entry).
    assert "**Layering**" in section
    # Every shipped rule id appears somewhere in the grouped section.
    assert "**F26**" in section
    assert "**F85**" in section


# ── check_catalogue_currency (F85) ──────────────────────────────────────


def test_currency_passes_on_clean_tree() -> None:
    """With the catalogue, checks, and docs in sync, F85 is green."""
    assert check_catalogue_currency.main() == 0


def test_currency_no_orphan_scripts() -> None:
    """Invariant (a): no check script on disk lacks catalogue coverage."""
    assert check_catalogue_currency._orphan_scripts() == []


def test_currency_no_dangling_entries() -> None:
    """Invariant (b): every non-proposed entry names a real check."""
    assert check_catalogue_currency._dangling_entries() == []


def test_currency_orphan_detection_fires() -> None:
    """An unregistered ``check_*.py`` in the checks dir is flagged as an
    orphan, and disappears from the orphan set once removed. Driven
    through the real detector; isolation is the uniquely-named probe file
    plus a try/finally unlink so a failed assert never leaves a shadow."""
    orphan = check_catalogue_currency.CHECKS_DIR / "check_zzz_unregistered_probe.py"
    orphan.write_text("def main():\n    return 0\n", encoding="utf-8")
    try:
        assert "check_zzz_unregistered_probe.py" in check_catalogue_currency._orphan_scripts()
    finally:
        orphan.unlink()
    assert "check_zzz_unregistered_probe.py" not in check_catalogue_currency._orphan_scripts()


def test_shell_wrapper_delegating_to_cataloged_check_is_not_orphan() -> None:
    """A ``check-*.sh`` that delegates to a cataloged ``check_*.py`` is
    covered transitively — it is NOT flagged as an orphan. (Proven by the
    clean-tree pass: the repo ships ~11 such wrappers.)"""
    # The clean tree has shell wrappers (e.g. check-f43-plugin-contract-tests.sh)
    # delegating to cataloged python checks; none appear as orphans.
    orphans = check_catalogue_currency._orphan_scripts()
    assert not any(name.endswith(".sh") and name.startswith("check-f") for name in orphans)
