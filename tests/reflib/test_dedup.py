"""Tests for reference library deduplication."""

from pathlib import Path

import pytest

from kairix.knowledge.reflib.dedup import (
    choose_canonical,
    find_exact_duplicates,
    hash_content,
    jaccard_similarity,
)


@pytest.mark.unit
class TestHashContent:
    def test_same_content_same_hash(self):
        assert hash_content("Hello world") == hash_content("Hello world")

    def test_different_content_different_hash(self):
        assert hash_content("Hello") != hash_content("World")


@pytest.mark.unit
class TestJaccardSimilarity:
    def test_identical_texts(self):
        assert jaccard_similarity("the quick brown fox", "the quick brown fox") == 1.0

    def test_similar_texts(self):
        sim = jaccard_similarity("the quick brown fox", "the quick brown dog")
        assert 0.5 < sim < 1.0

    def test_completely_different(self):
        sim = jaccard_similarity("aaa bbb ccc", "xxx yyy zzz")
        assert sim < 0.1

    def test_empty_text(self):
        # Empty strings produce empty shingle sets
        assert jaccard_similarity("", "abc def ghi") == 0.0


@pytest.mark.unit
class TestFindExactDuplicates:
    def test_finds_duplicates(self):
        files = [
            (Path("a/doc.md"), "hash1"),
            (Path("b/doc.md"), "hash1"),
            (Path("c/other.md"), "hash2"),
        ]
        dupes = find_exact_duplicates(files)
        assert "hash1" in dupes
        assert len(dupes["hash1"]) == 2
        assert "hash2" not in dupes

    def test_no_duplicates(self):
        files = [
            (Path("a.md"), "hash1"),
            (Path("b.md"), "hash2"),
        ]
        assert find_exact_duplicates(files) == {}


@pytest.mark.unit
class TestChooseCanonical:
    def test_prefers_longer_body(self):
        paths = [Path("short.md"), Path("long.md")]
        bodies = {Path("short.md"): "x" * 10, Path("long.md"): "x" * 1000}
        result = choose_canonical(paths, bodies)
        assert result == Path("long.md")

    def test_single_path(self):
        assert choose_canonical([Path("only.md")]) == Path("only.md")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            choose_canonical([])
