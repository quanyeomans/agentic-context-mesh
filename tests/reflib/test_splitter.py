"""Tests for reference library file splitting and merging."""

import pytest

from kairix.knowledge.reflib.splitter import (
    is_too_small,
    needs_split,
    split_at_headings,
    to_kebab_case,
)


@pytest.mark.unit
class TestNeedsSplit:
    def test_small_file_no_split(self):
        assert not needs_split("Short content")

    def test_large_file_needs_split(self):
        assert needs_split("x" * 60_000)

    def test_custom_threshold(self):
        assert needs_split("x" * 1000, max_size=500)
        assert not needs_split("x" * 100, max_size=500)


@pytest.mark.unit
class TestIsTooSmall:
    def test_empty_is_too_small(self):
        assert is_too_small("")

    def test_whitespace_is_too_small(self):
        assert is_too_small("   \n\n  ")

    def test_short_text_is_too_small(self):
        assert is_too_small("hi")

    def test_substantial_text_not_too_small(self):
        assert not is_too_small("x" * 1000)


@pytest.mark.unit
class TestSplitAtHeadings:
    def test_small_file_not_split(self):
        text = "# One\nContent\n# Two\nMore content"
        parts = split_at_headings(text, "test")
        assert len(parts) == 1
        assert parts[0][0] == "test"

    def test_large_file_split_at_h1(self):
        section1 = "# Section One\n" + "Content A. " * 3000 + "\n"
        section2 = "# Section Two\n" + "Content B. " * 3000 + "\n"
        text = section1 + section2
        parts = split_at_headings(text, "doc", max_size=5000)
        assert len(parts) == 2
        assert "section-one" in parts[0][0]
        assert "section-two" in parts[1][0]

    def test_preserves_content(self):
        section1 = "# First\n" + "A " * 5000 + "\n"
        section2 = "# Second\n" + "B " * 5000 + "\n"
        text = section1 + section2
        parts = split_at_headings(text, "test", max_size=1000)
        all_content = " ".join(p[1] for p in parts)
        assert "First" in all_content
        assert "Second" in all_content

    def test_no_headings_returns_whole(self):
        text = "x" * 60000
        parts = split_at_headings(text, "test")
        assert len(parts) == 1


@pytest.mark.unit
class TestToKebabCase:
    def test_camel_case(self):
        assert to_kebab_case("MyFileName.md") == "my-file-name.md"

    def test_spaces_and_underscores(self):
        assert to_kebab_case("hello_world test.md") == "hello-world-test.md"

    def test_preserves_extension(self):
        assert to_kebab_case("Guide.md") == "guide.md"

    def test_special_characters_removed(self):
        assert to_kebab_case("file (1).md") == "file-1.md"

    def test_collapses_hyphens(self):
        assert to_kebab_case("a---b.md") == "a-b.md"

    def test_all_caps(self):
        assert to_kebab_case("README.md") == "readme.md"
