# Runbook — CI `startup_failure` before any step runs

**Severity:** P1 for `main` — every PR's required check fails at startup; nothing can merge until it's fixed.

**Symptom:** A CI run shows `startup_failure` (or "Invalid workflow file" / "workflow file issue") and **no steps execute** — the run goes red before the first job logs anything. The GitHub Actions UI shows the workflow as failed with an annotation pointing at a workflow file, not a step.

---

## What's happening

`startup_failure` means GitHub could not even start the run from the workflow definition. The work never began, so there is no step log to read — the fault is in the workflow file or its `workflow_call` wiring. Causes, in priority order:

1. **Broken `workflow_call` contract on a reusable-workflow caller** — the caller passes a `with:` input the reusable workflow renamed or removed, or a `secrets:` entry that no longer exists. Common after a workflow-only PR merged without exercising the caller (see [how-to-consume-a-shared-reusable-workflow](how-to-consume-a-shared-reusable-workflow.md) for how that PR slipped through change-detection gating).
2. **Bad `@<ref>` pin** — the caller `uses: <org>/<repo>/.github/workflows/<file>.yml@<ref>` and that tag/branch was deleted or moved on the shared repo.
3. **Invalid YAML or a bad `${{ }}` expression** — a syntax error, a malformed matrix, or an expression that doesn't parse.

---

## Quick diagnosis

```bash
# 1. Find the failing run and confirm the conclusion is startup_failure
gh run list --workflow ci.yml --limit 5
gh run view <run-id> --json conclusion,jobs --jq '.conclusion'
# Expect: "startup_failure"

# 2. Read the run-level annotation (the step logs are empty by definition)
gh run view <run-id> --log 2>&1 | head -40
# Look for "Invalid workflow file", an input/secret name, or a ref that 404s.

# 3. Validate the workflow files locally
actionlint .github/workflows/*.yml
# Catches YAML + expression errors (cause 3) without pushing.
```

## Fix 1 — Broken `workflow_call` contract (most common)

A caller passes inputs/secrets the reusable workflow no longer accepts (or stopped passing a required one). Reconcile the two:

```bash
# Show what the reusable workflow declares it accepts
gh api repos/<org>/<repo>/contents/.github/workflows/<file>.yml --jq '.content' \
  | base64 -d | sed -n '/on:/,/jobs:/p'
# Compare its `workflow_call: inputs:` and `secrets:` to the caller's `with:` / `secrets:` blocks.
```

- If the contract genuinely changed and the caller can't satisfy it, **revert the caller to an inline job** (or pin back to the last working `@<ref>`) to unblock `main`, then fix the input/secret wiring in a follow-up PR that exercises the caller.
- If the caller is correct but was never run on the PR that introduced the break, the fix PR MUST force the caller to run — see [how-to-consume-a-shared-reusable-workflow](how-to-consume-a-shared-reusable-workflow.md).

## Fix 2 — Bad `@<ref>` pin

```bash
# Confirm the pinned ref still exists on the shared repo
gh api repos/<org>/<repo>/git/ref/tags/<ref> >/dev/null 2>&1 \
  && echo "ref OK" || echo "ref MISSING — re-pin to a live tag/sha"
```

Re-pin `uses: <org>/<repo>/.github/workflows/<file>.yml@<live-ref>` to a tag or SHA that exists, then push.

## Fix 3 — Invalid YAML / expression

`actionlint` (from Quick diagnosis) reports the line and column. Fix the syntax, re-run `actionlint` until clean, then push.

---

## See also

- [how-to-consume-a-shared-reusable-workflow](how-to-consume-a-shared-reusable-workflow.md) — the upstream prevention: force a gated caller to run in the same PR so a broken `workflow_call` contract can't reach `main`.
- The shared fitness/CI engine is consumed as a **pinned package dependency** (see `pyproject.toml`) configured through declarative factories, not local code patches — a version-pin mismatch between the dependency pin and any prose/docs is itself a cause of confusing CI behaviour. If the engine version moved, confirm the pin in `pyproject.toml` and the prose in [CLAUDE.md](../../../CLAUDE.md) agree before debugging further.
