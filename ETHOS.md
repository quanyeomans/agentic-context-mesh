---
title: kairix Ethos
status: canon
last_reviewed: 2026-07-10
---

# kairix Ethos

This file is the load-bearing principle set for kairix. It is read by humans and agents before changes to
platform structure, and — where a repo injects it — into an agent's runtime identity at generation time. Each
principle below is specific enough to overrule a judgment call in PR review: when a change conflicts with an
ethos rule, the rule wins unless the PR explicitly proposes an amendment to this file with a documented failure
case. The seven principles were distilled from convergent findings across six agent-platform repositories studied
plus accumulated operational memory.

## 🛑 Canonical standards — read before touching CI, gates, fitness functions, coverage, mutation, or governance

These already exist and are detailed. **Do NOT re-derive them.** Converge *up* to them; if something
is missing or weak, propose the change *into* the canonical home — never fork a parallel standard.

- **Canonical index:** [`tc-pipelines/governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md)
- **Requirements / OKRs / Waves:** Build & Release Health initiative (Linear) — incl. the `<60s` local loop
- **Fitness-function spec (F-series, tiered execution):** [kairix#499](https://github.com/three-cubes/kairix/issues/499)
- **Canonical homes:** `tc-fitness` (gate engine) · `tc-pipelines` (reusable CI + governance templates)

## 1. Boil the Lake

**Rule:** Before iterating on agent behaviour, prompts, configs, or skills, investigate the runtime composition first — then design once.

**Why:** Iterating prompt content blindly produces regressions because most behaviour is composed at runtime from many sources (identity, injected principles, skills, harness defaults, MCP tool descriptions). Editing one slice without mapping the full composition optimises for the wrong layer.

**How to apply:**
- Before changing an agent prompt, dump what actually reaches the model at runtime (identity + injected ethos + active skills + tool list) and confirm the *real* failure layer.
- A PR that edits a prompt without showing the runtime trace it was tested against is not ready for review.
- "I'll just tweak the wording" is a violation; "I traced the composition, the issue is in layer X, here is the minimal change" is the pattern.
- Re-research is cheap; re-iterating on the wrong layer is expensive.

## 2. The Harness Is a Contract, Not a Framework

**Rule:** Treat the harness as a contract boundary — approvals stop where tool calls begin — with thin runtime and fat skills.

**Why:** Most agent capability comes from the harness, not the weights: tool routing, approval policy, sandbox scope, model selection, and skill discovery are harness concerns, and they form the *contract* the agent operates inside. Runtime stays thin so it can be reasoned about, while skills carry the domain weight. A "framework" mindset adds runtime branching; a "contract" mindset keeps the runtime declarative and pushes capability into skills.

**How to apply:**
- New behavior belongs in a skill (or its references/scripts) — not in harness-layer conditionals.
- If a change would add a runtime branch keyed off agent name, model, or task type, stop: that branch belongs as a skill the harness routes to.
- Approval policy and tool-permission scope are *expressed* at the harness boundary, never inside skill content.
- The runtime should be readable end-to-end in one sitting; skills can be deep.

## 3. Authoring Context Never Reaches Runtime

**Rule:** Files governing how *contributors* edit the repo live at root; files governing how *agents* behave at runtime live inside the skill. The two surfaces never mix.

**Why:** `AGENTS.md`, `CONTRIBUTING.md`, and this `ETHOS.md` (in its authoring form) are read by humans and agents *editing* the repo. Skill definitions, skill scripts, and references are read by agents *executing* against users. Mixing them produces two failure modes: authoring rules leak into runtime as noise, and runtime rules get hidden in contributor docs the executing agent never loads.

**How to apply:**
- A behavioural rule that must reach the agent at runtime lives inside the skill — full stop.
- Root-level `AGENTS.md` / `CONTRIBUTING.md` may *describe* runtime behavior for human readers but cannot be the *source* of it.
- `ETHOS.md` is exempt only where a repo explicitly injects it into agent identity at generation time; that injection makes the authoring/runtime bridge auditable.
- If you find yourself writing "agents should..." in `CONTRIBUTING.md`, move it into a skill.

## 4. Semantic Categories, Not Name-Based Allowlists

**Rule:** Express permissions, approvals, model routing, and trust policy as classes of things or named profiles — never as ad-hoc lists of tool names.

**Why:** Name-based allowlists rot the moment a tool is renamed, split, or added; semantic categories survive refactors. When policy describes *intent* (categories like `read-only.*`, `execution.*`, `control-plane.*`; named security profiles like `readonly`, `repo-edit`, `background-safe`) a registry maps tools into categories. Reviewers can reason about a category; nobody can reason about a 200-line tool-name list.

**How to apply:**
- A new tool gets a category assignment in the registry, not an entry in each consumer's allowlist.
- If a config needs an exception for a single tool name, that is a smell — usually the category is wrong, or a new category is needed.
- Approval prompts cite the category that triggered them, not the tool name alone.
- Profiles compose categories; categories compose tools. Never skip a layer.

## 5. No Hand-Edits to Generated Artefacts (Without a `// PATCH(...)` Index)

**Rule:** When source-of-truth and runtime artefact diverge, record the divergence — do not hide it.

**Why:** Generated artefacts that get silently hand-edited become drift bombs: the next regeneration either overwrites the fix or papers over a bug the upstream fix would have resolved. A patch manifest names every intentional hand-edit, and `// PATCH(upstream issue#)` comments index them in the artefact source. Re-generation never silently overwrites a patched line; it either preserves the patch (with a checksum match) or fails loud.

**How to apply:**
- A hand-edit to a generated file requires both a `// PATCH(<issue>)` marker and an entry in the patch manifest for that artefact.
- A regenerator that finds an unrecorded divergence must fail, not overwrite.
- "I'll fix the generator later" without recording the patch is a violation.
- Patches are temporary by definition; each one should link to the upstream issue that will retire it.

## 6. No Assumed Absolute Paths

**Rule:** Tools, scripts, and configs discover artefacts via the manifest — never via hardcoded filesystem paths.

**Why:** Hardcoded paths bind code to one developer's machine, one VM layout, or one container mount. A manifest names artefacts by role; consumers resolve roles to paths at runtime. At most one legitimate absolute path may exist in a platform — its VM-target root — and even that should appear only in scripts that run *on* the VM.

**How to apply:**
- A script that hardcodes a developer-specific home path is broken, even if it works on the author's machine (kairix enforces this as fitness rule F31).
- A single sanctioned VM-target path is allowed only inside scripts that are explicitly VM-targeted (and that fact should be obvious from filename or shebang).
- New artefact types get a manifest entry first; consumers read the manifest.
- Tests run from any cwd and any user.

## 7. Every Artefact Carries Its Own Provenance

**Rule:** A snapshot without provenance is drift — every generated artefact carries its own `.<name>.json` manifest with source checksum, generator version, run id, and timestamp.

**Why:** Without provenance, a regeneration cannot tell "this is unchanged" from "this matches by coincidence", and a code review cannot tell "this hand-edit was deliberate" from "this is stale output". Each artefact has a sibling provenance record; patches (Principle 5) are tracked separately so the provenance record reflects only the generator's intent. A re-generation that doesn't match expected provenance fails loud rather than overwriting.

**How to apply:**
- A new generator ships with its provenance schema before its first artefact is committed.
- CI verifies provenance on every PR that touches a generated path.
- A "regenerate" command compares provenance, applies recorded patches, and refuses to proceed on mismatch without an explicit `--force` flag (which itself logs the override).
- An artefact missing its provenance file is treated as untrusted on next regeneration.

## Applying This Ethos

When an ethos principle conflicts with a deadline, the principle wins — the deadline is rescheduled or the scope is cut. When a principle proves wrong in practice, the response is not to ignore it: propose an edit to this file via PR with the failure case documented in the PR body, and update the `last_reviewed` date. Reviewers cite ethos numbers in PR comments (`violates ETHOS-4`); authors cite them in PR descriptions when the principle drove a design choice. The seven principles above are deliberately few; if an eighth is needed, the PR proposing it must show why none of the existing seven covered the case.
