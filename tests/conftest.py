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
    "tests.bdd.steps.search_intent_gated_boosts_steps",
    "tests.bdd.steps.search_chunk_date_recency_steps",
    "tests.bdd.steps.search_collection_retrieval_overrides_steps",
    "tests.bdd.steps.search_cli_steps",
    "tests.bdd.steps.summarise_cli_steps",
    "tests.bdd.steps.kairix_cli_top_level_steps",
    "tests.bdd.steps.store_cli_steps",
    "tests.bdd.steps.brief_cli_steps",
    "tests.bdd.steps.setup_cli_steps",
    "tests.bdd.steps.wikilinks_cli_steps",
    "tests.bdd.steps.entity_cli_steps",
    "tests.bdd.steps.entity_audit_steps",
    "tests.bdd.steps.curator_cli_steps",
    "tests.bdd.steps.mcp_cli_steps",
    "tests.bdd.steps.embed_cli_steps",
    "tests.bdd.steps.timeline_cli_steps",
    "tests.bdd.steps.soak_steps",
    "tests.bdd.steps.warm_steps",
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
    # #318 added the @orphan scenario after the 2026-05-25 SharePoint
    # incident left 36 GB of unreferenced blobs.
    "tests.bdd.steps.connector_bronze_steps",
    # #316 — bronze_ttl_gc feature flag (default OFF). F54 both-branch
    # coverage drives the TTL GC stage through a scheduler-shaped closure.
    "tests.bdd.steps.feature_flag_bronze_ttl_gc_steps",
    # PR-2 — feature-flag scaffold (kairix features status CLI + MCP tool).
    # See docs/architecture/feature-flag-architecture.md.
    "tests.bdd.steps.cli_features_steps",
    "tests.bdd.steps.mcp_features_status_steps",
    # PR-6 — IM-6 recast: ``obsidian_connector_primary`` flag at introduce
    # stage. Both-branch coverage per F54.
    "tests.bdd.steps.feature_flag_obsidian_connector_primary_steps",
    # Wave 5 KP-1 — Dex CRM connector flag at introduce stage. F54.
    "tests.bdd.steps.feature_flag_connector_dex_crm_steps",
    "tests.bdd.steps.connector_dex_crm_steps",
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
    # Two flags: ``connector_github`` (introduce stage, gates the
    # connector slot) + ``topology_v2_github`` (Wave E per-repo
    # Container emission pilot). F54 both-branch coverage.
    "tests.bdd.steps.connector_github_steps",
    "tests.bdd.steps.feature_flag_connector_github_steps",
    "tests.bdd.steps.feature_flag_topology_v2_github_steps",
    # Wave E Slack — workspace-channels connector + flag. See
    # docs/architecture/connector-scope-topology/connector-design-specs/slack.md.
    "tests.bdd.steps.connector_slack_steps",
    "tests.bdd.steps.feature_flag_connector_slack_steps",
    # Topology v2 Wave A — schema additions + new dataclasses behind
    # ``topology_v2_schema`` flag. F54 both-branch coverage. See
    # docs/architecture/connector-scope-topology/ADR.md.
    "tests.bdd.steps.feature_flag_topology_v2_schema_steps",
    # IM-6 FTS-gap regression pin — connector-ingested chunks must be
    # findable via BM25 (the cutover surfaced 68,814 chunks in the
    # ``obsidian`` collection invisible to BM25 because the chunk-writer
    # skipped the FTS5 write).
    "tests.bdd.steps.connector_search_round_trip_steps",
    # Topology v2 Wave B — capability mix-in Protocols + default-impl
    # shims behind ``topology_v2_protocol`` flag. F54 both-branch coverage.
    "tests.bdd.steps.feature_flag_topology_v2_protocol_steps",
    # Topology v2 Wave C — cc_pair lifecycle + CollectionRouter + Chunker
    # registry + ScopeProfileResolver + ResultEnvelope behind the
    # ``topology_v2_runtime`` flag. F54 both-branch coverage.
    "tests.bdd.steps.feature_flag_topology_v2_runtime_steps",
    # Topology v2 Wave D — operator config promotion (6 YAML blocks +
    # 5 cross-reference validators + kairix cc-pair CLI + topology v2
    # diagnostics in `kairix features status`) behind the
    # ``topology_v2_config`` flag. F45 / F54 coverage.
    "tests.bdd.steps.cli_cc_pair_steps",
    "tests.bdd.steps.mcp_cc_pair_steps",
    "tests.bdd.steps.feature_flag_topology_v2_config_steps",
    # KFEAT-018 — release-time paydown doc snapshot currency gate.
    # See docs/features/KFEAT-018-paydown-doc-refresh/BRIEF.md.
    "tests.bdd.steps.check_paydown_doc_currency_steps",
    # Topology v2 Wave E — per-connector multi-container pilot for the
    # obsidian connector behind the ``topology_v2_obsidian`` flag.
    # F45 / F54 coverage. Each top-level vault folder becomes its own
    # Container with its own delta cursor; load_hierarchy walks the
    # filesystem parent-before-child per F58.
    "tests.bdd.steps.feature_flag_topology_v2_obsidian_steps",
    # Topology v2 Wave E — per-connector multi-container pilot for the
    # m365_email_headers connector behind the
    # ``topology_v2_m365_email_headers`` flag. F45 / F54 coverage. Each
    # configured mailbox becomes its own Container with its own Graph
    # delta cursor; load_hierarchy emits one root FOLDER plus one
    # FOLDER per mailbox parent-before-child per F58.
    "tests.bdd.steps.feature_flag_topology_v2_m365_email_headers_steps",
    # Topology v2 Wave E — per-connector multi-container pilot for the
    # dex_crm connector behind the ``topology_v2_dex_crm`` flag.
    # F45 / F54 coverage. Dex's API is single-tenant single-cursor so
    # the connector emits one tenant Container; load_hierarchy emits
    # the Dex / Person / Organisation / Relationship FOLDER tree
    # parent-before-child per F58.
    "tests.bdd.steps.feature_flag_topology_v2_dex_crm_steps",
    # Topology v2 Wave E — per-connector slice for the m365_calendar
    # connector behind the ``topology_v2_m365_calendar`` flag. Sibling
    # to the obsidian pilot. Each configured calendar (per UPN) becomes
    # its own Container with its own Graph @odata.deltaLink cursor;
    # load_hierarchy emits a root FOLDER node plus one child per
    # configured calendar parent-before-child per F58.
    "tests.bdd.steps.feature_flag_topology_v2_m365_calendar_steps",
    # Topology v2 Wave E — per-connector slice for the sharepoint
    # connector behind the ``topology_v2_sharepoint`` flag. Sibling
    # to the m365_calendar / obsidian / dex_crm pilots. Each configured
    # Graph drive becomes its own Container with its own
    # @odata.deltaLink cursor; load_hierarchy emits a root SITE FOLDER
    # plus one DRIVE FOLDER per configured drive parent-before-child
    # per F58; Resolver.reindex replays only the supplied failed ids.
    "tests.bdd.steps.feature_flag_topology_v2_sharepoint_steps",
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
