"""
Core embedding logic — fetches vectors from Azure OpenAI and writes to kairix's SQLite.
"""

import logging
import sqlite3
import time
from collections.abc import Generator

from .date_extract import extract_chunk_date
from .schema import (
    EMBED_VECTOR_DIMS,
    SchemaVersionError,
    migrate_content_vectors,
)

logger = logging.getLogger(__name__)

# Azure OpenAI
DEFAULT_DEPLOYMENT = "text-embedding-3-large"
DEFAULT_DIMS = EMBED_VECTOR_DIMS
DEFAULT_BATCH_SIZE = 250  # Balanced: large enough for throughput, small enough to avoid Azure 429s
MAX_RETRIES = 6  # used by OpenAI SDK max_retries

# Chunking — mirrors kairix's CHUNK_SIZE_TOKENS / CHUNK_OVERLAP_TOKENS
CHUNK_SIZE_CHARS = 3600  # ~900 tokens at 4 chars/token
CHUNK_OVERLAP_CHARS = 200


# ── Encoding ──────────────────────────────────────────────────────────────────


def build_hash_seq(content_hash: str, seq: int) -> str:
    """Build the hash_seq key used by usearch index metadata."""
    return f"{content_hash}_{seq}"


# ── Chunking ──────────────────────────────────────────────────────────────────


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[dict]:
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

        # Try to split on paragraph boundary
        if end < len(text):
            para_break = text.rfind("\n\n", pos, end)
            if para_break > pos + chunk_size // 2:
                end = para_break + 2
            else:
                # Fall back to sentence boundary
                sent_break = max(
                    text.rfind(". ", pos, end),
                    text.rfind(".\n", pos, end),
                )
                if sent_break > pos + chunk_size // 2:
                    end = sent_break + 1

        chunk_text_val = text[pos:end].strip()
        if chunk_text_val:
            chunks.append({"seq": seq, "pos": pos, "text": chunk_text_val})
            seq += 1

        pos = end - overlap if end < len(text) else len(text)

    return chunks


# ── Azure API ─────────────────────────────────────────────────────────────────


def _get_azure_config() -> tuple[str, str, str]:
    """
    Read embed API config via ``get_credentials("embed")``. Supports Azure,
    OpenRouter, or any OpenAI-compatible endpoint.

    Raises OSError when credentials cannot be resolved.
    """
    from kairix.credentials import get_credentials

    creds = get_credentials("embed")
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


def preflight_check(api_key: str, endpoint: str, deployment: str) -> int:
    """
    Verify the embedding API is reachable with a single-item embed call.
    Returns embedding dimensions on success, raises on failure.
    Does NOT touch the DB — safe to call before any writes.
    """
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
_embed_client_key: tuple = ("", "")


def _get_embed_client(api_key: str, endpoint: str):  # type: ignore[no-untyped-def]
    """Return a cached OpenAI client. Creates a new one if credentials change."""
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
) -> list[list[float]]:
    """
    Embed a batch of texts via Azure OpenAI using the OpenAI SDK.

    Client is reused across batches for connection pooling and rate-limiter
    state persistence. The SDK handles retry with exponential backoff and
    Retry-After headers automatically.

    Returns list of float vectors in same order as input texts.
    Raises on persistent failures after SDK retries are exhausted.
    On BadRequestError (batch too large), splits and recurses.
    """
    import openai

    if not texts:
        return []

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
        logger.warning("BadRequestError on batch of %d — splitting into %d + %d", len(texts), mid, len(texts) - mid)
        left = embed_batch(texts[:mid], api_key, endpoint, deployment, dims)
        right = embed_batch(texts[mid:], api_key, endpoint, deployment, dims)
        return left + right


# ── DB writes ─────────────────────────────────────────────────────────────────


def stage_embedding(
    db: sqlite3.Connection,
    content_hash: str,
    seq: int,
    pos: int,
    vector: list[float],
    model: str,
    embedded_at: int,
    chunk_date: str | None = None,
) -> None:
    """
    Write chunk metadata to content_vectors.

    content_vectors is a normal SQLite table and supports INSERT OR REPLACE.
    Vectors are written to the usearch ANN index separately via
    _update_usearch_index() at batch commit time.

    chunk_date is the ISO date extracted from the document (frontmatter or path).
    It is the same for all chunks of a given document (document-level property).
    """
    db.execute(
        "INSERT OR REPLACE INTO content_vectors"
        " (hash, seq, pos, model, embedded_at, chunk_date) VALUES (?, ?, ?, ?, ?, ?)",
        (content_hash, seq, pos, model, embedded_at, chunk_date),
    )


# ── Batch generator ───────────────────────────────────────────────────────────


def batched(items: list, size: int) -> Generator[list, None, None]:
    """Yield successive batches of `size` from `items`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── usearch index update ─────────────────────────────────────────────────────


def _open_usearch_index():  # type: ignore[no-untyped-def]
    """Open (or create) the usearch ANN index for the embed run.

    Returns a mutable VectorIndex that can be reused across all batches.
    Auto-deletes old index if dimensions have changed.
    """
    try:
        from kairix.core.search.vec_index import VectorIndex
        from kairix.paths import db_path as get_db_path

        db_p = get_db_path()
        index_path = db_p.parent / "vectors.usearch"
        meta_path = db_p.parent / "vectors.meta.json"
        idx = VectorIndex(index_path=index_path, meta_path=meta_path, db_path=db_p)
        idx.load()  # auto-deletes if dims mismatch
        return idx
    except Exception as e:
        logger.error("usearch index open failed: %s", e)
        return None


# ── Main embed runner ─────────────────────────────────────────────────────────


def run_embed(
    db: sqlite3.Connection,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
) -> dict:
    """
    Main embedding loop. Reads pending chunks, calls Azure, writes vectors.

    Args:
        db:         Open SQLite connection (caller holds the lock)
        force:      Re-embed everything, not just pending
        batch_size: Chunks per Azure API call (Azure supports up to 2048; default 500)
        limit:      Cap total chunks (for validation/testing)

    Returns dict with: embedded, skipped, failed, duration_s, estimated_cost_usd
    """
    # Resolve document root for file mtime fallback in chunk date extraction
    try:
        from kairix.paths import document_root

        doc_root = str(document_root())
    except Exception:
        doc_root = None

    api_key, endpoint, deployment = _get_azure_config()

    # Preflight — verify Azure before touching DB
    actual_dims = preflight_check(api_key, endpoint, deployment)
    if actual_dims != DEFAULT_DIMS:
        raise SchemaVersionError(
            f"Azure returned {actual_dims} dims but expected {DEFAULT_DIMS}. "
            f"Check KAIRIX_EMBED_MODEL and dimensions setting."
        )

    # Ensure chunk_date column exists (idempotent — no-op when already present)
    migrate_content_vectors(db)

    # Gather chunks to embed
    if force:
        # Clear existing vectors first (after successful preflight)
        logger.info("--force: clearing all existing vectors")
        db.execute("DELETE FROM content_vectors")
        db.commit()

    # Get all document bodies that need chunking + embedding
    if force:
        rows = db.execute("""
            SELECT c.hash, c.doc, d.path
            FROM content c
            JOIN documents d ON c.hash = d.hash
            WHERE d.active = 1
              AND c.doc IS NOT NULL
              AND length(c.doc) > 0
        """).fetchall()
    else:
        rows = db.execute("""
            SELECT c.hash, c.doc, d.path
            FROM content c
            JOIN documents d ON c.hash = d.hash
            LEFT JOIN content_vectors v ON c.hash = v.hash AND v.seq = 0
            WHERE v.hash IS NULL
              AND d.active = 1
              AND c.doc IS NOT NULL
              AND length(c.doc) > 0
        """).fetchall()

    # Expand into chunks, extracting chunk_date once per document
    all_chunks = []
    for content_hash, body, path in rows:
        doc_date = extract_chunk_date(body, path, document_root=doc_root)  # document-level date
        for chunk in chunk_text(body):
            all_chunks.append(
                {
                    "hash": content_hash,
                    "seq": chunk["seq"],
                    "pos": chunk["pos"],
                    "text": chunk["text"],
                    "path": path,
                    "chunk_date": doc_date,
                }
            )

    if limit:
        all_chunks = all_chunks[:limit]

    total = len(all_chunks)
    if total == 0:
        logger.info("Nothing to embed — index is up to date.")
        return {"embedded": 0, "skipped": 0, "failed": 0, "duration_s": 0, "estimated_cost_usd": 0.0}

    logger.info(f"Embedding {total} chunks across {len(rows)} documents (batch_size={batch_size})")

    embedded = 0
    failed_chunks = []
    start_time = time.time()
    now = int(start_time)

    # Open usearch index once — reuse across all batches (avoids O(n²) rebuild)
    vec_index = _open_usearch_index()
    save_interval = 10  # save index to disk every N batches

    for batch_idx, batch in enumerate(batched(all_chunks, batch_size)):
        texts = [c["text"] for c in batch]

        try:
            vectors = embed_batch(texts, api_key, endpoint, deployment, actual_dims)
        except (RuntimeError, KeyError, ValueError, OSError) as e:
            logger.error(f"Batch {batch_idx} failed: {e} — logging {len(batch)} chunks as failed")
            failed_chunks.extend(batch)
            continue

        # Write chunk metadata to content_vectors, then update usearch index.
        try:
            with db:  # transaction: write metadata atomically
                for chunk, vector in zip(batch, vectors, strict=False):
                    stage_embedding(
                        db,
                        chunk["hash"],
                        chunk["seq"],
                        chunk["pos"],
                        vector,
                        deployment,
                        now,
                        chunk_date=chunk.get("chunk_date"),
                    )
            # Write to usearch ANN index (reuse mutable index across batches)
            if vec_index is not None:
                try:
                    batch_hash_seqs = [build_hash_seq(c["hash"], c["seq"]) for c in batch]
                    vec_index.add_vectors(batch_hash_seqs, vectors)
                    if (batch_idx + 1) % save_interval == 0:
                        vec_index.save()
                except Exception as e:
                    logger.error("usearch batch %d failed: %s", batch_idx, e)
            embedded += len(batch)
            logger.info(
                "Embed progress: %d/%d chunks (%.0f%%) — batch %d",
                embedded,
                total,
                100.0 * embedded / total if total > 0 else 0,
                batch_idx + 1,
            )
        except sqlite3.Error as e:
            logger.error("DB write for batch %d failed: %s", batch_idx, e)
            failed_chunks.extend(batch)
            continue

        # No unconditional sleep — rate limiting is handled reactively via
        # Retry-After headers in embed_batch() when Azure actually pushes back.

    # Final save of usearch index
    if vec_index is not None:
        try:
            vec_index.save()
            logger.info("usearch: saved index with %d vectors", len(vec_index))
        except Exception as e:
            logger.error("usearch final save failed: %s", e)

    duration_s = time.time() - start_time

    # Cost estimate: text-embedding-3-large at $0.00013/1K tokens
    # Assume avg 200 tokens/chunk
    estimated_tokens = embedded * 200
    estimated_cost = (estimated_tokens / 1000) * 0.00013

    if failed_chunks:
        failed_paths = list({c["path"] for c in failed_chunks})[:10]
        logger.warning(f"{len(failed_chunks)} chunks failed. Affected paths (sample): {failed_paths}")

    # Count how many chunks have chunk_date populated (for diagnostics / ERR-001 guard)
    chunk_date_count = sum(1 for c in all_chunks if c.get("chunk_date"))
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
