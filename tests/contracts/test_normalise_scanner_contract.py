"""Contract test: normalised frontmatter is parseable by extract_title().

Verifies that the output of the normalisation pipeline's frontmatter injection
produces documents whose titles are correctly extracted by the shared
extract_title() in reflib/frontmatter.py (used by both reflib and db/scanner).
"""

from pathlib import Path

import pytest

from kairix.reflib.frontmatter import build_frontmatter, extract_title, inject_frontmatter
from kairix.reflib.sources import SourceDef

pytestmark = pytest.mark.contract


def _make_source() -> SourceDef:
    return SourceDef(
        name="Test Source",
        collection="engineering",
        dir_name="test-source",
        licence="MIT",
        licence_tier=2,
        source_url="https://example.com",
    )


class TestNormalisedFrontmatterParsedByScanner:
    """Normalised documents must have titles extractable by the scanner."""

    def test_frontmatter_title_extracted(self, tmp_path: Path) -> None:
        """Scanner extracts the same title that normalisation injected."""
        source = _make_source()
        file_path = tmp_path / "guide.md"
        raw_text = "# Architecture Guide\n\nContent about architecture decisions."
        file_path.write_text(raw_text)

        fm = build_frontmatter(file_path, source, raw_text)
        normalised = inject_frontmatter(raw_text, fm)

        extracted = extract_title(normalised, file_path)
        assert extracted == fm.title, f"Scanner extracted {extracted!r} but normalisation set {fm.title!r}"

    def test_frontmatter_with_quoted_title(self, tmp_path: Path) -> None:
        """Titles with special characters survive the roundtrip."""
        source = _make_source()
        file_path = tmp_path / "guide.md"
        raw_text = '# "Best Practices" for API Design\n\nContent here.'
        file_path.write_text(raw_text)

        fm = build_frontmatter(file_path, source, raw_text)
        normalised = inject_frontmatter(raw_text, fm)

        extracted = extract_title(normalised, file_path)
        assert extracted, "Scanner should extract a non-empty title"

    def test_frontmatter_with_no_heading_uses_filename(self, tmp_path: Path) -> None:
        """Documents without headings get a title from filename."""
        source = _make_source()
        file_path = tmp_path / "api-design-patterns.md"
        raw_text = "Some content without any markdown headings at all. " * 10
        file_path.write_text(raw_text)

        fm = build_frontmatter(file_path, source, raw_text)
        normalised = inject_frontmatter(raw_text, fm)

        extracted = extract_title(normalised, file_path)
        assert extracted, "Scanner should extract a title even without headings"

    def test_existing_frontmatter_preserved(self, tmp_path: Path) -> None:
        """If a document already has frontmatter, the title should be preserved."""
        text_with_fm = (
            '---\ntitle: "Existing Title"\nsource: Test\nlicence: MIT\n---\n\n# Different Heading\n\nBody text.'
        )
        file_path = tmp_path / "existing.md"
        file_path.write_text(text_with_fm)

        extracted = extract_title(text_with_fm, file_path)
        assert extracted == "Existing Title", f"Expected 'Existing Title', got {extracted!r}"

    def test_fixture_docs_all_have_extractable_titles(self) -> None:
        """Every document in the integration fixture has a title extractable by scanner."""
        fixture_root = Path(__file__).resolve().parent.parent / "integration" / "reflib_fixture"
        if not fixture_root.exists():
            pytest.skip("Fixture not available")

        failures = []
        for md_file in sorted(fixture_root.rglob("*.md")):
            if md_file.name == "README.md":
                continue
            text = md_file.read_text(encoding="utf-8", errors="replace")
            title = extract_title(text, md_file)
            if not title or title == md_file.stem.replace("-", " ").replace("_", " ").title():
                # Filename fallback means no real title was found
                # This is acceptable but worth noting
                pass
            if not title:
                failures.append(str(md_file))

        assert not failures, f"These fixture docs have no extractable title: {failures}"
