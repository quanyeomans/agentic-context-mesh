#!/usr/bin/env python3
"""Catalogue-driven fitness-function runner — kairix's thin consumer shim.

kairix is a THIN CONSUMER of the shared ``tc_fitness.runner`` (EPIC #499
common-process). The in-process + subprocess dispatch, the named verdict
ledger, the ``--all`` / ``--gate`` / ``--staged`` modes, the parse-once
``CheckContext``, and the sound staged-selection logic all live in the shared
``three-cubes-fitness`` package now; this module only:

* declares kairix's catalogue (``RULES = ALL_ENTRIES``);
* wires the FOUR injection seams the shared runner exposes so kairix's behaviour
  stays BYTE-IDENTICAL to the pre-migration local runner:

    1. ``scope_resolver`` — kairix derives a file-local rule's staged scope from
       its check module's own detector (the import-boundary ``RULE.roots``, the
       ``FitnessRule.roots`` ABC attribute, or the location/singleton engine's
       ``kairix/`` walk). See :func:`_kairix_scope_resolver`.
    2. ``enumeration_narrower`` — kairix narrows TWO extra file-enumeration
       surfaces the shared ``restrict_python_files`` doesn't know about: the
       ``FitnessRule.enumerate_files`` ABC method and every already-imported
       ``check_*`` module's ``from tc_fitness import python_files`` binding. See
       :func:`_kairix_enumeration_narrower`.
    3. ``conditional_check`` — the F7/F9 coverage check reads its Cobertura-XML
       path from ``KAIRIX_COVERAGE_XML`` and is skipped when ``--skip-coverage``
       is passed OR the report is absent, with kairix's EXACT skip text. See
       :func:`_make_conditional_check`.
    4. ``subprocess_arg_env`` — declared on the F7/F9 catalogue rows
       (``KAIRIX_COVERAGE_XML`` / ``coverage.xml``) so the shared runner
       dispatches the coverage check as a guarded subprocess with the resolved
       path appended.

* owns the ``--skip-coverage`` CLI flag (the shared ``main_cli`` parser doesn't
  carry it — it is a kairix-specific affordance ``run-all.sh`` /
  ``safe-commit.sh`` pass), strips it before dispatch, and threads it into the
  conditional-check seam.

``run-all.sh`` and ``.pre-commit-config.yaml`` invoke this module exactly as
before: ``run_checks.py --all [--skip-coverage]`` / ``run_checks.py --staged
[--skip-coverage]`` / ``run_checks.py --gate <id>``.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The shared catalogue-driven runner + its primitives (the merged core both
# kairix and tc-agent-zone consume). kairix supplies the injection seams below.
from _fitness_rule import FitnessRule
from _rule_catalogue import ALL_ENTRIES
from tc_fitness.catalogue import (  # noqa: F401  # is_dispatchable re-exported for the runner tests
    RuleEntry,
    is_dispatchable,
)
from tc_fitness.context import CheckContext
from tc_fitness.runner import (
    ConditionalCheck,
    ConditionalResult,
    RunnerConfig,
    Verdicts,
    resolve_script,
    run,
)
from tc_fitness.runner import (
    _dispatches_in_process as _pkg_dispatches_in_process,
)
from tc_fitness.runner import (
    _load_check_main as _pkg_load_check_main,
)
from tc_fitness.runner import (
    _run_one_inprocess as _pkg_run_one_inprocess,
)
from tc_fitness.runner import (
    _run_one_subprocess as _pkg_run_one_subprocess,
)
from tc_fitness.runner import (
    _select_all as _pkg_select_all,
)
from tc_fitness.runner import (
    _select_gate as _pkg_select_gate,
)
from tc_fitness.staged import (
    StagedDecision,
    staged_in_scope,  # noqa: F401  # re-exported unchanged for the staged-selection tests
)
from tc_fitness.staged import decide as _pkg_decide
from tc_fitness.staged import resolve_staged_scope as _pkg_resolve_staged_scope

#: kairix's catalogue — the ``tuple[RuleEntry, ...]`` the shared runner reads.
RULES: tuple[RuleEntry, ...] = ALL_ENTRIES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECKS_DIR = REPO_ROOT / "scripts" / "checks"

_YELLOW = "\033[0;33m"
_RESET = "\033[0m"


# ── seam 1: scope_resolver (kairix FitnessRule-aware staged scope) ───────────
#
# The shared ``decide`` derives a file-local rule's staged scope by calling the
# consumer's ``ScopeResolver(script) -> tuple[str, ...] | None``. kairix derives
# it from the check module's own detector — preserving the exact scope the
# pre-migration local ``_staged_selection.resolve_staged_scope`` computed.

# The location/singleton engine (F29 / F38 / F61) always walks ``kairix/``
# regardless of each rule's ``allowed_roots`` allow-list, so a check that
# imports it is scoped to the production package.
_LOCATION_ENGINE_MODULE = "_location_engine"
# The boundary engine shims expose a module-level ``RULE`` carrying ``roots``.
_BOUNDARY_RULE_ATTR = "RULE"


def _module_name_for(script: str) -> str | None:
    """Module name for a ``check_<x>.py`` script, or ``None`` for a ``.sh``
    detector (shell rules can't be introspected for scan roots — their scope
    must be carried explicitly on the catalogue entry)."""
    if not script.endswith(".py"):
        return None
    return script[: -len(".py")]


def _imports_location_engine(module: object) -> bool:
    """True if the check module is a location/singleton-engine shim.

    The shims (F29 / F38 / F61) all ``from _location_engine import
    LocationRule``, so the imported ``LocationRule`` name resolving to the
    engine's class is the distinguishing signal. The import-boundary shims
    import ``ImportBoundaryRule`` instead and are already handled by the
    ``RULE.roots`` branch upstream of this call.
    """
    location_rule = getattr(module, "LocationRule", None)
    return location_rule is not None and getattr(location_rule, "__module__", "") == _LOCATION_ENGINE_MODULE


def _roots_from_module(module_name: str) -> tuple[str, ...] | None:
    """Derive a rule's scan roots by importing its check module and reading,
    in order of specificity:

      1. a module-level ``RULE`` with a ``roots`` tuple (import-boundary
         engine shims: F26 / F27 / F34 / F35 / F37 / F44);
      2. a ``FitnessRule`` subclass's ``roots`` class attribute (the ABC
         checks: F8 / F15 / F47 / F63 / …);
      3. ``("kairix",)`` when the module imports the location/singleton engine
         (F29 / F38 / F61 — those always walk the production package).

    Returns ``None`` when nothing resolves (the caller treats that as
    always-in-scope — fail-safe, never a silent skip). Import failures are
    swallowed into ``None`` for the same reason.
    """
    try:
        module = importlib.import_module(module_name)
    except BaseException:  # pragma: no cover - import hiccup → fail-safe None
        # An un-importable check can't be narrowed; fall back to always-run.
        return None

    rule = getattr(module, _BOUNDARY_RULE_ATTR, None)
    boundary_roots = getattr(rule, "roots", None)
    if isinstance(boundary_roots, tuple) and boundary_roots:
        return boundary_roots

    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if obj is FitnessRule or not issubclass(obj, FitnessRule):
            continue
        if obj.__module__ != module.__name__:
            # Skip the imported FitnessRule ABC / re-exports; only the check's
            # OWN subclass declares its roots.
            continue
        roots = getattr(obj, "roots", None)
        if isinstance(roots, tuple) and roots:
            return roots

    if _imports_location_engine(module):
        return ("kairix",)

    return None


def _kairix_scope_resolver(script: str) -> tuple[str, ...] | None:
    """The ``ScopeResolver`` seam: derive ``script``'s scan roots from its check
    module's own detector. ``None`` for a ``.sh`` detector or an unresolvable
    module → the shared ``decide`` runs the rule unconditionally (fail-safe)."""
    module_name = _module_name_for(script)
    if module_name is None:
        return None
    return _roots_from_module(module_name)


# kairix-resolver-bound staged-selection helpers. The shared ``decide`` /
# ``resolve_staged_scope`` take an explicit ``resolver`` argument; these bind
# kairix's FitnessRule-aware resolver as the default so existing call sites (and
# tests/checks/test_staged_selection.py) get kairix's exact scope derivation
# without threading the resolver through every call.


def decide(
    entry: RuleEntry,
    script: str,
    staged: list[str],
    resolver: Any = _kairix_scope_resolver,
) -> StagedDecision:
    """Decide whether — and over what — to run ``entry`` given ``staged``,
    binding kairix's scope resolver by default (the shared ``decide`` semantics)."""
    return _pkg_decide(entry, script, staged, resolver)


def resolve_staged_scope(
    entry: RuleEntry,
    script: str,
    resolver: Any = _kairix_scope_resolver,
) -> tuple[str, ...] | None:
    """The repo-relative path-prefix scope for ``entry`` under ``script``,
    binding kairix's scope resolver by default."""
    return _pkg_resolve_staged_scope(entry, script, resolver)


# ── seam 2: enumeration_narrower (kairix-specific file-index surfaces) ────────
#
# The shared ``restrict_python_files`` already narrows the package-level
# ``tc_fitness.python_files`` to the staged set. kairix funnels file-local checks
# through TWO additional enumeration surfaces the shared narrowing can't see:
#
#   * ``FitnessRule.enumerate_files`` — the ABC method the ~25 FitnessRule
#     subclasses inherit;
#   * each already-imported ``check_*`` module's ``from tc_fitness import
#     python_files`` binding (bound BY VALUE at import, so re-patching
#     ``tc_fitness.python_files`` alone doesn't reach the local name).
#
# This narrower layers both on top of the shared one, so a file-local staged run
# walks ONLY the staged files while the per-file verdict stays byte-identical.


def _filter_to_staged(paths: list[Path], staged_abs: frozenset[Path]) -> list[Path]:
    """Keep only the ``paths`` that are in the staged set (by resolved path)."""
    out: list[Path] = []
    for p in paths:
        try:
            resolved = p.resolve()
        except OSError:  # pragma: no cover - resolve hiccup → drop conservatively only if not staged
            resolved = p
        if resolved in staged_abs:
            out.append(p)
    return out


@contextmanager
def _kairix_enumeration_narrower(repo_root: Path, staged: list[str]) -> Iterator[None]:
    """The ``EnumerationNarrower`` seam: narrow the kairix-specific enumeration
    surfaces to ``staged`` for the duration of the ``with`` block, then restore.

    Patches :meth:`FitnessRule.enumerate_files` and every already-imported
    ``check_*`` module's local ``python_files`` binding so each yields only the
    staged files (intersected with what it would otherwise walk). The detectors'
    own ``is_in_scope`` / extension filtering still applies on top, so a staged
    file outside a rule's scope is still skipped. Correctness-preserving for
    file-local rules: the set a rule inspects is ``what-it-would-walk ∩ staged``
    and the per-file verdict for each is identical to the full run.

    The shared ``restrict_python_files`` narrows the package-level
    ``tc_fitness.python_files`` itself; this narrower only adds the kairix
    surfaces, so the two compose without double work.
    """
    import tc_fitness

    staged_abs = frozenset((repo_root / s).resolve() for s in staged)

    real_enumerate = FitnessRule.enumerate_files
    real_python_files = tc_fitness.python_files

    def _scoped_enumerate(self: FitnessRule) -> list[Path]:
        return _filter_to_staged(real_enumerate(self), staged_abs)

    def _scoped_python_files(*roots: str, repo_root: Path | None = None, **kwargs: Any) -> list[Path]:
        full = real_python_files(*roots, repo_root=repo_root, **kwargs)
        return _filter_to_staged(full, staged_abs)

    # The ABC method file-local checks inherit. ``_fitness_rule`` itself does not
    # bind ``python_files`` (it uses ``FitnessRule.enumerate_files``), so only
    # the ABC method needs patching here; the per-check ``from tc_fitness import
    # python_files`` bindings are patched in the loop below.
    FitnessRule.enumerate_files = _scoped_enumerate  # type: ignore[method-assign]  # run-scoped staged narrowing; restored in finally

    # Patch every already-imported check module that bound ``python_files`` by
    # value so its local reference also narrows. Record originals to restore
    # exactly. ``module`` is the dynamic check module object; ``setattr`` rebinds
    # its local name.
    patched_modules: list[tuple[Any, Any]] = []
    for module in list(sys.modules.values()):
        candidate: Any = module
        name = getattr(candidate, "__name__", "")
        if not name.startswith("check_"):
            continue
        if getattr(candidate, "python_files", None) is real_python_files:
            patched_modules.append((candidate, real_python_files))
            candidate.python_files = _scoped_python_files

    try:
        yield
    finally:
        FitnessRule.enumerate_files = real_enumerate  # type: ignore[method-assign]  # restore pristine enumeration
        for patched, original in patched_modules:
            patched.python_files = original


# ── seam 3: conditional_check (coverage skip text + --skip-coverage flag) ────
#
# The F7/F9 coverage check declares ``subprocess_arg_env="KAIRIX_COVERAGE_XML"``,
# so the shared runner dispatches it as a guarded subprocess with the resolved
# Cobertura-XML path appended — UNLESS this hook says skip. The hook reproduces
# kairix's EXACT skip text for both skip reasons:
#
#   * ``--skip-coverage`` passed → "skip [F7] check_per_file_coverage.py —
#     --skip-coverage" (the run-all.sh / safe-commit.sh inner-loop path);
#   * report absent → "skip [F7] ... — coverage report not found" + a "run:"
#     hint line.


def _coverage_xml_path() -> Path | None:
    """The Cobertura XML the coverage check should read, or ``None`` to skip.
    ``KAIRIX_COVERAGE_XML`` wins (safe-commit's per-invocation artifact); else
    the repo-root ``coverage.xml``; skip if neither exists."""
    import os

    env_path = os.environ.get("KAIRIX_COVERAGE_XML")
    candidate = Path(env_path) if env_path else (REPO_ROOT / "coverage.xml")
    return candidate if candidate.exists() else None


def _make_conditional_check(*, skip_coverage: bool) -> ConditionalCheck:
    """Build the ``ConditionalCheck`` hook for the F7/F9 coverage rule, bound to
    the runner's ``--skip-coverage`` flag. Reproduces kairix's exact skip lines."""

    def _conditional(entry: RuleEntry) -> ConditionalResult | None:
        script = resolve_script(entry)
        if skip_coverage:
            return ConditionalResult(
                run=False,
                skip_lines=(f"{_YELLOW}skip [{entry.id}]{_RESET} {script} — --skip-coverage",),
            )
        coverage_xml = _coverage_xml_path()
        if coverage_xml is None:
            return ConditionalResult(
                run=False,
                skip_lines=(
                    f"{_YELLOW}skip [{entry.id}]{_RESET} {script} — coverage report not found",
                    "   run: pytest --cov=kairix --cov-report=xml first, then re-run this check.",
                ),
            )
        return ConditionalResult(run=True, extra_args=(str(coverage_xml),))

    return _conditional


# ── seam 4: paved_road_footer (the affordance line under a FAIL) ─────────────


def _paved_road_footer(entry: RuleEntry) -> str | None:
    """The affordance line the shared runner prints under a FAIL when the rule
    carries a curated ``exemplar`` — points the agent at the query surface."""
    if entry.exemplar:
        return f"  paved-road: python3 scripts/checks/rules.py --rule {entry.id}"
    return None


# ── shared injection-seam kwargs (one place; threaded into every dispatch) ───


def _seam_kwargs(*, skip_coverage: bool) -> dict[str, Any]:
    """The injection seams the shared runner consumes, as a kwargs dict."""
    return {
        "repo_root": REPO_ROOT,
        "checks_dir": CHECKS_DIR,
        "scope_resolver": _kairix_scope_resolver,
        "enumeration_narrower": _kairix_enumeration_narrower,
        "paved_road_footer": _paved_road_footer,
        "conditional_check": _make_conditional_check(skip_coverage=skip_coverage),
    }


# ── back-compat surface for the runner / staged-selection unit tests ─────────
#
# tests/checks/test_catalogue_runner.py + tests/checks/test_staged_selection.py
# drive the kairix runner through these module-level symbols. They now delegate
# to the shared package (binding kairix's scope_resolver where the package
# signature takes one), so the tests keep asserting kairix's exact selection +
# dispatch behaviour without re-implementing it here.


def _select_all() -> list[RuleEntry]:
    """In-scope rules for ``--all``: dispatchable AND ``run_all``."""
    return _pkg_select_all(RULES)


def _select_gate(gate_id: str) -> list[RuleEntry]:
    """Rules whose catalogue ``id`` matches ``gate_id`` (case-insensitive)."""
    return _pkg_select_gate(RULES, gate_id)


def _dispatches_in_process(entry: RuleEntry) -> bool:
    """True iff ``entry``'s check runs in-process (pure-python, no runtime arg)."""
    return _pkg_dispatches_in_process(entry)


def _load_check_main(script: str) -> Any:
    """Import ``script``'s check module and return a zero-arg ``main`` invoker."""
    return _pkg_load_check_main(script)


def _footer_config() -> RunnerConfig:
    """A minimal ``RunnerConfig`` carrying the paved-road footer hook + the
    coverage conditional-check seam — used by the per-rule back-compat shims so
    a FAILING rule with an exemplar still prints the affordance line."""
    return RunnerConfig(
        repo_root=REPO_ROOT,
        checks_dir=CHECKS_DIR,
        paved_road_footer=_paved_road_footer,
        conditional_check=_make_conditional_check(skip_coverage=True),
    )


def _run_one_inprocess(entry: RuleEntry, ctx: CheckContext) -> int:
    """Dispatch ``entry``'s pure-python check IN-PROCESS (the shared impl).

    The shared ``_run_one_inprocess`` takes the resolved ``RunnerConfig`` (for
    the paved-road footer hook), not a bare context — the parse cache is shared
    via the surrounding ``ctx.install()`` the caller wraps this in (the ``ctx``
    argument is kept for the existing test signature)."""
    return _pkg_run_one_inprocess(entry, _footer_config())


def _run_one(entry: RuleEntry, *, skip_coverage: bool) -> int | None:
    """Back-compat: dispatch ``entry`` once and print its named verdict +
    paved-road footer — the per-rule output-contract surface
    tests/architecture/test_rules_query.py drives. Routes through the shared
    runner's in-process or subprocess path depending on the rule's shape;
    returns 0 pass / 1 fail / ``None`` skip."""
    cfg = RunnerConfig(
        repo_root=REPO_ROOT,
        checks_dir=CHECKS_DIR,
        paved_road_footer=_paved_road_footer,
        conditional_check=_make_conditional_check(skip_coverage=skip_coverage),
    )
    if _pkg_dispatches_in_process(entry):
        ctx = CheckContext(repo_root=REPO_ROOT)
        with ctx.install():
            return _pkg_run_one_inprocess(entry, cfg)
    return _pkg_run_one_subprocess(entry, cfg)


def _staged_decisions(staged: list[str]) -> list[tuple[RuleEntry, StagedDecision]]:
    """Per-rule staged decision for every ``--all`` entry, in catalogue order,
    deduped by resolved script — binding kairix's scope resolver."""
    out: list[tuple[RuleEntry, StagedDecision]] = []
    seen_scripts: set[str] = set()
    for entry in _select_all():
        script = resolve_script(entry)
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
        out.append((entry, decide(entry, script, staged, _kairix_scope_resolver)))
    return out


def _rule_touches_staged(entry: RuleEntry, staged: list[str]) -> bool:
    """Back-compat shim: True iff ``entry`` would be DISPATCHED for ``staged``."""
    return decide(entry, resolve_script(entry), staged, _kairix_scope_resolver).run


def _dispatch_staged(staged: list[str], *, skip_coverage: bool) -> int:
    """Precise staged dispatch — delegates to the shared runner's staged mode
    with kairix's seams. Returns the process exit code (0 clean, 1 any fail).

    Kept as a kairix-internal entry point for tests/checks/test_staged_selection.py,
    which drives the real staged dispatch over probe files."""
    return run(RULES, mode="staged", staged_files=staged, **_seam_kwargs(skip_coverage=skip_coverage)).exit_code


# ── CLI entrypoint ───────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Parse ``--all`` / ``--staged`` / ``--gate`` + ``--skip-coverage`` and
    dispatch through the shared runner with kairix's injection seams.

    ``--skip-coverage`` is a kairix-specific flag the shared ``main_cli`` parser
    doesn't carry (``run-all.sh`` / ``safe-commit.sh`` pass it for the F7/F9
    coverage stage). This shim owns it, strips it, and threads it into the
    conditional-check seam."""
    parser = argparse.ArgumentParser(
        prog="run_checks.py",
        description="Catalogue-driven fitness-function runner (kairix thin consumer of tc_fitness.runner).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="run every in-scope rule (default)")
    group.add_argument("--staged", action="store_true", help="run only rules a staged change could trip")
    group.add_argument("--gate", metavar="ID", help="run one rule by catalogue id (e.g. F26)")
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="skip the F7/F9 per-file coverage check (needs a coverage.xml report)",
    )
    args = parser.parse_args(argv)

    seams = _seam_kwargs(skip_coverage=args.skip_coverage)

    if args.gate:
        verdict: Verdicts = run(RULES, mode="gate", gate_id=args.gate, **seams)
        if verdict.failures == ["<no-such-gate>"]:
            return 2
        return verdict.exit_code

    if args.staged:
        return run(RULES, mode="staged", **seams).exit_code

    return run(RULES, mode="all", **seams).exit_code


if __name__ == "__main__":
    sys.exit(main())
