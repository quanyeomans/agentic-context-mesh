# Release checklist (main)

Tag-creation, GitHub-Release authoring, and CHANGELOG-section extraction
are automated by the **`5 · Release`** workflow (`.github/workflows/release.yml`).
This checklist covers the parts a human still drives — cutting the release,
the post-release CHANGELOG bump, and the deploy/UAT loop. Shared release canon:
[tc-pipelines `governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md).

## Pre-merge

- [ ] Release PR is green on both required checks — **`CI gate`** (the `check` fan-in in `1 · Quality gate`) and **`PR compliance check`** (`2 · Pre-merge PR gates`). SonarCloud analysis is advisory, not a required context.
- [ ] CHANGELOG.md `[Unreleased]` section is fully populated (no empty sub-sections).
- [ ] Version label matches calendar version: `vYYYY.M.D[aN]` where `aN` is the same-day alpha suffix.

## Merge

The release PR **auto-merges on green** like any other PR — the `three-cubes-agent`
App arms `gh pr merge --auto` and GitHub merges the moment both required checks
pass. Merge method is a merge commit; there is no manual squash step.

## Tag + release (automated)

```bash
# Trigger the release workflow with the version label.
# Inputs:
#   version: vYYYY.M.D[aN]
#   changelog_label: Unreleased   (default — the [Unreleased] section)
gh workflow run "5 · Release (tag + GitHub release from CHANGELOG)" \
    -f version=v2026.5.9 \
    -f changelog_label=Unreleased

# Watch the run.
gh run watch
```

The workflow:
1. Validates the version matches CalVer (`vYYYY.M.D[aN]`) and isn't already tagged.
2. Extracts the `[Unreleased]` (or supplied) CHANGELOG section as release notes.
3. Tags `main` HEAD and pushes the tag.
4. Creates the GitHub Release with the extracted notes — which fires the
   downstream `3 · Docker publish (release)` and `4 · PyPI publish (release)` workflows.

## Post-release

- [ ] Confirm `3 · Docker publish (release)` workflow ran and pushed the image to ghcr.io.
- [ ] Confirm `4 · PyPI publish (release)` workflow ran and pushed the wheel/sdist to PyPI.
- [ ] Bump CHANGELOG: rename `[Unreleased]` → `[X.Y.Z] - YYYY-MM-DD`, and open a follow-up PR with a new empty `[Unreleased]` block (it auto-merges on green).

## Deployment

An **alpha prerelease** deploys to the VM automatically: `release-vm-deploy.yml`
calls the tc-pipelines `azure-vm-deploy.yml@v1` reusable (see
[ADR-017](../docs/architecture/ADR-017-deployment-architecture.md)) — snapshot
skipped, rollback by re-pinning `KAIRIX_IMAGE_TAG`, and the post-apply probe is
`apply-alpha.sh`'s in-band `kairix onboard check --json` plus the reference-library
gate. The manual fallback, when CI is unavailable, is to pull the tagged image and
restart the container on the box:

```bash
# Substitute <your-deploy-host> with the SSH alias / hostname that targets your
# kairix VM (e.g. an entry in ~/.ssh/config, or a DNS name).

# Pull the image on the VM
ssh <your-deploy-host> 'docker pull ghcr.io/three-cubes/kairix:vYYYY.M.D'

# Restart the kairix container (preserves /data/kairix mounts)
ssh <your-deploy-host> 'cd /opt/kairix && docker compose pull && docker compose up -d'

# Verify health
ssh <your-deploy-host> 'curl -fsS http://127.0.0.1:8182/healthz'
```

## UAT

After deploy, run UAT smoke from a host that has CLI + MCP reach:

```bash
bash scripts/uat-smoke.sh --mcp-url http://<vm-host>:8182
```

The script exits 0 only if every check in the list passes.

For the dogfood agents (multi-agent UAT), distribute the script and ask each agent to run it against the deployed instance and report a PASS/FAIL summary back. Agents should report the failure summary rather than fix their environment, so we have a per-agent UAT signal.
