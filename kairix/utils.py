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
