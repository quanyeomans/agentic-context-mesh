"""F34: ``kairix/core/connectors/**`` may not import ``kairix/connectors/**`` or
``kairix/extractors/**``.

The connector-framework layer split (see
``docs/architecture/connector-ingestion-architecture.md`` §2 + §3) places a
hard boundary between the orchestration layer (``kairix/core/connectors/``),
the per-source connector plugins (``kairix/connectors/``), and the per-format
extractor plugins (``kairix/extractors/``). The orchestration layer knows
about Protocols, not implementations — Protocols live in
``kairix/core/protocols.py``.

Allowed from ``kairix/core/connectors/``:
  - ``from kairix.core.protocols import ...`` (Protocol types — the seam
    between layers).
  - sibling ``kairix.core.*`` imports.
  - the ``kairix`` top-level package itself.

Rejected from ``kairix/core/connectors/``:
  - ``from kairix.connectors... import ...``
  - ``from kairix.extractors... import ...``
  - ``import kairix.connectors...`` / ``import kairix.extractors...``

The detector AST-walks every ``.py`` file under ``kairix/core/connectors/``
and flags any ``Import`` / ``ImportFrom`` node whose module path starts with
``kairix.connectors`` or ``kairix.extractors``. Pre-existing violations are
grandfathered in ``.architecture/baseline/f34-files.txt``.

If ``kairix/core/connectors/`` does not exist (Wave 0 landing, before Wave 1
scaffolds the orchestration layer) or holds no Python files, the check passes
trivially — F34 only fires once orchestration code appears. Mirrors the F26
shape exactly.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import REPO_ROOT, repo_relative  # noqa: F401 — back-compat for test imports

# Module prefixes the core/connectors/ tree is forbidden from importing.
# Anchored with a trailing dot so we don't accidentally flag a hypothetical
# ``kairix.connectors_helpers`` sibling.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "kairix.connectors",
    "kairix.extractors",
)

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


def _module_is_forbidden(module: str | None) -> bool:
    """True if ``module`` is a forbidden import target for core/connectors code.

    A module path matches when it equals one of the forbidden prefixes or
    starts with ``<prefix>.``. Plain ``kairix``, ``kairix.core.*``, and any
    hypothetical ``kairix.connectors_helpers`` sibling are never matched.
    """
    if module is None:
        return False
    for prefix in _FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def file_has_violation(path: Path) -> bool:
    """True if ``path`` (a .py file under kairix/core/connectors/) contains
    any forbidden import.

    Inspects both ``ImportFrom`` (``from kairix.connectors... import x``) and
    ``Import`` (``import kairix.extractors.markitdown``) nodes.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _module_is_forbidden(node.module):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _module_is_forbidden(alias.name):
                    return True
    return False


class F34(FitnessRule):
    """F34 as a FitnessRule subclass — see module docstring for rule semantics."""

    name = "f34"
    remediation = REMEDIATION
    roots = ("kairix/core/connectors",)

    def file_has_violation(self, path: Path) -> bool:
        return file_has_violation(path)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat helper for direct test imports."""
    return F34(repo_root=repo_root).collect_violations()


def main() -> int:
    return F34().run()


if __name__ == "__main__":
    sys.exit(main())
