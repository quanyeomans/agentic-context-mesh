---
title: kairix Scorecard
audience: contributors, CI gate
target: ">=90/100 for production-ready state"
last_reviewed: 2026-07-10
---

# kairix — SCORECARD.md

> The health frame the gate protects: five pillars, each a set of binary, evidence-backed checks. Target
> **>=90/100**. Per-pillar passes are independent — a 0 in one pillar doesn't zero the others.

## 🛑 Canonical standards — read before touching CI, gates, fitness functions, coverage, mutation, or governance

These already exist and are detailed. **Do NOT re-derive them.** Converge *up* to them; if something
is missing or weak, propose the change *into* the canonical home — never fork a parallel standard.

- **Canonical index:** [`tc-pipelines/governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md)
- **Requirements / OKRs / Waves:** Build & Release Health initiative (Linear) — incl. the `<60s` local loop
- **Fitness-function spec (F-series, tiered execution):** [kairix#499](https://github.com/three-cubes/kairix/issues/499)
- **Canonical homes:** `tc-fitness` (gate engine) · `tc-pipelines` (reusable CI + governance templates)

## How kairix is scored

kairix's machine-scored grade is the **F-numbered fitness catalogue** run through the shared engine —
`uv run tc-fitness run` dispatching `scripts/checks/_rule_catalogue.py:ALL_ENTRIES`. The authoritative,
always-current enumeration of every check (id, category, scope, status, summary) is the generated table in
[`docs/architecture/fitness-functions.md`](docs/architecture/fitness-functions.md); F92 fails the build if that
table drifts from the catalogue. The five pillars below are the *frame* over that catalogue — each names the
concern and the representative checks that enforce it. Add a check to the catalogue, not a hand-maintained
count here.

## Pillar 1 — Contract surface (harness + boundaries)
The root harness set is complete (`CLAUDE.md`, `AGENTS.md`, `RESOLVER.md`, `ETHOS.md`, `SCORECARD.md`,
`CONTRIBUTING.md`) and references the canon — enforced by the shared CORE check `harness_canon_reference`.
Domain boundaries are Protocols in `kairix/core/protocols.py`; file placement + naming is enforced by F22
(`check_path_naming.py`). The catalogue is self-hosting: F92 proves checks, entries, and generated docs agree.

## Pillar 2 — Runtime composition (pipelines, factory, connectors)
Behaviour composes through `kairix.core.factory.build_*`, never by direct construction (F46/F47). Agent-facing
result surfaces carry the shared `SourceRef` breadcrumb and an expand-acceptable locator (F97/F98). Write-mode
selection lives at one chokepoint (F95); embed-discovery keys on a state column, not presence (F96). `mypy
--strict` over `kairix/` holds the type contract.

## Pillar 3 — Observability + eval
Every new top-level capability ships a BDD feature + outcome test in the same commit (F45) and a composed
`tests/e2e/test_composed_<capability>_path.py` in the same wave (F48). The pytest pyramid (unit / bdd /
contract / integration / e2e) is the eval surface; the evaluation harness lives under `docs/evaluation/` and
the runtime evaluation tooling in `kairix/`.

## Pillar 4 — Security + provenance
No AI/LLM self-attribution residue in first-party source/docs (SGO-156, `no_llm_attribution`); every commit
author + committer over the PR range carries an allow-listed identity (SGO-158, `canonical_commit_identity`).
No hardcoded developer/VM absolute paths (F31, ETHOS-6). Secrets are baseline-scanned (`detect-secrets`) and
the confidential-content check runs pre-commit. Control-plane config (`pyproject.toml`, `_core_bindings.py`) is
CODEOWNERS-gated so an agent cannot self-exempt.

## Pillar 5 — Operational hygiene
`make check == CI` by construction — both run the same catalogue over the same `[tool.tc_fitness]` block, so
green-local implies green-CI. The `<60s` inner loop (`safe-commit.sh --check` / `make smoke`) keeps feedback
fast; the full gate + `--pre-pr` integration replay is the merge bar. New-code coverage holds an 80% floor
(`new_code_coverage`) alongside the per-file floor (F7).

## Running the scorecard

```bash
uv run tc-fitness run                                  # the full catalogue = the machine grade
uv run python3 scripts/checks/check_per_file_coverage.py coverage.xml   # per-file coverage floor (F7)
```

## Honesty rules

- Don't tweak a check to make a state pass. If a check is wrong, propose a PR change to the check itself — in the canonical home where it lives, never a repo fork.
- A concern with no enforcing check scores 0, not N/A. Close the gap by landing the check in the catalogue.
- Don't grade from a dirty working tree — commit first.
