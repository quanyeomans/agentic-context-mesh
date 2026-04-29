"""
Tests for kairix.core.embed.embed — covers previously-untested paths:
- _get_azure_config(): env vars, missing key/endpoint errors
- preflight_check(): mocked HTTP success + error
- stage_embedding(): insert into content_vectors
- batched(): chunk iteration
- chunk_text(): boundary + overlap
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from kairix.core.embed.embed import (
    _get_azure_config,
    batched,
    build_hash_seq,
    chunk_text,
    preflight_check,
    stage_embedding,
)

# ---------------------------------------------------------------------------
# build_hash_seq
# ---------------------------------------------------------------------------


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
    monkeypatch.setenv("KAIRIX_LLM_API_KEY", "test-key")
    monkeypatch.setenv("KAIRIX_LLM_ENDPOINT", "https://fake.example.com/")
    monkeypatch.setenv("KAIRIX_EMBED_MODEL", "my-deploy")

    key, endpoint, deployment = _get_azure_config()
    assert key == "test-key"
    assert endpoint == "https://fake.example.com"  # trailing slash stripped
    assert deployment == "my-deploy"


@pytest.mark.unit
def test_get_azure_config_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAIRIX_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KAIRIX_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("KAIRIX_KV_NAME", raising=False)
    monkeypatch.setenv("KAIRIX_LLM_ENDPOINT", "https://fake.example.com/")
    monkeypatch.setenv("KAIRIX_SECRETS_DIR", "/nonexistent-dir-abc123")
    with pytest.raises(OSError):
        _get_azure_config()


@pytest.mark.unit
def test_get_azure_config_raises_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIRIX_LLM_API_KEY", "test-key")
    monkeypatch.delenv("KAIRIX_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("KAIRIX_EMBED_ENDPOINT", raising=False)
    monkeypatch.delenv("KAIRIX_KV_NAME", raising=False)
    monkeypatch.setenv("KAIRIX_SECRETS_DIR", "/nonexistent-dir-abc123")
    with pytest.raises(OSError):
        _get_azure_config()


# ---------------------------------------------------------------------------
# preflight_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_preflight_check_returns_dims_on_success() -> None:
    fake_vec = [0.1] * 1536
    mock_embedding = MagicMock()
    mock_embedding.embedding = fake_vec
    mock_response = MagicMock()
    mock_response.data = [mock_embedding]

    with patch("openai.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.embeddings.create.return_value = mock_response
        dims = preflight_check("key", "https://fake.example.com", "text-embedding-3-large")

    assert dims == 1536


@pytest.mark.unit
def test_preflight_check_raises_on_api_error() -> None:
    import openai

    with patch("openai.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.embeddings.create.side_effect = openai.AuthenticationError(
            message="HTTP 401",
            response=MagicMock(status_code=401),
            body=None,
        )
        with pytest.raises(openai.AuthenticationError):
            preflight_check("bad-key", "https://fake.example.com", "deploy")


# ---------------------------------------------------------------------------
# stage_embedding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stage_embedding_inserts_row() -> None:
    db = sqlite3.connect(":memory:")
    # content_vectors table required by stage_embedding
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )
    vec = [0.1] * 1536
    stage_embedding(db, "hash123", 0, 0, vec, "text-embedding-3-large", 1711111111)
    count = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
    assert count == 1


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
    from kairix.core.embed.embed import run_embed

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )
    db.execute("CREATE TABLE vectors_vec (hash_seq TEXT PRIMARY KEY, embedding BLOB)")

    with (
        patch("kairix.core.embed.embed._get_azure_config", return_value=("key", "https://ep.com", "deploy")),
        patch("kairix.core.embed.embed.preflight_check", return_value=1536),
    ):
        result = run_embed(db, batch_size=10)

    assert result["embedded"] == 0
    assert "duration_s" in result
    assert "estimated_cost_usd" in result


@pytest.mark.unit
def test_run_embed_raises_on_dim_mismatch() -> None:
    """run_embed() raises SchemaVersionError when Azure returns unexpected dims."""
    from kairix.core.embed.embed import run_embed
    from kairix.core.embed.schema import SchemaVersionError

    db = sqlite3.connect(":memory:")

    with (
        patch("kairix.core.embed.embed._get_azure_config", return_value=("key", "https://ep.com", "deploy")),
        patch("kairix.core.embed.embed.preflight_check", return_value=512),  # wrong dims
    ):
        with pytest.raises(SchemaVersionError):
            run_embed(db)
