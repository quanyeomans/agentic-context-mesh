"""Unit tests for batch splitting and chunk text logic."""

import pytest

from kairix.embed.embed import batched, chunk_text


@pytest.mark.unit
class TestBatched:
    @pytest.mark.unit
    def test_even_split(self):
        items = list(range(300))
        batches = list(batched(items, 100))
        assert len(batches) == 3
        assert all(len(b) == 100 for b in batches)

    @pytest.mark.unit
    def test_remainder(self):
        items = list(range(250))
        batches = list(batched(items, 100))
        assert len(batches) == 3
        assert len(batches[-1]) == 50

    @pytest.mark.unit
    def test_no_items_lost(self):
        items = list(range(847))
        batches = list(batched(items, 100))
        flat = [x for b in batches for x in b]
        assert flat == items

    @pytest.mark.unit
    def test_single_item(self):
        batches = list(batched([42], 100))
        assert batches == [[42]]

    @pytest.mark.unit
    def test_empty(self):
        assert list(batched([], 100)) == []

    @pytest.mark.unit
    def test_batch_larger_than_input(self):
        items = list(range(50))
        batches = list(batched(items, 100))
        assert len(batches) == 1
        assert batches[0] == items


@pytest.mark.unit
class TestChunkText:
    @pytest.mark.unit
    def test_short_doc_is_single_chunk(self):
        text = "Hello world"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0]["seq"] == 0
        assert chunks[0]["pos"] == 0
        assert chunks[0]["text"] == text

    @pytest.mark.unit
    def test_long_doc_splits(self):
        text = "x " * 2000  # well over CHUNK_SIZE_CHARS
        chunks = chunk_text(text)
        assert len(chunks) > 1

    @pytest.mark.unit
    def test_seq_increments(self):
        text = "paragraph\n\n" * 500
        chunks = chunk_text(text)
        seqs = [c["seq"] for c in chunks]
        assert seqs == list(range(len(chunks)))

    @pytest.mark.unit
    def test_pos_increases(self):
        text = "word " * 1000
        chunks = chunk_text(text)
        positions = [c["pos"] for c in chunks]
        assert positions == sorted(positions)

    @pytest.mark.unit
    def test_no_empty_chunks(self):
        text = "content\n\n\n\n" * 300
        chunks = chunk_text(text)
        assert all(len(c["text"]) > 0 for c in chunks)

    @pytest.mark.unit
    def test_exact_chunk_size_boundary(self):
        # At exactly chunk_size, should still be one chunk
        from kairix.embed.embed import CHUNK_SIZE_CHARS

        text = "a" * CHUNK_SIZE_CHARS
        chunks = chunk_text(text)
        assert len(chunks) == 1
