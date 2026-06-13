"""F35: ``kairix/connectors/<name>/**`` may not import another connector or
any extractor.

Each connector under ``kairix/connectors/<name>/`` is independently shippable;
importing a sibling connector — or reaching into the extractor layer directly
— breaks that guarantee. Shared concerns go through ``kairix/core/connectors/``;
extraction is dispatched through the Extractor Protocol, not imported.

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``sibling-plugin`` mode with the extractor ban as
an extra forbidden prefix; this module re-exports the back-compat surface
(``collect_violations`` / ``main`` / ``REMEDIATION``) the F35 unit test loads
by file path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

REMEDIATION = """Refactor to remove the cross-connector / extractor import —
a connector must not depend on another connector or reach into the extractor
layer directly.

fix: extract the shared concern to kairix/core/connectors/ (Bronze write,
Silver chunking, signal extraction, cursor management — anything universal
across source families goes in the orchestration layer per §2). Extraction
goes through the Extractor Protocol via the registry, not by direct import.
If the concern is genuinely source-specific shape, duplicate it inline
rather than importing another connector; plugins must stay independently
shippable as separate pip distributions.
next: re-run python3 scripts/checks/check_f35_no_cross_connector.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(connectors): move <concern> to kairix/core/connectors/"

Pass example:
  # kairix/connectors/sharepoint/sync.py
  from kairix.transport.http import http_client          # shared transport
  from kairix.connectors._base import SourceConnector    # shared base
  from kairix.core.protocols import RawArtefact          # Protocol surface

Forbidden example:
  # kairix/connectors/sharepoint/sync.py
  from kairix.connectors.obsidian import scan_vault      # F35 — sibling connector
  import kairix.connectors.dex_crm.client                # F35 — sibling connector
  from kairix.extractors.markitdown import MarkitdownExtractor  # F35 — extractor layer

Why: see docs/architecture/connector-ingestion-architecture.md §2 "Layer
responsibilities" and §5.1 "Weak runtime encapsulation". Each connector is
meant to ship independently (a third party can pip install
kairix-connector-foo with zero kairix changes); a connector that imports
another can't be split out without dragging its sibling along, and the
dependency graph becomes a tangle that defeats the plugin model. Direct
extractor imports re-introduce the chunking-duplication failure mode that
the Bronze/Silver split exists to prevent (§4)."""

RULE = register(
    ImportBoundaryRule(
        name="f35",
        roots=("kairix/connectors",),
        mode="sibling-plugin",
        extra_forbidden_prefixes=("kairix.extractors",),
        remediation=REMEDIATION,
    )
)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F35 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
