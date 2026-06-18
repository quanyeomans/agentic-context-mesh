# How-to — Consume a shared reusable workflow safely

**When to use:** Any time you add, edit, or re-pin a CI job that calls a shared reusable workflow with `uses: <org>/<repo>/.github/workflows/<file>.yml@<ref>` — especially when that job is gated behind a change-detection / path filter.

**Time:** ~5 minutes of extra diligence per PR; saves a `startup_failure` incident on `main`.

---

## The change-detection-gating trap

When a CI job calls a shared reusable workflow with `uses: <org>/<repo>/.github/workflows/<file>.yml@<ref>` and that job is gated behind a path/change-detection filter, the caller does **not** run on a PR that touches only files outside the filter. A workflow-only PR (editing `.github/workflows/*.yml`, the `@<ref>` pin, or nothing the filter watches) can therefore merge **without ever exercising the `workflow_call` contract**. If the contract is broken — a renamed required input, a removed secret, a changed `on:` shape — the breakage is invisible until the next PR that *does* trigger the caller, where it surfaces as a `startup_failure` (CI fails before any step runs). See [CI startup_failure diagnosis](runbook-ci-startup-failure.md) for the symptom side.

### Fix: force the caller to run in the same PR

Before merging any PR that changes a reusable-workflow caller or its `@<ref>` pin, include a change that satisfies the gating filter so the caller actually executes on that PR:

```bash
# Make a no-op change that the path filter watches (e.g. a code path),
# in the SAME PR as the workflow edit, so the gated caller runs.
# Verify on the PR's checks that the reusable-workflow job executed
# (not "skipped") and went green BEFORE merge.
gh pr checks <pr-number>
# The caller job must appear with a non-skipped conclusion.
```

- next: confirm the reusable-workflow job shows a green run (not `Skipped`) on the PR's check list.
- run: `gh pr checks <pr-number>` — the caller job must appear with a non-skipped conclusion.

Never merge a workflow-only PR on the assumption that "it's just YAML" — the `workflow_call` contract is only validated by an actual invocation.

---

## Keep callers secret-free

A shared reusable workflow must never embed literal secret values. The **caller** passes secrets in (via `secrets:` or `secrets: inherit`); the reusable workflow only references them by name. This keeps the shared workflow safe to read by anyone and means a secret rotation is a caller-side change, not an edit to shared machinery.

```yaml
# caller — passes the secret by reference, never a literal
jobs:
  quality:
    uses: <org>/<repo>/.github/workflows/quality.yml@<ref>
    secrets:
      TOKEN: ${{ secrets.SOME_TOKEN }}   # value lives in repo/org secrets, not here
```

Do not name private sibling repositories, internal hostnames, or infra identifiers anywhere in committed workflow files or these docs — the token-pattern scanner (F73) blocks net-new private identifiers. Reference shared workflows generically (`<org>/<repo>`) in documentation.

---

## See also

- [runbook-ci-startup-failure](runbook-ci-startup-failure.md) — diagnose a CI run that fails with `startup_failure` before any step executes (broken `workflow_call` contract, bad `@<ref>` pin, invalid YAML).
- [CI / Workflow Secret Hygiene](../../../SECURITY.md) — the caller-passes-secrets and no-private-infra rules in the security policy.
- [Reusable-workflow callers: force a triggering change](../../architecture/local-first-feedback-loops.md) — the same discipline framed against the local-first feedback model.
