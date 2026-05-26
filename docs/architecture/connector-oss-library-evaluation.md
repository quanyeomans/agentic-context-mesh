---
type: evaluation
title: OSS connector framework evaluation — post-ADR-018 reset
status: in-progress
date: 2026-05-26
related:
  - ADR-018-dlt-connector-framework
  - connector-ingestion-architecture
---

# OSS connector framework evaluation

ADR-018 proposed adopting dlt with high conviction; three negative-fit findings during implementation revealed dlt is the wrong shape. The ADR closed with "spike first, ADR second" as a lesson learned. This document is that spike — proper structured research before any recommendation. **No library will be implemented until the user has reviewed the evidence and chosen.**

The dlt failure mode was: pattern-matching a familiar library name to a vague "stop reinventing" goal without first articulating the architectural constraints the library would need to fit. This document fixes that by leading with the constraints, then surveying the candidate space, then evaluating each candidate against the constraints with concrete evidence.

---

## Section 1 — Hard requirements (derived from the dlt failures + kairix's actual architecture)

Each row is a yes/no question that determines whether a library is even worth deeper evaluation. If a candidate fails any HARD requirement, it drops out at this layer — no implementation spike needed.

| # | Requirement | Source of constraint | dlt result |
|---|---|---|---|
| H1 | **Supports raw binary blobs as first-class output**, not exclusively rows-into-tables. The bronze layer stores original PDFs / DOCX / markdown bytes on disk for re-extraction. | `kairix/core/connectors/bronze.py` — `<bronze_root>/<source>/<hash[:2]>/<hash>` layout | FAIL (filesystem destination only writes `jsonl/parquet/csv` per dlt docs) |
| H2 | **Composes with an externally-owned SQLite transaction**, OR provides equivalent atomic-write-across-stores semantics that bind bronze + cursor + dead-letter together. | `kairix/core/connectors/pipeline.py` — single `sqlite3.Connection` shared across stores; chunked commit ties them together | FAIL (dlt owns its own pipeline lifecycle; resource_state only usable inside pipeline.run) |
| H3 | **Supports opaque-token cursor shapes** (Microsoft Graph deltaLink URLs, Slack RTM event positions, GitHub event IDs), not just numeric/timestamp incremental fields. | `kairix/connectors/sharepoint/connector.py` — deltaLink per-drive JSON map; future connectors have similar opaque shapes | FAIL (`dlt.sources.incremental` works on numeric/timestamp fields) |
| H4 | **Python-native library**, not requiring separate runtime processes, JVM, Docker daemon, or external orchestrator. | kairix runs as a Python service in one Docker container; no JVM in scope per CLAUDE.md "no third language without ADR" | dlt PASSES on this; many other candidates fail |
| H5 | **Apache-2.0 / MIT / BSD licence**. Kairix is Apache-2.0; ship-with-product requires permissive licence. | `pyproject.toml` — Apache-2.0 declared | dlt PASSES (Apache-2.0) |
| H6 | **Mature / production-quality / actively maintained.** Last release within the past 6 months; >100 GitHub stars; commits in the past 30 days. | Operator risk surface | dlt PASSES |
| H7 | **Reasonable transitive dependency footprint** — does not pull pandas, pyarrow, JVM, or a >100 MB tree just to use one feature. | kairix is bandwidth-bounded on operator installs; `markitdown[pdf]` is already the heaviest dep at ~150 MB transitive | dlt borderline (~30 MB transitive with sqlalchemy extra) |

## Section 2 — Soft requirements (tiebreakers between candidates that pass the hard set)

| # | Requirement | Why it matters |
|---|---|---|
| S1 | **Drop-in to the existing `SourceConnector` Protocol** — minimal surface-area change at the kairix integration point | reduces migration cost; lets multiple connectors migrate independently |
| S2 | **Has examples of the kairix use case shape** in docs / GitHub — raw-blob fetch + structured ingest + atomic commits | reduces unknown unknowns |
| S3 | **Supports both incremental and full-refresh sync modes** without separate code paths | matches kairix's "first sync = full drive walk, subsequent ticks = delta" pattern |
| S4 | **Has retry / dead-letter primitives** that compose with kairix's existing `DeadLetterStore` | reduces homegrown surface area |
| S5 | **Has resource composition** — multi-drive, multi-account, multi-workspace via a single source declaration | matches SharePoint multi-drive, M365 multi-mailbox, Slack multi-workspace patterns |
| S6 | **First-class testability** — can be exercised in pytest with fixtures, not requiring external services / Docker / mock servers | matches kairix's BDD/contract/integration test discipline |

## Section 3 — Anti-requirements (things we explicitly DO NOT need; libraries that lead with these are over-shaped)

- Schema inference — kairix has frozen dataclasses (`Chunk`, `BronzeRef`, etc.)
- Schema evolution / migration — kairix controls its own schema via `kairix/core/db/schema.py`
- Data warehouse destinations (Snowflake / BigQuery / Redshift) — kairix is SQLite-only by ADR-017
- Auto-normalization of nested JSON into tables — kairix's `SilverProcessor` owns chunking semantics
- Streaming / sub-second latency — kairix's connector ticks run every 60s-24h
- Multi-tenant orchestration with workspace isolation — kairix runs one-engagement-per-deployment per CLAUDE.md

## Section 4 — Candidate space (all the plausible options I'm aware of, listed before filtering)

| # | Candidate | Type | Initial fit hypothesis |
|---|---|---|---|
| C1 | **dlt** | Python-native ELT framework | Evaluated in ADR-018; three negative-fit findings; OUT |
| C2 | **Airbyte Python CDK** | Connector Development Kit (the lightweight Python subset of the Airbyte protocol) | Designed for cursor diversity; needs deep eval |
| C3 | **Singer specification + a Python tap library** (e.g. `singer-python`, `meltano/sdk`) | JSON-pipe protocol with per-tap libraries | Older, mature; process-per-tap historically |
| C4 | **Meltano SDK** | Singer-based but with proper Python SDK | Cursor-diversity focus; needs deep eval |
| C5 | **PyAirbyte** | Python library API to Airbyte connectors | New (2024+); thin wrapper over CDK |
| C6 | **Estuary Flow (`flowctl`)** | Streaming-first ELT | Cloud-managed primarily; self-host story weak |
| C7 | **Mage** | ELT + orchestrator | Heavy; orchestrator-shaped |
| C8 | **Dagster + custom IO managers** | Asset / pipeline orchestrator | Could wrap kairix's existing connector logic |
| C9 | **Prefect + custom flows** | Workflow orchestrator | Could schedule connector runs but doesn't help with cursor/atomicity |
| C10 | **Apache Beam Python SDK** | Distributed data processing | Heavy; designed for parallel processing |
| C11 | **Bonobo** | Small Python ETL toolkit | Simple; possibly underpowered |
| C12 | **Stay homegrown + harden with proper primitives** (SQLAlchemy, Alembic, tenacity for retry, structlog for observability) | Not a connector framework — primitives library | Counter-proposal; evaluated against C2-C5 |

## Section 5 — First-pass filter against hard requirements

Each candidate gets a Y/N/? against each hard requirement. Anything with any N drops out; anything with ? gets a deeper look in Section 6.

| Candidate | H1 raw blobs | H2 atomic w/ SQLite | H3 opaque cursors | H4 Python-native | H5 licence | H6 maintained | H7 footprint | First-pass verdict |
|---|---|---|---|---|---|---|---|---|
| C1 dlt | N | N | N | Y | Y | Y | Y | **OUT** (proven) |
| C2 Airbyte CDK | ? | ? | Y | Y | Y (MIT) | Y | ? | Deep-dive |
| C3 Singer + python tap | ? | N (separate processes) | Y | partially (separate processes is the protocol) | Y | Y | ? | OUT — separate processes break atomicity |
| C4 Meltano SDK | ? | ? | Y | Y | Y (Apache-2.0) | Y | ? | Deep-dive |
| C5 PyAirbyte | ? | ? | Y | Y | Y (MIT) | Y | ? | Deep-dive |
| C6 Estuary Flow | N (cloud-first) | N (own runtime) | Y | Y (Go runtime, Python connectors) | Y | Y | N (heavy) | OUT |
| C7 Mage | ? | N (orchestrator owns runtime) | Y | Y | Y | Y | N | OUT |
| C8 Dagster | N/A (doesn't solve our problem; orchestrator) | N/A | N/A | Y | Y | Y | N | OUT — wrong layer |
| C9 Prefect | N/A | N/A | N/A | Y | Y | Y | N | OUT — wrong layer |
| C10 Apache Beam | N | N | Y | Y | Y | Y | N (very heavy) | OUT |
| C11 Bonobo | N | N | N | Y | Y | inactive (last release 2019) | Y | OUT — abandoned |
| C12 Stay homegrown | Y | Y | Y | Y | Y | Y | Y | Strong counter-baseline |

**Survivors for Section 6 deep-dive: C2 Airbyte CDK, C4 Meltano SDK, C5 PyAirbyte, C12 stay-homegrown-with-better-primitives.**

## Section 6 — Deep-dive evidence (per surviving candidate)

Four research agents executed in parallel, one per surviving candidate. Each was given the hard requirements above + the production code context + instructed to cite specific docs URLs / source files / pyproject.toml entries. Verdicts converged: every OSS warehouse-ingest framework fails H1 in the same shape.

### C2 — Airbyte Python CDK

| Requirement | Verdict | Evidence |
|---|---|---|
| H1 raw binary blobs | **FAIL** | `AirbyteRecordMessage.data` is `dict[str, Any]`; the `file_based` source CDK PARSES files into structured records (CSV→rows, JSONL→objects, PDF/DOCX/Markdown via `unstructured` parser into a `content` text field) — no raw-bytes-passthrough path. Source: [file_based source tree](https://github.com/airbytehq/airbyte-python-cdk/tree/main/airbyte_cdk/sources/file_based) |
| H3 opaque cursors | FAIL | `cursor_field` refers to a field IN records; `get_updated_state` expects comparable values for ordering. Source: [Incremental streams docs](https://docs.airbyte.com/connector-development/cdk-python/incremental-stream) |
| H2 transactional composition | structurally PASS | `AbstractSource.read()` returns `Iterator[AirbyteMessage]`; caller can wrap in own transaction |
| H7 deps | borderline FAIL | Pins `pandas 2.2.3`, plus ~33 runtime deps including `google-cloud-secret-manager`, `cryptography`. Conflict risk with `markitdown`/`sentence-transformers`. Source: [pyproject.toml](https://github.com/airbytehq/airbyte-python-cdk/blob/main/pyproject.toml) |

**Verdict: OUT.** Same shape as dlt — designed for rows-into-warehouse; raw blobs aren't supported.

### C4 — Meltano Singer SDK

| Requirement | Verdict | Evidence |
|---|---|---|
| H1 raw binary blobs | **FAIL** | Type system is JSON-Schema-only (`singer_sdk/typing.py:85-107` — `StringType`, `IntegerType`, etc., no `BinaryType`). Records serialised to stdout as JSON lines. |
| H3 opaque cursors | FAIL | `replication_key` is a field name on records; `replication_key_value = latest_record[replication_key]` (`singer_sdk/helpers/_state.py:228`). Maintainers acknowledge the gap — see still-open [issue #2753](https://github.com/meltano/sdk/issues/2753). |
| H2 transactional composition | FAIL | SDK owns sync lifecycle via `Tap.cli()` → `sync_all()` → stdout. State emitted asynchronously per `STATE_MSG_FREQUENCY`. No caller-owned transaction hook. |
| H7 deps | PASS | ~25-35 MB transitive, no pandas/pyarrow forced. SQLAlchemy 2 unconditional but small. |

**Verdict: OUT.** Process-shaped (tap | target | state file), not library-shaped. Same three failure shapes as dlt.

### C5 — PyAirbyte

| Requirement | Verdict | Evidence |
|---|---|---|
| H1 raw binary blobs | **FAIL** | `_message_iterators.py` surfaces records as `dict[str, Any]` via `StreamRecord.from_record_message()`. File-source family explicitly states: *"This connector does not support syncing unstructured data files such as raw text, audio, or videos."* Source: [docs.airbyte.com/integrations/sources/file](https://docs.airbyte.com/integrations/sources/file) |
| H3 opaque cursors | **PASS** | `AirbyteStateMessage` is opaque JSON to PyAirbyte; `StaticInputState` accepts caller-supplied state. A `{"deltaLink": "https://..."}` works at PyAirbyte's API surface. |
| H2 transactional composition | PARTIAL | `source.get_records(stream_name)` returns a `LazyDataset` you can iterate into your own SQLite transaction. BUT: state persistence is bound to the cache layer; bypassing the cache means re-implementing the StateWriter. |
| H4 runtime | PASS | Subprocess-per-connector via venv executor (default) — heavier than truly in-process but no Docker daemon required. |
| H7 deps | borderline | venv per connector adds host footprint; underlying CDK pulls `pandas 2.2.3` if the connector requires the file_based path. |

**Verdict: OUT.** H3 passes cleanly, H2 is salvageable, but H1 is still a hard fail — and kairix's bronze layer is the WHOLE reason we'd adopt a framework.

### C12 — Stay homegrown + selective primitives (counter-baseline)

This candidate is "do nothing structural; harden the existing ~1250 LoC framework with targeted OSS primitives where they earn their keep."

**Current state per production issue (after this week's Wave 1 + #316/#318 + #320 work):**

| Issue | Current resolution | Residual risk |
|---|---|---|
| #316 — no bronze GC | `FilesystemBronzeStore.gc_aged()` (`bronze.py:166-201`) + TTL flag (`bronze_ttl_gc`) | Single global TTL per source; no per-content-type policy. Not urgent. |
| #318 — orphan bronze | `reap_orphans()` (`bronze.py:95-119`) + maintenance scheduler stage | O(N) directory scan; fine to ~10⁵ files per source. |
| #319 — test discipline gap | F30 baseline at zero; F46/F47 lock composition; F48 enforces E2E | Connector-specific contract assertions vary in rigour. |
| #321 — single-txn batches | `ConnectorPipeline._process_batch` chunked-commit (`pipeline.py:209-240`) | `chunk_size` uniform; heavy-tailed extractors might want adaptive chunking. |
| #320 — cold-start opaque error | Warm in daemon thread; ColdStartMiddleware returns structured 503 during warm | Connection-refused window before uvicorn binds (~1s) needs client-side retry per the downstream tc-agent-zone PR. |

**Library-by-library cost/benefit (against the homegrown baseline):**

| Library | Verdict | Rationale |
|---|---|---|
| `sqlalchemy` | REJECT | Fights the "single sqlite3.Connection, caller owns commit" pattern that's the architectural backbone of `BronzeStore`/`CursorStore`/`DeadLetterStore`. Rewriting the transaction story across all six stores would be ~2000 LoC for marginal benefit. |
| `alembic` | DEFER | Schema lives in `kairix/core/db/schema.py:55` as `CREATE TABLE IF NOT EXISTS` + `kairix_meta` version row. Adopt when (a) we hit a destructive migration AND (b) we need rollback ordering. Neither holds today. |
| `tenacity` | **ACCEPT (scoped)** | Replaces ~30 LoC of hand-rolled retry at `kairix/connectors/dex_crm/client.py:205-233`. One dep, no transitives, ~1h refactor. Prevents future connectors reinventing the loop. Wave 2 hardening item. |
| `structlog` | REJECT | Current `logger.info("msg", extra={...})` gives us the same structured-fields affordance more cheaply than touching every `logger.*` call across 50+ files. |
| `pydantic` | REJECT | Boundary types are `@dataclass(frozen=True)` per F42. Pydantic's value-add (JSON ser+validation) isn't needed because boundaries are Python-to-Python. Would dilute F42. |

**Genuinely useful patterns the homegrown code could borrow without importing a framework:**

1. **Outbox table for entity-graph writes** (~80 LoC) — `EntityGraphSink.stage()` writes to SQLite; a separate process flushes to Neo4j. Add `(staged_at, dispatched_at, dispatch_attempts)` columns + dispatcher worker.
2. **Watermark-with-replay cursor** — `(cursor_token, replay_cursor_token, last_extractor_version)` triple lets extractor-bump re-extracts run behind the live cursor without disturbing it. Worth doing when F40 extractor versioning ships.
3. **Per-source circuit breaker** (~50 LoC) — current `DeadLetterStore.is_poisoned()` is per-ITEM. A SharePoint tenant returning 503 on every request currently fills the DLQ one item at a time. Short-circuit the whole batch when upstream is down.
4. **Content-hash chunk identity reuse** — `Chunk.content_hash` is already SHA-256 of the text. Skip re-embed when `content_hash` unchanged is a ~2x perf win on incremental syncs.

**Verdict: ADOPT.** None of the four OSS frameworks fit because the H1 raw-blob requirement is kairix-specific. The OSS ecosystem optimises for warehouse rows because that's where the money is.

### Was there a fifth candidate?

The stay-homegrown research agent scanned for: `singer-python` (same H1 fail), `airbyte-protocol-models` (just pydantic models, not a framework), `prefect`/`dagster` (orchestrators, wrong layer), `apache-beam` (overkill, JVM-rooted), `fivetran-connector-sdk` (cloud-coupled to Fivetran runtime). **No fifth candidate worth full evaluation.** The file-shaped re-extractable bronze model is kairix-specific.

---

## Section 7 — Recommendation

**ADOPT C12 (stay homegrown + selective `tenacity` adoption).** Evidence:

1. **All four OSS candidates fail H1 (raw binary blobs)** with the same shape: each is row-shaped for warehouse ingest. kairix's bronze model — original PDF/DOCX bytes on disk for re-extraction — is the WHOLE reason a framework would help, and none of them support it without a custom destination/parser that bypasses the framework's value-add.
2. **The four production issues (#316/#318/#319/#321) are already addressed** by Wave 1 chunking + the maintenance-stage work shipped this week. The homegrown ~1250 LoC framework is small enough that the maintenance cost is lower than the cost of forcing any candidate into a misfit.
3. **One targeted primitive adoption (`tenacity`)** removes ~30 LoC of hand-rolled HTTP retry and prevents future connectors reinventing the loop. One dep, no transitives, scoped to one file. Wave 2 hardening item.
4. **Three kairix-domain primitives are worth building in-house** as ~150 LoC additions to `kairix/core/connectors/`: outbox table for entity-graph writes, watermark-with-replay cursor (when F40 extractor versioning ships), per-source circuit breaker.

### Spike (per the ADR-018 lesson — small + reversible before ADR commitment)

The `tenacity` adoption is the SOLE library-import change this evaluation produces. The spike shape:

1. Replace `kairix/connectors/dex_crm/client.py:205-233` `_send_with_retry` with a `tenacity.retry` decorator carrying `wait_exponential` + `retry_if_exception_type(HTTPError)`.
2. The existing client unit tests stay green (the contract is exception-on-failure-after-N, which `tenacity.retry(reraise=True)` preserves).
3. Add `tenacity>=8` to `pyproject.toml` dependencies (not an extra — it's used in production by the dex_crm connector path).

If the spike lands cleanly, the same decorator pattern applies to any future connector's HTTP client (SharePoint Graph, Slack Web API, etc.) — but those connectors keep their current bespoke shape until a real need surfaces. **No framework adoption. No multi-wave commitment.**

### What this evaluation explicitly does NOT recommend

- Adopting dlt / Airbyte Python CDK / Meltano SDK / PyAirbyte — all rejected on H1 fail.
- Adopting sqlalchemy / alembic / structlog / pydantic — rejected per cost/benefit.
- Migrating any existing connector to a framework — there isn't a framework worth migrating to.
- Building a kairix "connector framework" that wraps another framework — that's worse than either pure approach.

### Engineering pattern reinforced

This evaluation honoured the ADR-018 "spike first, ADR second" lesson by:

1. Writing requirements as testable criteria BEFORE surveying candidates
2. Running parallel research per candidate against the same questions
3. Letting the evidence converge rather than picking based on familiarity
4. Allowing "stay homegrown" as a legitimate outcome rather than treating framework adoption as a goal
5. Producing a SMALL spike scope (`tenacity` only) instead of a multi-wave migration plan

The whole evaluation cost ~3 hours and produced a defensible answer. The previous ADR-018 cost ~2 days of investigation work to reach the same "homegrown wins" conclusion via three failed pivots. **This is the methodology going forward** for any "should we adopt OSS library X" question.

---

## Process notes (for the engineering record)

- This evaluation exists because ADR-018 was written with high conviction but inadequate research. The dlt prototype work surfaced three negative-fit findings (one per layer) before the wrong-library conclusion landed.
- The "spike first, ADR second" lesson from ADR-018 applies here: Section 6 evidence should include actual code reading + small experimental runs where feasible, not just "the docs say X".
- The user gate: no implementation work proceeds until the user has reviewed Sections 6 + 7 and chosen.
- The evaluation explicitly allows "stay homegrown" as the chosen option — adopting an external framework is not a goal in itself. The goal is to address the production issues (#316/#318/#321) more durably than the homegrown shape does, and Wave 1 chunking already addresses them.
