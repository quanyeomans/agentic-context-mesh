"""F37: change-detection / sync code only under the connector trees.

Mirrors F29's singular-surface shape, inverted: an import of a known
change-detection library (``watchdog`` / ``msgraph`` / ``notion_client`` /
``slack_sdk.rtm|.socket_mode`` / ``dulwich``) is forbidden anywhere under
``kairix/`` UNLESS the file lives in one of the two sanctioned homes —
``kairix/core/connectors/`` (orchestration) or ``kairix/connectors/<name>/``
(per-source change detection). The bare ``slack_sdk.web`` HTTP surface is not a
sync loop and is not flagged.

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``sync-lib`` mode; this module re-exports the
back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION`` /
``_is_sync_import``) the F37 unit test loads by file path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, _is_sync_lib_import, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

# Module names whose presence in an import signals change-detection / sync
# code. Matched against the FIRST dotted segment of the import target.
_SYNC_LIBRARY_ROOTS: tuple[str, ...] = (
    "watchdog",
    "msgraph",
    "msgraph_core",
    "notion_client",
    "slack_sdk",
    "dulwich",
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

RULE = register(
    ImportBoundaryRule(
        name="f37",
        roots=("kairix",),
        mode="sync-lib",
        forbidden_prefixes=_SYNC_LIBRARY_ROOTS,
        allowed_roots=("kairix/core/connectors", "kairix/connectors"),
        remediation=REMEDIATION,
    )
)


def _is_sync_import(target: str) -> bool:
    """True if a dotted ``target`` references a change-detection library.
    Re-exported for the F37 test; delegates to the engine classifier."""
    return _is_sync_lib_import(target, _SYNC_LIBRARY_ROOTS)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F37 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
