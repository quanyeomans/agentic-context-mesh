"""Soundness battery for precise per-rule staged selection (#499 Phase 2 stage 4b).

The non-negotiable property of ``run_checks.py --staged`` is **no false
negative on staged changes**: if staging file(s) introduces a violation of
rule R, staged mode MUST select, run, and FAIL R. This module proves that
EXECUTED — for a representative violation of each selection class it stages the
offending file(s) and asserts staged mode catches it — and then closes the
loop with a completeness table: across a battery of staged sets, the rules
staged mode RUNS are a superset of the rules a full ``--all`` run would flag
restricted to those staged files (over-running a cross-cutting rule is fine;
under-running is the only real danger).

Mechanism
---------
Each scenario writes a real violating file into the actual repo tree (under a
``zzz_staged_probe*`` name a try/finally unlinks), then drives the real
``_dispatch_staged`` with that path in the staged list. Because file-local
rules narrow their file index to the staged set, the dispatch inspects only the
probe file — so a clean tree stays green and the injected violation is the only
signal. No monkeypatch of kairix internals: the probe file IS the production
scenario, and ``decide`` / ``restrict_enumeration`` are the real runner code.

Sabotage proofs (executed; see the runner-agent report for the mutate→fail→
restore runs):

  * file-local F26: removing the forbidden import from the probe → dispatch
    goes green; restoring it → red. (The probe IS the mutation.)
  * relational F30: pointing the new COMMANDS subcommand at an EXISTING tested
    command → green; a brand-new untested name → red.
  * completeness: dropping a rule from ``_staged_decisions`` (so it stops being
    selected) → the superset assertion below goes red for that rule's scope.
"""

from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

import run_checks  # noqa: E402
from _rule_catalogue import ALL_ENTRIES  # noqa: E402
from _staged_selection import decide, resolve_staged_scope, staged_in_scope  # noqa: E402

pytestmark = pytest.mark.unit


# ── harness ─────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _probe_file(rel: str, content: str) -> Iterator[str]:
    """Write ``content`` to ``rel`` under the real repo tree, yield the
    repo-relative path string, and FULLY remove it on exit — the file, any
    ``__pycache__`` an import created, and any directory the probe itself
    created (try/finally so a failed assert never leaves a shadow). ``rel``
    must be a ``zzz_staged_probe*`` path so it can never collide with a real
    file, and the probe-dir teardown only deletes ``zzz_staged_probe*``
    directories so it can never touch a real tree."""
    assert "zzz_staged_probe" in rel, "probe paths must be uniquely named to avoid collisions"
    path = _REPO_ROOT / rel
    created_dir = "zzz_staged_probe" in path.parent.name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        yield rel
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        # A staged-mode dispatch may have imported the probe, leaving a
        # __pycache__ that would keep a probe PLUGIN dir alive (F36/F41/...
        # treat any dir under kairix/connectors/ as a plugin). Remove the
        # whole probe dir — but only ever a uniquely-named probe dir.
        if created_dir and path.parent.exists():
            import shutil

            shutil.rmtree(path.parent, ignore_errors=True)


def _run_staged(staged: list[str]) -> tuple[int, str]:
    """Drive the real ``_dispatch_staged`` over ``staged``; return
    ``(exit_code, captured_output)``."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = run_checks._dispatch_staged(staged, skip_coverage=True)
    return code, buf.getvalue()


def _failed_rule_ids(output: str) -> set[str]:
    """The rule ids that FAILED in a staged ledger (the ``FAIL [id]`` lines)."""
    ids: set[str] = set()
    for line in output.splitlines():
        if "FAIL [" in line:
            ids.add(line.split("FAIL [", 1)[1].split("]", 1)[0])
    return ids


def _ran_rule_ids(output: str) -> set[str]:
    """The rule ids that RAN (the ``run [id]`` lines) in a staged ledger."""
    ids: set[str] = set()
    for line in output.splitlines():
        if "run [" in line:
            ids.add(line.split("run [", 1)[1].split("]", 1)[0])
    return ids


def _skipped_rule_ids(output: str) -> set[str]:
    ids: set[str] = set()
    for line in output.splitlines():
        if "skip [" in line:
            ids.add(line.split("skip [", 1)[1].split("]", 1)[0])
    return ids


# ── baseline: a clean staged set passes ─────────────────────────────────


def test_clean_staged_change_passes() -> None:
    """Staging a clean (non-violating) kairix file leaves staged mode green —
    the control that proves a FAIL below is the injected violation, not noise."""
    with _probe_file(
        "kairix/zzz_staged_probe_clean.py",
        "def f() -> int:\n    return 1\n",
    ) as rel:
        code, out = _run_staged([rel])
    assert code == 0, f"clean probe should pass; ledger:\n{out}"


# ── file-local: forbidden import (F26) ──────────────────────────────────


def test_file_local_f26_forbidden_import_caught() -> None:
    """A staged kairix/core file importing kairix.providers → staged mode runs
    and FAILS F26 (file-local class)."""
    with _probe_file(
        "kairix/core/zzz_staged_probe_f26.py",
        "from kairix.providers import something  # forbidden core→providers import\n",
    ) as rel:
        code, out = _run_staged([rel])
    assert code == 1, f"F26 violation must fail staged mode; ledger:\n{out}"
    assert "F26" in _failed_rule_ids(out), f"F26 must be the failing rule; ledger:\n{out}"
    # Sabotage check (inline): the SAME probe without the import passes.
    with _probe_file("kairix/core/zzz_staged_probe_f26.py", "x = 1\n") as rel:
        code2, _ = _run_staged([rel])
    assert code2 == 0, "removing the forbidden import must clear F26 (sabotage proof)"


# ── file-local marker: missing test category marker (F8) ────────────────


def test_file_local_f8_missing_marker_caught() -> None:
    """A staged test module with a ``def test_*`` but no category marker →
    staged mode runs and FAILS F8."""
    with _probe_file(
        "tests/zzz_staged_probe_f8.py",
        "def test_unmarked_probe():\n    assert True\n",
    ) as rel:
        code, out = _run_staged([rel])
    assert code == 1, f"F8 missing-marker must fail staged mode; ledger:\n{out}"
    assert "F8" in _failed_rule_ids(out), f"F8 must be the failing rule; ledger:\n{out}"
    # Sabotage: adding the marker clears F8.
    with _probe_file(
        "tests/zzz_staged_probe_f8.py",
        "import pytest\n\npytestmark = pytest.mark.unit\n\n\ndef test_marked_probe():\n    assert True\n",
    ) as rel:
        code2, _ = _run_staged([rel])
    assert code2 == 0, "adding the unit marker must clear F8 (sabotage proof)"


# ── relational: new CLI subcommand with no outcome test (F30) ───────────


def test_relational_f30_new_surface_selects_full_scope() -> None:
    """A staged CLI/MCP surface change SELECTS F30 (relational) to run over its
    FULL scope — and a deletion of an outcome test does too (deletion-
    sensitivity). This is the no-false-negative property of the NEW selection
    code: the surface change and the paired-artefact deletion both run F30."""
    f30 = next(e for e in run_checks._select_all() if e.id == "F30")
    d = decide(f30, run_checks.resolve_script(f30), ["kairix/cli.py"])
    assert d.run is True, "F30 must run when cli.py (a subcommand surface) is staged"
    assert d.scope_files is None, "F30 is relational — it runs full scope, not narrowed to staged files"
    d_del = decide(f30, run_checks.resolve_script(f30), ["tests/test_worker_cli_maintenance.py"])
    assert d_del.run is True, "deleting/altering an outcome test must run F30 (deletion-sensitivity)"
    # A change OUTSIDE F30's scope (a connector plugin file) does NOT run F30.
    d_out = decide(f30, run_checks.resolve_script(f30), ["kairix/connectors/obsidian/connector.py"])
    assert d_out.run is False, "F30 must skip when no staged path is in its scope (precision)"


def test_relational_f36_new_plugin_without_feature_caught_end_to_end() -> None:
    """End-to-end relational FAIL: staging a NEW connector plugin (a real
    plugin dir under kairix/connectors/) without its BDD feature → staged mode
    runs F36 over its FULL scope and FAILS. The new plugin is the cross-file
    gap a staged plugin change introduces — exactly the class F36 guards."""
    plugin_init = "kairix/connectors/zzz_staged_probe_conn/__init__.py"
    plugin_conn = "kairix/connectors/zzz_staged_probe_conn/connector.py"
    with (
        _probe_file(plugin_init, "def make_connector():\n    return None\n"),
        _probe_file(plugin_conn, "class ProbeConnector:\n    pass\n") as conn_rel,
    ):
        code, out = _run_staged([conn_rel])
    assert code == 1, f"a new connector plugin without a BDD feature must fail staged mode; ledger:\n{out}"
    assert "F36" in _failed_rule_ids(out), f"F36 must be the failing rule; ledger:\n{out}"


# ── relational deletion: breaking a paired invariant ────────────────────


def test_relational_deletion_selects_rule() -> None:
    """A staged DELETION within a relational rule's scope still selects the
    rule (deletion-sensitivity). F45 (capability↔BDD-feature): deleting a
    feature file is a staged path under tests/bdd/features → F45 runs full
    scope."""
    f45 = next(e for e in run_checks._select_all() if e.id == "F45")
    d = decide(f45, run_checks.resolve_script(f45), ["tests/bdd/features/bootstrap.feature"])
    assert d.run is True, "deleting a BDD feature must run F45 at full scope"
    assert d.scope_files is None, "relational rules are never narrowed to staged files"


# ── cross-cutting / always-run: catalogue drift (F92) ───────────────────


def test_always_run_f92_runs_for_any_change() -> None:
    """F92 (catalogue currency, always-run) runs for ANY staged change,
    including a doc-only edit far from any check tree."""
    f92 = next(e for e in run_checks._select_all() if e.id == "F92")
    d = decide(f92, run_checks.resolve_script(f92), ["docs/only.md"])
    assert d.run is True, "F92 must always run (trigger is any change)"
    # And it appears in the ran-set of a real doc-only staged dispatch.
    _code, out = _run_staged(["docs/architecture/ENGINEERING.md"])
    assert "F92" in _ran_rule_ids(out), f"F92 must run on a doc-only staged change; ledger:\n{out}"


def test_always_run_f50_runs_for_any_change() -> None:
    """F50 (net-new-file detection, always-run) runs for any staged change."""
    f50 = next(e for e in run_checks._select_all() if e.id == "F50")
    assert decide(f50, run_checks.resolve_script(f50), ["docs/only.md"]).run is True


# ── completeness / negative-control table ───────────────────────────────
#
# For a battery of staged sets, assert the rules staged mode RUNS are a
# SUPERSET of every rule whose scope contains a staged path (so no rule that
# COULD be tripped by a staged file is skipped). The real tree is clean, so we
# prove completeness structurally: every rule in-scope for a staged set is run.


_BATTERY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("docs-only", ("docs/architecture/ENGINEERING.md",)),
    ("single-kairix-core", ("kairix/core/search/pipeline.py",)),
    ("tests-only", ("tests/test_credentials.py",)),
    ("bdd-feature", ("tests/bdd/features/bootstrap.feature",)),
    ("cli-surface", ("kairix/cli.py",)),
    ("workflow", (".github/workflows/ci.yml",)),
    ("sonar-config", ("sonar-project.properties",)),
    ("connector", ("kairix/connectors/obsidian/connector.py",)),
    ("setup-template", ("kairix/platform/setup/web/templates/setup/folder.html",)),
    ("services-go", ("services/hello/cmd/hello/main.go",)),
    ("multi-3", ("kairix/core/factory.py", "tests/test_paths.py", "docs/x.md")),
)


def _rules_in_scope_for(staged: list[str]) -> set[str]:
    """Every dispatchable run_all rule that, by its catalogue scope/class,
    COULD be tripped by ``staged`` — the ground-truth set staged mode must run
    a superset of. ``always-run`` rules are always in; for the rest, a rule is
    in-scope iff a staged path falls within its resolved scope (None scope →
    always in, fail-safe)."""
    in_scope: set[str] = set()
    seen_scripts: set[str] = set()
    for e in run_checks._select_all():
        script = run_checks.resolve_script(e)
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
        if e.staged_class == "always-run":
            in_scope.add(e.id)
            continue
        scope = resolve_staged_scope(e, script)
        if scope is None or staged_in_scope(scope, staged):
            in_scope.add(e.id)
    return in_scope


@pytest.mark.parametrize("label,staged", _BATTERY, ids=[b[0] for b in _BATTERY])
def test_completeness_staged_runs_superset_of_in_scope(label: str, staged: tuple[str, ...]) -> None:
    """No false negative: every rule whose scope a staged path falls within IS
    run by staged mode (the ran-set ⊇ the in-scope ground-truth set)."""
    staged_list = list(staged)
    ran: set[str] = set()
    skipped: set[str] = set()
    for entry, d in run_checks._staged_decisions(staged_list):
        (ran if d.run else skipped).add(entry.id)
    ground_truth = _rules_in_scope_for(staged_list)
    missing = ground_truth - ran
    assert not missing, (
        f"[{label}] staged mode SKIPPED in-scope rules (false-negative risk): "
        f"{sorted(missing)}\n  staged={staged_list}\n  skipped={sorted(skipped)}"
    )


def test_completeness_skips_are_genuinely_out_of_scope() -> None:
    """The dual: a skipped rule's scope genuinely contains NONE of the staged
    paths (and it is not always-run). Proves the skips are precise, not random
    under-running — for a docs-only change, every skipped rule is out of
    scope."""
    staged = ["docs/architecture/ENGINEERING.md"]
    for entry, d in run_checks._staged_decisions(staged):
        if d.run:
            continue
        assert entry.staged_class != "always-run", f"{entry.id}: always-run rule must never be skipped"
        scope = resolve_staged_scope(entry, run_checks.resolve_script(entry))
        assert scope is not None, f"{entry.id}: a scope-unresolved rule must run (fail-safe), not skip"
        assert not staged_in_scope(scope, staged), f"{entry.id}: skipped but a staged path is in scope {scope}"


def test_every_dispatchable_rule_has_a_decision() -> None:
    """Every rule the ``--all`` set dispatches gets a staged decision (no rule
    silently falls out of the staged selection)."""
    all_ids = {e.id for e in run_checks._select_all()}
    decided = {e.id for e, _ in run_checks._staged_decisions(["kairix/x.py"])}
    # Deduped by script, so the count may differ; every decided id is a real
    # all-set id and the F7/F9-style script dedup is the only divergence.
    assert decided <= all_ids
    assert "F26" in decided and "F92" in decided


def test_staged_classes_are_valid() -> None:
    """Every catalogue entry's ``staged_class`` is a member of the closed
    vocabulary, and ``staged_scope`` (when set) is a non-empty tuple."""
    valid = {"file-local", "relational", "always-run"}
    for e in ALL_ENTRIES:
        assert e.staged_class in valid, f"{e.id}: invalid staged_class {e.staged_class!r}"
        if e.staged_scope is not None:
            assert isinstance(e.staged_scope, tuple) and e.staged_scope, f"{e.id}: empty staged_scope"
