# How-to — Improve a shared fitness gate or pipeline

Change a gate or pipeline in its **one canonical home** and let consumers repin — never fork a parallel copy in a consumer repo. Gates are CORE checks in the `tc-fitness` engine; pipelines are reusable workflows and composite actions in `tc-pipelines`. The canonical engineering-standards index is [tc-pipelines `governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md); the `harness_canon_reference` gate requires the harness to cite it.

**When to use:** You want to add, tighten, or fix a fitness gate, or change a CI/deploy workflow, and the change is useful beyond one repo.

---

## Decide: local kairix rule, or shared CORE check?

- **kairix-domain rule** — encodes a kairix-specific invariant (a package boundary, a repo path convention, a retrieval-layer contract). Keep it **local**: add an F-numbered check under `scripts/checks/` per [fitness-functions.md → "Adding a new fitness function"](../architecture/fitness-functions.md#adding-a-new-fitness-function). Stop here.
- **Generically-useful gate** — any Golden-Path repo would want it. Author it as a **CORE check in `tc-fitness`** and follow the gate steps below.

Prefer local first; promote to CORE once a second repo needs the same rule.

---

## Improve a gate (tc-fitness CORE check)

1. **Add the check** in `tc-fitness`: `src/tc_fitness/core_checks/<name>.py`, following an existing core check as the template.
2. **Test it** — add a contract/unit test that sabotage-proves it: plant a violation and confirm the check fails with an actionable message, then confirm a clean tree passes.
3. **Release an additive immutable tag `vX.Y.Z`.** Additive means existing check signatures stay **byte-identical** and the new surface is **opt-in with safe defaults** — a consumer that repins without binding the new check sees no behaviour change.
4. **Repin the consumer.** Bump the `three-cubes-fitness` pin in `pyproject.toml` to the new tag.
5. **Bind it.** Add a `[tool.tc_fitness.core_checks.<name>]` block (with any config the check reads) plus the catalogue row, so the runner dispatches it.
6. **Replay locally** — `uv run tc-fitness run` — then push. The consumer PR that repins is where the new gate first runs.

Consumers repin on their own schedule; the tag is immutable, so a repin is a deliberate, reviewable step.

---

## Improve a pipeline (tc-pipelines reusable / composite)

1. **Make the change** in the `tc-pipelines` reusable workflow (e.g. `python-quality-gate.yml`) or composite action.
2. **SHA-pin every `uses:`** to a 40-char commit SHA with the tag in a sidecar comment (Sonar `S7637`). Resolve a tag → SHA with `gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq .object.sha`.
3. **Tag it** — release a new immutable tag.
4. **Update consumers** to the new tag, and **force the gated caller to run in the same PR** so the `workflow_call` contract is exercised before merge — see [how-to-consume-a-shared-reusable-workflow.md](how-to-consume-a-shared-reusable-workflow.md). A skipped caller never validates the contract; a broken contract that reaches `main` fails CI at *startup* (see [runbook-ci-startup-failure.md](runbook-ci-startup-failure.md)).

---

## Reference, don't duplicate

Cite the canonical body; do not copy it into a consumer (duplication is the drift this removes):

- [tc-pipelines `governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md) — the canonical engineering-standards index.
- [`three-cubes-fitness` (`tc-fitness`)](https://github.com/three-cubes/tc-fitness) — the gate engine that owns CORE checks.
- [`docs/architecture/fitness-functions.md`](../architecture/fitness-functions.md) — the kairix F-rule canon and the local-vs-CORE decision.
- [`docs/architecture/ENGINEERING.md`](../architecture/ENGINEERING.md) §2 — the CI/CD pipeline and no-bypass merge model.
