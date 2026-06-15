"""Soak: the catalogue-runner parse cache earns its keep at check-count scale.

ADR-024 Soak tier test re-tiered off the per-commit path (#506 cost triage).
This drives a real in-process ``--all`` dispatch — every dispatchable fitness
check imported and run against the live tree — and asserts the shared
``CheckContext`` parse/walk cache is genuinely amortising the AST cost across
checks at production check-count scale (~20s wall-clock).

Why soak, not unit
------------------
The per-commit cache-CORRECTNESS class — a ``(filename, source)`` pair is never
parsed twice, and the cache hits at all — is fully pinned by the <0.1s
exact-count tests in ``tests/checks/test_check_context.py``. Running the WHOLE
live check registry to re-prove that on every commit cost ~20s for no extra
per-commit signal. The unique value of THIS test is at-scale: that across the
real ~80-check registry the cache saves materially more work than it costs.
That is an at-scale-corpus integration concern, so per CLAUDE.md "Soak tier" it
runs nightly in ``soak-suite.yml`` (``pytest -m soak``) and on-demand, excluded
from Stage 2/3 per-commit CI.

This module carries ``pytestmark = pytest.mark.soak`` at module level (the repo
convention — a per-function ``@pytest.mark.soak`` STACKS with a module-level
``unit`` marker rather than replacing it, so the test would still match
``-m "unit or bdd or contract"`` and stay on the per-commit path; a dedicated
soak module is the only way to actually re-tier it).
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

import run_checks  # noqa: E402
from tc_fitness.context import CheckContext  # noqa: E402

pytestmark = pytest.mark.soak


def test_full_run_parses_each_file_at_most_once() -> None:
    """Parse-once invariant at SCALE: across a real in-process ``--all``
    dispatch (every dispatchable fitness check against the live tree), no file
    is parsed twice AND the parse cache SAVES real work — the number of cache
    HITS exceeds the number of MISSES, so the shared ``CheckContext`` is
    genuinely amortising the AST/walk cost across checks rather than re-parsing
    every file per check.

    The per-commit cache-correctness class (parsed-at-most-once + cache hits at
    all) is pinned by the <0.1s exact-count tests in
    ``tests/checks/test_check_context.py``; this soak test's unique value is the
    at-scale ratio assertion below."""
    ctx = CheckContext(repo_root=run_checks.REPO_ROOT)
    seen: set[str] = set()
    with ctx.install():
        for entry in run_checks._select_all():
            script = run_checks.resolve_script(entry)
            if script in seen or not run_checks._dispatches_in_process(entry):
                continue
            seen.add(script)
            try:
                check_main = run_checks._load_check_main(script)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    check_main()
            except BaseException:
                # Mirror the runner's isolation: a crashing check must not
                # fail the cache invariant under test.
                pass

    # Every real parse corresponds to a distinct (filename, source) — no file is
    # parsed twice (the parse-once invariant).
    distinct_keys = sum(len(by_text) for by_text in ctx._tree_cache.values())
    assert ctx.parse_misses == distinct_keys, "a (filename, source) was parsed more than once"
    # The cache must SAVE real work at scale: re-inspection (hits) dominates the
    # one-time parse cost (misses). A ratio floor — not the tautological
    # ``parse_hits > 0`` existence check — so a regression that quietly defeats
    # cross-check sharing (e.g. a per-check fresh context) is caught by the soak.
    assert ctx.parse_hits > ctx.parse_misses, (
        f"parse cache is not paying off at scale: {ctx.parse_hits} hits vs {ctx.parse_misses} misses "
        "(hits must exceed misses — the cache should save more re-parses than it pays in first parses)"
    )
    # Walk cache likewise earns its keep.
    assert ctx.walk_hits > 0, "the walk cache never hit — it is doing nothing"
