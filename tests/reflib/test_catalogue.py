"""Unit tests for kairix.knowledge.reflib.catalogue — CATALOGUE.md and LICENSE-NOTICES.md generation."""

from __future__ import annotations

from datetime import date

import pytest

from kairix.knowledge.reflib.catalogue import CatalogueEntry, generate_catalogue, generate_licence_notices


def _make_entries() -> list[CatalogueEntry]:
    """Three entries across two collections."""
    return [
        CatalogueEntry(
            collection="engineering",
            source_name="ADR Examples",
            source_url="https://example.com/adr",
            licence="CC0-1.0",
            licence_tier=1,
            file_count=20,
            total_size_kb=150.0,
            date_verified="2025-01-01",
        ),
        CatalogueEntry(
            collection="engineering",
            source_name="Design Patterns",
            source_url="https://example.com/patterns",
            licence="MIT",
            licence_tier=2,
            file_count=35,
            total_size_kb=420.5,
            date_verified="2025-02-15",
        ),
        CatalogueEntry(
            collection="philosophy",
            source_name="Stoic Texts",
            source_url="https://example.com/stoic",
            licence="CC-BY-4.0",
            licence_tier=3,
            file_count=10,
            total_size_kb=80.0,
            date_verified="2025-03-10",
        ),
    ]


class TestGenerateCatalogue:
    @pytest.mark.unit
    def test_header_present(self):
        md = generate_catalogue(_make_entries())
        assert "# Reference Library Catalogue" in md

    @pytest.mark.unit
    def test_generated_date(self):
        md = generate_catalogue(_make_entries())
        assert f"Generated: {date.today().isoformat()}" in md

    @pytest.mark.unit
    def test_collection_sections(self):
        md = generate_catalogue(_make_entries())
        assert "## engineering/" in md
        assert "## philosophy/" in md

    @pytest.mark.unit
    def test_table_headers(self):
        md = generate_catalogue(_make_entries())
        assert "| Source | Licence | Files | Size (KB) | Verified | URL |" in md

    @pytest.mark.unit
    def test_entry_rows(self):
        md = generate_catalogue(_make_entries())
        assert "ADR Examples" in md
        assert "Design Patterns" in md
        assert "Stoic Texts" in md

    @pytest.mark.unit
    def test_total_summary(self):
        md = generate_catalogue(_make_entries())
        # 20 + 35 + 10 = 65 files, 150 + 420.5 + 80 = 650.5 → 650 KB
        assert "65 files" in md
        assert "2 collections" in md

    @pytest.mark.unit
    def test_collection_file_counts(self):
        md = generate_catalogue(_make_entries())
        # engineering: 20 + 35 = 55 files
        assert "55 files" in md
        # philosophy: 10 files
        assert "10 files" in md

    @pytest.mark.unit
    def test_sorted_collections(self):
        md = generate_catalogue(_make_entries())
        eng_pos = md.index("## engineering/")
        phil_pos = md.index("## philosophy/")
        assert eng_pos < phil_pos

    @pytest.mark.unit
    def test_sorted_entries_within_collection(self):
        md = generate_catalogue(_make_entries())
        adr_pos = md.index("ADR Examples")
        dp_pos = md.index("Design Patterns")
        assert adr_pos < dp_pos

    @pytest.mark.unit
    def test_empty_entries(self):
        md = generate_catalogue([])
        assert "# Reference Library Catalogue" in md


class TestGenerateLicenceNotices:
    @pytest.mark.unit
    def test_tier1_section(self):
        md = generate_licence_notices(_make_entries())
        assert "## Public Domain / CC0 (Tier 1)" in md
        assert "- ADR Examples (CC0-1.0)" in md

    @pytest.mark.unit
    def test_tier2_section(self):
        md = generate_licence_notices(_make_entries())
        assert "## MIT / Apache 2.0 / MPL (Tier 2)" in md
        assert "### Design Patterns" in md
        assert "Licence: MIT" in md

    @pytest.mark.unit
    def test_tier3_section(self):
        md = generate_licence_notices(_make_entries())
        assert "## CC-BY (Tier 3)" in md
        assert "### Stoic Texts" in md
        assert "CC-BY-4.0" in md

    @pytest.mark.unit
    def test_tier3_attribution(self):
        md = generate_licence_notices(_make_entries())
        assert "Attribution: Stoic Texts" in md

    @pytest.mark.unit
    def test_generated_date(self):
        md = generate_licence_notices(_make_entries())
        assert f"Generated: {date.today().isoformat()}" in md

    @pytest.mark.unit
    def test_empty_entries(self):
        md = generate_licence_notices([])
        assert "# Licence Notices" in md
