# ADR-035 — SearchPipeline async-ification assessment

**Status:** Investigation. Decision: defer.
**Owner:** retrieval / performance
**Related:** #397 (MCP Performance Sprint Workstream C), commit `108d12fe`
(`_SerializingSqliteConnection` proxy fix), R8 uvicorn timeouts
(`48910f9f`, `a46cac7a`, `748a4e61`), Phase 2 cache landings
(`c99632ec` … `582d9f73`).

## Context

`SearchPipeline._dispatch_backends`
(`kairix/core/search/pipeline.py:266-294`) currently runs BM25 (SQLite
FTS5) then vector (in-memory HNSW + SQLite metadata fetch) **sequentially**:

```python
bm25_results = self.bm25.search(...)        # ~50-200ms
vec_results, _ = self._dispatch_vector(...)  # ~30-50ms
```

Each backend already isolates its own failure path (BM25 raises → empty
list; vector raises → empty list + `vec_failed=True`); the two could in
principle run concurrently and the pipeline would gather their results
in parallel. This ADR records the analysis of whether that's worth
doing today.

The Azure embed call that produces the query vector (250-1000ms HTTP
tail) already happens **before** `_dispatch_backends` (it's part of
`vector.search()`'s internal flow, called once and reused). The embed
HTTP latency is therefore **not** the parallelisation target here —
only the local BM25 + local ANN/SQLite-metadata-fetch costs are.

## Investigation

### Latency breakdown (production probes, current state)

From `kairix/quality/probe` data captured against the deployed instance
in the week leading into this ADR:

| Stage | p50 | p99 | Notes |
|---|---|---|---|
| BM25 | ~50ms | ~200ms | SQLite FTS5; CPU-bound rank, tight loop |
| vector (incl. embed_http) | ~280ms | ~1000ms | embed_http dominates |
| vector_ann (local) | ~30ms | ~50ms | HNSW search + SQLite metadata fetch |
| fusion | <5ms | <10ms | RRF + intent weighting |

The sequentially-blocking portion that concurrent execution could
overlap is `bm25` (~50-200ms) with `vector_ann` (~30-50ms). Best-case
savings from parallel execution:

  savings ≈ min(bm25_time, vector_ann_time) ≈ 30-50ms per query

That's the optimistic case where the two backends share no resource
contention. In practice they both touch SQLite (BM25 via FTS5, vector
via the chunk metadata join), and we just landed
`_SerializingSqliteConnection` (`108d12fe`) precisely because shared
sqlite Connection objects can't be safely concurrent under threads
without serialisation. Adding more threaded concurrency over the same
DB is exactly the failure class that proxy was built to fix.

### Required architectural changes

Going async-await for the dispatch stage would require one of:

1. **Thread-pool offload.** Wrap each backend's `.search()` in
   `asyncio.to_thread` and `asyncio.gather` the two. This is the
   pattern Workstream C C1 used for the brief pipeline (see
   `kairix/agents/briefing/pipeline.py::_fetch_sources_async`). It
   leaves the sync backend code unchanged but adds a layer of
   thread scheduling per query.

2. **Async DB driver.** Replace `sqlite3` with `aiosqlite` (or run
   SQLite under a thread pool with explicit serialisation). This is
   invasive — every `cursor.execute(...).fetchall()` site under
   `kairix/core/search/` would need to be rewritten.

3. **Native async backends.** Neither sqlite3-FTS5 nor usearch ship
   async APIs. We would have to either thread-pool-offload (option 1)
   or accept blocking calls inside an async function (anti-pattern).

Option 1 is the realistic path. It costs ~30-50ms savings per query in
exchange for adding asyncio scheduling overhead (estimated <5ms per
gather), an additional thread context switch per backend, and a new
class of concurrent-sqlite-access bug that needs `_SerializingSqliteConnection`-style
mitigation if the SQLite handle is shared (which it currently is —
`build_search_pipeline` constructs one connection per pipeline).

### Resource contention risk

The two backends share:

- A single SQLite connection (BM25 reads `chunks_fts`, vector reads
  `content_vectors` + the chunk metadata join). Concurrent access
  triggered InterfaceError until `_SerializingSqliteConnection` landed;
  even with serialisation, the win shrinks because the serialiser
  forces sequential access at the driver level.
- The Python GIL. The BM25 rank-and-score loop is CPU-bound under
  the GIL, so parallel execution under `asyncio.to_thread` interleaves
  but doesn't truly parallelise CPU work.

Net realistic win is much smaller than the 30-50ms upper bound — likely
10-20ms per query after subtracting the serialiser cost + scheduler
overhead.

## Decision

**Defer.** Not worth the ~10-20ms median latency reduction in exchange
for:

- A new class of concurrent-SQLite-access bugs (we just shipped the
  proxy fix for the existing case).
- Either invasive async-await rewrites (option 2 / 3) or another layer
  of thread scheduling (option 1) for marginal win.
- Reduced reasoning clarity: backend-code stays synchronous everywhere
  except the dispatch layer, producing a mixed sync/async surface
  that's easy to mis-call.

The brief pipeline benefitted from `asyncio.gather` because each source
runs **independent file/SQLite/HTTP I/O with no shared mutable state**
and the wall-clock asymmetry was large (5 cheap sources hiding behind
one slow hybrid_search). The search dispatch case is the inverse: two
backends touching the same SQLite handle with similar wall-clocks. The
asymmetry that makes parallelisation pay isn't here.

## When to revisit

Re-evaluate this ADR if `mcp_call_log` data (now flowing per
`async_tool_handler` instrumentation) shows the `bm25` or `vector_ann`
stages persistently hitting p99 >= 200ms — that would mean the
sequential cost has materially grown and the savings calculation
flips. The latency probe at `kairix/quality/probe/` already records the
per-stage split (see `_dispatch_backends`' `stages` kwarg) so the
trigger metric is observable.

Also re-evaluate if:

- A retrieval-side workload arrives that fans out across **more than
  two** backends (e.g. a fact retriever AND a graph retriever AND
  BM25 AND vector). Three+ parallelisable backends shifts the
  calculation because the gather amortises across more concurrent work.
- The SQLite handle stops being shared (i.e. each backend gets its
  own connection from a pool) — that removes the
  `_SerializingSqliteConnection` ceiling on concurrent gains.

## Consequences

### Accepted

- `SearchPipeline._dispatch_backends` stays synchronous. BM25 then
  vector, in that order. No code change from this ADR.
- Async lives only at the MCP tool boundary
  (`kairix/agents/mcp/errors.py::async_tool_handler` →
  `asyncio.to_thread`). Per-query work runs sync inside a worker
  thread; concurrent `/mcp` requests are scheduled onto the event
  loop and don't block each other.
- This is the supported pattern for "make MCP feel concurrent" — the
  per-call work stays sync; the **between-call** scheduling is async.

### Rejected (and why)

- Option 1 (thread-pool offload of dispatch): see the resource
  contention + GIL discussion above; the realistic win is too small.
- Option 2 (aiosqlite): too invasive for the latency saving;
  unwarranted refactor of the entire core/search tree.
- Option 3 (native async backends): not feasible — neither
  sqlite3-FTS5 nor usearch ship async APIs.

## References

- `kairix/core/search/pipeline.py::_dispatch_backends`
- `kairix/agents/mcp/errors.py::async_tool_handler` — the supported
  async boundary
- `kairix/agents/briefing/pipeline.py::_fetch_sources_async` — the
  asymmetric-fan-out case where async.gather **was** worth it (#397 C1)
- commit `108d12fe` — `_SerializingSqliteConnection` proxy (the
  contention class this ADR avoids creating more of)
- `docs/agents/MCP-LATENCY-EXPECTATIONS.md` — per-tool latency budgets
- ADR-026 — observability primitives (proposed F76 / F77 will catch
  some of the failure modes a future async dispatch would create)
