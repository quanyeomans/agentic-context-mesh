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
_PER_TYPE_CHUNKING_SPEC = "docs/architecture/ADR-028-per-type-chunking-and-evaluation.md"
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
# Canonical spec for flags whose behaviour is the flag mechanism itself
# (no dedicated capability ADR yet).
_FEATURE_FLAG_ARCHITECTURE_SPEC = "docs/architecture/feature-flag-architecture.md"
# F17 — search-pipeline owner repeated across the entity-summary,
# intent-confidence, and entity-first-routing flags; extract so adding the
# next search flag doesn't re-duplicate the literal.
_SEARCH_PIPELINE_OWNER = "search-pipeline"


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
    # retire-extension: 12-month window intentional — per-customer AAD
    # Sites.Read.All consent cadence is slower than the M365 siblings; flag
    # stays past the 6-month F51 ceiling until adoption soaks. Retirement
    # tracked under the flag-retirement wave (PLA-278).
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
    # retire-extension: 12-month window intentional — the §6.6 build sequence
    # (Steps 4-7: slim-perms, Resolver, sensitivity routing, webhooks) lands
    # incrementally behind this one flag, past the 6-month F51 ceiling.
    # Retirement tracked under the flag-retirement wave (PLA-278).
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
    # retire-extension: 12-month Wave-E window intentional — greenfield
    # connector still in introduce-stage soak (secondary-rate-limit backoff,
    # installation-token rotation, force-push reconcile) past the 6-month F51
    # ceiling. Retirement tracked under the flag-retirement wave (PLA-278).
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
    # retire-extension: 12-month Wave-E window intentional — per-workspace
    # admin consent + bot-scope review cadence is slower than the M365 flows;
    # flag stays past the 6-month F51 ceiling until per-channel routing soaks.
    # Retirement tracked under the flag-retirement wave (PLA-278).
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
    # retire-extension: 12-month Wave-E window intentional — greenfield
    # connector with per-workspace API-key provisioning cadence; flag stays
    # past the 6-month F51 ceiling until adoption soaks. Retirement tracked
    # under the flag-retirement wave (PLA-278).
    "connector_linear": FeatureFlag(
        name="connector_linear",
        default=False,
        description=(
            "Enable the Linear connector — polls workspace roadmap + docs "
            "(issues / projects / documents / initiatives / project updates) "
            "via the Linear GraphQL API filtered by updatedAt, renders each "
            "entity to Markdown, and dispatches the markdown bytes through "
            "the kairix extractor registry. HTTPS-only; incremental poll, "
            "NOT webhooks (linear.md §13). When OFF the connector slot is a "
            "no-op; when ON the cc_pair drains every 5 entity types on the "
            "updatedAt high-water-mark cursor."
        ),
        stage="introduce",
        # Linear cutover plan (per docs/architecture/feature-flag-architecture.md §7 and
        # docs/architecture/connector-scope-topology/connector-design-specs/linear.md §8):
        # Greenfield (no legacy slice). Twelve-month retire window matches the
        # slower-adoption connector cohort (per-workspace API-key provisioning
        # cadence); shares the Wave-E window constant with the slack/github pilots.
        introduced_in=_FLAG_INTRODUCED_WAVE_E_LATER,
        target_retire_in=_LONG_RETIRE_WINDOW_WAVE_E,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec="docs/architecture/connector-scope-topology/connector-design-specs/linear.md",
    ),
    "connector_skills": FeatureFlag(
        name="connector_skills",
        default=False,
        description=(
            "Enable the skills connector — indexes locally installed Claude Code "
            "skills, slash-commands, and sub-agents into the capabilities corpus so "
            "the recommender can rank them. Reads the host's ~/.claude tree; degrades "
            "to no-op where absent."
        ),
        stage="introduce",
        introduced_in=_FLAG_INTRODUCED_WAVE_E_LATER,
        target_retire_in=_FLAG_TARGET_RETIRE_WAVE5_2026_11_30,
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_CONNECTOR_INGESTION_SPEC,
    ),
    # bronze_ttl_gc removed in Phase 7 of streaming-bronze (#27) — streaming
    # bronze writes no on-disk blobs so there's nothing for a TTL-based GC
    # to bound. The maintenance stage that backed this flag is a no-op now.
    # retire-extension: 12-month Wave-E window intentional — KFEAT-021 prune +
    # rebuild cadence awaits dogfood-VM cutover validation before the flag can
    # flip ON, so it stays past the 6-month F51 ceiling. Retirement tracked
    # under the flag-retirement wave (PLA-278).
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
        owner=_SEARCH_PIPELINE_OWNER,
        related_spec="docs/architecture/ADR-036-entity-summary-indexing-surface.md",
    ),
    "entity_first_routing_enabled": FeatureFlag(
        name="entity_first_routing_enabled",
        # Default-OFF: pre-#429-Phase-2b ranking preserved byte-for-byte.
        # Operators flip ON (after entity_summary_indexing_enabled is ON,
        # so there are summaries to route) to lift entity-summaries to the
        # top for entity-named queries.
        default=False,
        description=(
            "When ON, ENTITY-intent queries ('tell me about X', 'who is X') "
            "route the 'entity-summaries' collection first — the ADR-036 "
            "projector's Wikidata summaries are lifted to the top of results "
            "via EntityFirstRoutingBoost instead of sitting de-prioritised at "
            "tier reference (x0.6). When OFF (the default), ranking is "
            "unchanged byte-for-byte. Needs entity_summary_indexing_enabled "
            "ON for there to be summaries to route. #429 Phase 2b; the "
            "production cutover is #463 (PLA-173)."
        ),
        stage="introduce",
        introduced_in="v2026.6.19",
        target_retire_in="v2026.12.1",
        owner=_SEARCH_PIPELINE_OWNER,
        related_spec="docs/architecture/ADR-036-entity-summary-indexing-surface.md",
    ),
    "recommender": FeatureFlag(
        name="recommender",
        # Default-OFF: installing the recommender code is a no-op for
        # operators. The `kairix recommend` CLI + the recommend_capabilities
        # MCP tool return a disabled envelope, and the worker skips the
        # capability-corpus build, until an operator deliberately flips this ON.
        default=False,
        description=(
            "Enable the capability recommender — 'kairix recommend' and the "
            "recommend_capabilities MCP tool rank kairix tools and local skills "
            "for a described task. Builds the capabilities corpus on worker start. "
            "When OFF, both surfaces return a disabled envelope and the worker "
            "skips the corpus build, so installing the code is a no-op."
        ),
        stage="introduce",
        introduced_in="v2026.6.20",
        # Reuses the Wave 5 6-month retire ceiling (safely under the F51
        # current-SCM+6mo bound); the recommender is not a connector but
        # shares the same cadence window.
        target_retire_in=_FLAG_TARGET_RETIRE_WAVE5_2026_11_30,
        owner=_SEARCH_PIPELINE_OWNER,
        related_spec="docs/architecture/capability-recommender/recommender-mvp-design.md",
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
        owner=_SEARCH_PIPELINE_OWNER,
        related_spec=_FEATURE_FLAG_ARCHITECTURE_SPEC,
    ),
    "chunker_registry_dispatch_enabled": FeatureFlag(
        name="chunker_registry_dispatch_enabled",
        default=False,
        description=(
            "When ON, Silver routes passthrough (no-pages) content through the "
            "per-type chunker registry (build_default_registry) instead of the "
            "paragraph fallback — markdown_structural for obsidian / notion / "
            "github markdown, DocxHeadingChunker for DOCX, with the bounded "
            "paragraph fallback for any unregistered (kind, mime). Chunks carry "
            "the per-type chunker_version + structural metadata (heading_path) so "
            "a re-chunk sweep can identify them. OFF (default) keeps the "
            "byte-identical silver-markdown-v1 fallback. CAVEAT: existing "
            "documents re-chunk lazily on their next sync, but the per-document "
            "upsert keys on source_uri#seq — a document whose chunk count SHRINKS "
            "keeps stale tail chunks (old chunker_version) searchable until the "
            "re-chunk sweep (forthcoming) retires them, so prefer enabling on "
            "fresh deployments or pair the flip with a re-embed sweep. ADR-028 "
            "markdown-first cutover; page-bearing PPTX / XLSX take the page path "
            "(their registry entries are inert until per-page dispatch lands)."
        ),
        stage="introduce",
        introduced_in="v2026.6.25",
        target_retire_in="v2026.12.25",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_PER_TYPE_CHUNKING_SPEC,
    ),
    "re_chunk_sweep_enabled": FeatureFlag(
        name="re_chunk_sweep_enabled",
        default=False,
        description=(
            "When ON, the worker runs the bounded re-chunk sweep maintenance tick "
            "(ADR-028 Wave F.4): it re-chunks already-ingested documents whose "
            "recorded documents_media.chunker_version is behind the current chunker "
            "registry, re-running Silver from the source markdown persisted at "
            "ingest (silver_source) WITHOUT re-fetching from the remote connector. "
            "Each tick scans at most KAIRIX_RECHUNK_SWEEP_PER_TICK_CAP documents "
            "from a persisted cursor (F66) and writes un-embedded chunks the embed "
            "worker picks up on its own cycle (no inline embed -> no #352 OOM). "
            "REQUIRES chunker_registry_dispatch_enabled to ALSO be ON: the sweep "
            "converges docs to the registry chunker versions, so running it while "
            "ingest still uses the legacy chunker would churn — the tick no-ops "
            "when registry dispatch is OFF. Paged formats (PPTX/XLSX/DOCX) are "
            "skipped (their chunkers need extracted.pages, not persisted by the "
            "worker path) and deferred to the operator re-fetch path. OFF (default) "
            "is a complete no-op."
        ),
        stage="introduce",
        introduced_in="v2026.6.25",
        target_retire_in="v2026.12.25",
        owner=_CONNECTOR_FRAMEWORK_OWNER,
        related_spec=_PER_TYPE_CHUNKING_SPEC,
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
