# Kairix operational resilience sprint

## Goal

Maximise agent affordance and operational resilience for Kairix-backed agents by reducing circular retrieval loops, making source handoff explicit, and hardening the live document/index maintenance path against permission drift, vector-store drift, and unsafe deploy recovery.

## Accepted decisions

1. SQLite remains the operational source of truth for document and vector metadata. USEARCH is a derived serving index and must be caught up via a deliberate rebuild/catch-up path, not by blindly enabling worker live writes under memory pressure.
2. VM deploy snapshots should be mandatory for production deployments. The current alpha workflow still skips snapshots because the GitHub/Azure identity lacks Disk Snapshot Contributor; resolving that IAM gap is required before the workflow can safely fail closed.
3. Local document permission failures are expected in a multi-agent estate. Kairix must diagnose and skip unreadable files without breaking scan/embed/summary, and operators need a repeatable ownership/mode repair path.
4. Orphan `content_vectors` rows may be pruned only after a database backup or VM snapshot has been taken.
5. Backlogs should be reduced aggressively once permissions/exclusions are fixed: FTS gaps first, then embed/summary gaps, then USEARCH catch-up.
6. Agent-facing outputs must make the next action obvious: source link for citation, source reference for provenance, and concrete expand arguments for deeper context.

## Architecture

- Search affordance: the shared CLI/MCP search envelope keeps the existing `source_uri`, `locator`, and `seq` fields and adds `source_link`, `source_ref`, and `actions.expand`.
- Permission resilience: scanner and summariser treat unreadable local files as recoverable diagnostics, not fatal batch failures. The runtime records counts, paths and F21-style remediation hints so operators can repair ownership/modes or exclude/quarantine noisy trees.
- Orphan cleanup: use existing maintenance/preflight pruning only after an operator-visible backup step. The next implementation slice should add an explicit backup acknowledgement or backup command wrapper around pruning.
- USEARCH catch-up: do not set `KAIRIX_WORKER_WRITES_VEC_INDEX=1` as the default recovery. The next implementation slice should provide an offline rebuild command that takes a DB backup/lock, rebuilds or re-embeds into a temporary USEARCH index, verifies parity, then atomically swaps the index.
- Snapshot policy: update Azure IAM for the deploy identity, then change the alpha VM workflow to fail closed when a production deploy attempts `skip-snapshot: true`.

## Implementation slices

### Slice 1 — shipped in this PR

- Add `source_link`, `source_ref`, and `actions.expand` to search result envelopes.
- Keep backwards compatibility with flat `source_uri` / `seq` and `SearchOutput.from_envelope`.
- Add scanner permission-denied diagnostics with unreadable path aggregation.
- Add summary-generation skip behavior for unreadable files before model calls.
- Update agent usage guidance and operational runbooks.

### Slice 2 — next

- Add a host-side document permission audit/remediation command that groups unreadable paths by owner/group/mode and supports dry-run, chmod/chgrp repair, and quarantine/exclude recommendations.
- Add a production-safe orphan prune wrapper requiring a backup/snapshot confirmation.
- Add a USEARCH offline rebuild/catch-up command with lock, backup, temp index, parity threshold, atomic swap, and rollback.

### Slice 3 — deploy plane

- Grant Disk Snapshot Contributor to the GitHub/Azure deploy identity.
- Flip the VM alpha workflow from snapshot-skip to snapshot-required by default.
- Add PVT evidence to release notes: snapshot id, deployed image tag, onboard check, scoped search, preflight, orphan count, USEARCH parity.

## Verification

- Unit: focused tests for search envelope affordance and permission-skip behavior.
- Integration: `kairix worker preflight --json`, `kairix embed rebuild-fts`, `kairix search --collection sharepoint --json`.
- VM PVT after merge: confirm deployed Kairix version, healthy container, scoped search returns only requested collection, preflight has no fatal gaps, permission diagnostics are visible rather than raw tracebacks.

## Open risks

- USEARCH cannot be rebuilt purely from current `content_vectors` metadata because vector bytes are not the canonical persisted value. The offline catch-up path must either re-embed or read from the embedding cache, then write a temporary serving index.
- Snapshot enforcement depends on Azure IAM outside the repo.
- Permission remediation must be careful not to widen access to secrets or unrelated private documents.
