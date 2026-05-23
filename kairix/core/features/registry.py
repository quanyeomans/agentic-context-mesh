"""Feature flag registry — the canonical schema for kairix feature flags.

See ``docs/architecture/feature-flag-architecture.md`` §3.2 for the
value-object shape and §3 for how the registry plugs into the resolver,
CLI, and MCP tool.

Every feature flag declared at landing time is a single entry in
:data:`REGISTRY`. The registry is the schema — the resolver, CLI, and
MCP tool all introspect it; no other module declares flags.

PR-2 lands the registry empty. Future PRs add entries (PR-6 adds
``obsidian_connector_primary``; Wave 5 adds the connector flags).

Per the spec §3.2, fields are:

* ``name`` — snake_case identifier; matches the dict key in REGISTRY.
* ``default`` — the safe value (almost always ``False`` at introduce).
* ``description`` — one-line operator-facing summary.
* ``stage`` — ``introduce`` / ``cutover`` / ``retire`` (Literal).
* ``introduced_in`` — version string when the flag landed.
* ``target_retire_in`` — version string; F51 fires past this deadline.
* ``owner`` — team / squad responsible for the cutover.
* ``related_spec`` — optional path to the canonical spec doc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FlagStage = Literal["introduce", "cutover", "retire"]


@dataclass(frozen=True)
class FeatureFlag:
    """One feature flag declaration. Frozen — the registry IS the schema."""

    name: str
    default: bool
    description: str
    stage: FlagStage
    introduced_in: str
    target_retire_in: str
    owner: str
    related_spec: str | None = None


# Public registry. PR-6 lands the first entry — ``obsidian_connector_primary``
# at introduce stage (default off). Future PRs add Wave 5+ connector flags.
REGISTRY: dict[str, FeatureFlag] = {
    "obsidian_connector_primary": FeatureFlag(
        name="obsidian_connector_primary",
        default=False,
        description=(
            "Route document indexing through kairix.connectors.obsidian instead of the legacy DocumentScanner."
        ),
        stage="introduce",
        # IM-6 cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. ``target_retire_in`` is 2 months from the
        # introduce-stage landing (current 2026-05 dispatch window).
        introduced_in="v2026.5.23",
        target_retire_in="v2026.7.23",
        owner="connector-framework",
        related_spec="docs/architecture/connector-ingestion-architecture.md",
    ),
}


def validate_registry(registry: dict[str, FeatureFlag]) -> None:
    """Defensive check: every registry key matches its ``FeatureFlag.name``.

    A typo where the key disagrees with the value's ``name`` would let
    ``flag("foo")`` and ``flag("bar")`` both resolve through the same
    entry, breaking F52's call-site reference integrity. Cheap to catch
    at import time; fails fast in safe-commit if a future contributor
    adds a mismatched entry.

    Public so tests can pin the validator's behaviour on synthetic
    registries without rebinding the module-global REGISTRY.
    """
    for key, entry in registry.items():
        if key != entry.name:
            raise ValueError(
                f"REGISTRY key {key!r} does not match FeatureFlag.name {entry.name!r}. "
                "fix: rename the key to match the dataclass `name` field."
            )


validate_registry(REGISTRY)
