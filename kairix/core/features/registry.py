"""Feature flag registry — the canonical schema for kairix feature flags.

See ``docs/architecture/feature-flag-architecture.md`` §3.2 for the
value-object shape and §3 for how the registry plugs into the resolver,
CLI, and MCP tool.

Every feature flag declared at landing time is a single entry in
:data:`REGISTRY`. The registry is the schema — the resolver, CLI, and
MCP tool all introspect it; no other module declares flags.

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
# the check; these are the canonical recurring fields.
_CONNECTOR_FRAMEWORK_OWNER = "connector-framework"
_CONNECTOR_INGESTION_SPEC = "docs/architecture/connector-ingestion-architecture.md"
_FLAG_INTRODUCED_IN_DISPATCH_WINDOW = "v2026.5.23"
_FLAG_TARGET_RETIRE_IN = "v2026.7.23"
# Long-window retire ceiling for connectors with slower per-customer adoption
# (Sharepoint / Notion: AAD or workspace re-install consent latency).
_LONG_RETIRE_WINDOW = "v2027.5.23"
# Wave E newer-flag dispatch window (slack/github/notion/sharepoint pilots).
_FLAG_INTRODUCED_WAVE_E_LATER = "v2026.5.24"
_LONG_RETIRE_WINDOW_WAVE_E = "v2027.5.24"
# Wave 5 connector pilots (gmail) introduced in v2026.5.30 with the 6-month
# F51 retire ceiling.
_FLAG_INTRODUCED_WAVE5_2026_05_30 = "v2026.5.30"
_FLAG_TARGET_RETIRE_WAVE5_2026_11_30 = "v2026.11.30"


# Public registry. The topology_v2_* family + ``obsidian_connector_primary``
# retired post-cutover (task #132 — production worker logs source='config'
# effective=True for every member of the family); their gated call sites have
# been inlined to the post-cutover behaviour and the OFF-branch shims removed.
REGISTRY: dict[str, FeatureFlag] = {
    "connector_dex_crm": FeatureFlag(
        name="connector_dex_crm",
        default=False,
        description=(
            "Enable the Dex CRM connector — pulls Person/Org entity signals "
            + "from the Dex API into the entity_signals staging table."
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
            + "metadata via Microsoft Graph delta query. NO body content per ADR-004."
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
            + "via Microsoft Graph delta query. Feeds entity signals + timeline."
        ),
        stage="introduce",
        # KP-3 cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. ``target_retire_in`` is 2 months from the
        # introduce-stage landing (current 2026-05 dispatch window).
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "connector_sharepoint": FeatureFlag(
        name="connector_sharepoint",
        default=False,
        description=(
            "Enable the SharePoint connector — pulls document libraries (PDF / DOCX / "
            + "PPTX / XLSX) via Microsoft Graph drive-delta query, then dispatches binaries "
            + "through the kairix extractor registry."
        ),
        stage="introduce",
        # SharePoint cutover plan (per feature-flag-architecture.md §7):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. Twelve-month target accommodates per-customer
        # AAD app-registration rollout cadence — SharePoint deployments
        # are slower to authorise than the email-headers / calendar
        # siblings because Sites.Read.All needs higher-tier consent.
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_LONG_RETIRE_WINDOW,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "connector_notion": FeatureFlag(
        name="connector_notion",
        default=False,
        description=(
            "Enable the Notion connector — pulls pages + database rows from "
            "the configured workspace via POST /v1/search + GET /v1/blocks/{id}/children, "
            "renders block trees to Markdown, then dispatches the markdown bytes "
            "through the kairix extractor registry (passthrough / markitdown)."
        ),
        stage="introduce",
        # Notion cutover plan (per feature-flag-architecture.md §7 and
        # docs/architecture/connector-scope-topology/connector-design-specs/notion.md):
        # 4 weeks dogfood UAT at introduce stage → 4 weeks cutover-stage
        # soak → retire. Twelve-month target accommodates the multi-step §6.6
        # implementation sequence (Steps 1-3 land here; Steps 4-7 — slim-perms,
        # Resolver, sensitivity routing, webhooks — land as separate commits
        # behind the same flag with per-step cutover discipline).
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_LONG_RETIRE_WINDOW,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/connector-scope-topology/connector-design-specs/notion.md",
    ),
    "connector_github": FeatureFlag(
        name="connector_github",
        default=False,
        description=(
            "Enable the GitHub connector — pulls code (commits + blobs), "
            + "issues, and pull requests via the REST + GraphQL APIs and "
            + "drives webhook (push / issues / pull_request / "
            + "installation_repositories) signal ingest under per-cc_pair "
            + "installation-token rotation."
        ),
        stage="introduce",
        # GitHub Wave-E cutover plan (per docs/architecture/feature-flag-architecture.md §7):
        # The connector is greenfield (no legacy slice to preserve) so the
        # introduce-stage soak focuses on the proactive failure modes —
        # secondary-rate-limit backoff, installation-token rotation under
        # cc_pair lock, and force-push full-container reconcile (Break #7).
        introduced_in=_FLAG_INTRODUCED_WAVE_E_LATER,
        target_retire_in=_LONG_RETIRE_WINDOW_WAVE_E,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/connector-scope-topology/connector-design-specs/github.md",
    ),
    "connector_slack": FeatureFlag(
        name="connector_slack",
        default=False,
        description=(
            "Enable the Slack connector — pulls public/private channel + DM message "
            "history via the Slack Web API delta surface (conversations.history) and "
            "the realtime push surface (Socket Mode WebSocket + Events API HTTP). "
            "F39 sensitivity routing per channel kind (public → internal, "
            "private/mpim → client-confidential, im → personal) per slack.md §1. "
            "When OFF, the connector emits a single root WORKSPACE hierarchy node "
            "and list_changes_for_container delegates to the legacy single-cursor "
            "list_changes path. When ON, each channel becomes a Container with its "
            "own ts cursor and load_hierarchy walks Workspace → channel → thread "
            "parent-before-child per F58. Default-off until the per-channel routing "
            "soaks against the dogfood workspace; mirrors the obsidian Wave E pilot's "
            "shape and the m365_email_headers per-mailbox pilot."
        ),
        stage="introduce",
        # Slack cutover plan (per docs/architecture/feature-flag-architecture.md §7):
        # 12-month retire window — Slack workspace re-installs are slower to authorise
        # than the M365 sibling flows (admin consent + bot scope review per workspace).
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_LONG_RETIRE_WINDOW_WAVE_E,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/connector-scope-topology/connector-design-specs/slack.md",
    ),
    # bronze_ttl_gc removed in Phase 7 of streaming-bronze (#27) — streaming
    # bronze writes no on-disk blobs so there's nothing for a TTL-based GC
    # to bound. The maintenance stage that backed this flag is a no-op now.
    "maintenance_loop": FeatureFlag(
        name="maintenance_loop",
        default=False,
        description=(
            "KFEAT-021 Phase 1 — when ON, the worker runs a periodic "
            + "MaintenanceScheduler.tick that prunes orphan content_vectors "
            + "rows (moved into content_vectors_pruned with a 7-day soft-delete "
            + "retention), rebuilds the usearch index, and heals FTS5 orphans. "
            + "Default-off until the dogfood VM cutover validates the prune + "
            + "rebuild cadence — flipping ON cleans up the 4,370-row leak the "
            + "KFEAT-020 preflight surfaced. Cadence default 24h, tunable via "
            + "KAIRIX_MAINTENANCE_INTERVAL_S."
        ),
        stage="introduce",
        # KFEAT-021 cutover plan (per docs/architecture/feature-flag-architecture.md §7):
        # 2 weeks dogfood UAT at introduce stage → 2 weeks cutover-stage soak → retire.
        introduced_in=_FLAG_INTRODUCED_WAVE_E_LATER,
        target_retire_in=_LONG_RETIRE_WINDOW_WAVE_E,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/features/KFEAT-021-automated-orphan-cleanup/BRIEF.md",
    ),
    "pipeline_status_emit": FeatureFlag(
        name="pipeline_status_emit",
        default=False,
        description=(
            "ADR-025 Phase 1: write status_emit rows to pipeline_item_status at every "
            "stage boundary (fetch/extract/silver/chunk/embed/entity/drain). When OFF, "
            "emit_for is a no-op context manager. When ON, the table fills per-item "
            "per-stage and the kairix worker inspect / status-summary CLIs return data."
        ),
        stage="introduce",
        introduced_in="v2026.5.29",
        target_retire_in="v2026.11.29",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/ADR-025-pipeline-observability-and-status-surface.md",
    ),
    "agent_query_queue": FeatureFlag(
        name="agent_query_queue",
        default=False,
        description=(
            "ADR-029 G.1 spike: route tool_search through the dispatch_or_queue "
            "decorator + carry-along middleware. When OFF, tool_search runs "
            "synchronously as today (legacy ColdStart envelope path still applies "
            "via the warm-gate decorator). When ON, slow calls (>1.5s) queue to "
            "pending_queries, return plain text 'Processing your request...' "
            "(NOT an error envelope), and the next tool_search call from the same "
            "agent carries the completed result back as a prefix. Default-off until "
            "the G.1 spike validates the carry-along delivery shape; G.2 will roll "
            "the same pattern across remaining MCP tools."
        ),
        stage="introduce",
        introduced_in="v2026.5.30",
        target_retire_in="v2026.11.30",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/ADR-029-agent-query-queue-and-carry-along-delivery.md",
    ),
    "connector_gmail": FeatureFlag(
        name="connector_gmail",
        default=False,
        description=(
            "Enable the Gmail connector — pulls full message body + envelope "
            "(Subject / From / To / Cc / Bcc / Date / Thread / Labels) via the "
            "Gmail REST API (users.history.list + users.messages.get). One Gmail "
            "message becomes one document; attachments surface as metadata only. "
            "OAuth2 with gmail.readonly scope. Default-off until the Workspace "
            "OAuth credentials are provisioned (tracked under GH #356)."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_WAVE5_2026_05_30,
        target_retire_in=_FLAG_TARGET_RETIRE_WAVE5_2026_11_30,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    "entity_summary_indexing_enabled": FeatureFlag(
        name="entity_summary_indexing_enabled",
        # Default-OFF: pre-#457 behaviour preserved byte-for-byte. Operators
        # flip ON after declaring the synthetic 'entity-summaries' collection
        # tier in kairix.config.yaml. The worker tick then projects Neo4j
        # n.summary content into the chunk store so Wikidata descriptions
        # participate in first-pass BM25 + vector retrieval.
        default=False,
        description=(
            "When ON, the worker tick runs EntitySummaryProjectorStage to "
            "project Neo4j n.summary text into the synthetic "
            "'entity-summaries' collection. Closes #429: pre-flag, Wikidata "
            "descriptions written by enrich_entity were unreachable from "
            "search (entity-category NDCG 0.380 in the 2026-06-08 reflib "
            "eval). When OFF, the projector stage is a no-op — zero Neo4j "
            "queries, zero chunk-writer calls — and pre-#457 ranking is "
            "preserved. ADR-036 locks the full architecture + cutover "
            "protocol; #459/#460/#461/#462 are the four implementation "
            "slices."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner="search-pipeline",
        related_spec="docs/architecture/ADR-036-entity-summary-indexing-surface.md",
    ),
    "intent_confidence_gated_boosts": FeatureFlag(
        name="intent_confidence_gated_boosts",
        # Default-OFF: today's binary-enum behaviour is preserved byte-for-byte.
        # Operators flip ON to enable confidence-gated boosts; ambiguous
        # queries (confidence < min_intent_confidence in the boost configs)
        # then fall back to plain RRF fusion instead of triggering
        # potentially-wrong boosts like ChunkDateBoost on a query whose
        # TEMPORAL match was a false positive.
        default=False,
        description=(
            "When ON, boost strategies (ProceduralBoost, TemporalDateBoost, "
            "ChunkDateBoost, EntityBoost) gate on intent confidence in addition "
            "to intent matching. The classifier emits IntentDecision(primary, "
            "confidence, alternatives) via classify_with_confidence(); the "
            "pipeline puts both intent + confidence in the boost context dict; "
            "each boost compares confidence against its min_intent_confidence "
            "config (default 0.5). When OFF, boosts use the legacy binary "
            "intent==X check, ignoring confidence. Closes #456: ambiguous "
            "queries like 'what changed in v1.2.3' currently match a TEMPORAL "
            "pattern via 'what changed' and trigger ChunkDateBoost — wrong "
            "answer. Confidence-gating makes the boost skip when the "
            "TEMPORAL classification is contested by another signal."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
        target_retire_in=_FLAG_TARGET_RETIRE_IN,
        owner="search-pipeline",
        related_spec="docs/architecture/feature-flag-architecture.md",
    ),
    "cli_routes_through_warm_mcp": FeatureFlag(
        name="cli_routes_through_warm_mcp",
        # Default-ON: every composer-equipped subcommand (PRs 2.1-2.7)
        # ships with green envelope-parity contracts so the warm-MCP
        # text rendering matches in-process byte-for-byte. Operators
        # who want the legacy fall-through set this flag OFF via the
        # config overlay; the dispatcher then falls through to
        # in-process for text mode regardless of MCP responsiveness.
        # JSON mode is never gated by this flag — it kept its
        # always-on routing semantics from PR 2.0.
        default=True,
        description=(
            "When ON, text-mode CLI subcommands (search / prep / timeline / "
            "research / brief / contradict / bootstrap) route through warm "
            "MCP when one is responsive and the subcommand has a registered "
            "composer in kairix.agents.mcp.text_mode_composers. When OFF, "
            "text mode falls through to the in-process path even when MCP "
            "is responsive. Subcommands without a registered composer "
            "(features / worker / secrets / dead-letter) always fall "
            "through regardless of this flag. JSON-mode routing was "
            "always enabled and is NOT gated by this flag."
        ),
        stage="cutover",
        introduced_in="v2026.6.6",
        # SCM+6mo per F51 retire-deadline rule. The cutover lands ON
        # by default with green parity tests; retirement is "delete
        # the gate, keep the composer wiring" once dogfood confirms
        # no operator has overridden it OFF.
        target_retire_in="v2026.12.6",
        owner="cli-warm-mcp",
        related_spec="docs/architecture/feature-flag-architecture.md",
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
