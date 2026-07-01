"""F98: every agent result-row surface exposes an expand-acceptable locator,
and ``expand`` accepts a source_uri-only call (the anti-dead-end lock, PLA-297).

The wave's centrepiece — "search hit → expand the neighbouring chunks" — used
to dead-end on document / section-level (L2) hits: those carry a ``source_uri``
but ``seq=null``, so an agent falling back to ``expand(source_uri, seq=0)`` hit
``no chunk stored at <uri>#0`` with no recoverable path. PLA-297 makes a
source_uri-only locator a first-class expand key, so a doc-level hit resolves
its chunks by prefix (or returns the whole document with an explicit
"no finer chunks" signal) instead of failing.

This rule is the structural lock — a sibling to F97 (PLA-274) — that stops the
dead-end re-accruing. It has TWO limbs:

  * **Limb A — every registered agent result-row surface exposes an
    expand-acceptable locator.** Each surface (search / timeline / entity /
    prep / research / contradict / expand) must expose the resolvable
    ``source_uri`` an agent feeds to ``expand`` — via a ``source_ref()``
    accessor, a ``SourceRef``-typed field, OR a ``source_uri`` field. That is
    the pointer the expand handoff consumes.

  * **Limb B — ``expand`` accepts a source_uri-only call.**
    ``kairix/use_cases/expand.py::run_expand`` must declare its ``seq``
    parameter OPTIONAL (a default value or a ``| None`` annotation), so a
    locator WITHOUT a seq is expandable and can't dead-end at a required
    ``#seq`` again.

Intentionally NOT caught (precision over recall):
  * Arbitrary dataclasses carrying a ``path`` / ``source_uri`` field — only the
    explicitly-registered agent surfaces are scanned, so the rule has zero
    false-positive noise against unrelated dataclasses in the tree.
  * The RUNTIME behaviour of the source_uri-only path (that it actually
    resolves the right window) — the AST proves only the structural
    affordance; the unit / BDD / E2E tests prove the behaviour.

If a registered surface module is missing / unparseable / its class is gone,
that IS a violation — the contract names a surface that must exist and stay
expand-acceptable, which is exactly the drift this rule guards.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tc_fitness import REPO_ROOT, gate

# Limb A — the agent result-row surfaces that MUST expose an expand-acceptable
# locator. (repo-relative module path, class name). Append a row here when a
# new agent surface ships that hands a hit off to expand.
_EXPAND_LOCATOR_SURFACES: tuple[tuple[str, str], ...] = (
    ("kairix/use_cases/search.py", "SearchHit"),
    ("kairix/use_cases/timeline.py", "TimelineHit"),
    ("kairix/use_cases/entity_get.py", "EntityGetOutput"),
    ("kairix/use_cases/prep.py", "PrepOutput"),
    ("kairix/use_cases/research.py", "ResearchChunk"),
    ("kairix/use_cases/contradict.py", "ContradictionHit"),
    ("kairix/use_cases/expand.py", "ExpandedChunk"),
)

# Limb B — the expand entry point whose seq parameter must stay OPTIONAL.
_EXPAND_MODULE = "kairix/use_cases/expand.py"
_EXPAND_FUNCTION = "run_expand"
_SEQ_PARAM = "seq"

# The shared breadcrumb type name (the EMBED / source_ref option) and the bare
# locator field name (the plain option) that make a surface expand-acceptable.
_SOURCE_REF_NAME = "SourceRef"
_SOURCE_REF_ACCESSOR = "source_ref"
_SOURCE_URI_FIELD = "source_uri"

REMEDIATION = """F98: an agent result-row surface does not expose an
expand-acceptable locator, OR expand no longer accepts a source_uri-only call
(the anti-dead-end lock, PLA-297).

Every result-row an agent reads (search / timeline / entity / prep / research /
contradict / expand) must expose the resolvable ``source_uri`` it can hand to
``expand`` — so a document / section-level (L2) hit whose ``seq`` is null still
has a recoverable path instead of dead-ending at ``no chunk stored at
<uri>#0``. And ``expand`` must accept that source_uri WITHOUT a seq.

fix: EITHER
  - on the flagged surface dataclass, expose the locator — add a
    ``source_ref(self) -> SourceRef`` accessor (built via ``SourceRef.of(...)``),
    a ``SourceRef``-typed field, OR a ``source_uri`` field; OR
  - on ``kairix/use_cases/expand.py::run_expand``, keep ``seq`` optional
    (``seq: int | None = None``) so a source_uri-only locator is expandable.
If you ADDED a new agent surface, also append its (module, class) row to
``_EXPAND_LOCATOR_SURFACES`` in
scripts/checks/check_f98_expand_locator_contract.py.
next: re-run python3 scripts/checks/check_f98_expand_locator_contract.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh \"feat(expand): expand-acceptable locator on <surface>\"

Pass example:
  @dataclass(frozen=True)
  class TimelineHit:
      path: str
      source_uri: str = \"\"          # a source_uri field — expand-acceptable
      def source_ref(self) -> SourceRef:
          return SourceRef.of(path=self.path, source_uri=self.source_uri)

  def run_expand(source_uri: str, seq: int | None = None, ...):  # seq optional
      ...

Forbidden example:
  @dataclass(frozen=True)
  class TimelineHit:        # F98 — no source_ref/SourceRef/source_uri locator
      path: str             # bare path — expand can't key on it
      title: str

  def run_expand(source_uri: str, seq: int, ...):  # F98 — seq REQUIRED, a
      ...                                           # source_uri-only hit dead-ends

Why: see PLA-297 — a doc-level hit carries source_uri but no seq; making
source_uri a first-class expand key (surface exposes it + expand accepts it)
is the structural fix that stops the L2 expand dead-end re-accruing."""


def _annotation_allows_none(node: ast.expr | None) -> bool:
    """True iff the annotation admits ``None`` (``int | None`` / ``Optional[int]``)."""
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and child.value is None:
            return True
        # ``Optional[...]`` — the subscript's value is the ``Optional`` name.
        if isinstance(child, ast.Name) and child.id == "Optional":
            return True
    return False


def _annotation_mentions_sourceref(node: ast.expr | None) -> bool:
    """True iff the annotation references ``SourceRef`` in any container shape."""
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == _SOURCE_REF_NAME:
            return True
        if isinstance(child, ast.Attribute) and child.attr == _SOURCE_REF_NAME:
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str) and _SOURCE_REF_NAME in child.value:
            return True
    return False


def _class_exposes_locator(cls: ast.ClassDef) -> bool:
    """True iff the class exposes an expand-acceptable locator.

    Any of: a ``source_ref`` accessor, a ``SourceRef``-typed field, or a field
    named ``source_uri`` (the resolvable pointer expand keys on).
    """
    for body_node in cls.body:
        if isinstance(body_node, ast.AnnAssign):
            target = body_node.target
            if isinstance(target, ast.Name) and target.id == _SOURCE_URI_FIELD:
                return True
            if _annotation_mentions_sourceref(body_node.annotation):
                return True
        if isinstance(body_node, ast.FunctionDef | ast.AsyncFunctionDef) and body_node.name == _SOURCE_REF_ACCESSOR:
            return True
    return False


def _param_is_optional(func: ast.FunctionDef, name: str) -> bool:
    """True iff parameter ``name`` on ``func`` is optional (default or ``| None``)."""
    positional = list(func.args.posonlyargs) + list(func.args.args)
    default_start = len(positional) - len(func.args.defaults)
    for index, arg in enumerate(positional):
        if arg.arg == name:
            return index >= default_start or _annotation_allows_none(arg.annotation)
    for arg, default in zip(func.args.kwonlyargs, func.args.kw_defaults, strict=True):
        if arg.arg == name:
            return default is not None or _annotation_allows_none(arg.annotation)
    return False


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _find_function(tree: ast.Module, func_name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def _parse(module_file: Path) -> ast.Module | None:
    """Parse ``module_file`` to an AST, or ``None`` when it can't be read/parsed."""
    if not module_file.is_file():
        return None
    try:
        return ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _surface_violations(repo_root: Path) -> set[Path]:
    """Limb A — surfaces that do not expose an expand-acceptable locator."""
    violations: set[Path] = set()
    for rel_module, class_name in _EXPAND_LOCATOR_SURFACES:
        tree = _parse(repo_root / rel_module)
        cls = _find_class(tree, class_name) if tree is not None else None
        if cls is None or not _class_exposes_locator(cls):
            violations.add(Path(f"{rel_module}::{class_name}"))
    return violations


def _expand_acceptance_violation(repo_root: Path) -> set[Path]:
    """Limb B — run_expand must accept a source_uri-only (optional-seq) call."""
    tree = _parse(repo_root / _EXPAND_MODULE)
    func = _find_function(tree, _EXPAND_FUNCTION) if tree is not None else None
    if func is None or not _param_is_optional(func, _SEQ_PARAM):
        return {Path(f"{_EXPAND_MODULE}::{_EXPAND_FUNCTION}")}
    return set()


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Synthetic ``<module>::<name>`` paths for every failing limb (PLA-297)."""
    return _surface_violations(repo_root) | _expand_acceptance_violation(repo_root)


def main() -> int:
    violations = collect_violations()
    return gate("f98", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
