"""Fitness function catalogue — the single source of truth for kairix's F-rules.

Every ``scripts/checks/check_*.py`` has a corresponding entry here.
Every entry references a real check. The bidirectional consistency is
proven by ``tests/checks/test_rule_catalogue.py``.

Rationale (ADR-026 follow-up)
-----------------------------
F1...F73 grew organically over 18 months. Numbers are stable for
commit / ADR / runbook traceability (CWE pattern), but the flat
numeric space lost conceptual cohesion. The catalogue adds two
orthogonal dimensions every rule carries:

* **category** — what concern the rule protects. Drives CLAUDE.md
  grouping and future "which rules govern plugin contracts?" queries.
* **scope** — what shape the rule fires on (per-file, per-plugin,
  per-flag, per-table, cross-cutting). Drives the FitnessRule
  abstraction choice (default enumeration vs custom override).

Numbers stay flat and global; categories carry the cohesion. The
CWE approach: never renumber, never reuse — the rule's ID is a
permanent ship tag, the category is mutable metadata.

Status vocabulary
-----------------
* ``shipped`` — fully enforced; baseline grandfathers existing
  offenders; net-new violations block at pre-commit / CI.
* ``vacuous`` — shipped detector but no current violations because
  the relevant tree doesn't exist yet (e.g. ``kairix/chunkers/``).
  Fires the moment Wave N lands the tree.
* ``proxy`` — structural approximation of a concern that ideally
  needs runtime instrumentation. Acknowledged limitation.
* ``proposed`` — designed and documented; not yet implemented.
  Deferred to a future ADR with prerequisites called out.
* ``superseded`` — replaced by another rule; entry kept for
  traceability of historical references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Category = Literal[
    "layering",
    "test-discipline",
    "plugin-contract",
    "production-safety",
    "schema-integrity",
    "feature-flag",
    "agent-affordance",
    "repo-hygiene",
    "observability",
    "go-discipline",
    "coverage",
    "process",
]

Scope = Literal[
    "per-file",
    "per-class",
    "per-method",
    "per-plugin",
    "per-flag",
    "per-table",
    "per-test",
    "per-protocol-method",
    "per-commit",
    "cross-cutting",
]

Status = Literal[
    "shipped",
    "vacuous",
    "proxy",
    "proposed",
    "superseded",
]


@dataclass(frozen=True)
class RuleEntry:
    """One row in the catalogue.

    ``id`` is the human-facing F-number or named identifier ("F26",
    "no-logging-secrets"). ``gate`` is the baseline filename root
    (``f26`` → ``.architecture/baseline/f26-files.txt``). ``check``
    is the script filename minus the ``check_`` prefix and ``.py``
    suffix.

    Runner dispatch (#499 Phase 2 — the catalogue-driven runner)
    -----------------------------------------------------------
    ``scripts/checks/run_checks.py`` derives the exact check
    invocation for each entry from two optional fields:

    * ``script`` — the exact script under ``scripts/checks/`` to run,
      WHEN it diverges from the default. Default (``script is None``)
      is the python check ``check_<check>.py`` invoked as
      ``python3 scripts/checks/check_<check>.py``. Set ``script`` to a
      ``check-*.sh`` name when the rule runs through a shell wrapper or
      a pure-shell detector (the runner infers shell-vs-python from the
      ``.sh`` / ``.py`` extension). This is required for the handful of
      rules whose run-all invocation is NOT ``check_<check>.py``:
      the pure-shell detectors (F3 / F4 / F10) whose ``check`` field
      points at a different python file, and the F2/F4, F3/F14, F10/F21
      duals that share a ``check`` field but run distinct scripts.

    * ``run_all`` — whether ``run_checks.py --all`` (and so
      ``run-all.sh`` / CI Stage 0 / pre-commit) dispatches this entry.
      Defaults to ``True``. Set ``False`` for rules that run elsewhere
      in the SDLC (``baseline-shrinking`` at release time;
      ``sonar-new-code`` in the security stage; ``worktree-isolation``
      and ``paydown-doc-currency`` invoked out-of-band) so the runner
      reproduces exactly the set ``run-all.sh`` ran before this change.
    """

    id: str
    gate: str
    check: str
    category: Category
    scope: Scope
    summary: str
    adr_origin: str | None = None
    status: Status = "shipped"
    tags: tuple[str, ...] = field(default_factory=tuple)
    script: str | None = None
    run_all: bool = True


_ENTRIES: tuple[RuleEntry, ...] = (
    # ----- layering --------------------------------------------------------
    RuleEntry(
        id="F26",
        gate="f26",
        check="provider_layer_imports",
        category="layering",
        scope="per-file",
        summary="kairix/core/** may not import kairix/providers/** or kairix/transport/**",
        adr_origin="docs/architecture/provider-plugin-architecture.md",
    ),
    RuleEntry(
        id="F27",
        gate="f27",
        check="no_cross_provider",
        category="layering",
        scope="per-file",
        summary="kairix/providers/<a>/ may not import another provider — plugins ship independently",
    ),
    RuleEntry(
        id="F34",
        gate="f34",
        check="f34_core_connector_layer_imports",
        category="layering",
        scope="per-file",
        summary="kairix/core/connectors/** may not import kairix/connectors/** or kairix/extractors/**",
        adr_origin="docs/architecture/connector-ingestion-architecture.md",
    ),
    RuleEntry(
        id="F35",
        gate="f35",
        check="f35_no_cross_connector",
        category="layering",
        scope="per-file",
        summary="kairix/connectors/<a>/ may not import another connector or any extractor",
    ),
    RuleEntry(
        id="F37",
        gate="f37",
        check="f37_singular_sync",
        category="layering",
        scope="per-file",
        summary="change-detection / sync code only under kairix/connectors or kairix/core/connectors",
        tags=("singularity",),
    ),
    RuleEntry(
        id="F38",
        gate="f38",
        check="f38_silver_singleton",
        category="layering",
        scope="per-file",
        summary="Silver processing (chunking + signal extraction) only in kairix/core/connectors/silver.py",
        tags=("singularity",),
    ),
    RuleEntry(
        id="F44",
        gate="f44",
        check="f44_engagement_firm_boundary",
        category="layering",
        scope="per-file",
        summary="engagement-scope code may not import firm-scope storage clients (psycopg etc.)",
        script="check-f44-engagement-firm-boundary.sh",
    ),
    RuleEntry(
        id="F61",
        gate="f61",
        check="f61_collection_router_singleton",
        category="layering",
        scope="per-file",
        summary="bare _SqliteChunkWriter(db, collection=...) construction only under kairix/core/connectors/",
        adr_origin="docs/architecture/connector-scope-topology/ADR.md",
        tags=("singularity",),
    ),
    # ----- test-discipline -------------------------------------------------
    RuleEntry(
        id="F1",
        gate="no-internal-patches",
        check="no_internal_patches",
        category="test-discipline",
        scope="per-file",
        summary="no @patch / monkeypatch on kairix internals — inject Fake* through a seam",
        script="check-no-internal-patches.sh",
    ),
    RuleEntry(
        id="F2",
        gate="no-env-monkeypatch",
        check="no_env_monkeypatch",
        category="test-discipline",
        scope="per-file",
        summary='no monkeypatch.setenv("KAIRIX_*") — pass deps as kwargs instead',
        script="check-no-env-monkeypatch.sh",
    ),
    RuleEntry(
        id="F5",
        gate="no-internal-test-imports",
        check="no_internal_imports",
        category="test-discipline",
        scope="per-file",
        summary="no internal-name imports in tests — use public surface only",
    ),
    RuleEntry(
        id="F6",
        gate="no-test-only-kwargs",
        check="no_test_only_kwargs",
        category="test-discipline",
        scope="per-method",
        summary="no *_fn=None test-only kwargs in production",
    ),
    RuleEntry(
        id="F7",
        gate="per-file-coverage-floor",
        check="per_file_coverage",
        category="coverage",
        scope="per-file",
        summary="per-file coverage ≥ 90% (unit) — Stage 2 floor",
    ),
    RuleEntry(
        id="F8",
        gate="test-markers",
        check="test_markers",
        category="test-discipline",
        scope="per-test",
        summary="every test_* carries a category marker (unit/bdd/contract/integration/e2e/slow/soak/invariant)",
    ),
    RuleEntry(
        id="F9",
        gate="per-file-coverage-floor-union",
        check="per_file_coverage",
        category="coverage",
        scope="per-file",
        summary="per-file coverage ≥ 90% on union of unit + integration (Stage 5)",
        run_all=False,
    ),
    RuleEntry(
        id="F11",
        gate="test-skip-rationale",
        check="test_skip_rationale",
        category="test-discipline",
        scope="per-test",
        summary="every pytest.mark.skip/skipif/xfail/importorskip has a rationale comment",
    ),
    RuleEntry(
        id="F12",
        gate="bdd-no-implementation-leaks",
        check="bdd_happy_path",
        category="test-discipline",
        scope="per-file",
        summary="every BDD feature has a happy-path scenario",
        tags=("bdd",),
    ),
    RuleEntry(
        id="F13",
        gate="bdd-no-implementation-leaks",
        check="bdd_no_implementation_leaks",
        category="test-discipline",
        scope="per-file",
        summary="BDD scenarios reject implementation symbols (Mock, kairix.<pkg>.<symbol>)",
        tags=("bdd",),
    ),
    RuleEntry(
        id="F45",
        gate="f45",
        check="f45_new_capability_bdd",
        category="test-discipline",
        scope="per-commit",
        summary="every new CLI/MCP/provider/connector/extractor adds a BDD feature in the same commit",
        adr_origin="docs/architecture/test-discipline-hardening.md",
        script="check-f45-new-capability-bdd.sh",
    ),
    RuleEntry(
        id="F46",
        gate="f46",
        check="f46_bdd_step_composition",
        category="test-discipline",
        scope="per-file",
        summary="BDD step impls compose via CLI/MCP/factory — no direct *Pipeline(...) construction",
        script="check-f46-bdd-step-composition.sh",
    ),
    RuleEntry(
        id="F47",
        gate="f47-integration-factory",
        check="f47_integration_factory",
        category="test-discipline",
        scope="per-file",
        summary="integration tests construct multi-component pipelines via kairix.core.factory.build_*",
    ),
    RuleEntry(
        id="F48",
        gate="f48",
        check="f48_e2e_present",
        category="test-discipline",
        scope="cross-cutting",
        summary="tests/e2e/test_composed_production_path.py exists, runs in CI Stage 4.5",
        script="check-f48-e2e-present.sh",
    ),
    RuleEntry(
        id="F54",
        gate="f54",
        check="f54_flag_both_branch_tested",
        category="feature-flag",
        scope="per-flag",
        summary=(
            "every flag has OFF + ON BDD scenarios, integration tests, and (for top-level) an E2E composed-path test"
        ),
        tags=("test-discipline",),
        script="check-f54-flag-both-branch-tested.sh",
    ),
    RuleEntry(
        id="F62",
        gate="f62-stateful-multi-tick",
        check="f62_stateful_multi_tick",
        category="test-discipline",
        scope="per-class",
        summary="every stateful tick/run_batch component has a multi-tick advance/idempotency test",
        adr_origin="2026-05 production cursor-write incident",
    ),
    RuleEntry(
        id="F68",
        gate="f68-protocol-failure-modes",
        check="f68_protocol_failure_modes",
        category="test-discipline",
        scope="per-protocol-method",
        summary="every Protocol method has a failure-injection contract test",
        adr_origin="docs/architecture/ADR-024-test-pyramid-redesign.md §F68",
    ),
    RuleEntry(
        id="F69",
        gate="f69-scale-bound-tests",
        check="f69_scale_bound_tests",
        category="test-discipline",
        scope="per-test",
        summary="every integration test with .fetchall()/list_changes has a ≥10K-row variant",
        adr_origin="docs/architecture/ADR-024-test-pyramid-redesign.md §F69",
    ),
    RuleEntry(
        id="F72",
        gate="f72-integrity-invariants",
        check="f72_integrity_invariants",
        category="test-discipline",
        scope="cross-cutting",
        summary="every cross-layer integrity invariant has a fixture-scale AND soak-scale test",
        adr_origin="docs/architecture/ADR-024-test-pyramid-redesign.md §F72",
    ),
    RuleEntry(
        id="F81",
        gate="f81-fresh-install-smoke",
        check="f81_fresh_install_smoke",
        category="test-discipline",
        scope="cross-cutting",
        summary=(
            "CI fresh-install smoke — clean dir → compose boot → healthz → MCP handshake → "
            "wizard 200 → BM25 search hit (scripts/checks/check-fresh-install-smoke.sh via "
            ".github/workflows/fresh-install-smoke.yml; per-commit leg checks the wiring)"
        ),
        adr_origin="onboarding tranche 3, 2026-06-11 — registered via EPIC #499 Phase 0",
    ),
    RuleEntry(
        id="F82",
        gate="f82",
        check="f82_wall_clock_ceilings",
        category="test-discipline",
        scope="per-test",
        summary=(
            "wall-clock ceiling assertions banned outside soak/probe tiers — elapsed-time vs numeric "
            "ceiling requires a slow/soak/load/pvt marker or # F82-allowed: rationale (#493 flake family)"
        ),
        adr_origin="EPIC #499 Phase 0 — #493 wall-clock flake family",
    ),
    RuleEntry(
        id="F84",
        gate="f84",
        check="f84_config_round_trip",
        category="test-discipline",
        scope="per-method",
        summary=(
            "every production config-write site (write_config_updates / update_config_file / "
            "write_config_yaml / config-writer-named yaml.dump) has a composed write→read "
            "round-trip test through the canonical layered reader (#492 overlay split-brain class)"
        ),
        adr_origin="EPIC #499 Phase 1 — #492 overlay split-brain (H1)",
    ),
    RuleEntry(
        id="F88",
        gate="f88",
        check="f88_docstring_raises_parity",
        category="test-discipline",
        scope="per-method",
        summary=(
            "every SetupService / KairixSetupService method documenting a concrete Raises: type "
            "is either handled (except, incl. superclass) in the wizard route that calls it or "
            "render-tested under tests/platform/setup (session-escape-5 raw-500 class)"
        ),
        adr_origin="EPIC #499 Phase 1 — session-escape-5 (save_source ValueError surfaced as 500)",
    ),
    RuleEntry(
        id="F87",
        gate="f87",
        check="f87_persist_load_corpus",
        category="test-discipline",
        scope="cross-cutting",
        summary=(
            "every registered persist/load pair (set_secret/load_secrets_file, FileTokenStore/secrets "
            "read, write_config_updates/load_merged_mapping, EmbeddingCache put_many/get_many) ships an "
            "adversarial round-trip corpus — multi-line + unicode + large (>=64KiB) + escape-lookalike "
            "(the GitHub-PEM consent-failure class)"
        ),
        adr_origin="EPIC #499 Phase 1 — GitHub-PEM multi-line secret round-trip (session escape 2)",
    ),
    # ----- plugin-contract -------------------------------------------------
    RuleEntry(
        id="F28",
        gate="f28",
        check="provider_bdd_completeness",
        category="plugin-contract",
        scope="per-plugin",
        summary="every provider plugin has matching BDD feature + Examples-table row in E2E features",
    ),
    RuleEntry(
        id="F36",
        gate="f36",
        check="f36_connector_bdd_parity",
        category="plugin-contract",
        scope="per-plugin",
        summary="every connector + extractor plugin has matching BDD feature + Examples-table row",
        script="check-f36-connector-bdd-parity.sh",
    ),
    RuleEntry(
        id="F40",
        gate="f40",
        check="f40_extractor_version",
        category="plugin-contract",
        scope="per-plugin",
        summary="every Extractor plugin declares module-level version: str + make_extractor factory",
    ),
    RuleEntry(
        id="F41",
        gate="f41",
        check="f41_plugin_typing",
        category="plugin-contract",
        scope="per-plugin",
        summary="every plugin tree has py.typed marker + no unjustified # type: ignore",
    ),
    RuleEntry(
        id="F42",
        gate="f42",
        check="f42_protocol_return_types",
        category="plugin-contract",
        scope="per-protocol-method",
        summary="Protocol methods return frozen-dc/tuple — never dict[str, Any] or bare Any",
        tags=("observability",),
    ),
    RuleEntry(
        id="F43",
        gate="f43",
        check="f43_plugin_contract_tests",
        category="plugin-contract",
        scope="per-plugin",
        summary="every plugin has tests/contracts/test_<name>_protocol.py exercising real + fake impls",
    ),
    RuleEntry(
        id="F55",
        gate="f55",
        check="f55_chunker_version",
        category="plugin-contract",
        scope="per-plugin",
        summary="every Chunker plugin declares version + every Chunk(...) passes chunker_version=",
        status="vacuous",
    ),
    RuleEntry(
        id="F56",
        gate="f56",
        check="f56_connector_capability_declaration",
        category="plugin-contract",
        scope="per-plugin",
        summary="every connector declares SourceConnector + at least one of {Poll, Checkpointed, Event}Connector",
        script="check-f56-connector-capability-declaration.sh",
    ),
    RuleEntry(
        id="F64",
        gate="f64-external-api-rate-limit",
        check="f64_external_api_rate_limit",
        category="plugin-contract",
        scope="per-plugin",
        summary="every plugin importing an HTTP client ships a rate-limit test (429/Retry-After)",
    ),
    RuleEntry(
        id="F65",
        gate="f65-connector-metadata",
        check="f65_connector_metadata",
        category="plugin-contract",
        scope="per-plugin",
        summary="every connector implements metadata_for + propagation test for chunk_date/author",
        adr_origin="docs/architecture/ADR-021-per-source-metadata-normalisation.md",
    ),
    # ----- production-safety ----------------------------------------------
    RuleEntry(
        id="F15",
        gate="no-logging-secrets",
        check="no_logging_secrets",
        category="production-safety",
        scope="per-file",
        summary="no logging of secret-named variables in plaintext outside kairix/{secrets,credentials}.py",
        tags=("security",),
    ),
    RuleEntry(
        id="F39",
        gate="f39",
        check="f39_chunk_metadata",
        category="production-safety",
        scope="per-method",
        summary="every Chunk(...) constructor call passes source_uri + source_modified_at + sensitivity explicitly",
    ),
    RuleEntry(
        id="F50",
        gate="net-new-baseline-additions",
        check="f50_net_new_file_violations",
        category="production-safety",
        scope="per-commit",
        summary="net-new files may not appear in any per-file F-rule baseline",
        script="check-f50-net-new-file-violations.sh",
    ),
    RuleEntry(
        id="F63",
        gate="f63-unbounded-fetchall",
        check="f63_unbounded_fetchall",
        category="production-safety",
        scope="per-file",
        summary="every .fetchall() includes LIMIT in the query or carries a # F63-bounded: rationale",
        adr_origin="2026-05 maintenance prune disk-IO saturation",
    ),
    RuleEntry(
        id="F66",
        gate="f66-connector-tick-budget",
        check="f66_connector_tick_budget",
        category="production-safety",
        scope="per-class",
        summary="every connector + tick-driven component declares per_tick_max_items + disk_watermark_min_free_bytes",
        adr_origin="docs/architecture/ADR-020-connector-tick-budget-watermark.md",
    ),
    RuleEntry(
        id="F73",
        gate="no-private-infra-refs",
        check="no_private_infra_refs",
        category="production-safety",
        scope="per-file",
        summary="token-pattern scanner for private infra identifiers (externalised pattern source)",
        tags=("security",),
    ),
    # ----- schema-integrity -----------------------------------------------
    RuleEntry(
        id="F57",
        gate="f57",
        check="f57_ccpair_lifecycle_integrity",
        category="schema-integrity",
        scope="per-file",
        summary="every UPDATE topology_cc_pairs SET status=? lives next to a _ALLOWED_TRANSITIONS dispatch dict",
        adr_origin="docs/architecture/connector-scope-topology/ADR.md",
    ),
    RuleEntry(
        id="F58",
        gate="f58",
        check="f58_hierarchy_parent_before_child",
        category="schema-integrity",
        scope="cross-cutting",
        summary="HierarchyConnector impls have a parent-before-child contract test",
        status="vacuous",
    ),
    RuleEntry(
        id="F67",
        gate="f67-staging-drain-symmetry",
        check="f67_staging_drain_symmetry",
        category="schema-integrity",
        scope="per-table",
        summary="every pushed_to_<sink> column has a matching UPDATE site flipping 0 → 1",
        adr_origin="GH #334 — entity_signals 2.3M un-pushed rows",
    ),
    RuleEntry(
        id="F70",
        gate="f70-schema-writer-symmetry",
        check="f70_schema_writer_symmetry",
        category="schema-integrity",
        scope="per-table",
        summary="every CREATE TABLE has at least one INSERT INTO site OR a # table-is-derived: rationale",
        adr_origin="GH #336 — documents_media 1M-chunk empty-table incident",
    ),
    RuleEntry(
        id="F71",
        gate="f71-preflight-truthfulness",
        check="f71_preflight_truthfulness",
        category="schema-integrity",
        scope="per-method",
        summary="every preflight _check_* counting external state has a count-equals-ground-truth contract test",
        adr_origin="docs/architecture/ADR-024-test-pyramid-redesign.md §F71",
    ),
    # ----- feature-flag ---------------------------------------------------
    RuleEntry(
        id="F51",
        gate="f51",
        check="f51_flag_retirement",
        category="feature-flag",
        scope="per-flag",
        summary="every FeatureFlag has target_retire_in ≤ current scm version + 6 months",
        adr_origin="docs/architecture/feature-flag-architecture.md §6",
        script="check-f51-flag-retirement.sh",
    ),
    RuleEntry(
        id="F52",
        gate="f52",
        check="f52_flag_call_sites",
        category="feature-flag",
        scope="per-flag",
        summary='every flag("<name>") call site references a name that exists in REGISTRY',
        script="check-f52-flag-call-sites.sh",
    ),
    RuleEntry(
        id="F53",
        gate="f53",
        check="f53_features_status_surface",
        category="feature-flag",
        scope="cross-cutting",
        summary="kairix features status CLI subcommand + tool_features_status MCP tool both exist",
        script="check-f53-features-status-surface.sh",
    ),
    # ----- agent-affordance -----------------------------------------------
    RuleEntry(
        id="F3",
        gate="suppressions-have-rationale",
        check="sonar_ignore_rationale",
        category="agent-affordance",
        scope="per-file",
        summary="every # noqa / # NOSONAR / # pragma / # type: ignore / # nosec has rationale text",
        script="check-suppressions-have-rationale.sh",
    ),
    RuleEntry(
        id="F10",
        gate="actionable-feedback",
        check="actionable_feedback",
        category="agent-affordance",
        scope="cross-cutting",
        summary="CI workflow silencers (continue-on-error, fail_ci_if_error: false) require rationale",
        script="check-workflow-silencers-have-rationale.sh",
    ),
    RuleEntry(
        id="F14",
        gate="sonar-ignore-rationale",
        check="sonar_ignore_rationale",
        category="agent-affordance",
        scope="cross-cutting",
        summary="every sonar.issue.ignore.multicriteria entry has a preceding rationale comment",
    ),
    RuleEntry(
        id="F16",
        gate="cognitive-complexity",
        check="cognitive_complexity",
        category="agent-affordance",
        scope="per-method",
        summary="cognitive complexity ≤ 15 per function (Sonar S3776)",
    ),
    RuleEntry(
        id="F17",
        gate="no-duplicate-string",
        check="no_duplicate_string",
        category="agent-affordance",
        scope="per-file",
        summary="no string literal ≥10 chars duplicated ≥3 times in a module (Sonar S1192)",
    ),
    RuleEntry(
        id="F18",
        gate="no-commented-out-code",
        check="no_commented_out_code",
        category="agent-affordance",
        scope="per-file",
        summary="no commented-out code (Sonar S125)",
    ),
    RuleEntry(
        id="F19",
        gate="unused-params-named",
        check="unused_params_named",
        category="agent-affordance",
        scope="per-method",
        summary="unused function parameters must be _-prefixed (Sonar S1172)",
    ),
    RuleEntry(
        id="F20",
        gate="empty-body-intent",
        check="empty_body_intent",
        category="agent-affordance",
        scope="per-method",
        summary="empty function bodies require docstring or # Intentionally empty — comment (Sonar S1186)",
    ),
    RuleEntry(
        id="F21",
        gate="actionable-feedback",
        check="actionable_feedback",
        category="agent-affordance",
        scope="cross-cutting",
        summary="every check_*.py failure-output carries fix:/next:/run: action markers",
    ),
    RuleEntry(
        id="F23",
        gate="readme-coverage",
        check="readme_coverage",
        category="agent-affordance",
        scope="cross-cutting",
        summary="every top-level directory has a README.md resolver",
    ),
    RuleEntry(
        id="F83",
        gate="f83",
        check="f83_gate_runner_contract",
        category="agent-affordance",
        scope="per-file",
        summary=(
            "gate-runner contract for shell gate scripts — no unguarded VAR=$(...) under set -e (#483 "
            "silent-death class), || true requires trailing rationale, shellcheck-clean at error "
            "severity, safe-commit.sh/run-all.sh stages emit named OK/FAIL verdicts"
        ),
        adr_origin="EPIC #499 Phase 0 — #483 silent gate-death class",
    ),
    # ----- repo-hygiene ---------------------------------------------------
    RuleEntry(
        id="F4",
        gate="env-reads-in-paths",
        check="no_env_monkeypatch",
        category="repo-hygiene",
        scope="per-file",
        summary='no os.environ.get("KAIRIX_*") outside paths.py / secrets.py',
        script="check-env-reads-stay-in-paths.sh",
    ),
    RuleEntry(
        id="F22",
        gate="path-naming",
        check="path_naming",
        category="repo-hygiene",
        scope="per-file",
        summary="repo paths follow per-tree naming conventions",
    ),
    RuleEntry(
        id="F24",
        gate="no-test-imports-in-prod",
        check="no_test_imports_in_prod",
        category="repo-hygiene",
        scope="per-file",
        summary="no `from tests.*` / `import tests` inside kairix/**/*.py — tests not shipped in wheel",
        adr_origin="GH #266 — v2026.5.15.1 → .2 incident",
    ),
    RuleEntry(
        id="F29",
        gate="f29",
        check="perf_singleton",
        category="repo-hygiene",
        scope="per-file",
        summary="performance-measurement code only under kairix/quality/probe/",
        tags=("singularity",),
    ),
    RuleEntry(
        id="F30",
        gate="f30-operator-outcome-tests",
        check="f30_operator_outcome_tests",
        category="test-discipline",
        scope="cross-cutting",
        summary="every CLI subcommand + every MCP tool has an outcome test (subprocess or direct handler)",
    ),
    RuleEntry(
        id="F32",
        gate="no-real-names-in-fixtures",
        check="no_real_names_in_fixtures",
        category="repo-hygiene",
        scope="per-file",
        summary="no real names in test fixtures (use agent-alpha etc. + reference library)",
    ),
    RuleEntry(
        id="F33",
        gate="shellcheck-disable-with-reason",
        check="shellcheck_disable_with_reason",
        category="repo-hygiene",
        scope="per-file",
        summary="shellcheck disable directives require rationale",
    ),
    # ----- ADR-026 cross-cutting primitives (Phase 3 — pending) -----------
    RuleEntry(
        id="F74",
        gate="f74-stage-runner-only",
        check="f74_stage_runner_only",
        category="observability",
        scope="per-class",
        summary="every Stage subclass is only invoked via a StageRunner — never direct .process() call",
        adr_origin="docs/architecture/ADR-026-cross-cutting-primitive-abstractions.md §A.5",
        status="vacuous",
    ),
    RuleEntry(
        id="F75",
        gate="f75-eval-suite-parity",
        check="(proposed)",
        category="test-discipline",
        scope="cross-cutting",
        summary="every CLI subcommand + MCP tool + connector appears in at least one eval-suite question",
        adr_origin="LoCoMo 95% → 5% regression incident",
        status="proposed",
    ),
    RuleEntry(
        id="F76",
        gate="f76-pii-content-interpolation",
        check="f76_pii_content_interpolation",
        category="production-safety",
        scope="per-file",
        summary=(
            "no f-string interpolation of content-like vars (raw/body/payload/markdown/...) "
            "in log/exception/dead-letter strings"
        ),
        adr_origin="2026-05 leak audit + extends F15 to content layer",
        tags=("security",),
    ),
    RuleEntry(
        id="F77",
        gate="f77-sqlite-single-writer",
        check="f77_sqlite_single_writer",
        category="schema-integrity",
        scope="per-file",
        summary="sqlite3.connect call sites outside the allow-list (worker/factory/scripts/tests) are flagged",
        adr_origin="ADR-026 blindspot audit — concurrency/coordinator discipline",
        status="proxy",
    ),
    # ----- proposed (not yet implemented) ---------------------------------
    RuleEntry(
        id="F78",
        gate="f78-memory-bounds",
        check="(proposed)",
        category="production-safety",
        scope="per-test",
        summary="soak suite asserts RSS / peak memory ≤ budget — needs runtime instrumentation",
        adr_origin="ADR-026 blindspot audit — next-most-likely production blowup profile",
        status="proposed",
    ),
    RuleEntry(
        id="F79",
        gate="f79-migration-reversibility",
        check="(proposed)",
        category="schema-integrity",
        scope="per-commit",
        summary="every schema delta has a tested rollback path; destructive changes keep N-day grace",
        adr_origin="ADR-026 blindspot audit — needs migration framework first (kairix uses create_schema only)",
        status="proposed",
    ),
    RuleEntry(
        id="F80",
        gate="f80-cross-scope-runtime-dataflow",
        check="(proposed)",
        category="layering",
        scope="cross-cutting",
        summary="engagement-scope code may not call firm-scope APIs at runtime — extends F44 from import to request",
        adr_origin="ADR-026 blindspot audit — needs request-level instrumentation",
        status="proposed",
    ),
    # ----- coverage --------------------------------------------------------
    RuleEntry(
        id="baseline-shrinking",
        gate="baseline-shrinking",
        check="baseline_shrinking",
        category="coverage",
        scope="cross-cutting",
        summary="F49: each release tag reduces F30/F46/F47 baselines by ≥1 (or keeps at zero)",
        run_all=False,
    ),
    RuleEntry(
        id="paydown-doc-currency",
        gate="paydown-doc-currency",
        check="paydown_doc_currency",
        category="agent-affordance",
        scope="cross-cutting",
        summary="grandfathering paydown doc reflects current baseline state",
        run_all=False,
    ),
    RuleEntry(
        id="sonar-new-code",
        gate="sonar-new-code",
        check="sonar_new_code",
        category="coverage",
        scope="cross-cutting",
        summary="SonarCloud new-code parity gate — issues introduced after baseline date must be zero",
        run_all=False,
    ),
    RuleEntry(
        id="worktree-isolation",
        gate="worktree-isolation",
        check="worktree_isolation",
        category="process",
        scope="cross-cutting",
        summary="subagent worktree isolation — no shadow copies in primary checkout",
        adr_origin="GH #208 — upstream anthropics/claude-code#59019",
        run_all=False,
    ),
    RuleEntry(
        id="F92",
        gate="f92",
        check="catalogue_currency",
        category="process",
        scope="cross-cutting",
        summary=(
            "catalogue currency — every check_*.{py,sh} has a RuleEntry, every RuleEntry maps to an "
            "existing check, and the generated doc regions match generate_catalogue_docs.py --check "
            "(the self-hosting guard for the catalogue-driven runner)"
        ),
        adr_origin="EPIC #499 Phase 2 — catalogue-driven runner single-source-of-truth",
    ),
    RuleEntry(
        id="capability-affordance",
        gate="capability-affordance",
        check="capability_affordance",
        category="agent-affordance",
        scope="cross-cutting",
        summary="agent-callable capabilities surface their affordances at the call boundary",
    ),
    RuleEntry(
        id="no-hardcoded-user-paths",
        gate="no-hardcoded-user-paths",
        check="no_hardcoded_user_paths",
        category="repo-hygiene",
        scope="per-file",
        summary="F31: no hardcoded /Users/ or /home/<dev>/ paths",
    ),
    # ----- go-discipline (active when services/*/go.mod exists) -----------
    RuleEntry(
        id="G1",
        gate="go-version-flag",
        check="go_version_flag",
        category="go-discipline",
        scope="per-file",
        summary="every Go binary exposes --version",
    ),
    RuleEntry(
        id="G6",
        gate="go-no-panic-outside-main",
        check="go_no_panic_outside_main",
        category="go-discipline",
        scope="per-file",
        summary="no panic outside main/init",
    ),
    RuleEntry(
        id="G8",
        gate="go-logging-discipline",
        check="go_logging_discipline",
        category="go-discipline",
        scope="per-file",
        summary="logging via log/slog (no log.Println or fmt.Println in service code)",
    ),
    RuleEntry(
        id="G9",
        gate="go-readme-coverage",
        check="go_readme_coverage",
        category="go-discipline",
        scope="per-plugin",
        summary="every services/<name>/ has a README.md",
    ),
    RuleEntry(
        id="G10",
        gate="go-dependency-rationale",
        check="go_dependency_rationale",
        category="go-discipline",
        scope="per-plugin",
        summary="dependency-rationale registry per services/<name>/DEPENDENCIES.md",
    ),
)


CATALOGUE: dict[str, RuleEntry] = {entry.gate: entry for entry in _ENTRIES}
"""Indexed by gate name — the stable baseline-filename identifier.

Note: a few catalogue entries share the same ``gate`` deliberately
(e.g. F12 + F13 both surface through ``bdd-no-implementation-leaks``).
The dict keeps the last-wins entry per gate; callers needing all
entries should iterate :data:`ALL_ENTRIES`.
"""

ALL_ENTRIES: tuple[RuleEntry, ...] = _ENTRIES
"""Full ordered tuple of every entry — preserves duplicates by gate."""


def by_category(category: Category) -> tuple[RuleEntry, ...]:
    """Return every entry whose category matches."""
    return tuple(entry for entry in ALL_ENTRIES if entry.category == category)


def by_status(status: Status) -> tuple[RuleEntry, ...]:
    """Return every entry whose status matches."""
    return tuple(entry for entry in ALL_ENTRIES if entry.status == status)


def categories_in_use() -> tuple[Category, ...]:
    """Return every distinct category referenced by an entry, in
    declaration order."""
    seen: set[Category] = set()
    out: list[Category] = []
    for entry in ALL_ENTRIES:
        if entry.category not in seen:
            seen.add(entry.category)
            out.append(entry.category)
    return tuple(out)
