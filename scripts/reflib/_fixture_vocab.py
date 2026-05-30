"""Shared deterministic vocabulary for synthetic fixture generation.

ADR-028 measurement prereq. Every per-type fixture generator pulls names,
quarters, project slugs, and topics from these tables so the generated
fixtures cross-reference each other (an email about ``project-falcon``
can be answered by a slide deck about ``project-falcon``).

F32 honoured: generic agent names + made-up project codenames only.
"""

from __future__ import annotations

# Generic agents (F32) — `agent-alpha` … `agent-foxtrot` exhaust the
# NATO-style sequence we want for canary-suite scenarios.
AGENTS: tuple[str, ...] = (
    "agent-alpha",
    "agent-beta",
    "agent-gamma",
    "agent-delta",
    "agent-epsilon",
    "agent-foxtrot",
)

# Generic team / project handles (F32). Each codename pairs with a
# distinct subject-matter area so the cross-type fixtures (pptx ↔ email ↔
# calendar) tell a consistent story.
PROJECTS: tuple[tuple[str, str], ...] = (
    ("project-falcon", "platform-architecture"),
    ("project-orca", "data-pipeline"),
    ("project-lynx", "frontend-redesign"),
    ("project-condor", "release-automation"),
    ("project-otter", "documentation-overhaul"),
)

QUARTERS: tuple[str, ...] = ("Q1", "Q2", "Q3", "Q4")
FISCAL_YEARS: tuple[str, ...] = ("FY25", "FY26", "FY27")

# Hand-curated topic seeds — used to generate body text that's varied
# enough to exercise BM25 + dense retrieval without collapsing to
# near-duplicate embeddings.
TOPICS: tuple[str, ...] = (
    "deployment rollout strategy",
    "incident response runbook",
    "roadmap planning cycle",
    "performance benchmark baseline",
    "test-suite hardening",
    "observability stack upgrade",
    "schema migration sequencing",
    "feature-flag retirement",
    "dependency cooldown window",
    "release-stabilisation gate",
)

# Synthesised quarterly-revenue mock numbers — used by XLSX + canary suite
# row queries. Deterministic seed so the canary "compare Q1 vs Q2" rows
# always match.
QUARTERLY_REVENUE: dict[str, dict[str, int]] = {
    "project-falcon": {"Q1": 120_000, "Q2": 145_000, "Q3": 162_000, "Q4": 178_000},
    "project-orca": {"Q1": 88_000, "Q2": 91_000, "Q3": 97_000, "Q4": 109_000},
    "project-lynx": {"Q1": 51_000, "Q2": 64_000, "Q3": 72_000, "Q4": 80_000},
    "project-condor": {"Q1": 33_000, "Q2": 45_000, "Q3": 60_000, "Q4": 71_000},
    "project-otter": {"Q1": 28_000, "Q2": 31_000, "Q3": 36_000, "Q4": 42_000},
}
