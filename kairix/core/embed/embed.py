"""
Core embedding logic — fetches vectors from Azure OpenAI and writes to kairix's SQLite.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .date_extract import extract_chunk_date
from .deps import EmbedDependencies
from .embedding_cache import EmbeddingCache, hash_chunk_text
from .schema import EMBED_VECTOR_DIMS, SchemaVersionError

logger = logging.getLogger(__name__)

# Azure OpenAI
DEFAULT_DEPLOYMENT = "text-embedding-3-large"
DEFAULT_DIMS = EMBED_VECTOR_DIMS
DEFAULT_BATCH_SIZE = 250  # Balanced: large enough for throughput, small enough to avoid Azure 429s
MAX_RETRIES = 6  # used by OpenAI SDK max_retries

# Parallel-batches ceiling. Above this, Azure 429 / quota-burn risk dominates the
# throughput gain and the worker can saturate downstream search-side reads off
# the same SQLite. Operators wanting more should re-shape the corpus or split
# the catch-up across multiple worker hosts.
# See docs/operations/runbooks/worker-memory-and-swap.md for sizing guidance.
MAX_PARALLEL_BATCHES = 10
DEFAULT_PARALLEL_BATCHES = 1  # default-safe: today's serial behaviour

# F17 — chunk_date appears as a dict key in both producer and consumer paths;
# extract so renames hit a single edit site.
_KEY_CHUNK_DATE = "chunk_date"

# Chunking — mirrors kairix's CHUNK_SIZE_TOKENS / CHUNK_OVERLAP_TOKENS
CHUNK_SIZE_CHARS = 3600  # ~900 tokens at 4 chars/token
CHUNK_OVERLAP_CHARS = 200


# ── Encoding ──────────────────────────────────────────────────────────────────


def build_hash_seq(content_hash: str, seq: int) -> str:
    """Build the hash_seq key used by usearch index metadata."""
    return f"{content_hash}_{seq}"


# ── Chunking ──────────────────────────────────────────────────────────────────


def _find_break_point(text: str, pos: int, end: int, chunk_size: int) -> int:
    """Find the best break point within a chunk boundary.

    Prefers paragraph breaks, then sentence breaks, then falls back to the raw end.
    """
    if end >= len(text):
        return end

    half = pos + chunk_size // 2
    para_break = text.rfind("\n\n", pos, end)
    if para_break > half:
        return para_break + 2

    sent_break = max(text.rfind(". ", pos, end), text.rfind(".\n", pos, end))
    if sent_break > half:
        return sent_break + 1

    return end


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[dict[str, Any]]:
    """
    Split text into overlapping chunks. Returns list of {seq, pos, text}.
    Mirrors kairix's chunkDocument() logic for consistency.
    Tries to split on paragraph boundaries first, falls back to char splits.
    """
    if len(text) <= chunk_size:
        return [{"seq": 0, "pos": 0, "text": text}]

    chunks = []
    pos = 0
    seq = 0

    while pos < len(text):
        end = min(pos + chunk_size, len(text))
        end = _find_break_point(text, pos, end, chunk_size)

        chunk_text_val = text[pos:end].strip()
        if chunk_text_val:
            chunks.append({"seq": seq, "pos": pos, "text": chunk_text_val})
            seq += 1

        pos = end - overlap if end < len(text) else len(text)

    return chunks


# ── Azure API ─────────────────────────────────────────────────────────────────


def _get_azure_config() -> tuple[str, str, str]:  # pragma: no cover  # prod lazy default; deps injection
    """
    Read embed API config via ``get_credentials("embed")``. Supports Azure,
    OpenRouter, or any OpenAI-compatible endpoint.

    Production lazy default for ``EmbedDependencies.get_azure_config``;
    tests inject a fake callable via ``run_embed(deps=...)`` so this
    function is never test-reachable. The credential resolution itself
    is exercised in ``tests/test_credentials.py``.

    Raises OSError when credentials cannot be resolved.
    """
    from kairix.credentials import Credentials, get_credentials

    creds = get_credentials("embed")
    if not isinstance(creds, Credentials):
        raise OSError("Embed credentials not available.")
    api_key = creds.api_key
    endpoint = creds.endpoint
    deployment = creds.model or DEFAULT_DEPLOYMENT

    if not api_key:
        raise OSError(
            "KAIRIX_LLM_API_KEY / KAIRIX_EMBED_API_KEY not set. "
            "Set the env var, add to secrets file, or configure Key Vault."
        )
    if not endpoint:
        raise OSError(
            "KAIRIX_LLM_ENDPOINT / KAIRIX_EMBED_ENDPOINT not set. "
            "Set the env var, add to secrets file, or configure Key Vault."
        )

    # Normalise endpoint — strip trailing slash, we'll add the path
    endpoint = endpoint.rstrip("/")
    return api_key, endpoint, deployment


def preflight_check(
    api_key: str,
    endpoint: str,
    deployment: str,
    *,
    client: Any | None = None,
) -> int:
    """
    Verify the embedding API is reachable with a single-item embed call.
    Returns embedding dimensions on success, raises on failure.
    Does NOT touch the DB — safe to call before any writes.

    ``client`` is an injection seam for tests — pass an OpenAI-compatible
    fake whose ``embeddings.create(...)`` returns a response with a
    ``.data[0].embedding`` list. Production callers leave it as ``None`` so
    the real client is built lazily via ``make_openai_client``.
    """
    if client is None:  # pragma: no cover  # prod lazy default; tests inject client=fake
        from kairix.credentials import make_openai_client

        client = make_openai_client(api_key, endpoint, max_retries=2, timeout=30.0)
    response = client.embeddings.create(
        model=deployment,
        input=["preflight check"],
        dimensions=DEFAULT_DIMS,
    )
    dims = len(response.data[0].embedding)
    logger.info("Preflight OK — dims=%d", dims)
    return dims


# Reuse a single SDK client across all batches. Connection pooling and the
# SDK's internal rate-limiter state carry over between calls, which prevents
# redundant Retry-After waits when the server quota is actually available.
_embed_client = None
_embed_client_key: tuple[str, str] = ("", "")


def _get_embed_client(api_key: str, endpoint: str) -> Any:  # pragma: no cover  # prod-only module cache
    """Return a cached OpenAI client. Creates a new one if credentials change.

    Production-only — every test that exercises ``embed_batch`` injects a
    ``client=`` kwarg, bypassing this cache. The cache is reachable only
    when the production lazy default fires (``client is None``).
    """
    from kairix.credentials import make_openai_client

    global _embed_client, _embed_client_key
    key = (api_key, endpoint)
    if _embed_client is not None and _embed_client_key == key:
        return _embed_client

    _embed_client = make_openai_client(api_key, endpoint, max_retries=MAX_RETRIES, timeout=60.0)
    _embed_client_key = key
    return _embed_client


def embed_batch(
    texts: list[str],
    api_key: str,
    endpoint: str,
    deployment: str,
    dims: int = DEFAULT_DIMS,
    *,
    client: Any | None = None,
) -> list[list[float]]:
    """
    Embed a batch of texts via Azure OpenAI using the OpenAI SDK.

    Client is reused across batches for connection pooling and rate-limiter
    state persistence. The SDK handles retry with exponential backoff and
    Retry-After headers automatically.

    ``client`` is an injection seam for tests — pass an OpenAI-compatible
    fake whose ``embeddings.create(...)`` returns a response with ``.data``
    items carrying ``.index`` and ``.embedding``. Production callers leave
    it as ``None`` so the cached production client is reused.

    Returns list of float vectors in same order as input texts.
    Raises on persistent failures after SDK retries are exhausted.
    On BadRequestError (batch too large), splits and recurses.
    """
    import openai

    if not texts:
        return []

    if client is None:  # pragma: no cover  # prod lazy default; tests inject client=fake
        client = _get_embed_client(api_key, endpoint)

    try:
        response = client.embeddings.create(
            model=deployment,
            input=texts,
            dimensions=dims,
        )
        results = sorted(response.data, key=lambda x: x.index)
        return [list(r.embedding) for r in results]
    except openai.BadRequestError:
        if len(texts) == 1:
            raise
        mid = len(texts) // 2
        logger.warning(
            "BadRequestError on batch of %d — splitting into %d + %d",
            len(texts),
            mid,
            len(texts) - mid,
        )
        left = embed_batch(texts[:mid], api_key, endpoint, deployment, dims, client=client)
        right = embed_batch(texts[mid:], api_key, endpoint, deployment, dims, client=client)
        return left + right


# ── DB writes ─────────────────────────────────────────────────────────────────


def stage_embedding(
    db: sqlite3.Connection,
    content_hash: str,
    seq: int,
    pos: int,
    _vector: list[float],
    model: str,
    embedded_at: int,
    chunk_date: str | None = None,
) -> None:
    """
    Write chunk metadata to content_vectors.

    content_vectors is a normal SQLite table and supports INSERT OR REPLACE.
    Vectors are written to the usearch ANN index separately via
    _update_usearch_index() at batch commit time.

    _vector is accepted for call-site compatibility (callers pass it
    positionally) but is not used here — vectors are written to the
    usearch ANN index by the caller.

    chunk_date is the ISO date extracted from the document (frontmatter or path).
    It is the same for all chunks of a given document (document-level property).
    """
    db.execute(
        "INSERT OR REPLACE INTO content_vectors"
        " (hash, seq, pos, model, embedded_at, chunk_date) VALUES (?, ?, ?, ?, ?, ?)",
        (content_hash, seq, pos, model, embedded_at, chunk_date),
    )


# ── Batch generator ───────────────────────────────────────────────────────────


def batched(items: list[Any], size: int) -> Generator[list[Any], None, None]:
    """Yield successive batches of `size` from `items`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── usearch index update ─────────────────────────────────────────────────────


def open_usearch_index_for_paths(
    *,
    index_path: Path,
    meta_path: Path,
    db_path: Path,
) -> Any:
    """Open (or create) the usearch ANN index at the supplied paths.

    Uses :meth:`VectorIndex.load_or_recreate` so a corrupt on-disk file
    auto-recovers to a fresh empty index instead of returning None
    (the silent-no-op bug seen 2026-05-31 — see
    tests/e2e/test_vec_index_corrupt_recovery_e2e.py).

    Always returns a usable VectorIndex. Callers don't have to handle
    None and don't have to inspect file state — the recovery happened
    here.
    """
    from kairix.core.search.vec_index import VectorIndex

    # GH #352 — worker is the only mutate-capable VectorIndex caller.
    # read_only=False (default, named explicitly here for clarity)
    # loads the index fully into memory at .load() time so the first
    # add_vectors() call has no first-write conversion path to hit.
    idx = VectorIndex(index_path=index_path, meta_path=meta_path, db_path=db_path, read_only=False)
    count, status = idx.load_or_recreate()
    logger.info(
        "vec_index: opened at %s (vectors=%d, status=%s)",
        index_path,
        count,
        status,
    )
    return idx


def _open_usearch_index() -> Any:  # pragma: no cover  # prod lazy default; deps injection
    """Production default for ``EmbedDependencies.open_usearch_index``.

    Thin wrapper that resolves canonical paths from ``KairixPaths`` and
    delegates to :func:`open_usearch_index_for_paths`. Tests inject
    :func:`open_usearch_index_for_paths` directly with tmp_path-scoped
    paths so the same recovery logic is exercised under integration +
    E2E coverage (the prior version had ``# pragma: no cover`` on the
    whole body and the recovery path was untested — that's why the
    production silent-None bug shipped).

    Returns ``None`` (skipping the usearch open + per-batch add) only
    when ``worker_writes_vec_index()`` is False — see #335 for context.
    """
    from kairix.paths import worker_writes_vec_index

    if not worker_writes_vec_index():
        logger.info(
            "vec_index: worker write disabled (KAIRIX_WORKER_WRITES_VEC_INDEX!=1) — "
            "SQLite content_vectors continues to advance; usearch on-disk index will "
            "drift (see #335 for rebuild plan)."
        )
        return None
    from kairix.paths import db_path as get_db_path

    db_p = get_db_path()
    return open_usearch_index_for_paths(
        index_path=db_p.parent / "vectors.usearch",
        meta_path=db_p.parent / "vectors.meta.json",
        db_path=db_p,
    )


# ── Extracted helpers (run_embed decomposition) ─────────────────────────────


def _gather_pending_chunks(
    db: sqlite3.Connection,
    force: bool,
    doc_root: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """Gather chunks that need embedding.

    In force mode, clears existing vectors and selects all documents.
    In incremental mode, selects only documents not yet embedded.

    Returns (all_chunks, document_count) where each chunk is a dict with
    keys: hash, seq, pos, text, path, chunk_date.
    """
    if force:
        logger.info("--force: clearing all existing vectors")
        db.execute("DELETE FROM content_vectors")
        db.commit()

    # GH #329 — pull documents.source_modified_at so we can fall back to
    # connector-supplied envelope timestamps (SharePoint lastModifiedDateTime,
    # GitHub commit date, etc.) when the body-text extractor can't find a
    # frontmatter / path-derived date. Closes the 98% temporal-boost coverage
    # gap reported by the chunk_date_populated onboard check. Older test
    # fixtures use a minimal documents schema without this column — the
    # PRAGMA guard keeps them working while production gets the fallback.
    _doc_cols = {row[1] for row in db.execute("PRAGMA table_info(documents)").fetchall()}
    smt_select = "d.source_modified_at" if "source_modified_at" in _doc_cols else "NULL AS source_modified_at"

    if force:
        rows = db.execute(f"""
            SELECT c.hash, c.doc, d.path, {smt_select}
            FROM content c
            JOIN documents d ON c.hash = d.hash
            WHERE d.active = 1
              AND c.doc IS NOT NULL
              AND length(c.doc) > 0
        """).fetchall()
    else:
        rows = db.execute(f"""
            SELECT c.hash, c.doc, d.path, {smt_select}
            FROM content c
            JOIN documents d ON c.hash = d.hash
            LEFT JOIN content_vectors v ON c.hash = v.hash AND v.seq = 0
            WHERE v.hash IS NULL
              AND d.active = 1
              AND c.doc IS NOT NULL
              AND length(c.doc) > 0
        """).fetchall()

    all_chunks: list[dict[str, Any]] = []
    for content_hash, body, path, source_modified_at in rows:
        # Body-text extractor first (matches Obsidian frontmatter / path
        # patterns); fall back to connector envelope timestamp (#329).
        doc_date = extract_chunk_date(body, path, document_root=doc_root) or source_modified_at
        for chunk in chunk_text(body):
            all_chunks.append(
                {
                    "hash": content_hash,
                    "seq": chunk["seq"],
                    "pos": chunk["pos"],
                    "text": chunk["text"],
                    "path": path,
                    _KEY_CHUNK_DATE: doc_date,
                }
            )

    return all_chunks, len(rows)


def _split_batch_against_cache(
    batch: list[dict[str, Any]],
    cache: EmbeddingCache | None,
    deployment: str,
    dims: int,
) -> tuple[
    list[tuple[dict[str, Any], list[float]]],
    list[dict[str, Any]],
]:
    """Split a batch into ``(cache_hits, cache_misses)``.

    Returns ``(hits, misses)`` where ``hits`` is a list of
    ``(chunk, vector_as_list_of_floats)`` pairs already populated from
    the persistent cache, and ``misses`` is the subset of ``batch`` that
    must go to the embed provider. With ``cache=None`` every chunk is a
    miss.

    Each chunk's ``text_hash`` is computed once here and stashed on the
    chunk dict so subsequent cache writes reuse the same hash without
    re-hashing.
    """
    if cache is None:
        for chunk in batch:
            chunk.setdefault("text_hash", hash_chunk_text(chunk["text"]))
        return [], list(batch)

    hashes: list[str] = []
    for chunk in batch:
        text_hash = chunk.setdefault("text_hash", hash_chunk_text(chunk["text"]))
        hashes.append(text_hash)

    cached = cache.get_many(deployment, dims, hashes)
    hits: list[tuple[dict[str, Any], list[float]]] = []
    misses: list[dict[str, Any]] = []
    for chunk in batch:
        cached_vec = cached.get(chunk["text_hash"])
        if cached_vec is not None:
            hits.append((chunk, cached_vec.tolist()))
        else:
            misses.append(chunk)
    return hits, misses


def _embed_batch_only(
    batch: list[dict[str, Any]],
    batch_idx: int,
    api_key: str,
    endpoint: str,
    deployment: str,
    dims: int,
    embed_batch_fn: Callable[..., list[list[float]]],
    *,
    cache: EmbeddingCache | None = None,
) -> tuple[list[dict[str, Any]], list[list[float]], list[dict[str, Any]], bool]:
    """Embed a single batch via the provider call only — no DB / index writes.

    Returns ``(matched_chunks, vectors, unaccounted_chunks, provider_failed)``.

    Cache-first: any chunk whose ``(deployment, dims, sha256(text))``
    tuple is present in the persistent cache short-circuits the provider
    call. The provider only sees the cache misses. After the provider
    response the new vectors are written to the cache BEFORE the caller
    persists them anywhere else — a crash between provider response and
    SQLite write leaves the cache with the vectors so the next run finds
    them and skips the provider.

    Split from the persistence step so multiple provider calls can run in
    parallel threads while the SQLite + usearch writes stay serialised
    on a single writer thread.

    ``provider_failed=True`` means the provider call raised — caller marks
    every miss in the batch as failed (cache hits are kept). ``unaccounted``
    are chunks the backend returned no vector for (partial 5xx / rate-limit).
    """
    hits, misses = _split_batch_against_cache(batch, cache, deployment, dims)

    miss_vectors: list[list[float]] = []
    provider_failed = False
    if misses:
        texts = [c["text"] for c in misses]
        try:
            miss_vectors = embed_batch_fn(texts, api_key, endpoint, deployment, dims)
        except (RuntimeError, KeyError, ValueError, OSError):
            logger.exception(
                "Batch %d failed — logging %d chunks as failed",
                batch_idx,
                len(misses),
            )
            provider_failed = True

    if provider_failed:
        # Cache hits are still good to surface — they cost nothing and
        # the production goal is to skip provider calls on chunks we
        # already paid for. Only the misses count as failed here.
        hit_chunks = [chunk for chunk, _ in hits]
        hit_vectors = [vec for _, vec in hits]
        return hit_chunks, hit_vectors, [], True

    matched_misses = misses[: len(miss_vectors)]
    unaccounted = list(misses[len(miss_vectors) :])
    if unaccounted:
        logger.error(
            "Batch %d: backend returned %d vectors for %d texts — %d chunks unaccounted",
            batch_idx,
            len(miss_vectors),
            len(misses),
            len(unaccounted),
        )

    if cache is not None and matched_misses:
        try:
            cache.put_many(
                deployment,
                dims,
                ((c["text_hash"], v) for c, v in zip(matched_misses, miss_vectors, strict=True)),
            )
        except sqlite3.Error:
            logger.exception("embedding_cache: put_many for batch %d failed (continuing)", batch_idx)

    matched = [chunk for chunk, _ in hits] + matched_misses
    vectors = [vec for _, vec in hits] + list(miss_vectors)
    if hits:
        logger.info(
            "Batch %d: %d/%d cache hits (saved %d provider calls)",
            batch_idx,
            len(hits),
            len(batch),
            len(hits),
        )
    return matched, vectors, unaccounted, False


def _embed_and_store_batch(
    batch: list[dict[str, Any]],
    batch_idx: int,
    db: sqlite3.Connection,
    vec_index: Any,
    api_key: str,
    endpoint: str,
    deployment: str,
    dims: int,
    now: int,
    save_interval: int,
    embed_batch_fn: Callable[..., list[list[float]]],
    *,
    cache: EmbeddingCache | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Embed a single batch and write results to DB + usearch index.

    Returns (embedded_count, failed_chunks) for this batch.

    ``embed_batch_fn`` is the embedding callable — production passes the
    Azure-backed ``embed_batch``; tests pass a fake. No default — caller
    constructs the right callable at the boundary (the ``run_embed``
    function below threads ``deps.embed_batch`` through). Removing the
    legacy ``= None`` default closes the F6 test-seam violation.

    Kept for the ``--parallel 1`` (serial) path and for tests that
    exercise the per-batch boundary directly.
    """
    matched, vectors, unaccounted, azure_failed = _embed_batch_only(
        batch, batch_idx, api_key, endpoint, deployment, dims, embed_batch_fn, cache=cache
    )
    if azure_failed:
        # Cache hits already in `matched` are real and should still
        # persist. The miss-set is the failed set.
        failed = [c for c in batch if c not in matched]
    else:
        failed = list(unaccounted)

    if not matched:
        return 0, failed

    try:
        _stage_batch_embeddings(db, matched, vectors, deployment, now)
    except sqlite3.Error:
        logger.exception("DB write for batch %d failed", batch_idx)
        return 0, list(batch)

    _add_batch_to_vec_index(vec_index, matched, vectors, batch_idx, save_interval)
    return len(matched), failed


def _stage_batch_embeddings(
    db: sqlite3.Connection,
    matched: list[dict[str, Any]],
    vectors: list[list[float]],
    deployment: str,
    now: int,
) -> None:
    """Stage all matched chunk embeddings in a single SQLite transaction.

    Extracted from ``_embed_and_store_batch`` to keep that function under
    F16's cognitive-complexity ceiling — the nested ``with db:`` + per-chunk
    ``stage_embedding`` loop + per-vector index-write block nested above 15.
    """
    with db:
        for chunk, vector in zip(matched, vectors, strict=True):
            stage_embedding(
                db,
                chunk["hash"],
                chunk["seq"],
                chunk["pos"],
                vector,
                deployment,
                now,
                chunk_date=chunk.get(_KEY_CHUNK_DATE),
            )


def _add_batch_to_vec_index(
    vec_index: Any,
    matched: list[dict[str, Any]],
    vectors: list[list[float]],
    batch_idx: int,
    save_interval: int,
) -> None:
    """Append batch vectors to the usearch ANN index, optionally checkpointing.

    No-op when ``vec_index`` is None. Any exception is logged and
    swallowed — usearch failures must not break the SQLite write path.
    Extracted from ``_embed_and_store_batch`` for the same F16 reason as
    ``_stage_batch_embeddings`` above.
    """
    if vec_index is None:
        return
    try:
        batch_hash_seqs = [build_hash_seq(c["hash"], c["seq"]) for c in matched]
        vec_index.add_vectors(batch_hash_seqs, vectors)
        if (batch_idx + 1) % save_interval == 0:
            vec_index.save()
    except Exception:
        logger.exception("usearch batch %d failed", batch_idx)


def _save_index_checkpoint(vec_index: Any) -> None:
    """Final save of the usearch ANN index to disk."""
    if vec_index is None:
        return
    try:
        vec_index.save()
        logger.info("usearch: saved index with %d vectors", len(vec_index))
    except Exception as e:
        logger.exception("usearch final save failed: %s", e)


# ── Parallel orchestration ───────────────────────────────────────────────────


def _validate_parallel(parallel: int) -> None:
    """Reject out-of-range ``--parallel`` values with an F21-shaped affordance.

    Default is 1 (serial — today's behaviour, no-op for operators who
    don't opt in). Range 1..MAX_PARALLEL_BATCHES. Above that the Azure
    quota-burn / 429-rate risk dominates the throughput gain — see the
    runbook.
    """
    if parallel < 1 or parallel > MAX_PARALLEL_BATCHES:
        raise ValueError(
            f"--parallel {parallel} out of range [1..{MAX_PARALLEL_BATCHES}]. "
            "fix: pass --parallel between 1 (serial, default) and "
            f"{MAX_PARALLEL_BATCHES} — higher values risk Azure rate-limit (429) "
            "burn and saturate downstream search-side reads on the same SQLite. "
            "next: pick --parallel 3 for the default VM size, --parallel 5 for "
            "a catch-up cycle on a worker sized per the runbook. "
            "run: see docs/operations/runbooks/worker-memory-and-swap.md "
            "for sizing guidance per corpus + per VM."
        )


def _persist_batch_result(
    matched: list[dict[str, Any]],
    vectors: list[list[float]],
    batch_idx: int,
    db: sqlite3.Connection,
    vec_index: Any,
    deployment: str,
    now: int,
    save_interval: int,
    db_lock: threading.Lock,
) -> bool:
    """Persist one Azure-completed batch under the single-writer lock.

    Returns True on success, False if the SQLite write raised (caller
    surfaces the batch's chunks as failed).

    The lock guards both SQLite (no shared cursor across threads) and
    the usearch ``add_vectors`` call (single-threaded today). Running
    this body under the lock keeps the writer fast — only the Azure
    call runs outside it, which is exactly where parallelism pays.
    """
    with db_lock:
        try:
            _stage_batch_embeddings(db, matched, vectors, deployment, now)
        except sqlite3.Error:
            logger.exception("DB write for batch %d failed", batch_idx)
            return False
        _add_batch_to_vec_index(vec_index, matched, vectors, batch_idx, save_interval)
        return True


# S107 waiver — 14 params vs ceiling 13. Each is a distinct concern
# (chunks / batch_size / parallel / db handle / vec_index handle /
# 4 Azure-config strings / current time / save_interval / total count /
# embed_batch fn / optional cache); grouping any subset into a dataclass
# would obscure the contract more than it clarifies. Paydown: extract
# AzureEmbedSpec(api_key, endpoint, deployment, actual_dims) when the
# next signature change lands so this drops from 14 to 11.
def _run_embed_loop_parallel(  # NOSONAR — S107 14 params; full rationale above def
    all_chunks: list[dict[str, Any]],
    batch_size: int,
    parallel: int,
    db: sqlite3.Connection,
    vec_index: Any,
    api_key: str,
    endpoint: str,
    deployment: str,
    actual_dims: int,
    now: int,
    save_interval: int,
    total: int,
    embed_batch_fn: Callable[..., list[list[float]]],
    *,
    cache: EmbeddingCache | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Run the embed loop with up to ``parallel`` concurrent Azure calls.

    The Azure call runs on a ``ThreadPoolExecutor`` (releases the GIL
    during the network wait). The SQLite + usearch writes run under a
    single ``db_lock`` so each batch's persistence is serialised even
    when many embed futures complete in parallel. This keeps the
    SQLite connection single-threaded (no ``check_same_thread`` games)
    and the usearch index single-writer (matches today's contract).

    F66-exempt: parallel batches are bounded by --parallel (1..10), not
    by per_tick_max_items — the bound is explicit at the CLI surface.

    Returns ``(embedded_count, failed_chunks)``.
    """
    embedded = 0
    failed_chunks: list[dict[str, Any]] = []
    db_lock = threading.Lock()

    batches = list(enumerate(batched(all_chunks, batch_size)))

    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="embed-batch") as pool:
        future_to_batch: dict[Future[Any], tuple[int, list[dict[str, Any]]]] = {
            pool.submit(
                _embed_batch_only,
                batch,
                batch_idx,
                api_key,
                endpoint,
                deployment,
                actual_dims,
                embed_batch_fn,
                cache=cache,
            ): (batch_idx, batch)
            for batch_idx, batch in batches
        }

        for fut in as_completed(future_to_batch):
            batch_idx, batch = future_to_batch[fut]
            matched, vectors, unaccounted, azure_failed = fut.result()
            if azure_failed:
                # Cache hits already in `matched` still persist; only the
                # provider-bound subset counts as failed.
                misses_failed = [c for c in batch if c not in matched]
                failed_chunks.extend(misses_failed)
                if not matched:
                    continue
            persisted = _persist_batch_result(
                matched,
                vectors,
                batch_idx,
                db,
                vec_index,
                deployment,
                now,
                save_interval,
                db_lock,
            )
            if not persisted:
                failed_chunks.extend(batch)
                continue
            failed_chunks.extend(unaccounted)
            embedded += len(matched)
            if matched:
                logger.info(
                    "Embed progress: %d/%d chunks (%.0f%%) — batch %d",
                    embedded,
                    total,
                    100.0 * embedded / total if total > 0 else 0,
                    batch_idx + 1,
                )

    return embedded, failed_chunks


# ── Main embed runner ─────────────────────────────────────────────────────────


def _maybe_clear_vec_index_for_force(force: bool, vec_index: Any) -> None:
    """GH #352 — under ``--force`` we already cleared SQLite content_vectors
    in :func:`_gather_pending_chunks`. The on-disk usearch index must be
    cleared too, otherwise the first ``add_vectors()`` call previously
    triggered a convert-on-mutate path that loaded every existing vector
    into memory just to discard it. ``clear()`` drops the on-disk files
    and resets in-memory state, so the upcoming ``add_vectors`` calls
    build a fresh mutable index from empty. Lifted out of ``run_embed``
    to keep that function under the F16 cognitive-complexity ceiling.
    """
    if force and vec_index is not None and hasattr(vec_index, "clear"):
        logger.info("--force: clearing on-disk usearch index")
        vec_index.clear()


def _run_embed_loop_serial(
    all_chunks: list[dict[str, Any]],
    batch_size: int,
    db: sqlite3.Connection,
    vec_index: Any,
    api_key: str,
    endpoint: str,
    deployment: str,
    actual_dims: int,
    now: int,
    save_interval: int,
    total: int,
    embed_batch_fn: Callable[..., list[list[float]]],
    *,
    cache: EmbeddingCache | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Run the embed loop serially — today's default, ``--parallel 1``.

    Lifted out of ``run_embed`` for symmetry with
    ``_run_embed_loop_parallel`` and to keep ``run_embed`` under the F16
    cognitive-complexity ceiling once the parallel branch is added.
    """
    embedded = 0
    failed_chunks: list[dict[str, Any]] = []
    for batch_idx, batch in enumerate(batched(all_chunks, batch_size)):
        batch_ok, batch_failed = _embed_and_store_batch(
            batch,
            batch_idx,
            db,
            vec_index,
            api_key,
            endpoint,
            deployment,
            actual_dims,
            now,
            save_interval,
            embed_batch_fn=embed_batch_fn,
            cache=cache,
        )
        embedded += batch_ok
        failed_chunks.extend(batch_failed)
        if batch_ok:
            logger.info(
                "Embed progress: %d/%d chunks (%.0f%%) — batch %d",
                embedded,
                total,
                100.0 * embedded / total if total > 0 else 0,
                batch_idx + 1,
            )
    return embedded, failed_chunks


def run_embed(
    db: sqlite3.Connection,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    deps: EmbedDependencies | None = None,
    parallel: int = DEFAULT_PARALLEL_BATCHES,
) -> dict[str, Any]:
    """
    Main embedding loop. Reads pending chunks, calls Azure, writes vectors.

    Args:
        db:         Open SQLite connection (caller holds the lock)
        force:      Re-embed everything, not just pending
        batch_size: Chunks per Azure API call (Azure supports up to 2048; default 500)
        limit:      Cap total chunks (for validation/testing)
        deps:       Injectable dependencies. Defaults to production implementations.
        parallel:   Number of batches to embed concurrently (1..10). Default 1
                    is today's serial behaviour. Higher values run the Azure
                    call on a ThreadPoolExecutor while the SQLite + usearch
                    writes stay serialised under a single-writer lock. See
                    docs/operations/runbooks/worker-memory-and-swap.md for
                    sizing guidance.

    Returns dict with: embedded, skipped, failed, duration_s, estimated_cost_usd
    """
    _validate_parallel(parallel)

    if deps is None:  # pragma: no cover  # prod lazy default; tests pass deps=
        deps = EmbedDependencies()

    doc_root = deps.get_document_root()

    api_key, endpoint, deployment = deps.get_azure_config()

    actual_dims = deps.preflight_check(api_key, endpoint, deployment)
    if actual_dims != DEFAULT_DIMS:
        raise SchemaVersionError(
            f"Azure returned {actual_dims} dims but expected {DEFAULT_DIMS}. "
            f"Check KAIRIX_EMBED_MODEL and dimensions setting."
        )

    deps.migrate_content_vectors(db)

    all_chunks, doc_count = _gather_pending_chunks(db, force, doc_root)

    if limit:
        all_chunks = all_chunks[:limit]

    total = len(all_chunks)
    if total == 0:
        logger.info("Nothing to embed — index is up to date.")
        return {
            "embedded": 0,
            "skipped": 0,
            "failed": 0,
            "duration_s": 0,
            "estimated_cost_usd": 0.0,
        }

    logger.info(
        "Embedding %d chunks across %d documents (batch_size=%d, parallel=%d)",
        total,
        doc_count,
        batch_size,
        parallel,
    )

    start_time = time.time()
    now = int(start_time)

    vec_index = deps.open_usearch_index()
    _maybe_clear_vec_index_for_force(force, vec_index)
    save_interval = 10

    cache = deps.open_embedding_cache()

    try:
        if parallel == 1:
            embedded, failed_chunks = _run_embed_loop_serial(
                all_chunks,
                batch_size,
                db,
                vec_index,
                api_key,
                endpoint,
                deployment,
                actual_dims,
                now,
                save_interval,
                total,
                deps.embed_batch,
                cache=cache,
            )
        else:
            embedded, failed_chunks = _run_embed_loop_parallel(
                all_chunks,
                batch_size,
                parallel,
                db,
                vec_index,
                api_key,
                endpoint,
                deployment,
                actual_dims,
                now,
                save_interval,
                total,
                deps.embed_batch,
                cache=cache,
            )
    finally:
        if cache is not None:
            cache.close()

    _save_index_checkpoint(vec_index)

    duration_s = time.time() - start_time
    estimated_tokens = embedded * 200
    estimated_cost = (estimated_tokens / 1000) * 0.00013

    if failed_chunks:
        failed_paths = list({c["path"] for c in failed_chunks})[:10]
        sample = [str(p)[:200] for p in failed_paths]
        logger.warning("%d chunks failed. Affected paths (sample): %s", len(failed_chunks), sample)

    chunk_date_count = sum(1 for c in all_chunks if c.get(_KEY_CHUNK_DATE))
    if chunk_date_count == 0 and total > 0:
        logger.warning(
            "embed: 0/%d chunks have chunk_date — temporal boost (TMP-7B) will be inert. "
            "Ensure documents have a date in frontmatter (date: YYYY-MM-DD) or in their filename.",
            total,
        )
    else:
        logger.info(
            "embed: chunk_date populated for %d/%d chunks (%.1f%%)",
            chunk_date_count,
            total,
            100 * chunk_date_count / total,
        )

    return {
        "embedded": embedded,
        "skipped": total - embedded - len(failed_chunks),
        "failed": len(failed_chunks),
        "failed_paths": list({c["path"] for c in failed_chunks}),
        "duration_s": round(duration_s, 1),
        "estimated_cost_usd": round(estimated_cost, 4),
        "total_chunks": total,
        "chunk_date_count": chunk_date_count,
    }
