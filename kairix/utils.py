"""Shared utility functions for the kairix package."""

from __future__ import annotations

import re


def slugify(name: str) -> str:
    """Convert a name to a lowercase, hyphenated slug.

    Replaces any run of non-alphanumeric characters with a single hyphen
    and strips leading/trailing hyphens.

    >>> slugify("Marcus Aurelius")
    'marcus-aurelius'
    >>> slugify("OWASP Cheat Sheet Series")
    'owasp-cheat-sheet-series'
    >>> slugify("dbt Labs")
    'dbt-labs'
    >>> slugify("")
    ''
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def display_name(slug: str) -> str:
    """Convert a slug or filename to a human-readable display name.

    Replaces hyphens and underscores with spaces and title-cases the result.

    >>> display_name("marcus-aurelius")
    'Marcus Aurelius'
    >>> display_name("owasp_cheat_sheet")
    'Owasp Cheat Sheet'
    >>> display_name("")
    ''
    """
    return slug.replace("-", " ").replace("_", " ").strip().title()
