"""Tests for reference library frontmatter generation."""

from pathlib import Path

import pytest

from kairix.reflib.frontmatter import (
    Frontmatter,
    build_frontmatter,
    extract_existing_frontmatter,
    extract_title,
    inject_frontmatter,
)
from kairix.reflib.sources import SourceDef

_TEST_SOURCE = SourceDef(
    name="Test Source",
    collection="test-collection",
    dir_name="test-dir",
    licence="MIT",
    licence_tier=2,
    source_url="https://github.com/test/repo",
)


@pytest.mark.unit
class TestExtractExistingFrontmatter:
    def test_parses_yaml_block(self):
        text = "---\ntitle: My Doc\nauthor: Jane\n---\n\nBody content."
        fm, body = extract_existing_frontmatter(text)
        assert fm is not None
        assert fm["title"] == "My Doc"
        assert fm["author"] == "Jane"
        assert "Body content." in body

    def test_no_frontmatter_returns_none(self):
        text = "# Just a heading\n\nBody."
        fm, body = extract_existing_frontmatter(text)
        assert fm is None
        assert body == text

    def test_strips_quotes_from_values(self):
        text = '---\ntitle: "Quoted Title"\n---\n\nBody.'
        fm, _body = extract_existing_frontmatter(text)
        assert fm["title"] == "Quoted Title"


@pytest.mark.unit
class TestExtractTitle:
    def test_from_frontmatter(self):
        text = "---\ntitle: FM Title\n---\n\n# Heading\n\nBody."
        assert extract_title(text, Path("doc.md")) == "FM Title"

    def test_from_heading(self):
        text = "# My Heading\n\nBody content."
        assert extract_title(text, Path("doc.md")) == "My Heading"

    def test_from_filename(self):
        text = "No heading, just body content."
        assert extract_title(text, Path("my-great-doc.md")) == "My Great Doc"

    def test_heading_strips_links(self):
        text = "# [Linked Heading](http://example.com)\n\nBody."
        assert extract_title(text, Path("doc.md")) == "Linked Heading"


@pytest.mark.unit
class TestBuildFrontmatter:
    def test_builds_from_source(self):
        text = "# Test Document\n\nContent."
        fm = build_frontmatter(Path("test.md"), _TEST_SOURCE, text)
        assert fm.title == "Test Document"
        assert fm.source == "Test Source"
        assert fm.licence == "MIT"
        assert fm.domain == "test-collection"
        assert fm.subdomain == "test-dir"


@pytest.mark.unit
class TestInjectFrontmatter:
    def test_adds_frontmatter_to_bare_markdown(self):
        text = "# Document\n\nContent."
        fm = Frontmatter(
            title="Document",
            source="Test",
            source_url="https://example.com",
            licence="MIT",
            domain="test",
            subdomain="sub",
            date_added="2026-04-25",
        )
        result = inject_frontmatter(text, fm)
        assert result.startswith("---\n")
        assert 'title: "Document"' in result
        assert "# Document" in result
        assert "Content." in result

    def test_replaces_existing_frontmatter(self):
        text = "---\ntitle: Old Title\n---\n\n# Doc\n\nBody."
        fm = Frontmatter(
            title="New Title",
            source="Test",
            source_url="https://example.com",
            licence="MIT",
            domain="test",
            subdomain="sub",
            date_added="2026-04-25",
        )
        result = inject_frontmatter(text, fm)
        assert "Old Title" not in result
        assert "New Title" in result
        assert "# Doc" in result
