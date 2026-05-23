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


# F17 — extract duplicated string literals so adding flags doesn't churn
# the literal across every entry. ≥3 occurrences with ≥10 chars triggers
# the check; these four are the canonical recurring fields.
_CONNECTOR_FRAMEWORK_OWNER = "connector-framework"
_CONNECTOR_INGESTION_SPEC = "docs/architecture/connector-ingestion-architecture.md"
_FLAG_INTRODUCED_IN_DISPATCH_WINDOW = "v2026.5.23"
_FLAG_TARGET_RETIRE_IN = "v2026.7.23"


# Public registry. PR-6 lands the first entry — ``obsidian_connector_primary``
# at introduce stage (default off). Wave 5 KP-1 adds ``connector_dex_crm``
# at introduce stage (default off); KP-2 / KP-3 follow for the M365 pair.
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
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "connector_dex_crm": FeatureFlag(
        name="connector_dex_crm",
        default=False,
        description=(
            "Enable the Dex CRM connector — pulls Person/Org entity signals "
            "from the Dex API into the entity_signals staging table."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "connector_m365_email_headers": FeatureFlag(
        name="connector_m365_email_headers",
        default=False,
        description=(
            "Enable the M365 email-headers connector — pulls From/To/CC/Subject/Date "
            "metadata via Microsoft Graph delta query. NO body content per ADR-004."
        ),
        stage="introduce",
        # KP-2 cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire.
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
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
