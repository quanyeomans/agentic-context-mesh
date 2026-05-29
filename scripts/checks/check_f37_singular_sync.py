"""F37: change-detection / sync code only under the connector trees.

Mirrors F29's singular-surface pattern (perf measurement only under
``kairix/quality/probe/``). Wave 1 of the connector + ingestion plan
(see ``docs/architecture/connector-ingestion-architecture.md`` §5.8 and
§6) introduces ``kairix/core/connectors/`` for the orchestration layer
and ``kairix/connectors/<name>/`` for per-source change detection.

The rule keeps every polling loop / watchdog observer / Graph
delta-query / cursor-advancement primitive in one of those two trees.
No parallel sync surfaces under ``kairix/worker.py``, ``kairix/corpus/``,
``kairix/core/temporal/``, or anywhere else.

Detection heuristic — deliberately conservative, easy to widen later:

  An import of a known change-detection library (``watchdog``,
  ``msgraph``, ``msgraph.core``, ``msgraph_core``, ``notion_client``,
  ``slack_sdk.rtm``, ``slack_sdk.socket_mode``, ``dulwich``) inside any
  ``kairix/**/*.py`` file that lives OUTSIDE the two allowed trees is
  flagged.

The AST signal for "polling-loop shape" (``while True:`` + ``time.sleep``)
is too fuzzy on its own — many legitimate non-sync loops match it
(retry-after backoff, healthcheck loops, the worker's tick loop). So
the detector keys on the import set, which is the unambiguous signal
that *change detection* is happening.

Today (pre-Wave 1) ``kairix/connectors/`` and ``kairix/core/connectors/``
do not exist; no other ``kairix/`` file imports any of the
change-detection libraries; the check passes trivially.

Allowed prefixes (sync code welcome here):
  - ``kairix/core/connectors/**`` — the orchestration layer.
  - ``kairix/connectors/<name>/**`` — per-source change detection.

Rejected prefixes (where a sync-library import is a regression):
  - everything else under ``kairix/``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT
from _fitness_rule import FitnessRule

# Module names whose presence in an import statement signals
# change-detection / sync code. Matched against the FIRST dotted
# segment of the import target (so ``from watchdog.observers import X``
# matches via "watchdog"; ``from msgraph.core import Y`` matches via
# "msgraph"; ``import notion_client`` matches via "notion_client").
_SYNC_LIBRARY_ROOTS: frozenset[str] = frozenset(
    {
        "watchdog",
        "msgraph",
        "msgraph_core",
        "notion_client",
        "slack_sdk",
        "dulwich",
    }
)

# Submodule patterns that further narrow ``slack_sdk`` to its real-time
# / socket-mode change-detection surfaces — the rest of the SDK
# (``slack_sdk.web``) is a one-shot HTTP client and is not a sync loop.
# An import is flagged if it matches a root above; for ``slack_sdk`` we
# additionally require one of these submodule tails to be present in
# the import target.
_SLACK_SYNC_TAILS: frozenset[str] = frozenset({"rtm", "socket_mode"})

# Allowed locations for sync-library imports (relative to repo root).
_ALLOWED_PREFIXES: tuple[Path, ...] = (
    Path("kairix") / "core" / "connectors",
    Path("kairix") / "connectors",
)

REMEDIATION = """Refactor to move change-detection / sync code under
kairix/connectors/<name>/ or kairix/core/connectors/. Those are the
two layers that own polling, watchdog observers, Graph delta-query
consumers, and cursor advancement. Any other location — kairix/worker.py,
kairix/corpus/, or a fresh kairix/<elsewhere>/ — creates a parallel
sync surface that drifts from the registry pattern.

fix: move sync code under kairix/connectors/<name>/ or kairix/core/connectors/.
     For per-source change detection (watchdog observers, Graph delta queries,
     long-polling adapters), put it under kairix/connectors/<name>/.
     For orchestration (cursor advancement, batch transaction, dead-letter),
     put it under kairix/core/connectors/.
next: register the connector via the kairix.connectors entry-point group so
     kairix/core/connectors/registry.py picks it up — no manual wiring needed.
run: python3 scripts/checks/check_f37_singular_sync.py to confirm green.

Pass example:
  kairix/connectors/obsidian/watcher.py        # imports watchdog — allowed (per-connector)
  kairix/connectors/sharepoint/delta.py        # imports msgraph — allowed (per-connector)
  kairix/core/connectors/pipeline.py           # orchestration — allowed
  kairix/core/connectors/cursor_store.py       # cursor mgmt — allowed

Forbidden example:
  kairix/worker.py imports watchdog            # F37 — parallel sync loop in worker
  kairix/corpus/poll.py imports msgraph        # F37 — sync code outside connector trees
  kairix/transport/notion_client.py            # F37 — sync code in transport layer

Why: see docs/architecture/connector-ingestion-architecture.md §5.8.
F37 mirrors F29's shape: one canonical surface for the work, so the
registry + cursor + dead-letter discipline carries through every new
source without parallel polling growing under worker.py or corpus/."""


def _import_targets(tree: ast.AST) -> list[str]:
    """Return the dotted module names referenced by every ``import``
    and ``from ... import`` in ``tree``.

    For ``import a.b.c`` we yield ``"a.b.c"``.
    For ``from a.b.c import x`` we yield ``"a.b.c"``.
    Relative imports (``from . import x``) are ignored — they cannot
    reach a third-party library by construction.
    """
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                targets.append(node.module)
    return targets


def _is_sync_import(target: str) -> bool:
    """True if a dotted ``target`` references a change-detection
    library. The root segment must be in ``_SYNC_LIBRARY_ROOTS``;
    ``slack_sdk`` additionally requires an rtm/socket_mode tail
    so the one-shot Web API surface is not flagged.
    """
    parts = target.split(".")
    if not parts:
        return False
    root = parts[0]
    if root not in _SYNC_LIBRARY_ROOTS:
        return False
    if root == "slack_sdk":
        return any(tail in parts[1:] for tail in _SLACK_SYNC_TAILS)
    return True


def _file_imports_sync_library(path: Path) -> bool:
    """True if ``path`` imports any change-detection library."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    return any(_is_sync_import(t) for t in _import_targets(tree))


def _is_allowed(rel_path: Path) -> bool:
    """True if a sync-importing file at ``rel_path`` (relative to repo
    root) is allowed under F37's exception roots.
    """
    parts = rel_path.parts
    for prefix in _ALLOWED_PREFIXES:
        prefix_parts = prefix.parts
        if len(parts) >= len(prefix_parts) and tuple(parts[: len(prefix_parts)]) == prefix_parts:
            return True
    return False


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Walk every .py file under ``repo_root/kairix/`` and return
    repo-relative paths whose imports reference a change-detection
    library AND that live outside the allowed connector trees.

    Scoped to ``kairix/`` (the production package). Files under
    ``tests/`` are not in scope — test fakes for connectors live in
    ``tests/fakes.py`` and do not import the real sync libraries.
    """
    kairix_dir = repo_root / "kairix"
    if not kairix_dir.exists():
        return set()

    violations: set[Path] = set()
    for path in kairix_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if not _file_imports_sync_library(path):
            continue
        try:
            rel = path.resolve().relative_to(repo_root)
        except ValueError:
            continue
        if _is_allowed(rel):
            continue
        violations.add(rel)
    return violations


class F37(FitnessRule):
    """F37 as a FitnessRule subclass — see module docstring."""

    name = "f37"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        if not _file_imports_sync_library(path):
            return False
        rel = self._repo_relative(path)
        return not _is_allowed(rel)


def main() -> int:
    return F37().run()


if __name__ == "__main__":
    sys.exit(main())
