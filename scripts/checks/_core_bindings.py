"""CORE-check binding config — the ``[tool.tc_fitness.core_checks.<module>]`` blocks.

This module holds the per-CORE-check config dicts kairix binds, as the single
Python-side source the catalogue rows and the equivalence harness both read. The
same dicts are mirrored verbatim into ``pyproject.toml``'s
``[tool.tc_fitness.core_checks.*]`` tables (the engine reads those at gate time).

Only checks PROVEN equivalent to their retired local reimplementation — same raw
``collect_violations()`` set on kairix's tree AND positive/negative differential
fixture parity — are bound here; every other local check stays local. The proof
that gated each row: ``local.file_has_violation(f) == core.file_has_violation(f)``
for every crafted fixture (positives and negatives) and an identical raw violation
set over ``kairix/`` (and ``tests/`` for the test-skip rule). ``no_logging_secrets``
is the canonical KEPT-LOCAL case: the CORE detector flagged a logged ``password`` /
``api_token`` parameter the local kairix detector did not — divergent, so it stays
local.
"""

from __future__ import annotations

from typing import Any

# Hoisted to module-level constants so this data table does not itself trip the
# no_duplicate_string CORE check it binds (S1192: explicit coupling).
_PY = [".py"]
_KAIRIX = ["kairix"]
_TESTS = ["tests"]

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
}
