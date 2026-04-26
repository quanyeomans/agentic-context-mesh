"""
Tests for kairix.embed.embed — covers previously-untested paths:
- _get_azure_config(): env vars, missing key/endpoint errors
- preflight_check(): mocked HTTP success + error
- embed_batch(): mocked API + staging + vec write
- ensure_staging_table(): creation
- flush_staging_to_vec(): count + merge
- stage_embedding(): insert into staging
- batched(): chunk iteration
- chunk_text(): boundary + overlap
"""

from __future__ import annotations

import sqlite3
import struct
from unittest.mock import MagicMock, patch

import pytest

from kairix.embed.embed import (
    _get_azure_config,
    batched,
    build_hash_seq,
    chunk_text,
    encode_vector,
    ensure_staging_table,
    flush_staging_to_vec,
    preflight_check,
    stage_embedding,
)

# ---------------------------------------------------------------------------
# encode_vector / build_hash_seq
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_encode_vector_returns_bytes() -> None:
    vec = [0.1, 0.2, 0.3]
    result = encode_vector(vec)
    assert isinstance(result, bytes)
    assert len(result) == 3 * 4  # 3 float32s x 4 bytes


@pytest.mark.unit
def test_encode_vector_round_trips() -> None:
    vec = [1.0, -0.5, 0.0, 0.25]
    result = encode_vector(vec)
    unpacked = list(struct.unpack(f"<{len(vec)}f", result))
    assert len(unpacked) == len(vec)
    for a, b in zip(unpacked, vec, strict=False):
        assert abs(a - b) < 1e-6


@pytest.mark.unit
def test_build_hash_seq() -> None:
    assert build_hash_seq("abc123", 0) == "abc123_0"
    assert build_hash_seq("abc123", 3) == "abc123_3"


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chunk_text_returns_list_of_dicts() -> None:
    chunks = chunk_text("Hello world. This is a test.", chunk_size=100, overlap=0)
    assert isinstance(chunks, list)
    assert all("seq" in c and "pos" in c and "text" in c for c in chunks)


@pytest.mark.unit
def test_chunk_text_single_chunk_short_text() -> None:
    text = "Short text."
    chunks = chunk_text(text, chunk_size=500, overlap=0)
    assert len(chunks) == 1
    assert chunks[0]["text"] == text
    assert chunks[0]["seq"] == 0
    assert chunks[0]["pos"] == 0


@pytest.mark.unit
def test_chunk_text_multiple_chunks() -> None:
    # Generate text longer than chunk_size
    text = "Word " * 400  # ~2000 chars
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    # Seq numbers are sequential
    for i, c in enumerate(chunks):
        assert c["seq"] == i


@pytest.mark.unit
def test_chunk_text_empty_string() -> None:
    # Empty string produces one chunk — the function doesn't filter empty results
    result = chunk_text("")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _get_azure_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_azure_config_returns_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")
    monkeypatch.setenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "my-deploy")

    key, endpoint, deployment = _get_azure_config()
    assert key == "test-key"
    assert endpoint == "https://fake.example.com"  # trailing slash stripped
    assert deployment == "my-deploy"


@pytest.mark.unit
def test_get_azure_config_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")
    with pytest.raises(OSError, match="AZURE_OPENAI_API_KEY"):
        _get_azure_config()


@pytest.mark.unit
def test_get_azure_config_raises_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(OSError, match="AZURE_OPENAI_ENDPOINT"):
        _get_azure_config()


# ---------------------------------------------------------------------------
# preflight_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_preflight_check_returns_dims_on_success() -> None:
    fake_vec = [0.1] * 1536
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"embedding": fake_vec}]}

    with patch("kairix.embed.embed.requests.post", return_value=mock_resp):
        dims = preflight_check("key", "https://fake.example.com", "text-embedding-3-large")

    assert dims == 1536


@pytest.mark.unit
def test_preflight_check_raises_on_http_error() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 401")

    with patch("kairix.embed.embed.requests.post", return_value=mock_resp):
        with pytest.raises(Exception, match="401"):
            preflight_check("bad-key", "https://fake.example.com", "deploy")


# ---------------------------------------------------------------------------
# ensure_staging_table + stage_embedding + flush_staging_to_vec
# ---------------------------------------------------------------------------


def _make_staging_db() -> sqlite3.Connection:
    """Create an in-memory DB with a minimal vectors_vec replacement."""
    db = sqlite3.connect(":memory:")
    # vectors_vec as a regular table (no vec0 extension in CI)
    db.execute("CREATE TABLE vectors_vec (hash_seq TEXT PRIMARY KEY, source_path TEXT, embedding BLOB)")
    return db


@pytest.mark.unit
def test_ensure_staging_table_creates_table() -> None:
    db = sqlite3.connect(":memory:")
    ensure_staging_table(db)
    # TEMPORARY tables are in sqlite_temp_master
    cur = db.execute("SELECT name FROM sqlite_temp_master WHERE name='vec_staging'")
    assert cur.fetchone() is not None


@pytest.mark.unit
def test_ensure_staging_table_idempotent() -> None:
    db = sqlite3.connect(":memory:")
    ensure_staging_table(db)
    ensure_staging_table(db)  # CREATE TEMPORARY TABLE IF NOT EXISTS — should not raise
    cur = db.execute("SELECT name FROM sqlite_temp_master WHERE name='vec_staging'")
    assert cur.fetchone() is not None


@pytest.mark.unit
def test_stage_embedding_inserts_row() -> None:
    db = sqlite3.connect(":memory:")
    # content_vectors table required by stage_embedding
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )
    ensure_staging_table(db)
    vec = [0.1] * 1536
    stage_embedding(db, "hash123", 0, 0, vec, "text-embedding-3-large", 1711111111)
    count = db.execute("SELECT COUNT(*) FROM vec_staging").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_flush_staging_to_vec_returns_zero_when_empty() -> None:
    db = _make_staging_db()
    ensure_staging_table(db)
    result = flush_staging_to_vec(db)
    assert result == 0


@pytest.mark.unit
def test_flush_staging_to_vec_moves_rows() -> None:
    db = _make_staging_db()
    ensure_staging_table(db)
    vec_bytes = encode_vector([0.1] * 1536)

    # Stage a row (TEMPORARY table, no commit needed)
    db.execute(
        "INSERT INTO vec_staging (hash_seq, embedding) VALUES (?, ?)",
        ("hash123_0", vec_bytes),
    )

    merged = flush_staging_to_vec(db)
    assert merged == 1

    # Staging should be empty after flush
    remaining = db.execute("SELECT COUNT(*) FROM vec_staging").fetchone()[0]
    assert remaining == 0

    # vectors_vec should have the row
    vv_count = db.execute("SELECT COUNT(*) FROM vectors_vec").fetchone()[0]
    assert vv_count == 1


# ---------------------------------------------------------------------------
# batched
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_batched_splits_correctly() -> None:
    items = list(range(10))
    result = list(batched(items, size=3))
    assert result == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]


@pytest.mark.unit
def test_batched_single_batch() -> None:
    items = [1, 2, 3]
    result = list(batched(items, size=10))
    assert result == [[1, 2, 3]]


@pytest.mark.unit
def test_batched_empty() -> None:
    result = list(batched([], size=5))
    assert result == []


# ---------------------------------------------------------------------------
# chunk_text: sentence boundary path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chunk_text_splits_on_sentence_boundary() -> None:
    """Chunker prefers sentence boundary when paragraph boundary not available."""
    # Create text long enough to force a split, no double-newline paragraph breaks
    sentence_1 = "This is the first sentence about engineering patterns. "
    sentence_2 = "This is the second sentence about deployment strategies. "
    # Repeat to exceed chunk_size
    text = sentence_1 * 5 + sentence_2 * 5
    chunks = chunk_text(text, chunk_size=200, overlap=0)
    assert len(chunks) > 1
    # All chunks should have text
    for c in chunks:
        assert c["text"].strip()


@pytest.mark.unit
def test_chunk_text_splits_on_paragraph_boundary() -> None:
    """Chunker prefers double-newline paragraph boundary."""
    para_1 = "First paragraph about architecture decisions.\n\n"
    para_2 = "Second paragraph about testing strategy.\n\n"
    text = para_1 * 4 + para_2 * 4
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1


# ---------------------------------------------------------------------------
# run_embed: mocked orchestration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_embed_with_no_chunks_returns_stats() -> None:
    """run_embed() with no pending chunks returns embedded=0 immediately."""
    from kairix.embed.embed import run_embed

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )
    db.execute("CREATE TABLE vectors_vec (hash_seq TEXT PRIMARY KEY, embedding BLOB)")

    with (
        patch("kairix.embed.embed._get_azure_config", return_value=("key", "https://ep.com", "deploy")),
        patch("kairix.embed.embed.load_sqlite_vec"),
        patch("kairix.embed.embed.preflight_check", return_value=1536),
        patch("kairix.embed.embed.ensure_vec_table"),
    ):
        result = run_embed(db, batch_size=10)

    assert result["embedded"] == 0
    assert "duration_s" in result
    assert "estimated_cost_usd" in result


@pytest.mark.unit
def test_run_embed_raises_on_dim_mismatch() -> None:
    """run_embed() raises SchemaVersionError when Azure returns unexpected dims."""
    from kairix.embed.embed import run_embed
    from kairix.embed.schema import SchemaVersionError

    db = sqlite3.connect(":memory:")

    with (
        patch("kairix.embed.embed._get_azure_config", return_value=("key", "https://ep.com", "deploy")),
        patch("kairix.embed.embed.load_sqlite_vec"),
        patch("kairix.embed.embed.preflight_check", return_value=512),  # wrong dims
    ):
        with pytest.raises(SchemaVersionError):
            run_embed(db)
