"""F34: ``kairix/core/connectors/**`` may not import ``kairix/connectors/**``
or ``kairix/extractors/**``.

The orchestration layer knows about Protocols (``kairix/core/protocols.py``),
not the per-source connector plugins or per-format extractor plugins. Mirrors
the F26 prefix shape exactly.

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``prefix`` mode; this module re-exports the
back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION``) the
F34 unit test loads by file path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

REMEDIATION = """Refactor to route the call through a Protocol in
kairix/core/protocols.py — orchestration code must not know which connector
or extractor plugin is loaded.

fix: define (or reuse) a Protocol in kairix/core/protocols.py (e.g.
SourceConnector, Extractor, BronzeStore, SilverProcessor) that expresses the
capability you need, then accept that Protocol as a constructor / factory
parameter. The production wire-up in kairix/core/connectors/registry.py
supplies the concrete plugin; tests inject a Fake from tests/fakes.py.
next: re-run python3 scripts/checks/check_f34_core_connector_layer_imports.py
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(core/connectors): route <capability> through Protocol"

Pass example:
  # kairix/core/connectors/pipeline.py
  from kairix.core.protocols import SourceConnector, Extractor, BronzeStore

  class ConnectorPipeline:
      def __init__(self, connector: SourceConnector, extractor: Extractor,
                   bronze: BronzeStore) -> None:
          self._connector = connector
          self._extractor = extractor
          self._bronze = bronze

Forbidden example:
  # kairix/core/connectors/pipeline.py
  from kairix.connectors.obsidian import make_connector       # F34
  from kairix.extractors.markitdown import MarkitdownExtractor  # F34

Why: see docs/architecture/connector-ingestion-architecture.md §2
"Layer responsibilities". Orchestration code that imports a concrete
connector or extractor ties the deployment shape into the domain layer and
reintroduces the class of bug the three-layer split exists to prevent (every
new connector means editing pipeline.py; every new format accretes inside
core/connectors/)."""

RULE = register(
    ImportBoundaryRule(
        name="f34",
        roots=("kairix/core/connectors",),
        mode="prefix",
        forbidden_prefixes=("kairix.connectors", "kairix.extractors"),
        remediation=REMEDIATION,
    )
)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F34 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
