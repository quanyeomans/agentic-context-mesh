# ADR-025 — Pipeline observability + agent-actionable status surface

**Status:** Phase 1A validated 2026-05-29 — see §11. Phase 1 implementation in progress.
**Drives:** F74 (status_emit mandatory at every stage boundary), F75 (search envelope carries provenance).
**Companion specs:** [connector-ingestion-architecture.md](connector-ingestion-architecture.md),
[agent-actionable-feedback.md](../../host/docs/standards/agent-actionable-feedback.md) (F21 affordance template applied at runtime, not just commit-time),
[feature-flag-architecture.md](feature-flag-architecture.md) (default-safe rollout + both-branch testing).

## 1. Context

The production pipeline today is observable only via:

- Free-text `last_error` strings in `connector_deadletter` (187 SharePoint rows; ~10 distinct natural-language shapes).
- Stage-aggregate log lines (`worker: connector sync complete — synced=0 failed=0 dead_letter_added=0`) that report success of the whole tick, not per-item state.
- Indirect inference via cross-table joins (`bronze_records` LEFT JOIN `content` → "missing in silver").

This produced the **2026-05-29 surprise**: 5,374 of 8,783 SharePoint items (61%) had a bronze record but no silver content, with no telemetry explaining where they fell off the pipeline. The worker log truthfully reported `synced=0 failed=0` for the previous 13h tick window because the *delta endpoint* had returned zero new events — but said nothing about the 5,374-item silver-side gap that opened during earlier ticks.

The agent UX is similarly opaque. A search result that returns a stub chunk gives the agent no signal about whether:

- The source document genuinely contains only that snippet (correct).
- The extractor failed and only the title/metadata landed (incomplete).
- The chunk is from a document that has since been deleted upstream (stale).
- A reextract is warranted, or whether the agent should consult the source directly.

The agent is left guessing, and guesses badly.

## 2. Decision

Three-layer architecture, three phases, three feature flags, three definitions of done.

```
Phase 1 — Data capture                Phase 2 — Agent surface             Phase 3 — Self-healing
─────────────────────────             ────────────────────────             ─────────────────────────
status_emit(stage, code, ...)         search result envelope.provenance   auto-reextract on transient
   ↓                                  MCP tool_inspect_provenance         stale-flag stuck states
pipeline_item_status table            agent prompt rubric                 maintenance prune guard
   ↓                                  CLI kairix inspect-item             status_summary dashboards
CLI kairix worker inspect              flag: provenance_in_search          flag: pipeline_self_healing
   flag: pipeline_status_emit
```

Each phase is independently shippable, default-OFF, both-branch tested per F54, and gates on the previous phase's DoD.

## 3. Cross-cutting principles

The nine principles below apply at every phase. They are non-negotiable; phase gates measure compliance.

### P1 — Status emit at every stage boundary

Every pipeline stage (`fetch`, `extract`, `silver`, `chunk`, `embed`, `entity`, `drain`) emits exactly one `status_emit(...)` call per item per pass — on success or failure. A stage that completes without emitting is a defect, not a degraded mode. F74 enforces this mechanically at pre-commit by AST-scanning stage entry points for a paired `status_emit` call.

### P2 — Codes are typed enums; messages are not the contract

Status codes are members of a `StatusCode` `Enum` defined in `kairix/core/observability/status_codes.py`. The wire format is the enum **name** (`EXTRACT_DISK_FULL`), never the message text. Free-text `detail` fields are allowed for human context but never participate in dispatch, retry decisions, or agent affordance lookups.

### P3 — Severity classification is mechanical

Each code carries a default `Severity` (`OK` / `WARN` / `ERROR`) declared once at the enum site. A call site cannot override severity. This makes dashboards and self-healing logic stable across pipeline refactors.

### P4 — Remediation is forward-looking, concrete, F21-shaped

Every non-OK code maps to a `Remediation` record carrying `fix:`, `next:`, `run:` lines (the F21 affordance template), plus Pass and Forbidden examples where applicable. The remediation appears in dead-letter rows, agent search-result envelopes, and CLI failure output — one source of truth. Per `feedback_agent_prompts_positive_assertion`: positive action + concrete example + why.

### P5 — Provenance reaches the search result envelope

Every search hit carries a `provenance` block populated from the latest `pipeline_item_status` row for that item. Operator dashboards and agent results consume the *same* shape. No agent-only or operator-only telemetry path.

### P6 — Status table is append-only; updates are forbidden

`pipeline_item_status` is an immutable timeline. New facts are new rows. UPDATE statements against the table are blocked by a schema-side rule (CHECK constraint plus an F76 import-ban on `UPDATE pipeline_item_status`). Maintenance pruning works by `INSERT (..., status_code='PRUNED_RETENTION', ...)` then DELETE of rows older than the retention window. The timeline is the audit log.

### P7 — Stage emit is the single source of truth

Once Phase 1 ships, `connector_deadletter` is fed by `status_emit(..., severity=ERROR, ...)` calls — not by direct INSERTs from connector code. Worker stdout structured-log lines are derived from the same emit. This removes the "two error stores that drift" failure mode by giving every error exactly one writer.

### P8 — Self-healing is opt-in via feature flag, default-safe

Self-healing actions (auto-reextract, auto-stale-flag, prune guard) ship behind `pipeline_self_healing`. Default OFF. When ON in production, a 7-day shadow-mode soak logs the "would have done X" decisions without taking them — the operator inspects the log before allowing real action. Per `feature-flag-architecture.md` §2.1.

### P9 — Per-item per-stage, not per-source aggregates

The smallest unit of telemetry is `(source_name, item_id, stage)`. Aggregate metrics (status-code histograms, drain throughput, etc.) are derived from the per-item timeline at query time, never written separately. This guarantees aggregates and per-item answers can never disagree.

## 4. Patterns

### Pattern A — `StatusCode` enum

```python
# kairix/core/observability/status_codes.py
from enum import Enum

class Severity(Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"

class StatusCode(Enum):
    # (stage, severity, retry_eligible)
    FETCH_OK                    = ("fetch",   Severity.OK,    False)
    FETCH_TIMEOUT               = ("fetch",   Severity.ERROR, True)
    FETCH_THROTTLED             = ("fetch",   Severity.WARN,  True)
    FETCH_NOT_FOUND             = ("fetch",   Severity.WARN,  False)  # source deleted upstream
    FETCH_FORBIDDEN             = ("fetch",   Severity.ERROR, False)  # access lost
    EXTRACT_OK                  = ("extract", Severity.OK,    False)
    EXTRACT_OK_EMPTY            = ("extract", Severity.WARN,  False)  # extractor ran, zero text
    EXTRACT_UNSUPPORTED_MIME    = ("extract", Severity.WARN,  False)
    EXTRACT_DISK_FULL           = ("extract", Severity.ERROR, True)   # transient infra
    EXTRACT_MISSING_DEPS        = ("extract", Severity.ERROR, True)   # transient — fixed by container rebuild
    EXTRACT_CORRUPT_INPUT       = ("extract", Severity.ERROR, False)
    SILVER_OK                   = ("silver",  Severity.OK,    False)
    SILVER_DEDUPED              = ("silver",  Severity.OK,    False)
    SILVER_PRUNED_BY_MAINTENANCE = ("silver", Severity.WARN,  True)
    CHUNK_OK                    = ("chunk",   Severity.OK,    False)
    CHUNK_OVERSIZE_SPLIT        = ("chunk",   Severity.OK,    False)  # informational
    EMBED_OK                    = ("embed",   Severity.OK,    False)
    EMBED_DEFERRED              = ("embed",   Severity.OK,    False)
    EMBED_RATE_LIMITED          = ("embed",   Severity.WARN,  True)
    ENTITY_EXTRACTED            = ("entity",  Severity.OK,    False)
    ENTITY_DRAIN_PENDING        = ("drain",   Severity.OK,    False)
    ENTITY_DRAIN_PUSHED         = ("drain",   Severity.OK,    False)
    ENTITY_DRAIN_FAILED         = ("drain",   Severity.ERROR, True)
    PRUNED_RETENTION            = ("audit",   Severity.OK,    False)  # tombstone for timeline retention
    # Phase 1A additions (validated against production 2026-05-29):
    FETCH_ZERO_BYTES            = ("fetch",   Severity.WARN,  False)  # source returned empty content
    EXTRACT_OUTPUT_EMPTY        = ("extract", Severity.WARN,  False)  # extractor ran, returned empty markdown
    SILVER_NO_CHUNKS_WRITTEN    = ("silver",  Severity.WARN,  False)  # chunker received empty markdown
    INFERRED_SILENT_DROP        = ("audit",   Severity.WARN,  False)  # backfill: pre-Phase-1 silent-drop items
    INFERRED_FROM_DEAD_LETTER   = ("audit",   Severity.WARN,  False)  # backfill: parsed from connector_deadletter
    PIPELINE_STAGE_NO_EMIT      = ("audit",   Severity.ERROR, False)  # P1 fail-safe: stage exited without emit

    @property
    def stage(self) -> str:      return self.value[0]
    @property
    def severity(self) -> Severity: return self.value[1]
    @property
    def retry_eligible(self) -> bool: return self.value[2]
```

The list above is the **starting** taxonomy. Phase 1A reconciles against actual production failure modes; the final enum lands with ADR-025 acceptance.

### Pattern B — `status_emit` context manager

```python
# kairix/core/observability/status_emit.py
@contextmanager
def emit_for(source_name: str, item_id: str, stage: str, *, db: sqlite3.Connection):
    """Wrap a stage call. Emits OK on clean exit, ERROR on raise."""
    started = time.time()
    try:
        result = yield Emitter(source_name, item_id, stage, db, started)
        # The body must call result.ok(...) or result.warn(...) before exit.
        # Bare exit without an emit is a defect; the F74 gate catches it
        # statically and the runtime emits PIPELINE_STAGE_NO_EMIT as a fail-safe.
    except Exception as exc:
        _emit_for_exception(source_name, item_id, stage, exc, db, started)
        raise
```

Every stage entry point uses this:

```python
def extract_item(item: BronzeRecord, *, db) -> ExtractedDocument:
    with emit_for(item.source_name, item.item_id, "extract", db=db) as emit:
        try:
            doc = self._extractor.extract(item.raw, item.mime)
        except DiskFullError as e:
            emit.error(StatusCode.EXTRACT_DISK_FULL, detail={"errno": 28, "raw_path": item.raw_path})
            raise
        if not doc.markdown.strip():
            emit.warn(StatusCode.EXTRACT_OK_EMPTY, detail={"mime": item.mime})
            return doc
        emit.ok(StatusCode.EXTRACT_OK, detail={"chars": len(doc.markdown), "mime": item.mime})
        return doc
```

### Pattern C — `Remediation` registry

```python
# kairix/core/observability/remediation.py
REMEDIATION: dict[StatusCode, Remediation] = {
    StatusCode.EXTRACT_DISK_FULL: Remediation(
        fix="Confirm the worker container has disk headroom; the 2026-05-29 consolidation gave the OS disk 247GB.",
        next="The item is auto-eligible for reextract once disk pressure clears (P3 self-healing).",
        run="kairix worker reextract --item <item_id>",
        agent_action="Source is reachable; this is a transient infra issue. Inspect the source URI directly: <source_uri>.",
    ),
    StatusCode.EXTRACT_OK_EMPTY: Remediation(
        fix="The extractor produced zero text — likely a scanned PDF without OCR or an unsupported binary format.",
        next="Try the OCR variant: `kairix worker reextract --item <item_id> --extractor markitdown+ocr`.",
        run="kairix worker reextract --item <item_id> --extractor markitdown+ocr",
        agent_action="The chunk is a stub. Inspect the source at <source_uri> for the full document content.",
    ),
    # ... one per non-OK code
}
```

The `agent_action` field is what flows into the search-result `provenance` block in Phase 2 (Pattern E).

### Pattern D — F74 emit-coverage gate

Mechanical check (`scripts/checks/check_f74_status_emit_coverage.py`):

> Every function in `kairix/core/connectors/`, `kairix/core/embed/`, `kairix/core/curator/`, `kairix/extractors/<name>/`, `kairix/core/maintenance/` whose name matches `^(fetch|extract|process|run|push|drain)_(item|batch)` must call `emit_for(...)` (call-graph depth ≤ 2). Pre-commit AST scan; baseline shrinks per release per F49.

### Pattern E — Provenance block in search envelope

```python
# kairix/core/search/provenance.py
@dataclass(frozen=True)
class Provenance:
    completeness: Literal["complete", "partial", "stub", "stale"]
    last_status_code: str          # StatusCode.name
    last_status_at: str            # ISO-8601
    timeline_summary: tuple[str, ...]  # ordered stage codes for quick scan
    source_inspection_uri: str | None
    agent_action: str              # from Remediation.agent_action

@classmethod
def from_item(cls, source_name: str, item_id: str, *, db) -> "Provenance | None":
    """Build provenance for the latest known status of (source, item)."""
    ...
```

`SearchHit` grows a `provenance: Provenance | None` field. Hits without status entries (legacy data) get `None`; the agent prompt rubric handles that case.

### Pattern F — `kairix worker inspect <source> <item>`

CLI subcommand that prints the full timeline for one item:

```
$ kairix worker inspect sharepoint 01AMCGZH3GKOCAMKLJWZGZU4N37MPGXEFM
source_name      sharepoint
item_id          01AMCGZH3GKOCAMKLJWZGZU4N37MPGXEFM
raw_path         /Agent Exchange/.../report.pdf
fetched_at       2026-05-26T09:36:09Z
content_hash     a7b2…

Stage timeline:
  09:36:09  fetch    FETCH_OK
  09:36:10  extract  EXTRACT_DISK_FULL   "ENOSPC at byte 4291 of 8123"
                       ↳ retry-eligible: YES; auto-reextract scheduled (P3)
                       ↳ agent_action: Source is reachable; inspect at sharepoint://...

Current state: pipeline stopped at `extract`; silver row absent.
Suggested next: kairix worker reextract --item 01AMCGZH3GKOCAMKLJWZGZU4N37MPGXEFM
```

### Pattern G — `kairix worker status-summary` histogram

```
$ kairix worker status-summary --source sharepoint --since 7d
Total items observed:         8,783
By latest status code:
  EXTRACT_OK                    3,409 (38.8%)
  EXTRACT_OK_EMPTY              1,210 (13.8%)
  EXTRACT_DISK_FULL               187  (2.1%)  ← retry-eligible
  EXTRACT_MISSING_DEPS          2,800 (31.9%)  ← retry-eligible (markitdown extras)
  FETCH_FORBIDDEN                 977 (11.1%)
  FETCH_NOT_FOUND                 200  (2.3%)

Retry-eligible total:         5,187 items
Awaiting human review:        2,387 items (UNSUPPORTED_MIME + FORBIDDEN + NOT_FOUND)
```

This is the operator surface that replaces the manual cross-join probes I ran in the 2026-05-29 audit.

## 5. Phase 1 detail — Data capture

### Scope

- New table `pipeline_item_status` per the schema in §6.
- `kairix/core/observability/{status_codes,status_emit,remediation}.py` modules.
- `emit_for(...)` instrumented at every stage entry point.
- `kairix worker inspect <source> <item>` CLI subcommand.
- `kairix worker status-summary` CLI subcommand.
- F74 (status_emit coverage) check + baseline.
- BDD feature `tests/bdd/features/pipeline_status_emit.feature` covering OFF + ON branches of the flag.
- F30 outcome tests for both new CLI subcommands.

### Out of scope (Phase 1)

- Search result envelope changes (Phase 2).
- MCP tool surface (Phase 2).
- Self-healing actions (Phase 3).
- Replacing `connector_deadletter` writes with status_emit calls (Phase 1 ships emit calls *alongside* existing dead-letter writes; cutover in Phase 1.5).

### Feature flag

`pipeline_status_emit` — default OFF. When OFF, `emit_for` is a no-op context manager; no schema dependency. When ON, all writes land. Both branches BDD-tested per F54.

### Phase 1 definition of done

| # | Criterion | Verification |
|---|---|---|
| 1.1 | `pipeline_item_status` table created via `kairix/core/db/schema.py` change with migration test | `tests/integration/test_pipeline_item_status_schema.py` |
| 1.2 | `StatusCode` enum covers every code observed in Phase 1A; F74 baseline shows zero gaps | `python3 scripts/checks/check_f74_status_emit_coverage.py` returns ok |
| 1.3 | Every stage entry-point function has a paired `emit_for(...)` call | F74 AST scan; baseline at zero |
| 1.4 | `kairix worker inspect <source> <item>` returns full timeline, exits 0 on found, 1 on not-found, F21-shaped error | `tests/integration/test_worker_inspect_cli.py` (F30 outcome test) |
| 1.5 | `kairix worker status-summary` returns histogram, supports `--source` + `--since` filters | `tests/integration/test_worker_status_summary_cli.py` (F30 outcome test) |
| 1.6 | BDD feature `tests/bdd/features/pipeline_status_emit.feature` has scenarios for flag OFF (no rows written) and flag ON (one row per stage per item) | `pytest -m bdd` green |
| 1.7 | Append-only invariant: integration test attempting `UPDATE pipeline_item_status` fails with constraint violation | `tests/integration/test_pipeline_status_append_only.py` |
| 1.8 | F74 detector ships with empty baseline; sabotage proof executed | `tests/checks/test_f74_status_emit_coverage.py` |
| 1.9 | 24h soak in staging with flag ON: zero `PIPELINE_STAGE_NO_EMIT` fail-safe codes recorded | Operator-attested via `kairix worker status-summary --code PIPELINE_STAGE_NO_EMIT --since 24h` |

### Phase 1 → Phase 2 gate

All 9 DoD criteria green AND staging soak passes AND ADR-025 accepted by operator review.

## 6. Phase 2 detail — Agent UX surface

### Scope

- `SearchHit` grows `provenance: Provenance | None` field.
- `kairix/core/search/provenance.py` module implements `Provenance.from_item(...)`.
- Search pipeline populates provenance for every hit during result assembly.
- MCP tool `tool_inspect_provenance(source_name, item_id)` returns the full timeline as a JSON envelope.
- CLI `kairix inspect <source> <item>` (top-level, agent-friendly) — alias of `kairix worker inspect` with simplified output.
- Agent prompt template addition: capability docs include the "stub / partial / complete" rubric + the recommended action per state.

### Feature flag

`provenance_in_search` — default OFF. When OFF, `SearchHit.provenance = None` everywhere (legacy callers unaffected). When ON, every hit carries the block.

### Phase 2 definition of done

| # | Criterion | Verification |
|---|---|---|
| 2.1 | `Provenance` dataclass declared frozen; F42 boundary compliance | `tests/contracts/test_protocols.py` |
| 2.2 | `SearchHit.provenance` populated on every hit when flag ON; never populated when flag OFF | `tests/bdd/features/feature_flag_provenance_in_search.feature` (F54 OFF + ON) |
| 2.3 | `tool_inspect_provenance` MCP tool registered, returns the full timeline envelope, F30-tested | `tests/integration/test_tool_inspect_provenance_outcome.py` |
| 2.4 | `kairix inspect <source> <item>` top-level CLI works with F30 outcome test | `tests/integration/test_inspect_cli_outcome.py` |
| 2.5 | Agent capability docs include the "interpret a partial / stub / stale result" rubric with concrete examples | `docs/agents/search-result-rubric.md` ships |
| 2.6 | E2E composed-path test: end-to-end search → result envelope carries provenance for a known-incomplete item | `tests/e2e/test_composed_provenance_path.py` (F48 compliance) |
| 2.7 | F75 (search-envelope-provenance-when-flag-on) check ships with baseline zero | `tests/checks/test_f75_search_envelope_provenance.py` |

### Phase 2 → Phase 3 gate

All 7 DoD criteria green AND agent dogfood report confirms at least one search-result-driven "stub → reextract → re-search → complete" loop executed end-to-end without operator intervention.

## 7. Phase 3 detail — Self-healing

### Scope

- `MaintenanceScheduler.tick` grows a `self_heal_pass` that:
  1. Selects items whose latest status is a retry-eligible ERROR code older than `STALE_AFTER`.
  2. Schedules `worker reextract` for each (rate-limited).
  3. Emits `SELF_HEAL_RETRY_SCHEDULED` status row per item.
- Stale-flag pass: items with `EXTRACT_OK_EMPTY` older than 7 days get `STALE_REVIEW_REQUIRED` status row; operator dashboard surfaces them.
- Maintenance prune guard: orphan-cleanup queries `pipeline_item_status` first — never prunes a hash whose latest status is `SILVER_OK` AND whose bronze parent is still active.
- `kairix worker status-summary --self-heal-actions` shows actions taken in the last N hours.
- Shadow mode: feature flag `pipeline_self_healing` has three states (`off`, `shadow`, `on`). `shadow` logs the decisions to `pipeline_item_status` (with `SELF_HEAL_WOULD_HAVE_X` codes) without acting.

### Feature flag

`pipeline_self_healing` — three-state flag (`off` | `shadow` | `on`). Default OFF in production. 7-day shadow soak required before flipping to `on`.

### Phase 3 definition of done

| # | Criterion | Verification |
|---|---|---|
| 3.1 | `self_heal_pass` schedules reextract for retry-eligible items older than `STALE_AFTER` | `tests/integration/test_self_heal_reextract_loop.py` |
| 3.2 | Stale-flag pass writes `STALE_REVIEW_REQUIRED` for `EXTRACT_OK_EMPTY` items > 7 days old | `tests/integration/test_self_heal_stale_flag.py` |
| 3.3 | Prune guard rejects deletion of hashes whose latest status is `SILVER_OK` + active bronze parent | `tests/integration/test_maintenance_prune_guard.py` |
| 3.4 | Three-state feature flag works: OFF skips, SHADOW logs WOULD_HAVE_X codes, ON acts. F54 compliance for all three branches. | `tests/bdd/features/feature_flag_pipeline_self_healing.feature` |
| 3.5 | `kairix worker status-summary --self-heal-actions` returns action histogram with F30 outcome test | `tests/integration/test_self_heal_actions_summary.py` |
| 3.6 | F77 (self-healing-actions-bounded) check: per-tick action cap respects `disk_watermark_min_free_bytes` (mirrors F66 pattern) | `tests/checks/test_f77_self_heal_bounded.py` |
| 3.7 | 7-day production shadow-mode soak captured; operator reviews `WOULD_HAVE_X` log and explicitly authorises the `on` flip via a separate commit toggling the registry default | Operator commit + retain-extension flag rationale comment |

### Phase 3 → ship gate

All 7 DoD criteria green AND shadow-soak review passes AND operator authorises the `on` flip per the per-action HITL principle (`feedback_release_hitl`).

## 8. Schema migration

```sql
CREATE TABLE IF NOT EXISTS pipeline_item_status (
    source_name      TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    stage            TEXT NOT NULL,
    status_code      TEXT NOT NULL,
    severity         TEXT NOT NULL CHECK (severity IN ('ok','warn','error')),
    detail_json      TEXT,
    occurred_at      TEXT NOT NULL,
    chunker_version  TEXT,
    extractor_version TEXT,
    PRIMARY KEY (source_name, item_id, stage, occurred_at)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_status_lookup
    ON pipeline_item_status (source_name, item_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_status_by_code
    ON pipeline_item_status (status_code, occurred_at DESC);

-- Append-only invariant enforced at insertion-time (no UPDATE statements allowed)
-- and checked by F76 import-ban + the test in §5 DoD criterion 1.7.
```

Table lifetime: rows retained for `STATUS_RETENTION_DAYS` (default 90). Maintenance prune writes a `PRUNED_RETENTION` row before deleting older rows. The retention prune is the only legitimate DELETE.

## 9. Open questions (Phase 1A resolves)

1. **Is `EXTRACT_OK_EMPTY` distinguishable from `EXTRACT_UNSUPPORTED_MIME`?** — markitdown returns empty for both unsupported formats AND scanned PDFs. May need a `detail.reason` discriminator.
2. **How is `SILVER_PRUNED_BY_MAINTENANCE` distinguished from `SILVER_DEDUPED`?** — the 5,374-gap audit suggests one of these is silently swallowing items. Phase 1A counts both shapes.
3. **What's the actual frequency of the dead-letter shape classes?** — initial sample says disk-full dominates (9/10 recent), but 187 across history may skew differently. Phase 1A produces the histogram.
4. **Does `content_vectors_pruned` carry source-attribution?** — if not, retroactively assigning `SILVER_PRUNED_BY_MAINTENANCE` codes to old items isn't possible; Phase 1 starts the timeline fresh.
5. **MCP tool naming:** `tool_inspect_provenance` vs `tool_explain_chunk` vs `tool_diagnose_search_result` — agent-facing name should follow `naming-for-agent-affordance.md`.

## 10. Phase 1A — mining plan

Concrete steps (executed immediately):

1. **Deadletter shape clustering** — group all 187 `connector_deadletter.last_error` rows by leading verb (`extract:`, `fetch:`, `silver:`) + error class. Output: histogram + the 5-10 distinct shapes that need codes.
2. **Worker log error/warn mining** — pull last 7 days of warn/error lines from `app-kairix-worker-1`. Extract patterns. Cross-reference against the deadletter shapes.
3. **Gap-item sample trace** — pick 20 of the 5,374 silver-gap SharePoint items at random. For each, reconstruct the timeline from `bronze_records`, `content_vectors_pruned`, `connector_deadletter`. Identify where they fell off + which proposed code best names that state.
4. **`content_vectors_pruned` source-attribution probe** — confirm whether pruned rows can be traced back to sharepoint hashes. If yes, retroactive `SILVER_PRUNED_BY_MAINTENANCE` codes are possible; if no, document the limitation.
5. **Reconcile taxonomy** — update the StatusCode enum in §4 with the validated set. Remove proposed codes that don't match observed reality; add codes for shapes the proposed set missed.

**Output:** an updated §4 + §9 in this ADR plus a short "Phase 1A findings" appendix (§11) documenting the histogram + the 20-item trace.

## 11. Phase 1A findings (executed 2026-05-29)

### 11.1 The gap is 96.5% silent

```
bronze_items_missing_from_silver        5,374
  ALSO present in connector_deadletter    185  (3.4%)
  SILENT (missing without dead-letter)  5,189  (96.5%)
```

This is the load-bearing finding. The 5,189 silent items have **no error record anywhere** — not in `connector_deadletter`, not in worker stdout, not in `content_vectors_pruned`. Bronze recorded that the item was fetched + a content_hash was computed; then the item vanished from the pipeline. Without status_emit, these items are structurally invisible.

**Implication:** Phase 1 must include a **backfill pass** that walks `bronze_records` and emits an initial `INFERRED_SILENT_DROP` status row for every item with no other status, so the timeline isn't empty when Phase 1 ships against pre-existing pipeline state.

### 11.2 Orphan-prune hypothesis rejected

```
content_vectors_pruned total rows                21,399
sharepoint bronze hashes in pruned table              2
```

Only 2 of the 5,374 missing-silver SharePoint items appear in `content_vectors_pruned`. The maintenance orphan-cleanup is not the cause. The `SILVER_PRUNED_BY_MAINTENANCE` code stays in the taxonomy but is not the explanation for the 2026-05-29 gap.

### 11.3 Gap-item state distribution (sample of 200 SharePoint bronze items)

| State | Count | % | Interpretation |
|---|---|---|---|
| `hash-set-but-no-content` | 127 | 63.5% | Extract completed, content_hash recorded in bronze, but no `content.hash=...` row exists. The silent-drop class. |
| `in-content` | 69 | 34.5% | Correctly landed in silver. |
| `no-hash` (content_hash NULL or empty) | 4 | 2.0% | Bronze fetched but no hash computed. |

The `no-hash` samples include items whose `raw_path` ends in the universal SHA-256 of zero bytes (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`). That's a recognised constant — these items fetched empty bytes from the source. They're not extractor failures; they're upstream-empty-content. New code: `FETCH_ZERO_BYTES`.

The `hash-set-but-no-content` class (the dominant 63.5%) had typical MIME types — `application/pdf`, `text/html`, DOCX. These extracted to a hash but silver wrote nothing. The most likely explanation is **extract returned empty markdown** → `silver._chunk_markdown("")` returned `()` → no chunks, no content rows, no dead-letter, no log line. The pipeline returns "success" while silently swallowing the item. New code: `EXTRACT_OUTPUT_EMPTY` and `SILVER_NO_CHUNKS_WRITTEN`.

### 11.4 Worker log warning patterns

Dominant warn signal class:
- `preflight gap — [info] vector-store-vs-content-vectors count=N` (recurring; vectors lagging content_vectors table)
- `preflight gap — [error] documents-without-vectors count=N` (sporadic small counts: 9, 56)

These confirm the preflight machinery is working (per F71). But the granularity is wrong — they tell you "N documents lack vectors" without naming which N. The proposed `EMBED_DEFERRED` code captures this at per-item granularity.

`db.scanner: Scan: N new, N updated, N removed, N unchanged` — filesystem scanner aggregate. Operates on the wrongly-pointed `/data/documents/reference-library` path noted earlier; will be removed by the reference-library config fix.

### 11.5 Dead-letter shape histogram (probe-output truncated; partial)

The full histogram couldn't be reliably reconstructed from the truncated probe output, but the dominant shape from the earlier sample of 10 most-recent rows was:

| Shape | Approx count | Code |
|---|---|---|
| `extract: [Errno 28] No space left on device` | 9 of 10 sampled | `EXTRACT_DISK_FULL` |
| `fetch: The read operation timed out` | 1 of 10 sampled | `FETCH_TIMEOUT` |
| Total in connector_deadletter (sharepoint) | 187 | mixed |

Phase 1 implementation includes a one-shot migration that reads existing `connector_deadletter.last_error` strings, parses them against a small classifier, and emits the matching `StatusCode` to populate the timeline retroactively for known-failed items.

### 11.6 Revised taxonomy — additions

The §4 enum sketch ships with these additions validated by Phase 1A:

| Code | Stage | Severity | Retry | Phase 1A evidence |
|---|---|---|---|---|
| `FETCH_ZERO_BYTES` | fetch | warn | false | `no-hash` items with `raw_path` ending in SHA-256-of-empty |
| `EXTRACT_OUTPUT_EMPTY` | extract | warn | false (without OCR change) | Hypothesis: 63.5% of gap items |
| `SILVER_NO_CHUNKS_WRITTEN` | silver | warn | false | Paired with `EXTRACT_OUTPUT_EMPTY` |
| `INFERRED_SILENT_DROP` | audit | warn | false | Backfill code for pre-Phase-1 items with no status entries |
| `INFERRED_FROM_DEAD_LETTER` | audit | warn | false | Backfill from existing connector_deadletter rows |
| `PIPELINE_STAGE_NO_EMIT` | audit | error | false | Runtime fail-safe per P1 |

### 11.7 Self-healing implications

For the 5,189 silent-drop items, self-healing can't trigger off `INFERRED_SILENT_DROP` alone because the inferred code doesn't tell us whether reextract would succeed. The Phase 3 self-heal pass needs to:

1. Identify silent-drop items via the backfill.
2. Schedule a single diagnostic reextract per item under low-rate-limit.
3. Capture the *real* status code on that retry (`EXTRACT_DISK_FULL` is fixable; `EXTRACT_OUTPUT_EMPTY` likely needs OCR variant; `EXTRACT_UNSUPPORTED_MIME` needs human classification).
4. Only after the diagnostic retry assigns a real code can auto-heal apply the retry-eligibility logic.

This adds a "diagnostic retry" stage to Phase 3 — captured here so it's not forgotten when Phase 3 implementation starts.

## 12. References

- `docs/architecture/connector-ingestion-architecture.md` — the connector framework this instruments
- `docs/architecture/ADR-020-connector-tick-budget-watermark.md` — F66 prior art for per-tick bounds in stage code
- `docs/architecture/ADR-021-per-source-metadata-normalisation.md` — F65 prior art for per-item metadata propagation
- `docs/architecture/feature-flag-architecture.md` — three-state flag pattern (Phase 3)
- `host/docs/standards/agent-actionable-feedback.md` — the F21 affordance template applied at runtime
- `feedback_agent_prompts_positive_assertion` — positive action + concrete example + why (memory)
- `feedback_release_hitl` — per-action HITL principle (governs Phase 3 ON flip)
