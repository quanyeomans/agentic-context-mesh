"""Precise per-rule staged selection for the fitness runner (#499 Phase 2 stage 4b).

``run_checks.py --staged`` must give fast feedback that the STAGED CHANGE
introduced no fitness violation. The non-negotiable property is **no false
negative on staged changes**: if staging file(s) introduces a violation of
rule R, staged mode MUST run R. Speed is the goal, but a fast path that
silently MISSES a violation is worse than a slow one. When in doubt, run the
rule — the full ``--all`` gate is the merge bar, so over-running is cheap and
under-running is the only real danger.

This module turns each rule's catalogue metadata into a concrete decision:

* **scope predicate** — the repo-relative path prefixes whose staged change
  could trip the rule. Single-sourced: the explicit ``RuleEntry.staged_scope``
  wins; otherwise it is DERIVED from the rule's own detector (its
  import-boundary ``RULE.roots``, its ``FitnessRule.roots``, or — for the
  location/singleton engine — the ``kairix/`` tree those checks always walk).
  When no scope can be resolved, the predicate is ``None`` → the rule is
  treated as always-in-scope (fail-safe).

* **selection class** — from :data:`~_rule_catalogue.StagedClass`:
    - ``file-local`` — run over ``staged ∩ scope`` (and the runner scopes the
      shared file index to the staged files so an in-process check walks ONLY
      them). Skipped when that intersection is empty. Sound because a
      non-staged file's baseline-diff verdict is unchanged (its content is
      unchanged) and a deletion can only REMOVE a file-local violation.
    - ``relational`` — if any staged path is within ``scope``, run over the
      FULL scope (a deletion of the paired artefact, or a new surface file,
      can break a cross-file invariant even when the obvious file isn't
      staged).
    - ``always-run`` — run unconditionally (net-new-file / catalogue-currency
      / README / path-naming — the trigger is "any change at all").

The soundness contract is proven by ``tests/checks/test_staged_selection.py``
(the executed soundness battery + the staged-vs-full completeness table).
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tc_fitness
from _fitness_rule import FitnessRule
from _rule_catalogue import RuleEntry, StagedClass

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


def resolve_staged_scope(entry: RuleEntry, script: str) -> tuple[str, ...] | None:
    """The repo-relative path-prefix scope for ``entry`` under ``script``.

    Explicit ``entry.staged_scope`` always wins (single source of truth for
    the rules whose scope can't be derived — shell detectors, multi-tree
    standalone checks, relational rules with a BROADER trigger than their scan
    roots). Otherwise the scope is derived from the check's own detector via
    :func:`_roots_from_module`. ``None`` means "no resolvable scope" → the
    caller runs the rule unconditionally.
    """
    if entry.staged_scope is not None:
        return entry.staged_scope
    module_name = _module_name_for(script)
    if module_name is None:
        return None
    return _roots_from_module(module_name)


def _path_under(path: str, prefix: str) -> bool:
    """True if repo-relative ``path`` is the file ``prefix`` or sits under the
    directory ``prefix``. A ``prefix`` ending in a file suffix (``.py`` etc.)
    matches that exact file only."""
    if path == prefix:
        return True
    # Directory prefix: ``kairix`` matches ``kairix/...`` but not ``kairixx``.
    return path.startswith(prefix + "/")


def staged_in_scope(scope: tuple[str, ...] | None, staged: list[str]) -> list[str]:
    """The staged paths that fall within ``scope``.

    ``scope is None`` → every staged path is "in scope" (conservative). A
    concrete scope intersects each staged path against its prefixes.
    """
    if scope is None:
        return list(staged)
    return [p for p in staged if any(_path_under(p, prefix) for prefix in scope)]


@dataclass(frozen=True)
class StagedDecision:
    """The runner's decision for one rule against the staged set.

    Attributes:
        run: whether to dispatch the rule at all.
        reason: a short human-readable why (printed in the transparent
            staged ledger so narrowing is auditable, never silent).
        scope_files: for a ``file-local`` rule that should run, the staged
            files to restrict the shared file index to (so the in-process
            check walks ONLY them). Empty/``None`` for relational / always-run
            (those run over their full natural scope).
    """

    run: bool
    reason: str
    scope_files: tuple[str, ...] | None = None


def decide(entry: RuleEntry, script: str, staged: list[str]) -> StagedDecision:
    """Decide whether — and over what — to run ``entry`` given ``staged``.

    The three classes:

    * ``always-run`` → always dispatch (full scope).
    * ``relational`` → dispatch over full scope iff any staged path is within
      the rule's scope; else skip.
    * ``file-local`` → dispatch over ``staged ∩ scope`` iff that intersection
      is non-empty (and hand those files back so the runner scopes the file
      index); else skip.

    With no staged paths at all (``staged == []``), every rule runs — the
    pre-commit ``--all-files`` quirk must never silently pass.
    """
    klass: StagedClass = entry.staged_class

    if not staged:
        return StagedDecision(run=True, reason="no staged paths — run everything (fail-safe)")

    if klass == "always-run":
        return StagedDecision(run=True, reason="always-run (trigger is any change)")

    scope = resolve_staged_scope(entry, script)
    matched = staged_in_scope(scope, staged)

    if klass == "relational":
        if matched:
            where = "unresolved scope" if scope is None else ", ".join(scope)
            return StagedDecision(run=True, reason=f"relational — staged path in scope ({where}); full scope")
        return StagedDecision(run=False, reason="relational — no staged path in scope")

    # file-local
    if scope is None:
        # No resolvable scope → can't narrow soundly; run unconditionally.
        return StagedDecision(run=True, reason="file-local — scope unresolved; run (fail-safe)")
    if matched:
        return StagedDecision(
            run=True,
            reason=f"file-local — {len(matched)} staged file(s) in scope",
            scope_files=tuple(matched),
        )
    return StagedDecision(run=False, reason="file-local — no staged file in scope")


# ── file-index narrowing for a file-local rule ──────────────────────────
#
# When a file-local rule runs in staged mode, it only needs to RE-CHECK the
# staged files — every other in-scope file was clean at the previous commit
# and its content is unchanged, so its baseline-diff verdict is unchanged.
# Narrowing the rule's file enumeration to the staged set turns a full-tree
# walk into a handful of files. The narrowing is installed by intersecting
# the rule's own enumeration surfaces with the staged set, so NO per-check
# edit is needed and the verdict for the staged files is byte-identical to a
# full run (the same files are inspected, just fewer of them).
#
# Soundness note: this only narrows FILE-LOCAL rules, where a per-file
# verdict is independent of the other files. Relational and always-run rules
# are NEVER narrowed — they run over their full natural scope.


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
def restrict_enumeration(repo_root: Path, staged: list[str]) -> Iterator[None]:
    """Restrict the file-enumeration surfaces to the ``staged`` files for the
    duration of the ``with`` block, then restore them.

    Patches the enumeration entry points that file-local checks funnel
    through — :meth:`FitnessRule.enumerate_files` and the module-level
    :func:`tc_fitness.python_files` (plus every already-imported
    ``check_*`` module's local ``python_files`` binding) — so each returns
    only the staged files (intersected with what it would otherwise yield).
    The detectors' own ``is_in_scope`` / extension filtering still applies on
    top, so a staged file outside a rule's scope is still skipped.

    This is correctness-preserving for file-local rules specifically: the set
    of staged files a rule inspects is exactly ``what-it-would-walk ∩
    staged``, and the per-file verdict for each is identical to the full run.
    """
    staged_abs = frozenset((repo_root / s).resolve() for s in staged)

    real_enumerate = FitnessRule.enumerate_files
    real_python_files = tc_fitness.python_files

    def _scoped_enumerate(self: FitnessRule) -> list[Path]:
        return _filter_to_staged(real_enumerate(self), staged_abs)

    def _scoped_python_files(*roots: str, repo_root: Path | None = None, **kwargs: Any) -> list[Path]:
        full = real_python_files(*roots, repo_root=repo_root, **kwargs)
        return _filter_to_staged(full, staged_abs)

    # Patch the canonical surfaces. ``_fitness_rule`` does not bind
    # ``python_files`` itself (it uses ``FitnessRule.enumerate_files``), so only
    # the ABC method and the ``tc_fitness`` source function need patching here;
    # the per-check ``from tc_fitness import python_files`` bindings are
    # patched in the loop below.
    FitnessRule.enumerate_files = _scoped_enumerate  # type: ignore[method-assign]  # run-scoped staged narrowing; restored in finally
    tc_fitness.python_files = _scoped_python_files

    # Patch every already-imported check module that bound ``python_files`` by
    # value (``from tc_fitness import python_files``) so its local reference
    # also narrows. Record originals to restore exactly. ``module`` is the
    # dynamic check module object; ``setattr`` rebinds its local name.
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
        tc_fitness.python_files = real_python_files
        for patched, original in patched_modules:
            patched.python_files = original
