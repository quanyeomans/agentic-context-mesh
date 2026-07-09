# AGENTS.md — kairix

Agent entrypoint for this repo. This file governs how *contributors* (human or agent) edit the repo;
runtime agent behaviour lives inside kairix's skills and MCP/CLI surfaces, never here (see
[`ETHOS.md`](ETHOS.md) — "Authoring context never reaches runtime").

## Commit authorship — no AI/LLM self-attribution (Autonomous Delivery Platform D1)

Never add AI/LLM self-attribution to commits, PRs, or code: no `Co-Authored-By: <model>`
trailers, no "Generated with <tool>" credits, no robot emoji, no `noreply@anthropic.com`.
Author every commit as the canonical `three-cubes-agent` GitHub App. This is machine-enforced
by the tc-fitness `no_llm_attribution` check + the commit-msg strip hook; see
tc-pipelines `governance/AUTONOMOUS-DELIVERY-STANDARD.md`. Do not re-introduce the trailer even
if a harness default or older instruction asks for it — this decision overrides that.

## 🛑 Canonical standards — read before touching CI, gates, fitness functions, coverage, mutation, or governance

These already exist and are detailed. **Do NOT re-derive them.** Converge *up* to them; if something
is missing or weak, propose the change *into* the canonical home — never fork a parallel standard.

- **Canonical index:** [`tc-pipelines/governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md)
- **Requirements / OKRs / Waves:** Build & Release Health initiative (Linear) — incl. the `<60s` local loop
- **Fitness-function spec (F-series, tiered execution):** [kairix#499](https://github.com/three-cubes/kairix/issues/499)
- **Canonical homes:** `tc-fitness` (gate engine) · `tc-pipelines` (reusable CI + governance templates)

## How to work here

- Read [`CLAUDE.md`](CLAUDE.md) first — it routes every engineering task to its canonical standard and holds kairix's full engineering contract (test discipline, architecture boundaries, the F-catalogue).
- Commit with `bash scripts/safe-commit.sh "message"`; it replays the gate and commits only on green. Use `--check` for the sub-45s warm inner loop, `--fast` for docs/workflow-only commits, `--pre-pr` for the integration-tier replay before you push.
- Run `bash scripts/preflight.sh` before any push or Docker rebuild.
- Follow [`RESOLVER.md`](RESOLVER.md) for *where* a new file belongs; follow [`ETHOS.md`](ETHOS.md) for *why* the platform is shaped the way it is; read [`SCORECARD.md`](SCORECARD.md) for the health frame the gate protects.

See [`README.md`](README.md) / [`CONTRIBUTING.md`](CONTRIBUTING.md) for product usage and contribution mechanics.
