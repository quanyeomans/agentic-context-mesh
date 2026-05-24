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
_TOPOLOGY_V2_SPEC = "docs/architecture/connector-scope-topology/ADR.md"
_FLAG_INTRODUCED_IN_DISPATCH_WINDOW = "v2026.5.23"
_FLAG_TARGET_RETIRE_IN = "v2026.7.23"
# Topology v2 retire window is longer — 7-wave migration ramping over ~12 months.
_TOPOLOGY_V2_TARGET_RETIRE_IN = "v2027.5.23"


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
    "connector_m365_calendar": FeatureFlag(
        name="connector_m365_calendar",
        default=False,
        description=(
            "Enable the M365 calendar connector — pulls event date/attendees/subject/location "
            "via Microsoft Graph delta query. Feeds entity signals + timeline."
        ),
        stage="introduce",
        # KP-3 cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. ``target_retire_in`` is 2 months from the
        # introduce-stage landing (current 2026-05 dispatch window).
        introduced_in="v2026.5.23",
        target_retire_in="v2026.7.23",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "connector_sharepoint": FeatureFlag(
        name="connector_sharepoint",
        default=False,
        description=(
            "Enable the SharePoint connector — pulls document libraries (PDF / DOCX / "
            "PPTX / XLSX) via Microsoft Graph drive-delta query, then dispatches binaries "
            "through the kairix extractor registry."
        ),
        stage="introduce",
        # SharePoint cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. Twelve-month target accommodates per-customer
        # AAD app-registration rollout cadence — SharePoint deployments
        # are slower to authorise than the email-headers / calendar
        # siblings because Sites.Read.All needs higher-tier consent.
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "topology_v2_schema": FeatureFlag(
        name="topology_v2_schema",
        default=False,
        description=(
            "Wave A of the connector/collection/scope topology v2 migration — "
            "controls whether the v2 schema tables (connectors, credentials, "
            "cc_pairs, containers, hierarchy_nodes, collections, collection_sources, "
            "federated_connectors, group_grants, scope_profiles, skills, task_collections) "
            "get POPULATED. Tables exist unconditionally (CREATE IF NOT EXISTS); "
            "the flag gates whether anything writes to them. Default-off until "
            "Wave B Protocol shims land."
        ),
        stage="introduce",
        # Topology v2 migration plan (per docs/architecture/connector-scope-topology/ADR.md):
        # Wave A schema → B Protocol → C runtime → D operator config → E per-connector
        # multi-container → F chunker plugins → G retirement. Each wave gets its own
        # flag; this one is the foundation.
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "topology_v2_protocol": FeatureFlag(
        name="topology_v2_protocol",
        default=False,
        description=(
            "Wave B of the connector/collection/scope topology v2 migration — "
            "controls whether the worker's connector-sync dispatch routes through "
            "the new capability-mix-in path (using PollConnector / CheckpointedConnector "
            "etc.) vs the legacy single-cursor SourceConnector path. Wave B lands "
            "the Protocols + shims with the flag default-off so existing behaviour "
            "is preserved; Wave C runtime activates the routing."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "topology_v2_config": FeatureFlag(
        name="topology_v2_config",
        default=False,
        description=(
            "Wave D of the connector/collection/scope topology v2 migration — "
            "controls whether the 6 operator-config blocks (connectors / credentials / "
            "cc_pairs / collections / scope_profiles / skills) are PARSED + APPLIED. "
            "When OFF, the parser still loads the YAML but the topology v2 surface "
            "is inert (rows aren't written, scope profiles aren't enforced at search, "
            "skills aren't dispatched). When ON, the worker startup + `kairix config "
            "validate` + `kairix features status` + `kairix cc-pair *` verbs read "
            "from the parsed surface. Default-off until the dogfood VM cutover "
            "validates the operator-config promotion path."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "topology_v2_runtime": FeatureFlag(
        name="topology_v2_runtime",
        default=False,
        description=(
            "Wave C of the connector/collection/scope topology v2 migration — "
            "controls whether the worker's connector-sync dispatch routes chunk "
            "writes through CollectionRouter (per-cc_pair, per-mapping) vs the "
            "legacy single-collection chunk writer. When ON, the runtime also "
            "wires the ChunkerRegistry dispatch + ScopeProfileResolver + "
            "ResultEnvelope freshness signals. When OFF, behaviour is bit-for-bit "
            "identical to today. Default-off until the dogfood VM cutover "
            "validates per-folder routing + chunker dispatch + HierarchyNode "
            "emission for the obsidian-personal cc_pair."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "topology_v2_obsidian": FeatureFlag(
        name="topology_v2_obsidian",
        default=False,
        description=(
            "Wave E of the connector/collection/scope topology v2 migration — "
            "per-connector pilot for the obsidian connector. When ON, the "
            "ObsidianConnector emits one Container per top-level vault folder "
            "(each with its own delta cursor) instead of a single "
            "connector-wide cursor, and load_hierarchy walks the vault "
            "filesystem emitting one FOLDER node per directory parent-before-child. "
            "When OFF, the connector retains the Wave B shim shape — "
            "list_changes_for_container delegates to the legacy single "
            "list_changes call, and load_hierarchy emits one root FOLDER node. "
            "Default-off until the per-folder routing pattern soaks against the "
            "dogfood vault; this pilot's shape is the template for the "
            "dex_crm / m365_* / sharepoint / notion / slack / github "
            "wave-E adoption."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_TOPOLOGY_V2_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "topology_v2_m365_email_headers": FeatureFlag(
        name="topology_v2_m365_email_headers",
        default=False,
        description=(
            "Wave E of the connector/collection/scope topology v2 migration — "
            "per-connector pilot for the m365_email_headers connector. When ON, "
            "the connector emits one Container per configured mailbox UPN "
            "(each with its own Graph deltaLink cursor) instead of a single "
            "connector-wide cursor, and load_hierarchy emits one root FOLDER "
            "node plus one FOLDER per mailbox parent-before-child per F58. "
            "When OFF, the connector retains the Wave B shim shape — "
            "list_changes_for_container delegates to the legacy single "
            "list_changes call, and load_hierarchy emits one root FOLDER node. "
            "Default-off until the per-mailbox routing pattern soaks against "
            "the dogfood tenant; mirrors the obsidian Wave E pilot landed in "
            "the topology_v2_obsidian flag."
        ),
        stage="introduce",
        introduced_in="v2026.5.24",
        target_retire_in="v2027.5.24",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_TOPOLOGY_V2_SPEC,
    ),
    "maintenance_loop": FeatureFlag(
        name="maintenance_loop",
        default=False,
        description=(
            "KFEAT-021 Phase 1 — when ON, the worker runs a periodic "
            "MaintenanceScheduler.tick that prunes orphan content_vectors "
            "rows (moved into content_vectors_pruned with a 7-day soft-delete "
            "retention), rebuilds the usearch index, and heals FTS5 orphans. "
            "Default-off until the dogfood VM cutover validates the prune + "
            "rebuild cadence — flipping ON cleans up the 4,370-row leak the "
            "KFEAT-020 preflight surfaced. Cadence default 24h, tunable via "
            "KAIRIX_MAINTENANCE_INTERVAL_S."
        ),
        stage="introduce",
        # KFEAT-021 cutover plan (per docs/architecture/feature-flag-architecture.md §7):
        # 2 weeks dogfood UAT at introduce stage → 2 weeks cutover-stage soak → retire.
        # 12-month target_retire_in matches the topology_v2_* fleet so retirement
        # batches can roll together once Phase 2 (connector-side delete
        # propagation) lands across every connector wave.
        introduced_in="v2026.5.24",
        target_retire_in="v2027.5.24",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/features/KFEAT-021-automated-orphan-cleanup/BRIEF.md",
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
