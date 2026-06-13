"""F52: every flag("<name>") call site references a real registry entry.

AST scan over ``kairix/**/*.py`` for ``flag("<name>")`` calls where
``flag`` is bound from ``kairix.core.features``. For each, the string
literal must match a key in ``REGISTRY``. Catches typos and dead-flag
references after retirement.

Skips the registry file itself (chicken-egg — it defines the names).

Defensive: vacuous-green when ``kairix.core.features`` is not importable
(PR-2 may not have landed yet).

Per F21, REMEDIATION carries ``fix:`` / ``next:`` / ``run:`` markers.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

KAIRIX_DIR = REPO_ROOT / "kairix"
REGISTRY_REL_PATH = Path("kairix/core/features/registry.py")
FEATURES_MODULE_PREFIX = "kairix.core.features"

REMEDIATION = """F52: flag("<name>") call site references a name not in REGISTRY.
fix: either correct the typo OR add the missing entry to
     kairix/core/features/registry.py REGISTRY dict. Every flag name must
     be declared in REGISTRY before it can be referenced.
next: see docs/architecture/feature-flag-architecture.md §3.2 (FeatureFlag
      value object) + §6 (F52 mechanics).
run: python3 scripts/checks/check_f52_flag_call_sites.py

Pass example:
  # kairix/core/search/ranker.py
  from kairix.core.features import flag
  if flag("hybrid_ranker_v2"):           # name is declared in REGISTRY
      ranker = HybridRankerV2(...)
  else:
      ranker = HybridRankerV1(...)

Forbidden example:
  # kairix/core/search/ranker.py
  from kairix.core.features import flag
  if flag("hybrid_ranker_v3"):           # typo; REGISTRY has v2 not v3
      ranker = HybridRankerV3(...)        # always returns the OFF branch
                                          # silently — bug is invisible."""


class _FlagAliasResolver(ast.NodeVisitor):
    """Track local names that resolve to ``kairix.core.features.flag``.

    Recognises:
      * ``from kairix.core.features import flag``  -> {"flag"}
      * ``from kairix.core.features import flag as ff``  -> {"ff"}
      * ``import kairix.core.features``            -> module access only
      * ``import kairix.core.features as features`` -> alias access only
    """

    def __init__(self) -> None:
        self.flag_aliases: set[str] = set()
        self.module_aliases: set[str] = set()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == FEATURES_MODULE_PREFIX:
            for alias in node.names:
                if alias.name == "flag":
                    self.flag_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == FEATURES_MODULE_PREFIX:
                # Bare ``import kairix.core.features`` binds 'kairix' locally;
                # ``import kairix.core.features as features`` binds 'features'.
                self.module_aliases.add(alias.asname or "kairix.core.features")
        self.generic_visit(node)


def _is_flag_call(node: ast.Call, aliases: _FlagAliasResolver) -> bool:
    """Return True if ``node`` is a call to the ``flag`` function from
    ``kairix.core.features``.

    Recognised shapes:
      * ``flag("x")`` where ``flag`` is in ``aliases.flag_aliases``
      * ``features.flag("x")`` where ``features`` is a module alias
      * ``kairix.core.features.flag("x")`` (fully qualified)
    """
    f = node.func
    if isinstance(f, ast.Name) and f.id in aliases.flag_aliases:
        return True
    if isinstance(f, ast.Attribute) and f.attr == "flag":
        # Module-alias form: ``<alias>.flag(...)``
        if isinstance(f.value, ast.Name) and f.value.id in aliases.module_aliases:
            return True
        # Fully qualified ``kairix.core.features.flag(...)``
        if isinstance(f.value, ast.Attribute) and f.value.attr == "features":
            inner = f.value.value
            if isinstance(inner, ast.Attribute) and inner.attr == "core":
                base = inner.value
                if isinstance(base, ast.Name) and base.id == "kairix":
                    return True
    return False


def _collect_call_sites(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, flag_name)]`` for each ``flag("...")`` call.

    Skips calls where the first arg is not a plain string literal (e.g.
    ``flag(name_var)`` — non-literal calls can't be statically verified
    and are out-of-scope for F52).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    aliases = _FlagAliasResolver()
    aliases.visit(tree)

    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_flag_call(node, aliases):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            sites.append((node.lineno, first.value))
    return sites


def _load_registry_names() -> set[str] | None:
    """Return the set of REGISTRY keys, or None when the module is absent.

    Defensive import — PR-2 may not be landed yet, so the gate stays
    vacuous-green.
    """
    try:
        from kairix.core.features.registry import REGISTRY
    except ImportError:
        return None
    return set(REGISTRY.keys())


def find_violations(registry_names: set[str]) -> list[str]:
    """Return ``["<path>:<line>:<unknown_name>", ...]`` sorted."""
    flagged: list[str] = []
    if not KAIRIX_DIR.is_dir():
        return flagged

    skip_path = REPO_ROOT / REGISTRY_REL_PATH
    for path in sorted(KAIRIX_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.resolve() == skip_path.resolve():
            continue
        for lineno, name in _collect_call_sites(path):
            if name not in registry_names:
                rel = path.resolve().relative_to(REPO_ROOT).as_posix()
                flagged.append(f"{rel}:{lineno}:{name}")
    return sorted(flagged)


def main() -> int:
    """Return 0 when clean / vacuous-green; 1 on net-new violations."""
    registry_names = _load_registry_names()
    if registry_names is None:
        print("ok [arch:f52-flag-call-sites] — kairix.core.features absent; vacuous-green.")
        return 0

    violations = find_violations(registry_names)
    if not violations:
        print("ok [arch:f52-flag-call-sites] — clean.")
        return 0

    print("FAIL [arch:f52-flag-call-sites] — flag() call site(s) reference unknown name:")
    for v in violations:
        print(f"  {v}")
    print()
    print(REMEDIATION)
    return 1


if __name__ == "__main__":
    sys.exit(main())
