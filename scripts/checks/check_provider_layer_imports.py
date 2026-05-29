"""F26: ``kairix/core/**`` may not import ``kairix/providers/**`` or ``kairix/transport/**``.

The three-layer provider-plugin split (see
``docs/architecture/provider-plugin-architecture.md``) places a hard
boundary between the domain (``kairix/core/``), the universal endpoint
concerns (``kairix/transport/``), and the per-provider plugins
(``kairix/providers/``). Core knows about Protocols, not
implementations.

Allowed from ``kairix/core/``:
  - ``from kairix.core.protocols import ...`` (Protocol types are the
    seam between layers — fine to import).
  - sibling ``kairix.core.*`` imports.
  - the ``kairix`` top-level package itself.

Rejected from ``kairix/core/``:
  - ``from kairix.providers... import ...``
  - ``from kairix.transport... import ...``
  - ``import kairix.providers...`` / ``import kairix.transport...``

The detector AST-walks every ``.py`` file under ``kairix/core/`` and
flags any ``Import`` / ``ImportFrom`` node whose module path starts
with ``kairix.providers`` or ``kairix.transport``. Pre-existing
violations are grandfathered in
``.architecture/baseline/f26-files.txt``.

If ``kairix/core/`` does not exist (fresh checkout) or has no Python
files, the check passes trivially — F26 only fires once core code
appears.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, repo_relative  # noqa: F401 — back-compat for test imports
from _fitness_rule import FitnessRule

# Module prefixes the core/ tree is forbidden from importing. Anchored
# with a trailing dot so we don't accidentally flag a hypothetical
# ``kairix.providers_helpers`` sibling.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "kairix.providers",
    "kairix.transport",
)

# Composition-root exemption. ``kairix/core/factory.py`` is the runtime
# wire-up — it must import concrete providers/transport to compose
# protocol implementations into pipelines. The exemption is encoded
# here (not as an open baseline entry) because it's an architectural
# invariant, not pre-existing tech debt. See
# ``docs/architecture/provider-plugin-architecture.md`` §"Composition
# root" for the rationale; the factory is the *only* file in core/
# whose job description is to cross the layer boundary.
_ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        "kairix/core/factory.py",
    }
)

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


def _module_is_forbidden(module: str | None) -> bool:
    """True if ``module`` is a forbidden import target for core code.

    A module path matches when it equals one of the forbidden prefixes
    or starts with ``<prefix>.``. Plain ``kairix`` and ``kairix.core.*``
    are never matched.
    """
    if module is None:
        return False
    for prefix in _FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def file_has_violation(path: Path) -> bool:
    """True if ``path`` (a .py file under kairix/core/) contains any
    forbidden import.

    Inspects both ``ImportFrom`` (``from kairix.providers... import x``)
    and ``Import`` (``import kairix.transport.pool``) nodes.
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


class F26(FitnessRule):
    """F26 as a FitnessRule subclass — see module docstring for rule semantics."""

    name = "f26"
    remediation = REMEDIATION
    roots = ("kairix/core",)
    exempt_files = _ALLOWLIST_PATHS

    def file_has_violation(self, path: Path) -> bool:
        return file_has_violation(path)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat helper for direct test imports."""
    return F26(repo_root=repo_root).collect_violations()


def main() -> int:
    return F26().run()


if __name__ == "__main__":
    sys.exit(main())
