# scripts/ci/

Per-job shell helpers invoked by `.github/workflows/*.yml`. Keeping the
heavy logic in shell scripts under version control (not inline YAML
heredocs) means:

- Local repro: `bash scripts/ci/<script>.sh` reproduces the CI step.
- Diffs are reviewable; YAML heredocs are not.
- Pre-commit's `ruff`/`shellcheck` (where wired) actually scan them.

## Inventory

| Script | Workflow | Purpose |
|--------|----------|---------|
| `eval-conversation-corpora.sh` | `reflib-benchmark-gate.yml` (job: `conversation-eval-gate`) | Discover every `reference-library/conversations/engagement-*` corpus, run `kairix eval --json` against each, enforce no >2pp regression against the pinned baseline in `reference-library/conversations/expected/`. Sentinel baselines (`{"baseline": "not-yet-measured"}`) run in record-only mode. |
| `locomo-nightly-run.sh` | `eval-locomo-nightly.yml` | Run `kairix eval suites/locomo --json` and emit both JSON SuiteResult and flat CSV for trend dashboards under `./artifacts/`. |
| `locomo-nightly-compare.sh` | `eval-locomo-nightly.yml` | Download the prior nightly's artifact via `gh run download`, compute pass-rate delta, post a regression comment on the latest `develop` commit if the drop exceeds 2pp. |

## Conventions

- `set -euo pipefail` at the top of every script.
- Actionable failures: every `echo "::error::..."` is followed by lines
  starting with `fix:` and `next:` (mirrors F21 for `scripts/checks/`).
- No CI silencers (no `continue-on-error: true`, no `|| true` to mask
  real failures). The workflow YAML is F10-clean by construction.
