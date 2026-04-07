"""
Core embedding logic — fetches vectors from Azure OpenAI and writes to QMD's SQLite.
"""

import logging
import os
import sqlite3
import struct
import time
from collections.abc import Generator

import requests

from .schema import (
    EMBED_VECTOR_DIMS,
    SchemaVersionError,
    ensure_vec_table,
    load_sqlite_vec,
)

logger = logging.getLogger(__name__)

# Azure OpenAI
DEFAULT_DEPLOYMENT = "text-embedding-3-large"
DEFAULT_DIMS = EMBED_VECTOR_DIMS
DEFAULT_BATCH_SIZE = 100
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds, doubles per retry

# Chunking — mirrors QMD's CHUNK_SIZE_TOKENS / CHUNK_OVERLAP_TOKENS
CHUNK_SIZE_CHARS = 3600  # ~900 tokens at 4 chars/token
CHUNK_OVERLAP_CHARS = 200


# ── Encoding ──────────────────────────────────────────────────────────────────


def encode_vector(vec: list[float]) -> bytes:
    """
    Encode a float list as packed binary float32 — the format sqlite-vec expects.
    sqlite-vec uses little-endian IEEE 754 float32.
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def build_hash_seq(content_hash: str, seq: int) -> str:
    """Build the hash_seq primary key used by vectors_vec. Matches QMD exactly."""
    return f"{content_hash}_{seq}"


# ── Chunking ──────────────────────────────────────────────────────────────────


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[dict]:
    """
    Split text into overlapping chunks. Returns list of {seq, pos, text}.
    Mirrors QMD's chunkDocument() logic for consistency.
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
    Read Azure config from env vars. Fails fast with clear message if missing.
    Callers (scripts/deploy.sh) set these after fetching from Key Vault.
    """
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", DEFAULT_DEPLOYMENT)

    if not api_key:
        raise OSError(
            "AZURE_OPENAI_API_KEY not set. "
            "Fetch from Key Vault: az keyvault secret show --vault-name ${KV_NAME} "
            "--name azure-openai-api-key --query value -o tsv"
        )
    if not endpoint:
        raise OSError(
            "AZURE_OPENAI_ENDPOINT not set. "
            "Fetch from Key Vault: az keyvault secret show --vault-name ${KV_NAME} "
            "--name azure-openai-endpoint --query value -o tsv"
        )

    # Normalise endpoint — strip trailing slash, we'll add the path
    endpoint = endpoint.rstrip("/")
    return api_key, endpoint, deployment


def preflight_check(api_key: str, endpoint: str, deployment: str) -> int:
    """
    Verify Azure is reachable with a single-item embed call.
    Returns embedding dimensions on success, raises on failure.
    Does NOT touch the DB — safe to call before any writes.
    """
    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2024-02-01"
    resp = requests.post(
        url,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json={"input": ["preflight check"], "dimensions": DEFAULT_DIMS},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    vec = data["data"][0]["embedding"]
    logger.info(f"Preflight OK — deployment={deployment} dims={len(vec)}")
    return len(vec)


def embed_batch(
    texts: list[str],
    api_key: str,
    endpoint: str,
    deployment: str,
    dims: int = DEFAULT_DIMS,
) -> list[list[float]]:
    """
    Embed a batch of texts via Azure OpenAI. Retries on 429/500.
    Returns list of float vectors in same order as input texts.
    Raises on 400 (bad request) or after MAX_RETRIES exhausted.
    """
    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2024-02-01"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                url,
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json={"input": texts, "dimensions": dims},
                timeout=60,
            )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", RETRY_BASE_DELAY * (2**attempt)))
                logger.warning(f"Rate limited — sleeping {retry_after}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(retry_after)
                continue

            if resp.status_code in (500, 502, 503):
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(f"Server error {resp.status_code} — retrying in {delay}s")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            data = resp.json()
            # Sort by index to preserve order (API may return out of order)
            results = sorted(data["data"], key=lambda x: x["index"])
            return [r["embedding"] for r in results]

        except requests.Timeout:
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(f"Request timeout — retrying in {delay}s")
            time.sleep(delay)

    raise RuntimeError(f"Azure embed failed after {MAX_RETRIES} attempts")


# ── DB writes ─────────────────────────────────────────────────────────────────


def ensure_staging_table(db: sqlite3.Connection) -> None:
    """
    Create a temporary staging table for vector upserts.

    sqlite-vec virtual tables (vec0) do not support INSERT OR REPLACE/IGNORE.
    We write vectors to a normal TEMPORARY table first (which supports all
    conflict clauses), then merge into vectors_vec in bulk at the end of each
    batch via DELETE WHERE hash_seq IN (...) + INSERT INTO ... SELECT.

    The TEMPORARY table is scoped to the connection — it auto-drops on close.
    """
    db.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS vec_staging (
            hash_seq TEXT PRIMARY KEY,
            embedding BLOB NOT NULL
        )
    """)


def flush_staging_to_vec(db: sqlite3.Connection) -> int:
    """
    Merge vec_staging into vectors_vec atomically within the current transaction.

    Steps:
      1. DELETE existing vectors_vec rows whose hash_seq is in staging
         (handles the upsert case — vec0 has no native upsert)
      2. INSERT all rows from staging into vectors_vec
      3. Clear staging for the next batch

    Returns number of rows merged.
    """
    count = int(db.execute("SELECT COUNT(*) FROM vec_staging").fetchone()[0])
    if count == 0:
        return 0

    db.execute("""
        DELETE FROM vectors_vec
        WHERE hash_seq IN (SELECT hash_seq FROM vec_staging)
    """)
    db.execute("""
        INSERT INTO vectors_vec (hash_seq, embedding)
        SELECT hash_seq, embedding FROM vec_staging
    """)
    db.execute("DELETE FROM vec_staging")
    return count


def stage_embedding(
    db: sqlite3.Connection,
    content_hash: str,
    seq: int,
    pos: int,
    vector: list[float],
    model: str,
    embedded_at: int,
) -> None:
    """
    Write chunk metadata to content_vectors and queue the vector in vec_staging.

    content_vectors is a normal SQLite table and supports INSERT OR REPLACE.
    The vector goes into vec_staging (also normal SQLite) — it will be merged
    into vectors_vec (sqlite-vec virtual table) via flush_staging_to_vec()
    at batch commit time.
    """
    hash_seq = build_hash_seq(content_hash, seq)
    packed = encode_vector(vector)

    db.execute(
        "INSERT OR REPLACE INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
        (content_hash, seq, pos, model, embedded_at),
    )
    db.execute(
        "INSERT OR REPLACE INTO vec_staging (hash_seq, embedding) VALUES (?, ?)",
        (hash_seq, packed),
    )


# ── Batch generator ───────────────────────────────────────────────────────────


def batched(items: list, size: int) -> Generator[list, None, None]:
    """Yield successive batches of `size` from `items`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── Main embed runner ─────────────────────────────────────────────────────────


def run_embed(
    db: sqlite3.Connection,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    inter_batch_sleep: float = 0.1,
) -> dict:
    """
    Main embedding loop. Reads pending chunks, calls Azure, writes vectors.

    Args:
        db:                 Open SQLite connection (caller holds the lock)
        force:              Re-embed everything, not just pending
        batch_size:         Chunks per Azure API call (max ~2048, 100 is safe)
        limit:              Cap total chunks (for validation/testing)
        inter_batch_sleep:  Seconds to sleep between batches (rate-limit courtesy)

    Returns dict with: embedded, skipped, failed, duration_s, estimated_cost_usd
    """
    api_key, endpoint, deployment = _get_azure_config()

    # Load sqlite-vec extension — must happen before any vec0 table operations
    load_sqlite_vec(db)

    # Preflight — verify Azure before touching DB
    actual_dims = preflight_check(api_key, endpoint, deployment)
    if actual_dims != DEFAULT_DIMS:
        raise SchemaVersionError(
            f"Azure returned {actual_dims} dims but expected {DEFAULT_DIMS}. "
            f"Check AZURE_OPENAI_EMBED_DEPLOYMENT and dimensions setting."
        )

    # Ensure vec table exists at correct dims, and staging table for upserts
    ensure_vec_table(db, actual_dims)
    ensure_staging_table(db)

    # Gather chunks to embed
    if force:
        # Clear existing vectors first (after successful preflight)
        logger.info("--force: clearing all existing vectors")
        db.execute("DELETE FROM content_vectors")
        db.execute("DELETE FROM vectors_vec")
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

    # Expand into chunks
    all_chunks = []
    for content_hash, body, path in rows:
        for chunk in chunk_text(body):
            all_chunks.append(
                {
                    "hash": content_hash,
                    "seq": chunk["seq"],
                    "pos": chunk["pos"],
                    "text": chunk["text"],
                    "path": path,
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

    for batch_idx, batch in enumerate(batched(all_chunks, batch_size)):
        texts = [c["text"] for c in batch]

        try:
            vectors = embed_batch(texts, api_key, endpoint, deployment, actual_dims)
        except Exception as e:
            logger.error(f"Batch {batch_idx} failed: {e} — logging {len(batch)} chunks as failed")
            failed_chunks.extend(batch)
            continue

        # Write batch atomically via staging table
        # Stage all vectors first (normal SQLite, supports OR REPLACE),
        # then flush staging → vectors_vec in one transaction.
        try:
            with db:  # transaction: stage + flush are atomic
                for chunk, vector in zip(batch, vectors, strict=False):
                    stage_embedding(
                        db,
                        chunk["hash"],
                        chunk["seq"],
                        chunk["pos"],
                        vector,
                        deployment,
                        now,
                    )
                flush_staging_to_vec(db)
            embedded += len(batch)
        except Exception as e:
            logger.error(f"DB write for batch {batch_idx} failed: {e}")
            # Clear staging so the next batch starts clean
            try:
                db.execute("DELETE FROM vec_staging")
                db.commit()
            except Exception:
                logger.debug("Non-critical cleanup failed", exc_info=True)
            # Dimension mismatch: QMD cron may have recreated vectors_vec with wrong dims.
            # Repair schema and retry the batch once.
            if "dimension" in str(e).lower():
                logger.warning(
                    f"Dimension mismatch on batch {batch_idx} -- "
                    "QMD cron may have recreated vectors_vec. Repairing schema and retrying."
                )
                try:
                    ensure_vec_table(db, actual_dims)
                    with db:
                        for chunk, vector in zip(batch, vectors, strict=False):
                            stage_embedding(
                                db,
                                chunk["hash"],
                                chunk["seq"],
                                chunk["pos"],
                                vector,
                                deployment,
                                now,
                            )
                        flush_staging_to_vec(db)
                    embedded += len(batch)
                    logger.info(f"Batch {batch_idx} retry succeeded after schema repair.")
                    continue
                except Exception as retry_e:
                    logger.error(
                        f"DB write retry for batch {batch_idx} failed after schema repair: {retry_e}"
                    )
            failed_chunks.extend(batch)
            continue

        # Progress log every 10 batches
        if (batch_idx + 1) % 10 == 0:
            pct = (embedded / total) * 100
            elapsed = time.time() - start_time
            rate = embedded / elapsed if elapsed > 0 else 0
            eta_s = (total - embedded) / rate if rate > 0 else 0
            logger.info(f"Progress: {embedded}/{total} ({pct:.1f}%) — {rate:.1f} chunks/s — ETA {eta_s / 60:.1f}m")

        if inter_batch_sleep > 0:
            time.sleep(inter_batch_sleep)

    duration_s = time.time() - start_time

    # Cost estimate: text-embedding-3-large at $0.00013/1K tokens
    # Assume avg 200 tokens/chunk
    estimated_tokens = embedded * 200
    estimated_cost = (estimated_tokens / 1000) * 0.00013

    if failed_chunks:
        failed_paths = list({c["path"] for c in failed_chunks})[:10]
        logger.warning(f"{len(failed_chunks)} chunks failed. Affected paths (sample): {failed_paths}")

    return {
        "embedded": embedded,
        "skipped": total - embedded - len(failed_chunks),
        "failed": len(failed_chunks),
        "failed_paths": list({c["path"] for c in failed_chunks}),
        "duration_s": round(duration_s, 1),
        "estimated_cost_usd": round(estimated_cost, 4),
        "total_chunks": total,
    }
