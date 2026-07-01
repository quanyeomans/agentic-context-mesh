"""CORE-check binding config — the ``[tool.tc_fitness.core_checks.<module>]`` blocks.

This module holds the per-CORE-check config dicts kairix binds, as the single
Python-side source the catalogue rows and the equivalence harness both read. The
same dicts are mirrored verbatim into ``pyproject.toml``'s
``[tool.tc_fitness.core_checks.*]`` tables (the engine reads those at gate time).

Two binding provenances live here. MIGRATED bindings replaced a retired local
reimplementation and were gated on equivalence — same raw ``collect_violations()``
set on kairix's tree AND positive/negative differential fixture parity
(``local.file_has_violation(f) == core.file_has_violation(f)`` for every crafted
fixture, and an identical raw violation set over ``kairix/``). ``no_logging_secrets``
is the canonical KEPT-LOCAL case: the CORE detector flagged a logged ``password`` /
``api_token`` parameter the local kairix detector did not — divergent, so it stays
local. NET-NEW bindings (``pattern_chokepoint``, ``integrity_state_predicate``) have
no local predecessor; they are forward regression guards authored directly in
tc-fitness (so the core set is shared across repos) and gated on zero violations +
zero false-positives over kairix's current tree.
"""

from __future__ import annotations

from typing import Any

# Hoisted to module-level constants so this data table does not itself trip the
# no_duplicate_string CORE check it binds (S1192: explicit coupling).
_PY = [".py"]
_KAIRIX = ["kairix"]
_TESTS = ["tests"]

# ── identity & attribution (Autonomous Delivery Platform SP-A) ──────────────
# The canonical three-cubes-agent GitHub App identity — author AND committer of
# every agent-authored commit — plus the named human maintainer and the platform
# committer identities that legitimately land on origin/main (GitHub web-flow
# merge committer, dependabot). All four are sourced from `git log` on main; none
# is an AI-vendor / off-allowlist identity the check exists to reject.
_AGENT_BOT_EMAIL = "295831460+three-cubes-agent[bot]@users.noreply.github.com"
_HUMAN_MAINTAINER_EMAIL = "10286112+quanyeomans@users.noreply.github.com"
_GITHUB_WEBFLOW_EMAIL = "noreply@github.com"
_DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"
# The origin/main HEAD at adoption; enforcement is bounded to cutover_ref..HEAD so
# pre-cutover history never fails (guard-forward, decision D2).
_IDENTITY_CUTOVER_REF = "f40aad21a773222ef1d407d3ed32d62ad5d52ba4"  # pragma: allowlist secret — cutover SHA

#: Each key is the CORE module name (the part after ``core:``); the value is the
#: config block the engine injects via ``build(config, repo_root=...)``.
CORE_BINDINGS: dict[str, dict[str, Any]] = {
    "no_duplicate_string": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "min_length": 10,
        "min_occurrences": 3,
        "name": "no-duplicate-string",
    },
    "no_commented_out_code": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "min_run": 3,
        "name": "no-commented-out-code",
    },
    "cognitive_complexity": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "threshold": 15,
        "name": "cognitive-complexity",
    },
    "unused_params_named": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "name": "unused-params-named",
    },
    "empty_body_intent": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "name": "empty-body-intent",
    },
    "no_test_imports_in_prod": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "name": "no-test-imports-in-prod",
    },
    "test_skip_rationale": {
        "roots": _TESTS,
        "extensions": _PY,
        "name": "test-skip-rationale",
    },
    # F95 — cypher write-mode chokepoint (#628). Only client.py may name the
    # write-mode selectors; any other module re-deriving read-vs-write is the
    # silent-write class. `exempt_files` is the allow-list (the chokepoint itself).
    "pattern_chokepoint": {
        "roots": _KAIRIX,
        "extensions": _PY,
        "patterns": ["default_access_mode", "_is_write_query"],
        "exempt_files": ["kairix/knowledge/graph/client.py"],
        "name": "cypher-write-mode-chokepoint",
    },
    # F96 — embed-discovery state predicate (#627 chunk-0). A completeness query
    # (LEFT JOIN content_vectors ... IS NULL) must reference a state column, never
    # presence alone. Scoped to kairix/core/embed so the legitimately presence-only
    # bronze-limbo check in kairix/core/db/integrity.py is out of scope.
    "integrity_state_predicate": {
        "roots": ["kairix/core/embed"],
        "extensions": _PY,
        "state_tables": {"content_vectors": ["model", "embedded_at"]},
        "name": "embed-discovery-state-predicate",
    },
    # SGO-156 — no AI/LLM self-attribution residue. Scans kairix's first-party
    # source + docs for attribution signatures (the banned set is intrinsic to the
    # engine; kairix supplies only the scan scope). NET-NEW residue fails; the
    # per-file baseline grandfathers any pre-cutover residue (decision D2).
    "no_llm_attribution": {
        "roots": ["kairix", "scripts", "services", "docs"],
        "extensions": [".py", ".md", ".rst", ".txt", ".sh", ".yaml", ".yml", ".go", ".toml"],
        "name": "no-llm-attribution",
    },
    # SGO-158 — canonical commit identity. Every author AND committer over
    # cutover_ref..HEAD must be on the allow-list. cutover_ref bounds enforcement
    # to commits made from adoption forward (guard-forward, decision D2).
    "canonical_commit_identity": {
        "allowed_emails": [
            _AGENT_BOT_EMAIL,
            _HUMAN_MAINTAINER_EMAIL,
            _GITHUB_WEBFLOW_EMAIL,
            _DEPENDABOT_EMAIL,
        ],
        "cutover_ref": _IDENTITY_CUTOVER_REF,
        "name": "canonical-commit-identity",
    },
}
