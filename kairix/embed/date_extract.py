"""
Extract a document date from frontmatter fields or filename patterns.

Priority order:
  1. YAML frontmatter (date / created / updated / created_at / date_added fields)
  2. ISO date pattern YYYY-MM-DD in the file path

Returns an ISO 8601 date string (e.g. "2026-04-09") or None if no reliable
date signal is found.

documents.modified_at is intentionally excluded -- that is the QMD index time,
not the document date, and including it would pollute temporal queries with
false signal.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

# Scan only the first 2 000 characters for frontmatter to keep extraction fast.
_FRONTMATTER_HEAD = 2000

# Fields recognised in YAML frontmatter (in priority order of appearance).
_FRONTMATTER_PATTERN = re.compile(
    r"^[ \t]*(?:date|created|updated|created_at|date_added)[ \t]*:[ \t]*[\"']?"
    r"(\d{4}-\d{2}-\d{2})(?:[T ][\d:]+)?[\"']?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

# ISO date anywhere in a path segment (filename or directory component).
_PATH_DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Year-month only (YYYY-MM) in frontmatter — not followed by a day component.
# Maps to first day of month (YYYY-MM-01) for temporal filtering purposes.
_FRONTMATTER_YEARMONTH_PATTERN = re.compile(
    # Match YYYY-MM date fields but NOT YYYY-MM-DD (negative lookahead for -DD)
    r"^[ \t]*(?:date|created|updated|created_at|date_added)[ \t]*:[ \t]*"
    r"[\"']*([0-9]{4}-[0-9]{2})(?!-[0-9]{2})[\"'\s]*$",
    re.MULTILINE | re.IGNORECASE,
)

# Inclusive validity window: dates must be >= 2000-01-01.
_MIN_DATE = date(2000, 1, 1)


def _is_valid_date(value: str) -> bool:
    """
    Return True if *value* is a real calendar date within the accepted window.

    Accepts dates from 2000-01-01 up to today + 1 day (future-proof for edge
    cases such as documents pre-dated by one day in a different timezone).
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False

    max_date = datetime.now(timezone.utc).date() + timedelta(days=1)
    return _MIN_DATE <= parsed <= max_date


def extract_chunk_date(doc: str, path: str) -> str | None:
    """
    Extract the best available document date.

    Args:
        doc:  Full document text (only the first 2 000 chars are scanned).
        path: File path from the ``documents`` table.

    Returns:
        ISO 8601 date string (``"YYYY-MM-DD"``) or ``None``.
    """
    # 1. Frontmatter
    head = doc[:_FRONTMATTER_HEAD]
    for match in _FRONTMATTER_PATTERN.finditer(head):
        candidate = match.group(1)
        if _is_valid_date(candidate):
            return candidate

    # 2. Year-month frontmatter (YYYY-MM → YYYY-MM-01)
    if not any(_FRONTMATTER_PATTERN.finditer(head)):
        for match in _FRONTMATTER_YEARMONTH_PATTERN.finditer(head):
            ym = match.group(1)  # "YYYY-MM"
            candidate = ym + "-01"
            if _is_valid_date(candidate):
                return candidate

    # 3. Filename / path
    for match in _PATH_DATE_PATTERN.finditer(path):
        candidate = match.group(1)
        if _is_valid_date(candidate):
            return candidate

    return None
