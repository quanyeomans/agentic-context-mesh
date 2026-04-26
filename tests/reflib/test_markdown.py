"""Tests for reference library markdown cleanup."""

import pytest

from kairix.reflib.markdown import (
    clean_markdown,
    collapse_blank_lines,
    normalise_line_endings,
    strip_badges,
    strip_gutenberg_boilerplate,
    strip_html_tags,
)


@pytest.mark.unit
class TestStripBadges:
    def test_removes_linked_badge(self):
        text = "Hello [![Build](https://img.shields.io/badge/build-passing)](https://ci.example.com) world"
        assert "[![" not in strip_badges(text)
        assert "Hello" in strip_badges(text)
        assert "world" in strip_badges(text)

    def test_removes_status_badge(self):
        text = "![ci status](https://github.com/org/repo/actions/badge.svg)"
        assert strip_badges(text).strip() == ""

    def test_preserves_normal_images(self):
        text = "![Architecture diagram](./images/arch.png)"
        assert "Architecture diagram" in strip_badges(text)


@pytest.mark.unit
class TestStripHtml:
    def test_strips_div_tags(self):
        text = "<div class='note'>Important content</div>"
        result = strip_html_tags(text)
        assert "<div" not in result
        assert "Important content" in result

    def test_strips_span_tags(self):
        text = "Hello <span style='color:red'>world</span>"
        result = strip_html_tags(text)
        assert "<span" not in result
        assert "Hello" in result
        assert "world" in result

    def test_converts_anchor_to_markdown(self):
        text = 'See <a href="https://example.com">the docs</a> for details.'
        result = strip_html_tags(text)
        assert "[the docs](https://example.com)" in result
        assert "<a " not in result

    def test_strips_comments(self):
        text = "Before <!-- hidden --> After"
        result = strip_html_tags(text)
        assert "hidden" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_img_tags(self):
        text = 'Text <img src="pic.png" alt="photo"> more text'
        result = strip_html_tags(text)
        assert "<img" not in result


@pytest.mark.unit
class TestGutenbergBoilerplate:
    def test_strips_header_and_footer(self):
        text = (
            "Project Gutenberg header stuff\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n"
            "Real content here.\n"
            "More real content.\n"
            "*** END OF THE PROJECT GUTENBERG EBOOK MEDITATIONS ***\n"
            "Small print footer."
        )
        result = strip_gutenberg_boilerplate(text)
        assert "Real content here." in result
        assert "More real content." in result
        assert "header stuff" not in result
        assert "Small print footer" not in result
        assert "***" not in result

    def test_no_markers_returns_unchanged(self):
        text = "Normal document content."
        assert strip_gutenberg_boilerplate(text) == text


@pytest.mark.unit
class TestLineEndings:
    def test_crlf_to_lf(self):
        text = "Line one\r\nLine two\r\n"
        result = normalise_line_endings(text)
        assert "\r" not in result
        assert "Line one\nLine two\n" == result


@pytest.mark.unit
class TestCollapseBlankLines:
    def test_collapses_excessive_blanks(self):
        text = "Para 1\n\n\n\n\n\nPara 2"
        result = collapse_blank_lines(text)
        # Should be at most 3 newlines (2 blank lines)
        assert "\n\n\n\n" not in result
        assert "Para 1" in result
        assert "Para 2" in result

    def test_preserves_double_blank(self):
        text = "Para 1\n\n\nPara 2"
        assert collapse_blank_lines(text) == text


@pytest.mark.unit
class TestCleanMarkdown:
    def test_combined_cleanup(self):
        text = (
            "<div>Hello</div>\r\n"
            "[![badge](https://img.shields.io/badge)](link)\r\n"
            "\r\n\r\n\r\n\r\n\r\n"
            "Content here."
        )
        result = clean_markdown(text)
        assert "<div>" not in result
        assert "\r" not in result
        assert "[![" not in result
        assert "Hello" in result
        assert "Content here." in result
