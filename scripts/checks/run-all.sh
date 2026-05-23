#!/usr/bin/env bash
# Architecture fitness function harness — run all checks; aggregate exit code.
#
# Each check fails on net-new violations vs its baseline; pre-existing
# violations are grandfathered. The aggregate exit code is non-zero if any
# individual check fails.
#
# Usage:
#   bash scripts/checks/run-all.sh                # run all
#   bash scripts/checks/run-all.sh --skip-coverage  # skip F7 (needs coverage.xml)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

skip_coverage=0
for arg in "$@"; do
    case "$arg" in
        --skip-coverage) skip_coverage=1 ;;
    esac
done

echo "=== Architecture fitness functions ==="
overall=0

# F1
bash "${SCRIPT_DIR}/check-no-internal-patches.sh" || overall=1

# F2
bash "${SCRIPT_DIR}/check-no-env-monkeypatch.sh" || overall=1

# F4
bash "${SCRIPT_DIR}/check-env-reads-stay-in-paths.sh" || overall=1

# F3
bash "${SCRIPT_DIR}/check-suppressions-have-rationale.sh" || overall=1

# F5 — AST-based
python3 "${SCRIPT_DIR}/check_no_internal_imports.py" || overall=1

# F6 — AST-based
python3 "${SCRIPT_DIR}/check_no_test_only_kwargs.py" || overall=1

# F8 — AST-based
python3 "${SCRIPT_DIR}/check_test_markers.py" || overall=1

# F10 — workflow YAML silencer rationale (shell + grep)
bash "${SCRIPT_DIR}/check-workflow-silencers-have-rationale.sh" || overall=1

# F11 — test skip rationale (AST)
python3 "${SCRIPT_DIR}/check_test_skip_rationale.py" || overall=1

# F12 — BDD happy-path coverage
python3 "${SCRIPT_DIR}/check_bdd_happy_path.py" || overall=1

# F13 — BDD no implementation symbols
python3 "${SCRIPT_DIR}/check_bdd_no_implementation_leaks.py" || overall=1

# F14 — sonar.issue.ignore entries require rationale
python3 "${SCRIPT_DIR}/check_sonar_ignore_rationale.py" || overall=1

# F15 — no logging of secret-named variables in plaintext
python3 "${SCRIPT_DIR}/check_no_logging_secrets.py" || overall=1

# F16 — cognitive complexity per function
python3 "${SCRIPT_DIR}/check_cognitive_complexity.py" || overall=1

# F17 — no duplicated string literal ≥10 chars / ≥3 occurrences
python3 "${SCRIPT_DIR}/check_no_duplicate_string.py" || overall=1

# F18 — no commented-out code
python3 "${SCRIPT_DIR}/check_no_commented_out_code.py" || overall=1

# F19 — unused parameter must be _ prefixed
python3 "${SCRIPT_DIR}/check_unused_params_named.py" || overall=1

# F20 — empty function body requires docstring or intent comment
python3 "${SCRIPT_DIR}/check_empty_body_intent.py" || overall=1

# F21 — actionable-feedback marker rule for check scripts
python3 "${SCRIPT_DIR}/check_actionable_feedback.py" || overall=1

# F22 — repo path naming conventions per tree
python3 "${SCRIPT_DIR}/check_path_naming.py" || overall=1

# F23 — every top-level directory has a README.md
python3 "${SCRIPT_DIR}/check_readme_coverage.py" || overall=1

# F24 — no imports of tests.* in kairix production code
python3 "${SCRIPT_DIR}/check_no_test_imports_in_prod.py" || overall=1

# F25 — every CLI subcommand has an MCP affordance (real binding or escalation stub)
python3 "${SCRIPT_DIR}/check_capability_affordance.py" || overall=1

# F26 — kairix/core/** may not import providers/ or transport/
python3 "${SCRIPT_DIR}/check_provider_layer_imports.py" || overall=1

# F27 — providers/<a>/ may not import providers/<b>/
python3 "${SCRIPT_DIR}/check_no_cross_provider.py" || overall=1

# F28 — every provider/<name>/ has matching BDD coverage
python3 "${SCRIPT_DIR}/check_provider_bdd_completeness.py" || overall=1

# F29 — perf-measurement code only under kairix/quality/probe/
python3 "${SCRIPT_DIR}/check_perf_singleton.py" || overall=1

# F30 — every CLI subcommand + MCP tool has an outcome test
python3 "${SCRIPT_DIR}/check_f30_operator_outcome_tests.py" || overall=1

# F31 — no hardcoded /Users/<dev>/ or /home/<dev>/ paths in committed code
python3 "${SCRIPT_DIR}/check_no_hardcoded_user_paths.py" || overall=1

# F32 — no real first names or organisation/client names in fixtures + docs
python3 "${SCRIPT_DIR}/check_no_real_names_in_fixtures.py" || overall=1

# F33 — shellcheck disable directives require rationale
python3 "${SCRIPT_DIR}/check_shellcheck_disable_with_reason.py" || overall=1

# F44 — engagement-scope code (kairix/**) may not import firm-scope storage clients (Postgres)
bash "${SCRIPT_DIR}/check-f44-engagement-firm-boundary.sh" || overall=1

# F45 — every new top-level capability ships with a BDD feature
bash "${SCRIPT_DIR}/check-f45-new-capability-bdd.sh" || overall=1

# F46 — BDD step impls call factory-composed production code (no direct *Pipeline)
bash "${SCRIPT_DIR}/check-f46-bdd-step-composition.sh" || overall=1

# F47 — integration tests construct pipelines via kairix.core.factory.build_*
python3 "${SCRIPT_DIR}/check_f47_integration_factory.py" || overall=1

# F48 — composed production path e2e exemplar exists and is e2e-marked
bash "${SCRIPT_DIR}/check-f48-e2e-present.sh" || overall=1

# F50 — net-new files cannot accrete F-rule baseline debt
bash "${SCRIPT_DIR}/check-f50-net-new-file-violations.sh" || overall=1

# F51 — feature flag target_retire_in deadline
bash "${SCRIPT_DIR}/check-f51-flag-retirement.sh" || overall=1

# F52 — every flag("<name>") call site references a real registry entry
bash "${SCRIPT_DIR}/check-f52-flag-call-sites.sh" || overall=1

# F53 — operator surface (features CLI + tool_features_status MCP) exists
bash "${SCRIPT_DIR}/check-f53-features-status-surface.sh" || overall=1

# F54 — every flag in REGISTRY has both-branch test coverage
bash "${SCRIPT_DIR}/check-f54-flag-both-branch-tested.sh" || overall=1

# F55 — Chunker plugin declares version + every Chunk(...) carries chunker_version=
python3 "${SCRIPT_DIR}/check_f55_chunker_version.py" || overall=1

# F57 — cc_pair lifecycle state-machine integrity (UPDATE topology_cc_pairs.status routed through _ALLOWED_TRANSITIONS)
python3 "${SCRIPT_DIR}/check_f57_ccpair_lifecycle_integrity.py" || overall=1

# F58 — HierarchyConnector requires a parent-before-child contract test under tests/contracts/
python3 "${SCRIPT_DIR}/check_f58_hierarchy_parent_before_child.py" || overall=1

# F61 — _SqliteChunkWriter constructor lives only under kairix/core/connectors/ (CollectionRouter singleton)
python3 "${SCRIPT_DIR}/check_f61_collection_router_singleton.py" || overall=1

bash "${SCRIPT_DIR}/check-f36-connector-bdd-parity.sh" || overall=1

python3 "${SCRIPT_DIR}/check_f34_core_connector_layer_imports.py" || overall=1

python3 "${SCRIPT_DIR}/check_f35_no_cross_connector.py" || overall=1

python3 "${SCRIPT_DIR}/check_f37_singular_sync.py" || overall=1

python3 "${SCRIPT_DIR}/check_f38_silver_singleton.py" || overall=1

python3 "${SCRIPT_DIR}/check_f39_chunk_metadata.py" || overall=1

python3 "${SCRIPT_DIR}/check_f40_extractor_version.py" || overall=1

python3 "${SCRIPT_DIR}/check_f41_plugin_typing.py" || overall=1

python3 "${SCRIPT_DIR}/check_f42_protocol_return_types.py" || overall=1

python3 "${SCRIPT_DIR}/check_f43_plugin_contract_tests.py" || overall=1

# G9 — every services/<name>/ has a README.md (Go side; mirrors F23)
python3 "${SCRIPT_DIR}/check_go_readme_coverage.py" || overall=1

# G1 — every Go binary exposes --version
python3 "${SCRIPT_DIR}/check_go_version_flag.py" || overall=1

# G10 — every direct Go dependency carries a rationale in DEPENDENCIES.md
python3 "${SCRIPT_DIR}/check_go_dependency_rationale.py" || overall=1

# G6 — no panic() in non-main packages
python3 "${SCRIPT_DIR}/check_go_no_panic_outside_main.py" || overall=1

# G8 — logging via log/slog only (no fmt.Print* / log.Print* in prod)
python3 "${SCRIPT_DIR}/check_go_logging_discipline.py" || overall=1

# F7 — needs coverage.xml. Skip if not present or skip flag set.
if [[ "$skip_coverage" -eq 0 ]]; then
    if [[ -f "${REPO_ROOT}/coverage.xml" ]]; then
        python3 "${SCRIPT_DIR}/check_per_file_coverage.py" "${REPO_ROOT}/coverage.xml" || overall=1
    else
        printf '\033[0;33mskip [arch:per-file-coverage-floor]\033[0m — coverage.xml not found.\n'
        printf '   Run: pytest --cov=kairix --cov-report=xml first, then re-run this check.\n'
    fi
fi

echo
if [[ "$overall" -eq 0 ]]; then
    printf '\033[0;32m=== All architecture fitness functions passed ===\033[0m\n'
else
    printf '\033[0;31m=== Architecture fitness functions FAILED ===\033[0m\n'
fi
exit "$overall"
