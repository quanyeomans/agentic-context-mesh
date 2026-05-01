"""
Unit tests for kairix.core.embed.date_extract.extract_chunk_date.

Targets >= 90% coverage of date_extract.py.
"""

from __future__ import annotations

import pytest

from kairix.core.embed.date_extract import extract_chunk_date

# ---------------------------------------------------------------------------
# Frontmatter: date field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frontmatter_date_unquoted() -> None:
    """date: YYYY-MM-DD (no quotes) is extracted."""
    doc = "---\ntitle: My Doc\ndate: 2026-03-15\n---\nBody text."
    assert extract_chunk_date(doc, "/notes/somefile.md") == "2026-03-15"


@pytest.mark.unit
def test_frontmatter_date_single_quotes() -> None:
    """date: 'YYYY-MM-DD' (single quotes) is extracted."""
    doc = "---\ndate: '2025-11-01'\n---\nBody."
    assert extract_chunk_date(doc, "/notes/x.md") == "2025-11-01"


@pytest.mark.unit
def test_frontmatter_date_double_quotes() -> None:
    """date: "YYYY-MM-DD" (double quotes) is extracted."""
    doc = '---\ndate: "2024-06-20"\n---\nBody.'
    assert extract_chunk_date(doc, "/notes/x.md") == "2024-06-20"


@pytest.mark.unit
def test_frontmatter_date_datetime_takes_date_part() -> None:
    """date: YYYY-MM-DD HH:MM:SS — only the date portion is returned."""
    doc = "---\ndate: 2026-01-10 14:30:00\n---\nContent."
    assert extract_chunk_date(doc, "/notes/x.md") == "2026-01-10"


@pytest.mark.unit
def test_frontmatter_created_field() -> None:
    """created: YYYY-MM-DD is recognised."""
    doc = "---\ncreated: 2023-07-04\n---\nBody."
    assert extract_chunk_date(doc, "/notes/x.md") == "2023-07-04"


@pytest.mark.unit
def test_frontmatter_updated_field() -> None:
    """updated: YYYY-MM-DD is recognised."""
    doc = "---\nupdated: 2022-12-31\n---\nBody."
    assert extract_chunk_date(doc, "/notes/x.md") == "2022-12-31"


@pytest.mark.unit
def test_frontmatter_created_at_field() -> None:
    """created_at: YYYY-MM-DD is recognised."""
    doc = "---\ncreated_at: 2021-05-19\n---\nBody."
    assert extract_chunk_date(doc, "/notes/x.md") == "2021-05-19"


# ---------------------------------------------------------------------------
# Filename / path patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_path_date_prefix() -> None:
    """YYYY-MM-DD at the start of the filename is found."""
    assert extract_chunk_date("", "/notes/2026-04-09-standup.md") == "2026-04-09"


@pytest.mark.unit
def test_path_date_middle() -> None:
    """YYYY-MM-DD embedded in a directory name is found."""
    assert extract_chunk_date("", "/archive/2025-01-01/index.md") == "2025-01-01"


@pytest.mark.unit
def test_path_date_suffix() -> None:
    """YYYY-MM-DD at the end of the stem is found."""
    assert extract_chunk_date("", "/reports/quarterly-2023-10-01.md") == "2023-10-01"


# ---------------------------------------------------------------------------
# Priority: frontmatter > filename
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frontmatter_wins_over_path() -> None:
    """When both frontmatter and path contain dates, frontmatter takes priority."""
    doc = "---\ndate: 2026-03-01\n---\nBody."
    result = extract_chunk_date(doc, "/notes/2020-01-01-old.md")
    assert result == "2026-03-01"


# ---------------------------------------------------------------------------
# Invalid / out-of-range dates
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invalid_date_string_falls_through_to_path() -> None:
    """An invalid frontmatter date (not-a-date) is skipped; path date is used."""
    doc = "---\ndate: not-a-date\n---\nBody."
    assert extract_chunk_date(doc, "/notes/2024-08-15-notes.md") == "2024-08-15"


@pytest.mark.unit
def test_out_of_range_year_before_2000() -> None:
    """A date before 2000-01-01 is rejected entirely."""
    doc = "---\ndate: 1999-12-31\n---\nBody."
    assert extract_chunk_date(doc, "/notes/no-date.md") is None


@pytest.mark.unit
def test_impossible_date_like_9999() -> None:
    """A year-9999 date is out of range and returns None."""
    doc = "---\ndate: 9999-99-99\n---\nBody."
    assert extract_chunk_date(doc, "/notes/no-date.md") is None


# ---------------------------------------------------------------------------
# No date anywhere
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_date_anywhere_returns_none() -> None:
    """When neither doc nor path contain a date, None is returned."""
    assert extract_chunk_date("Just some text.", "/notes/meeting.md") is None


@pytest.mark.unit
def test_empty_doc_and_path_returns_none() -> None:
    """Empty strings produce None without errors."""
    assert extract_chunk_date("", "") is None


# ---------------------------------------------------------------------------
# Frontmatter beyond first 2000 chars is not scanned
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frontmatter_beyond_2000_chars_not_matched() -> None:
    """A date buried after 2000 chars is ignored (only head is scanned)."""
    padding = "x" * 2001
    doc = padding + "\ndate: 2026-01-01\n"
    assert extract_chunk_date(doc, "/notes/no-date.md") is None


# ---------------------------------------------------------------------------
# Datetime format in frontmatter (T separator)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_frontmatter_date_iso_t_separator() -> None:
    """date: YYYY-MM-DDTHH:MM:SS (T separator) — only the date part is captured."""
    doc = "---\ndate: 2026-02-28T09:00:00\n---\nBody."
    assert extract_chunk_date(doc, "/notes/x.md") == "2026-02-28"


# ---------------------------------------------------------------------------
# TMP-5b: YYYY-MM year-month frontmatter handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_yearmonth_frontmatter_returns_first_of_month() -> None:
    """YYYY-MM in date field maps to YYYY-MM-01."""
    doc = "---\ndate: 2025-11\n---\n# Body"
    result = extract_chunk_date(doc, "test.md")
    assert result == "2025-11-01"


@pytest.mark.unit
def test_yearmonth_does_not_override_full_iso_date() -> None:
    """Full YYYY-MM-DD in frontmatter takes priority over YYYY-MM."""
    doc = "---\ndate: 2026-04-10\n---\n# Body"
    assert extract_chunk_date(doc, "test.md") == "2026-04-10"


@pytest.mark.unit
def test_yearmonth_created_field() -> None:
    """created field also matches YYYY-MM."""
    doc = "---\ncreated: 2025-07\n---\n# Body"
    assert extract_chunk_date(doc, "test.md") == "2025-07-01"


@pytest.mark.unit
def test_yearmonth_updated_field() -> None:
    """updated field also matches YYYY-MM."""
    doc = "---\nupdated: 2024-12\n---\n# Body"
    assert extract_chunk_date(doc, "test.md") == "2024-12-01"


@pytest.mark.unit
def test_yearmonth_path_date_fallback_still_works() -> None:
    """Filename YYYY-MM-DD still takes effect when no frontmatter match."""
    doc = "# No frontmatter\nJust body."
    assert extract_chunk_date(doc, "2026-04-08-daily.md") == "2026-04-08"


@pytest.mark.unit
def test_yearmonth_not_matched_if_dd_follows() -> None:
    """YYYY-MM-DD must NOT be matched by yearmonth pattern."""
    from kairix.core.embed.date_extract import _FRONTMATTER_YEARMONTH_PATTERN

    assert _FRONTMATTER_YEARMONTH_PATTERN.search("date: 2026-04-10") is None


# ---------------------------------------------------------------------------
# date_added frontmatter field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_date_added_frontmatter():
    """date_added: YYYY-MM-DD is recognised as a valid frontmatter date field."""
    doc = "---\ndate_added: 2026-03-15\ntags: [test]\n---\nContent here"
    assert extract_chunk_date(doc, "notes/test.md") == "2026-03-15"
