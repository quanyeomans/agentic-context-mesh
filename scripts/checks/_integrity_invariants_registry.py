"""F72 — registry of cross-layer integrity invariants (ADR-024 Bundle E).

Each entry maps a short snake_case ``invariant_name`` to a one-line
operator-language description. The F72 detection script
(``check_f72_integrity_invariants.py``) walks this registry and asserts
each invariant has a matching test file under ``tests/integrity_invariants/``
with both a fixture-scale and a soak-scale test function.

Adding a new invariant: add an entry below, then ship
``tests/integrity_invariants/test_<name>.py`` carrying
``test_invariant_holds_at_fixture_scale`` AND
``test_invariant_holds_at_soak_scale``. The fixture-scale variant runs
in CI Stage 3 (integration) — fast, N=10-100 rows. The soak-scale
variant carries ``@pytest.mark.soak`` and runs in the nightly soak
workflow at N=10**4 rows.

Why a registry vs auto-discovery: the registry forces an explicit
operator-language description of every invariant, which becomes the
text of any preflight runbook + post-mortem reference. Auto-discovery
would let an invariant ship without a human-readable description, which
defeats the "name the class, propose the rule" discipline from
ADR-024.

The five seed invariants below correspond to the four defect classes
ADR-024 §"Defects" identified: bronze-vs-content limbo (#1), vector
index drift (#2), staging-drain progress (#3, paired with F67), per-
document analytics completeness (#4, paired with F70), and cc_pair
lifecycle consistency (#5, extending F57 from single-tick to multi-
tick).
"""

from __future__ import annotations

# Mapping: invariant_name -> one-line operator-language description.
# Names are snake_case; the description should read like operator
# runbook text — no internal symbol names, no F-rule jargon.
INVARIANTS: dict[str, str] = {
    "bronze_coverage_parity": ("every bronze row maps to either content+1+ rows or a dead-letter entry"),
    "content_vectors_alignment": ("every content_vectors row traces to a content row (no orphan vector slots)"),
    "staging_drain_progress": (
        "pushed_to_<sink>=0 counts only grow when the sink is unreachable; preflight reports the true backlog size"
    ),
    "documents_media_extractor_completeness": (
        "every successfully-extracted content row has a documents_media row with a non-null extractor_name"
    ),
    "cc_pair_lifecycle_consistency": (
        "every topology_cc_pairs.status transition observed across multiple "
        "ticks is in the _ALLOWED_TRANSITIONS dispatch table"
    ),
}
