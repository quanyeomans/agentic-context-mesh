"""Shared FTS5 query tokenizer for the kairix search pipeline.

Provides a single entry point for building FTS5 query strings from
natural-language input, supporting three styles:

- **bare**: space-separated lowercase tokens (implicit AND)
- **prefix**: ``"token"*`` per token, AND-joined (production default)
- **quoted**: ``"token"`` per token, AND-joined (exact match)

All styles normalise punctuation, remove stop words, and filter
short tokens (< 2 chars).
"""

from __future__ import annotations

import re

from kairix.core.search.bm25 import FTS_STOP_WORDS


def tokenize_fts_query(query: str, style: str = "prefix") -> str | None:
    """Build an FTS5 query string from natural-language input.

    Args:
        query: Raw user query string.
        style: One of ``"bare"``, ``"prefix"``, or ``"quoted"``.

    Returns:
        FTS5 query string, or ``None`` if no meaningful tokens remain.
    """
    # Replace hyphens, underscores, and apostrophes with spaces
    raw = query.replace("-", " ").replace("_", " ").replace("'", " ").replace("\u2019", " ")
    # Extract word tokens (alphanumeric sequences only)
    tokens = re.findall(r"[a-zA-Z0-9]+", raw.lower())
    # Filter stop words and very short tokens
    tokens = [t for t in tokens if t not in FTS_STOP_WORDS and len(t) >= 2]
    if not tokens:
        return None

    if style == "bare":
        return " ".join(tokens)
    elif style == "prefix":
        return " AND ".join(f'"{t}"*' for t in tokens)
    elif style == "quoted":
        return " AND ".join(f'"{t}"' for t in tokens)
    else:
        # Unknown style — fall back to bare
        return " ".join(tokens)
