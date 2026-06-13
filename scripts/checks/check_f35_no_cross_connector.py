"""F35: ``kairix/connectors/<name>/**`` may not import another connector or
any extractor.

The connector-framework layer split (see
``docs/architecture/connector-ingestion-architecture.md`` §2 + §5.1) treats
each plugin under ``kairix/connectors/<name>/`` as independently shippable —
a third party can ``pip install kairix-connector-foo`` and register a new
source family without touching kairix's tree. That guarantee breaks the
moment one connector imports another, or reaches into the extractor layer
directly: the imported plugin must then ship alongside, the dependency
graph fans out, and chunking / extraction concerns leak across plugin
boundaries instead of through ``kairix/core/connectors/``.

Allowed from ``kairix/connectors/<name>/``:
  - sibling imports within the same plugin (``kairix.connectors.<name>.*``)
  - the shared base in ``kairix.connectors._base`` (SourceConnector Protocol,
    registry contract — explicitly designed for cross-plugin use)
  - ``kairix.core.*`` (the Protocol surface and shared orchestration)
  - ``kairix.transport.*`` (the universal concerns — that's what transport
    exists for)

Rejected from ``kairix/connectors/<name>/``:
  - ``from kairix.connectors.<other> import ...`` (any other connector)
  - ``import kairix.connectors.<other>`` (any other connector)
  - ``from kairix.extractors... import ...`` (any extractor)
  - ``import kairix.extractors...`` (any extractor)

The detector AST-walks every ``.py`` file under ``kairix/connectors/``
(skipping ``_base.py`` and ``__init__.py`` at the top level), figures out
which connector directory owns the file, and flags any import that points
at a sibling connector or any extractor. Pre-existing violations are
grandfathered in ``.architecture/baseline/f35-files.txt``.

If ``kairix/connectors/`` does not exist (Wave 0 landing) or contains no
plugin subdirectories, the check passes trivially — F35 only fires once
plugins appear. Mirrors the F27 shape exactly, with the addition of the
extractor-layer prohibition required by §2's three-layer split.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import REPO_ROOT, repo_relative  # noqa: F401 — back-compat for test imports

# Files / directories at the top level of kairix/connectors/ that are NOT
# plugins (they're the shared scaffolding the plugin Protocol lives in).
_NON_PLUGIN_ENTRIES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "_base.py",
        "_base",
        "__pycache__",
    }
)

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


def _plugin_dir_for(path: Path, connectors_root: Path) -> str | None:
    """Return the connector directory name that ``path`` lives in, or None
    if the path sits at the top level of ``connectors/`` (so isn't part of
    any plugin) or outside ``connectors/`` entirely.

    A "plugin directory" is the first path segment under
    ``kairix/connectors/`` for the file's location.
    """
    try:
        rel = path.relative_to(connectors_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        # Top-level file (e.g. _base.py, __init__.py) — not inside a plugin
        # directory.
        return None
    return parts[0]


def _is_cross_connector_import(module: str | None, owning_plugin: str) -> bool:
    """True if ``module`` (the source of an Import / ImportFrom node) points
    at a ``kairix.connectors.<other>`` connector different from
    ``owning_plugin``.

    ``kairix.connectors._base`` and ``kairix.connectors`` itself are
    explicitly NOT cross-plugin — they are the shared scaffolding.
    """
    if module is None:
        return False
    prefix = "kairix.connectors."
    if not module.startswith(prefix):
        return False
    rest = module[len(prefix) :]
    # Pull out the first segment after kairix.connectors. — that's the
    # plugin name (or the shared _base module name).
    head = rest.split(".", 1)[0]
    if not head:
        return False
    if head.startswith("_"):
        # Shared scaffolding (e.g. _base) — explicitly allowed.
        return False
    return head != owning_plugin


def _is_extractor_import(module: str | None) -> bool:
    """True if ``module`` targets any module under ``kairix.extractors``.

    Any reach into the extractor layer is forbidden from connectors — the
    extractor Protocol is dispatched through ``kairix/core/connectors/``'s
    registry, not by direct import.
    """
    if module is None:
        return False
    return module == "kairix.extractors" or module.startswith("kairix.extractors.")


def file_has_violation(path: Path, connectors_root: Path) -> bool:
    """True if ``path`` (a .py file under a connector directory) imports
    from another connector under ``kairix/connectors/`` or from any
    extractor under ``kairix/extractors/``.
    """
    owning = _plugin_dir_for(path, connectors_root)
    if owning is None:
        # Top-level connector scaffolding — not subject to F35.
        return False
    if owning.startswith("_") or owning in _NON_PLUGIN_ENTRIES:
        return False

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_cross_connector_import(node.module, owning):
                return True
            if _is_extractor_import(node.module):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_cross_connector_import(alias.name, owning):
                    return True
                if _is_extractor_import(alias.name):
                    return True
    return False


class F35(FitnessRule):
    """F35 as a FitnessRule subclass. Overrides ``file_has_violation``
    to thread ``connectors_root`` into the detection helper.
    """

    name = "f35"
    remediation = REMEDIATION
    roots = ("kairix/connectors",)

    def __init__(self, repo_root: Path | None = None) -> None:
        super().__init__(repo_root=repo_root)
        self._connectors_root = self._repo_root / "kairix" / "connectors"

    def file_has_violation(self, path: Path) -> bool:
        return file_has_violation(path, self._connectors_root)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat helper for direct test imports."""
    return F35(repo_root=repo_root).collect_violations()


def main() -> int:
    return F35().run()


if __name__ == "__main__":
    sys.exit(main())
