"""F5: No internal-name imports OR private-attribute access in test files.

Tests must drive every branch through the public surface; ``_``-prefixed
helpers are implementation detail. AST-based detection so the rule
correctly distinguishes:

  REJECTED:
      from kairix.foo import _bar
      from kairix.foo import bar, _baz
      from kairix.foo._impl import x        # importing FROM a private module

      import kairix.foo as alias
      alias._bar()                          # attribute access to _bar

      import kairix.foo
      kairix.foo._bar()                     # qualified attribute access

  ALLOWED:
      from kairix.foo import bar as _alias  # test-local rename
      from kairix.foo import _Bar as Bar    # rename of private name

      import kairix.foo as alias
      alias.bar()                           # public attribute access
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import python_files, repo_relative  # noqa: F401 — back-compat

REMEDIATION = """Refactor to drive the public function/class that calls the
private helper (no imports of ``_x`` from kairix.*; no imports from any
``kairix.<...>._private`` module; no attribute access like
``module._private`` on an imported kairix module) to pass.

fix: rewrite the test to call the public function/class on the kairix
boundary (one without a ``_`` prefix), passing a Fake* from
tests/fakes.py to drive the branch you care about. If the branch is
unreachable from the public surface, it's dead code — delete it.
next: re-run ``python3 scripts/checks/check_no_internal_imports.py``
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "test(<area>): drive <branch> through public surface"

If the public surface doesn't reach the branch you wanted to pin, the
branch is either dead code or the public contract is missing — in
either case, the answer is not to test the private name directly.

Pass example:
  from kairix.core.search import SearchPipeline  # public class
  result = SearchPipeline(retriever=FakeRetriever(...)).run('q')

Forbidden example: (direct private import)
  from kairix.core.search.bm25 import _tokenize  # private helper
  assert _tokenize('a b') == ['a', 'b']

Forbidden example (private attribute on imported module):
  from kairix.use_cases import eval_suite as _use_case
  _use_case._resolve_production_fact_store(db_path)  # private attr access

Forbidden example (private submodule import):
  from kairix.core.search._impl import build_index  # private module"""


def _is_kairix_module(module: str | None) -> bool:
    return module is not None and (module == "kairix" or module.startswith("kairix."))


def _module_is_private(module: str) -> bool:
    """True if any segment of the module path starts with ``_`` (excluding
    the ``__init__`` and ``__main__`` patterns).
    """
    return any(segment.startswith("_") and not segment.startswith("__") for segment in module.split("."))


def _is_private_name(name: str) -> bool:
    """True if ``name`` is single-underscore-private (not dunder)."""
    return name.startswith("_") and not name.startswith("__")


def _collect_kairix_aliases(tree: ast.AST) -> dict[str, str]:
    """Map each locally bound name to the dotted kairix module path it refers to.

    Recognises:

      - ``import kairix.foo.bar``
            binds top-level ``kairix`` → ``"kairix"`` (chained attribute
            access like ``kairix.foo.bar._x`` is then resolvable through
            the chain walker).
      - ``import kairix.foo.bar as ev``
            binds ``ev`` → ``"kairix.foo.bar"``.
      - ``from kairix import foo as ev``
            binds ``ev`` → ``"kairix.foo"`` (best-effort: ``foo`` may be a
            submodule or a public attribute — either way, accessing
            ``ev._x`` is reaching into a kairix-rooted namespace).
      - ``from kairix.foo import bar as ev``
            binds ``ev`` → ``"kairix.foo.bar"``.

    Names imported from non-kairix modules are not recorded; nor are
    names re-bound by `from kairix... import _x` (those are caught by
    the existing ImportFrom rule before we ever reach attribute scan).
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_kairix_module(alias.name):
                    continue
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    # ``import kairix.foo.bar`` binds the top-level
                    # ``kairix`` name; attribute access ``kairix.foo._x``
                    # is then detectable by walking the attribute chain.
                    top = alias.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if not _is_kairix_module(module):
                continue
            for alias in node.names:
                # ``from kairix.foo import bar [as ev]`` — ``ev`` (or
                # ``bar``) is rooted under ``kairix.foo``. We treat the
                # imported name optimistically as if it were a submodule;
                # if it's a public function the attribute check below
                # only flags ``_`` access on it (functions don't expose
                # private attrs, so legitimate code won't false-positive).
                bound = alias.asname or alias.name
                if alias.name == "*":
                    continue
                aliases[bound] = f"{module}.{alias.name}"
    return aliases


def _attribute_chain(node: ast.Attribute) -> tuple[str, list[str]] | None:
    """Decompose ``a.b.c.d`` into root ``"a"`` and attrs ``["b","c","d"]``.

    Returns ``None`` if the chain doesn't bottom out in a bare ``Name``
    (e.g. it starts from a function call result, a subscript, etc.).
    """
    attrs: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    attrs.reverse()
    return cur.id, attrs


def _import_from_violates(node: ast.ImportFrom) -> bool:
    """Existing rule: flag direct private-name imports / private-module
    sources on ``from kairix... import ...`` statements.
    """
    if not _is_kairix_module(node.module):
        return False
    if _module_is_private(node.module or ""):
        return True
    for alias in node.names:
        if _is_private_name(alias.name):
            # Rename-via-`as` does not exempt the import — the test still
            # depends on the private name's contract.
            return True
    return False


def _attribute_access_violates(
    node: ast.Attribute,
    aliases: dict[str, str],
) -> bool:
    """Flag any ``alias.[...].privatename[...]`` chain whose root binds to
    a kairix module path.

    Walks attrs left→right and returns True at the first ``_``-prefixed
    (non-dunder) attribute, since that's the moment the test reaches
    inside the private namespace.
    """
    chain = _attribute_chain(node)
    if chain is None:
        return False
    root, attrs = chain
    if root not in aliases:
        return False
    for attr in attrs:
        if _is_private_name(attr):
            return True
    return False


def file_has_violation(path: Path) -> bool:
    """Return True if ``path`` imports OR attribute-accesses a private
    name on a kairix.* module.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False

    # Pass 1: any direct private-name import?
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _import_from_violates(node):
            return True

    # Pass 2: any private attribute access on a kairix module alias?
    aliases = _collect_kairix_aliases(tree)
    if not aliases:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _attribute_access_violates(node, aliases):
            return True

    return False


class F5(FitnessRule):
    """F5 as a FitnessRule subclass — see module docstring."""

    name = "no-internal-test-imports"
    remediation = REMEDIATION
    roots = ("tests",)

    def file_has_violation(self, path: Path) -> bool:
        return file_has_violation(path)


def main() -> int:
    return F5().run()


if __name__ == "__main__":
    sys.exit(main())
