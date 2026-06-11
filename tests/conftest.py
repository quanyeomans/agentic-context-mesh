"""
Shared pytest fixtures for the kairix test suite.

Fixture hierarchy:
  no_azure_calls (autouse, all non-e2e tests) — blocks accidental Azure API calls
  fake_llm_backend — FakeLLM satisfying LLMBackend Protocol
  neo4j_client — FakeNeo4jClient satisfying Neo4jClient interface
  search_db / seeded_search_db — BM25 search index fixtures

BDD step modules must be declared as pytest_plugins at the root conftest level
(pytest restriction: pytest_plugins in sub-conftest files is not supported).
"""

# Early numpy import — pre-loads the C extension before pytest-cov starts
# instrumenting test modules. Python 3.14 + numpy 2.4 + pytest-cov hit a
# "cannot load module more than once per process" ImportError when numpy
# is first imported AFTER coverage tracing has begun (#211). Loading it
# here ensures numpy is in ``sys.modules`` before the first test module
# loads, so subsequent ``import numpy`` calls are pure dict lookups.
import numpy  # noqa: F401 — pre-load only; see #211
import pytest

# BDD step definition modules — registered here so pytest-bdd can discover them
# across the entire test run.
pytest_plugins = [
    "tests.bdd.steps.search_steps",
    "tests.bdd.steps.curator_steps",
    "tests.bdd.steps.reflib_steps",
    "tests.bdd.steps.normalisation_steps",
    "tests.bdd.steps.entity_steps",
    "tests.bdd.steps.onboard_steps",
    "tests.bdd.steps.onboard_scan_steps",
    "tests.bdd.steps.doctor_steps",
    "tests.bdd.steps.mcp_timeline_steps",
    "tests.bdd.steps.eval_tune_steps",
    "tests.bdd.steps.mcp_entity_steps",
    "tests.bdd.steps.eval_auto_gold_steps",
    "tests.bdd.steps.recall_steps",
    "tests.bdd.steps.benchmark_steps",
    "tests.bdd.steps.mcp_search_steps",
    "tests.bdd.steps.mcp_prep_steps",
    "tests.bdd.steps.timeline_absolute_steps",
    "tests.bdd.steps.mcp_contradict_steps",
    "tests.bdd.steps.chunk_date_steps",
    "tests.bdd.steps.research_synthesis_steps",
    "tests.bdd.steps.search_dedup_steps",
    "tests.bdd.steps.agent_collections_steps",
    "tests.bdd.steps.eval_gate_steps",
    "tests.bdd.steps.configurable_default_scope_steps",
    "tests.bdd.steps.collection_v2_default_in_scope_steps",
    "tests.bdd.steps.wikilinks_injection_steps",
    "tests.bdd.steps.eval_judge_steps",
    "tests.bdd.steps.eval_generate_steps",
    "tests.bdd.steps.eval_gold_builder_steps",
    "tests.bdd.steps.eval_monitor_steps",
    "tests.bdd.steps.embed_run_steps",
    "tests.bdd.steps.search_logging_steps",
    "tests.bdd.steps.search_backends_steps",
    "tests.bdd.steps.search_boosts_steps",
    "tests.bdd.steps.search_config_validation_steps",
    "tests.bdd.steps.search_planner_steps",
    "tests.bdd.steps.search_rerank_steps",
    "tests.bdd.steps.rrf_asymmetric_fusion_steps",
    "tests.bdd.steps.search_intent_gated_boosts_steps",
    "tests.bdd.steps.search_chunk_date_recency_steps",
    "tests.bdd.steps.search_collection_retrieval_overrides_steps",
    "tests.bdd.steps.search_cli_steps",
    "tests.bdd.steps.summarise_cli_steps",
    "tests.bdd.steps.kairix_cli_top_level_steps",
    "tests.bdd.steps.store_cli_steps",
    "tests.bdd.steps.brief_cli_steps",
    "tests.bdd.steps.agent_scope_callsites_steps",
    "tests.bdd.steps.setup_cli_steps",
    "tests.bdd.steps.wikilinks_cli_steps",
    "tests.bdd.steps.entity_cli_steps",
    "tests.bdd.steps.entity_audit_steps",
    "tests.bdd.steps.curator_cli_steps",
    "tests.bdd.steps.mcp_cli_steps",
    "tests.bdd.steps.cli_route_via_mcp_steps",
    "tests.bdd.steps.embed_cli_steps",
    "tests.bdd.steps.timeline_cli_steps",
    "tests.bdd.steps.soak_steps",
    "tests.bdd.steps.warm_steps",
    "tests.bdd.steps.sqlite_stats_steps",
    "tests.bdd.steps.mcp_maintenance_analyze_steps",
    "tests.bdd.steps.probe_steps",
    "tests.bdd.steps.probe_per_query_telemetry_steps",
    "tests.bdd.steps.worker_steps",
    # Worker preflight — persistence-integrity audit at boot / on demand.
    # See kairix/core/db/integrity.py for the IM-6 regression context.
    "tests.bdd.steps.worker_preflight_steps",
    "tests.bdd.steps.bootstrap_steps",
    "tests.bdd.steps.usage_guide_steps",
    "tests.bdd.steps.classify_steps",
    "tests.bdd.steps.classify_error_steps",
    "tests.bdd.steps.embed_pool_config_steps",
    "tests.bdd.steps.query_cache_steps",
    "tests.bdd.steps.enrich_cache_steps",
    "tests.bdd.steps.embed_cache_steps",
    "tests.bdd.steps.embedding_cache_steps",
    "tests.bdd.steps.embed_coalescer_steps",
    "tests.bdd.steps.vec_index_batched_metadata_steps",
    "tests.bdd.steps.transport_pool_steps",
    # transport_bdd_steps covers all four transport_(cache|coalesce|retry|timeout)
    # features in one module — shared step phrases would otherwise be
    # registered ambiguously across separate per-feature modules.
    "tests.bdd.steps.transport_bdd_steps",
    # Provider plugin BDD step modules. Five Wave-4 providers carry
    # skeleton skips until their implementations land.
    "tests.bdd.steps.provider_anthropic_steps",
    "tests.bdd.steps.provider_azure_foundry_steps",
    "tests.bdd.steps.provider_azure_legacy_steps",
    "tests.bdd.steps.provider_bedrock_steps",
    "tests.bdd.steps.provider_litellm_proxy_steps",
    "tests.bdd.steps.provider_ollama_steps",
    "tests.bdd.steps.provider_openai_steps",
    "tests.bdd.steps.provider_wire_common_steps",
    # Provider chat parameter-routing (gpt-5/o1/o3 max_completion_tokens translation).
    "tests.bdd.steps.provider_chat_max_completion_tokens_steps",
    # E2E provider journey step modules.
    "tests.bdd.steps.e2e_provider_chat_steps",
    "tests.bdd.steps.e2e_provider_embed_steps",
    "tests.bdd.steps.e2e_provider_health_steps",
    "tests.bdd.steps.e2e_provider_switch_steps",
    # probe-config health-check end-user CLI.
    "tests.bdd.steps.probe_config_health_steps",
    # Layered config loader — image-bundled base + sparse operator overlay.
    "tests.bdd.steps.config_layering_steps",
    # Plan B-parity Week 1 — conversation ingest CLI/use-case.
    "tests.bdd.steps.ingest_chat_steps",
    # Plan B-parity Week 2 — eval suite CLI/use-case.
    "tests.bdd.steps.eval_suite_steps",
    # Plan B-parity Week 4 Stream A — CI workflow extensions for eval gates.
    "tests.bdd.steps.eval_ci_gates_steps",
    # Plan B-parity Week 4 Stream C — soak BDD for fact-extractor pipeline.
    # Collected unconditionally; runtime-gated on KAIRIX_SOAK=1 inside the
    # step bodies so normal CI sees the scenarios but skips at first Given.
    "tests.bdd.steps.soak_fact_extractor_steps",
    # Plan B-parity Week 5 Stream A — MCP ingest + recall tools.
    "tests.bdd.steps.mcp_ingest_chat_steps",
    "tests.bdd.steps.mcp_facts_about_steps",
    # #472 — agent memory-write surfaces (kairix remember + memory_write MCP tool).
    "tests.bdd.steps.remember_cli_steps",
    "tests.bdd.steps.mcp_memory_write_steps",
    # P5 unified benchmark contract — quality + perf + stability lenses
    # wired through the canonical kairix benchmark run surface. Soak +
    # concurrent scenarios are tagged @pytest.mark.skip in the loader
    # until P3.b / P3.c land.
    "tests.bdd.steps.benchmark_unified_contract_steps",
    # Wave-2 IM-4 connector-framework extractors — passthrough +
    # markitdown plugins per
    # docs/architecture/connector-ingestion-architecture.md §2 + §3.
    "tests.bdd.steps.extractor_passthrough_steps",
    "tests.bdd.steps.extractor_markitdown_steps",
    # Wave-3 MM-1 connector-framework extractors — pdf_fallback plugin
    # (pdfplumber, MIT) per
    # docs/architecture/connector-ingestion-architecture.md §10 (Wave 3).
    "tests.bdd.steps.extractor_pdf_fallback_steps",
    # Wave-3 MM-2 OCR extractor — Tesseract default.
    "tests.bdd.steps.extractor_ocr_steps",
    "tests.bdd.steps.extractor_chain_escalation_steps",
    "tests.bdd.steps.connector_pipeline_failure_modes_steps",
    "tests.bdd.steps.silver_pathological_inputs_steps",
    # Wave-4 OF-1 slide-aware extractor — python-pptx-backed.
    "tests.bdd.steps.extractor_pptx_steps",
    # Wave-4 OF-2 docx extractor — python-docx, heading-hierarchy-aware.
    "tests.bdd.steps.extractor_docx_steps",
    # Wave-4 OF-3 xlsx extractor — openpyxl sheet-as-document.
    "tests.bdd.steps.extractor_xlsx_steps",
    # Connector plugin BDD step modules — Wave 2 IM-5 lands the first
    # connector (obsidian). Future connectors (sharepoint, dex_crm, ...)
    # append a sibling entry per F36.
    "tests.bdd.steps.connector_obsidian_steps",
    # Bronze store framework BDD — write/replay/orphan-reap scenarios.
    # Phase 7 of streaming-bronze removed the FilesystemBronzeStore class
    # and the bronze_ttl_gc / orphan-reap maintenance stages. The
    # corresponding BDD features were deleted in the same commit; their
    # step modules are gone from this list.
    # PR-2 — feature-flag scaffold (kairix features status CLI + MCP tool).
    # See docs/architecture/feature-flag-architecture.md.
    "tests.bdd.steps.cli_features_steps",
    "tests.bdd.steps.mcp_features_status_steps",
    # Canonical-credential-naming CLI (kairix secrets verify).
    # See kairix/secrets/cli.py + ADR-031. The legacy alias migration
    # surface (migrate-list) was retired in #369 once operators
    # migrated to canonical KAIRIX_* env-var names.
    "tests.bdd.steps.secrets_cli_steps",
    "tests.bdd.steps.secrets_set_steps",
    # MCP tool_secrets_verify — agent-callable preflight envelope.
    "tests.bdd.steps.mcp_secrets_verify_steps",
    # Dead-letter triage surface (kairix dead-letter status CLI +
    # tool_dead_letter_status MCP). See GH #337 / #351.
    "tests.bdd.steps.cli_dead_letter_steps",
    "tests.bdd.steps.mcp_dead_letter_status_steps",
    # Wave 5 KP-1 — Dex CRM connector flag at introduce stage. F54.
    "tests.bdd.steps.feature_flag_connector_dex_crm_steps",
    "tests.bdd.steps.connector_dex_crm_steps",
    # F62 reference test — multi-tick cursor persistence invariants.
    "tests.bdd.steps.connector_cursor_persistence_steps",
    # GH #334 — Neo4j entity-graph drain (Curator-coupling boundary).
    "tests.bdd.steps.neo4j_drain_steps",
    # GH #336 / ADR-024 Bundle B — documents_media writer surfacing
    # per-extractor + per-document outcome.
    "tests.bdd.steps.documents_media_writer_steps",
    # GH #338 / ADR-024 F70 paydown — document_pages writer for paged
    # extractors (PDF / PPTX / DOCX); enables MM-3 citation paths.
    "tests.bdd.steps.document_pages_writer_steps",
    # ADR-025 Phase 1 — pipeline_status_emit flag both-branch coverage.
    "tests.bdd.steps.feature_flag_pipeline_status_emit_steps",
    # F64 reference test — SharePoint Graph 429 / Retry-After handling.
    "tests.bdd.steps.sharepoint_rate_limit_steps",
    # F63 reference test — maintenance scale-bound per-tick row cap.
    "tests.bdd.steps.maintenance_scale_bound_steps",
    # ADR-020 / F66 — connector per-tick budget + disk-watermark gate.
    "tests.bdd.steps.per_tick_budget_steps",
    # Wave 5 KP-2 — M365 email-headers connector (header-only per ADR-004).
    "tests.bdd.steps.connector_m365_email_headers_steps",
    "tests.bdd.steps.feature_flag_connector_m365_email_headers_steps",
    # Wave 5 KP-3 — M365 calendar connector + flag.
    "tests.bdd.steps.connector_m365_calendar_steps",
    "tests.bdd.steps.feature_flag_connector_m365_calendar_steps",
    # Wave 5 SharePoint — document-library connector + flag. Shares the
    # M365 AAD app registration (Sites.Read.All + Files.Read.All on the
    # same client-credentials triple).
    "tests.bdd.steps.connector_sharepoint_steps",
    "tests.bdd.steps.feature_flag_connector_sharepoint_steps",
    "tests.bdd.steps.connector_sharepoint_path_filtering_steps",
    # Wave E Notion — workspace-pages connector + flag. See
    # docs/architecture/connector-scope-topology/connector-design-specs/notion.md.
    "tests.bdd.steps.connector_notion_steps",
    "tests.bdd.steps.feature_flag_connector_notion_steps",
    # Wave E GitHub — greenfield Wave-E build per
    # docs/architecture/connector-scope-topology/connector-design-specs/github.md.
    # ``connector_github`` (introduce stage) gates the connector slot.
    # ``topology_v2_github`` retired post-cutover (task #132).
    "tests.bdd.steps.connector_github_steps",
    "tests.bdd.steps.feature_flag_connector_github_steps",
    # Wave E Slack — workspace-channels connector + flag. See
    # docs/architecture/connector-scope-topology/connector-design-specs/slack.md.
    "tests.bdd.steps.connector_slack_steps",
    "tests.bdd.steps.feature_flag_connector_slack_steps",
    # IM-6 FTS-gap regression pin — connector-ingested chunks must be
    # findable via BM25 (the cutover surfaced 68,814 chunks in the
    # ``obsidian`` collection invisible to BM25 because the chunk-writer
    # skipped the FTS5 write).
    "tests.bdd.steps.connector_search_round_trip_steps",
    # Topology v2 Wave D — operator config promotion (6 YAML blocks +
    # 5 cross-reference validators + kairix cc-pair CLI + topology v2
    # diagnostics in `kairix features status`). Wave A/B/C/D flag gates
    # retired post-cutover (task #132); CLI/MCP surfaces stay.
    "tests.bdd.steps.cli_cc_pair_steps",
    "tests.bdd.steps.mcp_cc_pair_steps",
    # KFEAT-018 — release-time paydown doc snapshot currency gate.
    # See docs/features/KFEAT-018-paydown-doc-refresh/BRIEF.md.
    "tests.bdd.steps.check_paydown_doc_currency_steps",
    # Wave 5 Gmail — Google Workspace mailbox connector. Single-mailbox
    # per cc_pair (Onyx pattern); full-message body + envelope; History
    # API for change detection. ``connector_gmail`` (introduce stage)
    # gates the connector slot; ``topology_v2_gmail`` retired post-cutover
    # (task #132).
    "tests.bdd.steps.connector_gmail_steps",
    "tests.bdd.steps.feature_flag_connector_gmail_steps",
    # Wave E Google Drive — workspace-files connector + flag. See
    # kairix/connectors/google_drive/README.md for the connector
    # capability surface and operator-side credential provisioning
    # (tracked under GH #356). ``topology_v2_google_drive`` retired.
    "tests.bdd.steps.connector_google_drive_steps",
    # Apple iCloud CalDAV connector — ``topology_v2_apple_caldav``
    # retired post-cutover (task #132).
    "tests.bdd.steps.connector_apple_caldav_steps",
    # Google Calendar connector — ``topology_v2_google_calendar``
    # retired post-cutover (task #132). Ships OFF until Google Workspace
    # OAuth credentials are provisioned (GH #356).
    "tests.bdd.steps.connector_google_calendar_steps",
    # ADR-028 Wave G.1 — per-type chunkers (paged / structured formats).
    # Three chunker plugins shipping in one batch: SlideChunker (PPTX),
    # SheetRowChunker (XLSX / .xls / .xlsm), DocxHeadingChunker (DOCX).
    "tests.bdd.steps.chunker_slide_steps",
    "tests.bdd.steps.chunker_sheet_row_steps",
    "tests.bdd.steps.chunker_docx_heading_steps",
    # ADR-029 G.1 — agent-facing query queue + carry-along delivery.
    # tool_search-only spike behind the agent_query_queue flag (default OFF).
    "tests.bdd.steps.agent_query_queue_steps",
    "tests.bdd.steps.feature_flag_agent_query_queue_steps",
    # Issue #456 — F54 both-branch coverage for the
    # intent_confidence_gated_boosts feature flag (driven via the
    # intent_confidence_passes flag_reader DI seam).
    "tests.bdd.steps.feature_flag_intent_confidence_gated_boosts_steps",
    # ADR-036 #459 Slice A — F54 both-branch coverage for the
    # entity_summary_indexing_enabled feature flag (worker-tick gate
    # for projecting Neo4j n.summary into the chunk store).
    "tests.bdd.steps.feature_flag_entity_summary_indexing_enabled_steps",
    # ADR-036 #461 Slice C — operator-facing BDD for entity-summary
    # indexing (composed-path scenarios via the production factory).
    "tests.bdd.steps.entity_summary_indexing_steps",
    # #432 deferred BDD — source-tier ranking via SourceTierBoost.
    "tests.bdd.steps.source_tier_ranking_steps",
    # #431 deferred BDD — canonical-entity seeding into Neo4j.
    "tests.bdd.steps.canonical_entity_seeding_steps",
    # Plan 1 task 10 — kairix self-installer BDD step impls.
    # Drives kairix init + uninstall via the real CLI subprocess surface
    # (F46-compliant) against an XDG-redirected tmp root. Runtime-gated:
    # scenarios needing root / a live user systemd bus skip with fix-style
    # affordances; the user-mode sibling scenarios cover the equivalent
    # code paths on every dev box.
    "tests.bdd.steps.install_steps",
]

# PVT placeholder steps — catch-all ``pytest.skip`` until #284 harness ships.
# Gated on ``KAIRIX_PVT=1`` so the regex-catch-all parser doesn't intercept
# every Given/When/Then across the layer-2 BDD suite when PVT is off (the
# default). The tests/pvt/conftest.py autoskip is the primary defence — it
# skips PVT-marked items at collection time; this catch-all is the secondary
# defence that the PVT brief reserves for the ``KAIRIX_PVT=1`` mode where
# the autoskip is intentionally bypassed.
import os as _os  # noqa: E402 — keep pytest_plugins assembly above other imports

if _os.environ.get("KAIRIX_PVT") == "1":
    pytest_plugins.append("tests.pvt.steps.pvt_placeholder_steps")

# Hard kill-switch on the kairix.connect.oauth2.* default browser path.
# 2026-06-01 incident: a stream of real Slack "client_id not valid" approval
# popups appeared on the operator's desktop during agent test runs — root
# cause was the per-flow ``_DefaultBrowser`` fallback firing real
# ``webbrowser.open`` when a test path escaped the FakeBrowserLauncher
# injection seam. Setting the env var here at conftest import time means
# every test runs with the kill-switch ON; production leaves it unset.
# F4-clean: kairix-side read lives in kairix/paths.py::connect_browser_disabled.
_os.environ.setdefault("KAIRIX_CONNECT_DISABLE_BROWSER", "1")

from tests.fixtures.embeddings import fake_embedding  # noqa: E402
from tests.fixtures.neo4j_mock import FakeNeo4jClient  # noqa: E402


@pytest.fixture(autouse=True)
def no_azure_calls(monkeypatch, request):
    """Block accidental Azure API calls in all tests except those marked e2e.

    The ``delenv`` calls are the load-bearing protection — they remove
    real operator credentials (``KAIRIX_AZURE_API_KEY`` /
    ``KAIRIX_LLM_API_KEY``) from the per-test env so a test that hits a
    code path through ``kairix.secrets`` doesn't accidentally use the
    developer's Azure account. Tests marked ``@pytest.mark.e2e``
    bypass this and must set ``KAIRIX_E2E=1`` to confirm intent.

    This fixture is the reason ``tests/conftest.py`` stays baselined for
    F2 — the ``delenv`` operation is a deliberate safety net at the env
    boundary, not a test-shaping hack. F2 stays baselined here on
    purpose; promoting the fixture out of monkeypatch would lose the
    per-test isolation that prevents env leak between tests.
    """
    if "e2e" not in request.keywords:
        monkeypatch.delenv("KAIRIX_AZURE_API_KEY", raising=False)
        monkeypatch.delenv("KAIRIX_LLM_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _reset_embed_coalescer():
    """Drop the process-shared embed coalescer between tests (#288).

    The coalescer singleton owns a background dispatcher thread — if a
    test triggers construction (via ``embed_text`` without a ``client=``
    kwarg) the thread would survive into the next test and the next
    test's batch dispatcher closure would be stale. Resetting on teardown
    keeps each test's coalescer state isolated.
    """
    yield
    from kairix.transport.coalesce import reset_embed_coalescer

    reset_embed_coalescer()


@pytest.fixture(autouse=True)
def _reset_client_pool():
    """Drop the process-shared transport client between tests.

    The :mod:`kairix.transport.pool` singleton caches the built
    OpenAI-compatible client process-wide so coalescer batches reuse
    one ``httpx.Client`` connection pool. Tests that exercise that
    path through the production accessor (``_get_client``) would
    otherwise inherit a client from a previous test — including one
    built against now-deleted Azure credentials. Resetting on
    teardown keeps each test's pool state isolated, matching the
    pattern established by ``_reset_embed_coalescer``.
    """
    yield
    from kairix.transport.pool import reset_client_cache

    reset_client_cache()


@pytest.fixture(autouse=True)
def _reset_workstream_b_caches():
    """Drop the process-shared brief + prep + source caches between tests (#396 W-B).

    ``kairix.use_cases.brief``, ``kairix.use_cases.prep``, and
    ``kairix.agents.briefing.sources`` each own a process-shared TTL LRU
    added by the MCP perf sprint. Without these resets, a test that
    populates the cache leaks state into the next test, breaking
    deterministic hit/miss assertions. Mirrors the pattern established
    by ``_reset_embed_coalescer``.
    """
    yield
    from kairix.agents.briefing.sources import reset_brief_source_cache
    from kairix.use_cases.brief import (
        reset_brief_output_cache,
        reset_health_probe_cache,
    )
    from kairix.use_cases.prep import reset_prep_summary_cache

    reset_brief_output_cache()
    reset_prep_summary_cache()
    reset_brief_source_cache()
    reset_health_probe_cache()


@pytest.fixture
def neo4j_client():
    """FakeNeo4jClient with default test entities. No real Neo4j connection."""
    return FakeNeo4jClient()


@pytest.fixture
def neo4j_client_empty():
    """FakeNeo4jClient with no entities."""
    return FakeNeo4jClient(entities=[])


@pytest.fixture
def e2e_db(tmp_path):
    """One-line E2E setup: real schema in tmpdir; factory-ready.

    Builds a ``KairixPaths`` via ``FakePaths`` rooted at ``tmp_path``,
    creates the production SQLite schema (``create_schema``) in the
    target ``db_path``, and returns the paths object ready for
    ``factory.build_search_pipeline(paths=...)`` (or any other
    composed-production-path entry point).

    Used by F48 tests (``tests/e2e/test_composed_production_path.py``)
    and any other composed-path test that wants the canonical
    tmpdir+schema+factory wiring in one fixture rather than open-coding
    the four-line setup chain.

    See ``docs/architecture/test-discipline-hardening.md`` §4.4 for the
    affordance rationale.
    """
    import sqlite3

    from kairix.core.db.schema import create_schema
    from tests.fakes import FakePaths

    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    paths.document_root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(paths.db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    create_schema(db)
    db.close()
    return paths


@pytest.fixture
def fake_llm_backend():
    """Fake LLMBackend satisfying the Protocol. No Azure calls."""
    import hashlib
    import struct

    class FakeLLM:
        def chat(self, messages: list, max_tokens: int = 800) -> str:
            return "fake response"

        def embed(self, text: str) -> list[float]:
            # SHA-256 truncated to 32 bits — deterministic across runs (PYTHONHASHSEED
            # randomises hash()) and the 2^32 seed space makes collisions vanish (#240).
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
            return fake_embedding(seed=seed)

        def embed_as_bytes(self, text: str) -> bytes | None:
            vec = self.embed(text)
            return struct.pack(f"{len(vec)}f", *vec)

        def dimension(self) -> int:
            return 1536

    return FakeLLM()
