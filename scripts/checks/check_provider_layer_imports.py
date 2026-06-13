"""F26: ``kairix/core/**`` may not import ``kairix/providers/**`` or
``kairix/transport/**``.

Domain code talks to the provider / transport layers through Protocols
(``kairix/core/protocols.py``) only. ``kairix/core/factory.py`` is the
composition root and is exempt — it wires concrete providers into pipelines.

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``prefix`` mode; this module re-exports the
back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION``) the
F26 unit test loads by file path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

REMEDIATION = """Refactor to route the call through a Protocol in
kairix/core/protocols.py — domain code must not know which provider or
transport is loaded.

fix: define (or reuse) a Protocol in kairix/core/protocols.py that
expresses the capability you need, then accept that Protocol as a
constructor / factory parameter. The production wire-up in
kairix/core/factory.py (or the dedicated provider registry) supplies
the concrete provider; tests inject a Fake from tests/fakes.py.
next: re-run python3 scripts/checks/check_provider_layer_imports.py
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(core): route <capability> through Protocol"

Pass example:
  # kairix/core/search/pipeline.py
  from kairix.core.protocols import EmbeddingService, VectorSearchBackend

  class SearchPipeline:
      def __init__(self, embed: EmbeddingService, backend: VectorSearchBackend) -> None:
          self._embed = embed
          self._backend = backend

Forbidden example:
  # kairix/core/search/pipeline.py
  from kairix.providers.azure_foundry import AzureFoundryProvider  # F26
  from kairix.transport.pool import make_openai_client            # F26

Why: see docs/architecture/provider-plugin-architecture.md - "Decision".
Domain code that imports a concrete provider or transport surface ties
the deployment shape into the domain layer and reintroduces the
class of bug the three-layer split exists to prevent (every new
provider means editing _azure.py; every new perf concern accretes
inside core/)."""

RULE = register(
    ImportBoundaryRule(
        name="f26",
        roots=("kairix/core",),
        mode="prefix",
        forbidden_prefixes=("kairix.providers", "kairix.transport"),
        exempt=("kairix/core/factory.py",),
        remediation=REMEDIATION,
    )
)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F26 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
