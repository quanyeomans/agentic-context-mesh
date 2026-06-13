"""F27: ``kairix/providers/<name>/**`` may not import another provider.

Each plugin under ``kairix/providers/<name>/`` is independently shippable; a
plugin that imports a sibling plugin breaks that guarantee. Shared concerns go
through ``kairix/transport/``; the shared base is ``kairix.providers._base``.

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``sibling-plugin`` mode; this module re-exports
the back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION``)
the F27 unit test loads by file path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

REMEDIATION = """Refactor to remove the cross-provider import — a
plugin must not depend on another plugin.

fix: extract the shared concern to kairix/transport/ (auth resolution,
client pooling, retry/coalesce/cache — anything universal across
endpoint families goes in transport). If the concern is genuinely
provider-specific shape, duplicate it inline rather than importing
another plugin; plugins must stay independently shippable as separate
pip distributions.
next: re-run python3 scripts/checks/check_no_cross_provider.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(providers): move <concern> to kairix/transport/"

Pass example:
  # kairix/providers/openai/embed.py
  from kairix.transport.pool import get_openai_client     # shared transport
  from kairix.providers._base import Provider             # shared base

Forbidden example:
  # kairix/providers/openai/embed.py
  from kairix.providers.azure_foundry import auth_header  # F27 — sibling plugin
  import kairix.providers.bedrock.sigv4                   # F27 — sibling plugin

Why: see docs/architecture/provider-plugin-architecture.md - "Plugin
discovery". Each plugin is meant to ship independently (a third party
can pip install kairix-provider-foo with zero kairix changes); a plugin
that imports another can't be split out without dragging its sibling
along, and the dependency graph becomes a tangle that defeats the
plugin model."""

RULE = register(
    ImportBoundaryRule(
        name="f27",
        roots=("kairix/providers",),
        mode="sibling-plugin",
        remediation=REMEDIATION,
    )
)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F27 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
