"""
Shared mock retrieval engine for deterministic benchmark fixtures.

Provides tokenisation, keyword index building, and keyword-overlap retrieval
used by both ``mock_retrieval`` (vault fixtures) and ``mock_reflib_retrieval``
(reference-library fixtures). Extracting the shared logic here eliminates
duplication and ensures both mock backends evolve together.
"""

from __future__ import annotations

import re


def tokenise(text: str) -> set[str]:
    """Extract lowercase word tokens (including hyphenated compounds) from text."""
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()))


def build_keyword_index(
    fixture_docs: list[dict],
    keyword_field: str = "keywords",
) -> dict[str, list[int]]:
    """Build a keyword -> document-index mapping for O(k) lookup per query."""
    index: dict[str, list[int]] = {}
    for i, doc in enumerate(fixture_docs):
        for kw in doc[keyword_field]:
            index.setdefault(kw.lower(), []).append(i)
    return index


def mock_retrieve(
    fixture_docs: list[dict],
    keyword_index: dict[str, list[int]],
    query: str,
    limit: int,
    system_name: str,
    snippet_field: str = "snippet",
) -> tuple[list[str], list[str], dict]:
    """
    Return fixture documents whose keywords overlap with the query.

    Scoring: count of matching keywords. Ties broken by fixture index order.
    Returns ``(paths, snippets, metadata)`` matching the ``_retrieve()`` contract
    in ``runner.py``.
    """
    query_tokens = tokenise(query)
    scores: dict[int, int] = {}

    for token in query_tokens:
        for doc_idx in keyword_index.get(token, []):
            scores[doc_idx] = scores.get(doc_idx, 0) + 1

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = ranked[:limit]

    paths = [fixture_docs[i]["path"] for i, _ in top]
    snippets = [fixture_docs[i][snippet_field][:500] for i, _ in top]
    meta = {
        "system": system_name,
        "n_matched": len(top),
        "query_tokens": sorted(query_tokens),
    }
    return paths, snippets, meta
