"""F97: every agent-facing result surface embeds or returns the shared
``SourceRef`` breadcrumb (PLA-274).

The canonical ``source_uri`` is the resolvable pointer behind a hit, but
historically every agent surface hand-rolled its own pointer — ``search``
exposed ``path`` (a sometimes-munged synthetic chunk key), ``entity``
exposed ``vault_path``, ``timeline`` dropped the page + collection,
``contradict`` cited a bare path, ``research`` reduced chunks to
``{path, snippet}``, ``prep`` emitted human titles. Each surface drifted
independently, so "cite / re-open your source" worked nowhere
consistently.

PLA-274 defines ONE shared frozen dataclass
:class:`kairix.core.protocols.SourceRef` and threads ``source_uri`` through
the retrieval path. This rule is the structural lock that stops the drift
re-accruing: every registered agent-facing result-row dataclass must either

  * declare a field annotated with ``SourceRef`` (``SourceRef`` /
    ``SourceRef | None`` / ``list[SourceRef]`` / ``tuple[SourceRef, ...]``
    / ``Sequence[SourceRef]`` — the EMBED option), OR
  * define a ``source_ref`` method/property (the RETURN option).

Either satisfies deliverable 3's "EMBED or RETURN a SourceRef". The three
later breadcrumb issues (chunk-expansion, brief citations, facts
breadcrumb) conform by appending their new row type to
``_REGISTERED_SURFACES`` below — adding a surface to the registry is the
affordance, and the gate then holds it to the contract.

Intentionally NOT caught (precision over recall):
  * Arbitrary dataclasses that happen to carry a ``path`` field — only the
    explicitly-registered agent-facing surfaces are scanned, so the rule
    has zero false-positive noise against the hundreds of unrelated
    dataclasses in the tree.
  * The SHAPE of the SourceRef a method returns (the AST can't prove a
    ``-> SourceRef`` return without resolving types). The method's mere
    presence is the structural proof; the unit/contract tests prove the
    behaviour.

If a registered surface module does not exist (renamed / deleted) or its
class is gone, that IS a violation — the contract names a surface that
vanished, which is exactly the drift this rule guards.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tc_fitness import REPO_ROOT, gate

# The agent-facing result-row dataclasses that MUST carry the shared
# SourceRef breadcrumb. (repo-relative module path, class name). Append a
# row here when a new agent surface ships — that's the conforming move the
# later PLA-274 follow-up issues make.
_REGISTERED_SURFACES: tuple[tuple[str, str], ...] = (
    ("kairix/use_cases/search.py", "SearchHit"),
    ("kairix/use_cases/timeline.py", "TimelineHit"),
    ("kairix/use_cases/entity_get.py", "EntityGetOutput"),
    ("kairix/use_cases/contradict.py", "ContradictionHit"),
    ("kairix/use_cases/research.py", "ResearchChunk"),
    ("kairix/use_cases/prep.py", "PrepOutput"),
)

# The shared breadcrumb type name. A field whose annotation references this
# name (in any container / union shape) satisfies the EMBED option.
_SOURCE_REF_NAME = "SourceRef"

# The accessor name that satisfies the RETURN option.
_SOURCE_REF_ACCESSOR = "source_ref"

REMEDIATION = """F97: an agent-facing result surface does not embed or
return the shared SourceRef breadcrumb (PLA-274).

Every result-row an agent reads (search / timeline / entity / prep /
research / contradict, plus later breadcrumb surfaces) must expose the
canonical, resolvable source pointer the SAME way — through the shared
``kairix.core.protocols.SourceRef`` — so "cite / re-open your source"
works everywhere and no surface re-hand-rolls its own pointer.

fix: on the flagged dataclass, EITHER
  - add a field typed ``SourceRef`` (e.g. ``ref: SourceRef`` /
    ``sources: list[SourceRef]``), OR
  - add a ``source_ref(self) -> SourceRef`` method that builds one via
    ``SourceRef.of(path=..., source_uri=..., ...)`` so the source_uri→path
    fallback + non-paged locator derivation apply uniformly.
If you ADDED a new agent surface, also append its (module, class) row to
``_REGISTERED_SURFACES`` in scripts/checks/check_f97_source_ref_contract.py.
next: re-run python3 scripts/checks/check_f97_source_ref_contract.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh \"feat(search): carry SourceRef on <surface>\"

Pass example:
  @dataclass(frozen=True)
  class TimelineHit:
      path: str
      title: str
      def source_ref(self) -> SourceRef:
          return SourceRef.of(path=self.path, source_uri=self.source_uri)

  @dataclass(frozen=True)
  class ResearchChunk:
      ref: SourceRef          # EMBED option — a SourceRef-typed field

Forbidden example:
  @dataclass(frozen=True)
  class TimelineHit:        # F97 — no SourceRef field, no source_ref() method
      path: str             # bare path, hand-rolled pointer
      title: str

Why: see the PLA-274 breadcrumb-contract issue — the canonical
``documents.source_uri`` is the resolvable pointer; ``path`` is a
sometimes-munged display key. One shared SourceRef on every surface is the
structural fix that stops the per-surface pointer drift re-accruing."""


def _annotation_mentions_sourceref(node: ast.expr | None) -> bool:
    """Return True iff the annotation references ``SourceRef`` anywhere.

    Walks the whole annotation subtree so every container / union shape is
    covered: ``SourceRef``, ``SourceRef | None``, ``list[SourceRef]``,
    ``tuple[SourceRef, ...]``, ``Sequence[SourceRef]``,
    ``Optional[SourceRef]``, and the string forward-ref ``"SourceRef"``.
    """
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == _SOURCE_REF_NAME:
            return True
        if isinstance(child, ast.Attribute) and child.attr == _SOURCE_REF_NAME:
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            # ``"SourceRef"`` / ``"list[SourceRef]"`` forward-ref string.
            if _SOURCE_REF_NAME in child.value:
                return True
    return False


def _class_satisfies_contract(cls: ast.ClassDef) -> bool:
    """True iff the class embeds a SourceRef field OR defines source_ref()."""
    for body_node in cls.body:
        # EMBED — a field annotated with SourceRef.
        if isinstance(body_node, ast.AnnAssign) and _annotation_mentions_sourceref(body_node.annotation):
            return True
        # RETURN — a method/property named ``source_ref``.
        if isinstance(body_node, ast.FunctionDef | ast.AsyncFunctionDef) and body_node.name == _SOURCE_REF_ACCESSOR:
            return True
    return False


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef | None:
    """Return the top-level ``ClassDef`` named ``class_name`` (or None)."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Return synthetic ``<module>::<Class>`` paths for every registered
    agent-facing surface that neither embeds nor returns a ``SourceRef``.

    A registered module that is missing, unparseable, or whose class is
    gone is reported as a violation — the contract names a surface that
    must exist and carry the breadcrumb.
    """
    violations: set[Path] = set()
    for rel_module, class_name in _REGISTERED_SURFACES:
        synthetic = Path(f"{rel_module}::{class_name}")
        module_file = repo_root / rel_module
        if not module_file.is_file():
            violations.add(synthetic)
            continue
        try:
            tree = ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            violations.add(synthetic)
            continue
        cls = _find_class(tree, class_name)
        if cls is None or not _class_satisfies_contract(cls):
            violations.add(synthetic)
    return violations


def main() -> int:
    violations = collect_violations()
    return gate("f97", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
