---
title: kairix Resolver
audience: contributors, Claude Code, sub-agents
purpose: map intent → location
last_reviewed: 2026-07-10
---

# kairix — RESOLVER

Canonical "where does X belong" map. Read this when you don't know where to put a new file or where to find an existing one.

## 🛑 Canonical standards — read before touching CI, gates, fitness functions, coverage, mutation, or governance

These already exist and are detailed. **Do NOT re-derive them.** Converge *up* to them; if something
is missing or weak, propose the change *into* the canonical home — never fork a parallel standard.

- **Canonical index:** [`tc-pipelines/governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md)
- **Requirements / OKRs / Waves:** Build & Release Health initiative (Linear) — incl. the `<60s` local loop
- **Fitness-function spec (F-series, tiered execution):** [kairix#499](https://github.com/three-cubes/kairix/issues/499)
- **Canonical homes:** `tc-fitness` (gate engine) · `tc-pipelines` (reusable CI + governance templates)

## Mental model

kairix is the shared knowledge/memory runtime for human-agent teams: a Python core that ingests, chunks, embeds,
and serves knowledge, with Go services for the throughput-sensitive stages. Placement follows lifecycle
ownership — if a file straddles two concerns, it belongs with the one that owns its *incident* (whose 3am page is
this: the Python runtime, a Go service, the docs, or the developer toolchain?).

## Top-level layout

```
/kairix              The importable Python memory-runtime package — protocols (boundaries), factory
                     (production pipeline construction), search/embed/ingest pipelines, connectors, and the
                     CLI + MCP agent surfaces. Architecture detail in docs/architecture/ENGINEERING.md.
/services            Go modules (one services/<name>/go.mod each) for throughput-sensitive stages —
                     see docs/architecture/go-integration-plan.md. Empty tree ⇒ the Go gates no-op.
/docs                Engineering canon: architecture/ (ADRs live here), development/, evaluation/,
                     operations/, runbooks/, reference/, quality/, user-guide/.
/scripts             Repo-developer utilities — scripts/checks/ (the F-numbered fitness functions +
                     _rule_catalogue.py), safe-commit.sh, preflight.sh, smoke.sh, dev/ setup.
/tests               The pytest pyramid: unit / bdd / contract / integration / e2e, with fakes in
                     tests/fakes.py. Construct pipelines through the factory, not by hand.
/docker              Container build context for the runtime image.
/reference-library   Reference material consulted during development (not shipped).
```

`benchmark-results/` and `build/` are generated/ephemeral output roots — never hand-author into them.

## Jobs-to-be-done → location

Use these as decision trees. The first one that answers your job is correct.

### "I need to find or place runtime behaviour (ingest / chunk / embed / search / entity / connector)"
→ It lives in `kairix/` behind a Protocol; compose it through `kairix.core.factory.build_*`. Boundary protocols are in `kairix/core/protocols.py`. A throughput-sensitive stage that has migrated to Go lives under `services/<name>/`.

### "I need to add a fitness function / quality gate"
→ Add the check under `scripts/checks/check_<rule>.py`, register a `RuleEntry` in `scripts/checks/_rule_catalogue.py`, and bind engine CORE checks via `[tool.tc_fitness.core_checks.*]` (mirrored in `scripts/checks/_core_bindings.py`). Never fork a parallel standard — converge into `tc-fitness` (see the Canonical standards banner).

### "I need to write a test"
→ Route by tier: `tests/unit`, `tests/bdd`, `tests/contract`, `tests/integration`, `tests/e2e`. New top-level capabilities require a `tests/bdd/features/*.feature` + an outcome test in the same commit (F45) and an `e2e/test_composed_<capability>_path.py` in the same wave (F48).

### "I need to find or place documentation"
- Architecture / ADR / standard: `docs/architecture/` (ADRs numbered `ADR-NNN-*.md`).
- Runbook: `docs/operations/runbooks/` or `docs/runbooks/` (kebab-case basenames — F22).
- Developer how-to: `docs/development/`.

### "I need to add a configuration value, environment variable, or deployment identifier"
→ Keep a single source of truth; never hardcode the same value in two places, and never hardcode a developer/VM absolute path (ETHOS-6 / fitness F31).

## How this gets enforced

`uv run tc-fitness run` dispatches kairix's catalogue (`scripts/checks/_rule_catalogue.py:ALL_ENTRIES`). F22
(`check_path_naming.py`) fails a file whose basename breaks its tree's naming regex; F31 fails hardcoded
developer paths; F92 fails when the catalogue, its check scripts, or the generated doc regions drift. The
harness-canon reference itself is gated by the shared CORE check `harness_canon_reference`.

## How RESOLVER composes with the standards

- The canonical standards index answers *what's the rule?*; the RESOLVER answers *where does the file live?*
- Naming rules govern *syntax* (snake_case / kebab-case / file conventions — F22); RESOLVER governs *intent → location*.
- When intent → location is genuinely ambiguous, re-apply the *"whose 3am incident is this?"* test.

## When this table is incomplete

If you can't find your intent here, your work likely:
- crosses two concerns (consider whether it should be two PRs);
- introduces a new concern (propose a RESOLVER update in the same PR);
- is a refactor that legitimately moves things (sweep tool + migration plan in one PR).
