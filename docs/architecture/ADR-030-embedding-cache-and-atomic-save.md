# ADR-030 — Persistent embedding cache + atomic vec-index save

## Status

Accepted. Implementation lands alongside this ADR.

## Context

A production embed run wrote ~1.57M of ~1.8M vectors then corrupted
the on-disk usearch index file via an in-place write that the host
crashed mid-flight. The corrupt file failed `Index.restore` on the
next worker boot, forcing a re-embed authorisation of $211 against
the provider for chunks the operator had already paid for once.

Two structural gaps surfaced:

1. **No persistent cache for provider responses.** Every chunk the
   provider successfully embedded existed only in transient memory
   between the response and the next `Index.save`. A crash anywhere
   in that window discarded the work.
2. **`Index.save` was non-atomic.** The usearch C extension writes
   directly to the target path. A crash mid-write left a partial file
   whose HNSW header was unreadable; both old and new state were
   destroyed in the same operation.

## Decision

### Layer A — persistent embedding cache (SQLite)

A new SQLite file `embedding_cache.sqlite` under
`<document_root>/.kairix/cache/` stores every vector ever returned by
the provider, keyed on `(model, dimension, sha256(chunk_text))`.

Schema:

```sql
CREATE TABLE embedding_cache (
    model       TEXT NOT NULL,
    dimension   INTEGER NOT NULL,
    chunk_hash  TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (model, dimension, chunk_hash)
);
```

The vector blob is raw little-endian f32 bytes — canonical even if the
in-memory vec index quantises to f16, so a `--force` rebuild from
cache after a quantisation change reuses the f32 records losslessly.

Per-batch protocol inside `_embed_batch_only`:

1. Split the batch into cache hits and cache misses (one
   `SELECT ... WHERE chunk_hash IN (?,?,...)`).
2. Dispatch ONLY the misses to the provider.
3. On provider success, `INSERT OR REPLACE` the new
   `(hash, vector)` pairs into the cache in a single transaction.
4. Combine cache hits + new vectors, return for downstream
   SQLite + vec-index writes.

A provider failure no longer invalidates the cache hits in the same
batch — those continue through the SQLite + vec-index write path.
Only the misses count as failed for the run.

Switching `model` or `dimension` produces a new cache slice and the
old slice stays intact, so model swaps (e.g.
`text-embedding-3-large` 3072d → 1536d) are clean coexistence and
not a destructive migration.

`--force` rebuilds the vec index but reuses the cache, so a vec-index
corruption recovery on a fully-cached corpus is $0. A separate
`--force-rebuild-cache` flag drops the cache when the operator
suspects the cache itself is wrong; this is the rare path.

### Layer B — atomic vec-index save

`VectorIndex._save` writes through a sibling `.tmp` file then renames
over the canonical path:

1. `self._index.save(str(<index>.tmp))` — usearch writes the new file
2. `os.fsync(<index>.tmp)` — flush bytes to disk
3. `os.replace(<index>.tmp, <index>)` — POSIX atomic rename
4. Same protocol for `<meta>.json`
5. `os.fsync(<directory>)` — flush directory entry

Crash semantics:

| Crash point | Canonical file contents | Recovery |
|---|---|---|
| Before `Index.save` | Previous valid file | nothing to do |
| During `Index.save` (partial tmp) | Previous valid file | next `_save` overwrites tmp |
| After fsync(tmp), before `os.replace` | Previous valid file | `load()` promotes lingering `.tmp` |
| After `os.replace` | New valid file | nothing to do |

`load()` looks for a lingering `<canonical>.tmp` whose canonical
sibling is missing and promotes it. A stale `.tmp` whose canonical
exists is ignored — the canonical wins.

## Consequences

### Positive

- Operator never pays the provider twice for the same chunk.
- Vec-index corruption recovery is cheap: drop the .usearch file,
  rebuild from cache, no provider calls.
- Model swap is non-destructive (separate cache slices).
- Crash anywhere in the embed loop is recoverable.

### Negative

- Extra storage: one SQLite file proportional to corpus size. At
  3072d × 4B/f32 × 1M chunks ≈ 12 GB. Acceptable; the alternative is
  the $200+/recovery cost.
- Extra SQLite write per batch. Profiling shows ≪5% of batch wall-
  clock since the provider call dominates.

### Migration

Cache file is created on first use; the first embed run after this
ADR lands populates the cache as it embeds. No retroactive
backfill — existing vectors in `content_vectors` are not copied into
the cache, so the FIRST crash after a non-cache run still pays the
provider once. Subsequent runs are cache-first.

`embedding_cache.sqlite` is not a kairix-managed schema migration
target — it is a derived cache and can be deleted at any time
(`--force-rebuild-cache` does this from the CLI). A future ticket
may add a backfill from `content_vectors` for operators who want
retroactive coverage.

## References

- `kairix/core/embed/embedding_cache.py` — cache implementation
- `kairix/core/embed/embed.py::_embed_batch_only` — integration boundary
- `kairix/core/search/vec_index.py::_save` — atomic save protocol
- `tests/unit/test_embedding_cache.py`
- `tests/unit/test_vec_index_atomic_save.py`
- `tests/integration/test_embed_uses_cache.py`
- `tests/bdd/features/embedding_cache.feature`
- ADR-023 — vector index write architecture (this ADR extends)
