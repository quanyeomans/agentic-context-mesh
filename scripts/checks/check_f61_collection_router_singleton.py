"""F61: CollectionRouter is the singular construction surface for chunk writers.

ADR v2 (``docs/architecture/connector-scope-topology/ADR.md``) §"Table B"
extends F38 (Silver singleton) with a CollectionRouter that decides which
backing chunk-writer instance handles a given Chunk's collection. F61
keeps every ``_SqliteChunkWriter(db, collection=name)`` construction
inside the framework — specifically under ``kairix/core/connectors/`` —
so the orchestrator can't bypass the router.

Today ``kairix/worker.py:_run_one_connector_batch`` (around line 305)
directly constructs ``_SqliteChunkWriter(db, collection=name)``. This
is exactly the regression Wave C will fix by threading construction
through ``CollectionRouter``. ``kairix/worker.py`` is grandfathered in
``.architecture/baseline/f61-files.txt``; the Wave C rewire pays it down.

Detection (AST):

  Find every ``ast.Call`` whose ``func`` resolves to the bare name
  ``_SqliteChunkWriter`` under ``kairix/**/*.py``. A file is allowed
  to construct one when it lives under ``kairix/core/connectors/``
  (the framework owns the writer). Anywhere else under ``kairix/``
  trips F61.

  ``module._SqliteChunkWriter(...)`` (attribute access from another
  package) is NOT matched — F26/F27/F34 already catch cross-layer
  reach; F61 protects the in-module construction surface.

Per F21, ``REMEDIATION`` carries ``fix:`` / ``next:`` / ``run:`` markers.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate

# Name of the writer class whose construction F61 watches.
_WRITER_CLASS_NAME = "_SqliteChunkWriter"

# The only directory tree allowed to construct ``_SqliteChunkWriter``
# directly. The CollectionRouter (Wave C) lives here too.
_FRAMEWORK_PREFIX = Path("kairix") / "core" / "connectors"

REMEDIATION = """F61: _SqliteChunkWriter constructor call sits outside kairix/core/connectors/.

Every chunk write must flow through CollectionRouter so the per-collection
routing logic stays in one place. Direct construction of
_SqliteChunkWriter(db, collection=...) from outside the framework fans
out the routing decision and silently bypasses any future per-collection
gating (rate-limits, dual-writes, schema overlays).

fix: replace the direct construction with a CollectionRouter lookup —
     CollectionRouter resolves (collection_name, cc_pair_id) → writer.
     Wave C lands the router; until then keep new writer construction
     under kairix/core/connectors/ only.
next: see docs/architecture/connector-scope-topology/ADR.md §"Table B"
     (CollectionRouter singleton) + 11-implementation-gap-analysis.md
     §"Critical observations" (worker.py dispatch coupling).
run: python3 scripts/checks/check_f61_collection_router_singleton.py

Pass example:
  # kairix/core/connectors/collection_router.py  — framework owns it
  class CollectionRouter:
      def writer_for(self, collection: str) -> ChunkWriter:
          if collection not in self._writers:
              self._writers[collection] = _SqliteChunkWriter(self._db, collection=collection)
          return self._writers[collection]

  # kairix/worker.py (Wave C rewire) — uses the router
  pipeline = ConnectorPipeline(
      ...,
      chunk_writer=collection_router.writer_for(name),
      ...,
  )

Forbidden example:
  # kairix/worker.py (today; grandfathered baseline)
  pipeline = ConnectorPipeline(
      ...,
      chunk_writer=_SqliteChunkWriter(db, collection=name),  # F61 fires
      ...,
  )"""


def _is_writer_ctor(call: ast.Call) -> bool:
    """True if ``call`` is a bare ``_SqliteChunkWriter(...)`` construction.

    Matches only ``ast.Name`` shapes — ``module._SqliteChunkWriter(...)``
    via attribute access is intentionally out of scope (already covered
    by F26/F27/F34).
    """
    return isinstance(call.func, ast.Name) and call.func.id == _WRITER_CLASS_NAME


def _is_in_framework(rel_path: Path) -> bool:
    """True if ``rel_path`` (repo-relative) sits under
    ``kairix/core/connectors/``."""
    parts = rel_path.parts
    prefix = _FRAMEWORK_PREFIX.parts
    return len(parts) >= len(prefix) and tuple(parts[: len(prefix)]) == prefix


def _file_constructs_writer(path: Path) -> bool:
    """True if ``path`` contains at least one ``_SqliteChunkWriter(...)``
    bare-name construction.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    # Cheap pre-filter — skip the AST cost when the writer name isn't
    # even mentioned in the file.
    if _WRITER_CLASS_NAME not in source:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_writer_ctor(node):
            return True
    return False


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Walk every .py file under ``repo_root/kairix/`` and return
    repo-relative paths that construct ``_SqliteChunkWriter(...)``
    outside ``kairix/core/connectors/``.
    """
    kairix_dir = repo_root / "kairix"
    if not kairix_dir.exists():
        return set()

    violations: set[Path] = set()
    for path in kairix_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if not _file_constructs_writer(path):
            continue
        try:
            rel = path.resolve().relative_to(repo_root)
        except ValueError:
            continue
        if _is_in_framework(rel):
            continue
        violations.add(rel)
    return violations


def main() -> int:
    """Return 0 when clean / vacuous-green; 1 on net-new violations."""
    return gate("f61", collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
