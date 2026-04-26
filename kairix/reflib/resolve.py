"""Entity resolution — dedup, merge aliases, and fuzzy-match raw entities.

Takes the raw entity list from ``extract.py`` and produces a resolved,
deduplicated list ready for Neo4j stub emission.  Uses Levenshtein
similarity (>=0.85 within the same type) for fuzzy matching.  No external
NLP libraries — just string distance.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from kairix.reflib.extract import RawEntity


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class ResolvedEntity:
    """A deduplicated, canonical entity ready for graph loading."""

    id: str  # slug
    canonical_name: str
    entity_type: str
    description: str = ""
    domains: list[str] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _to_slug(name: str) -> str:
    """Convert a name to a lowercase, hyphenated slug.

    >>> _to_slug("Marcus Aurelius")
    'marcus-aurelius'
    >>> _to_slug("OWASP Cheat Sheet Series")
    'owasp-cheat-sheet-series'
    >>> _to_slug("dbt Labs")
    'dbt-labs'
    """
    s = name.lower().strip()
    s = _NON_ALNUM.sub("-", s)
    return s.strip("-")


# ---------------------------------------------------------------------------
# Levenshtein distance (pure Python, no external deps)
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _similarity(a: str, b: str) -> float:
    """Normalised similarity between two strings (0.0–1.0)."""
    if a == b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - _levenshtein(a, b) / max_len


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------


def _merge_lists(*lists: list[str]) -> list[str]:
    """Merge multiple lists, preserving order and removing duplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for lst in lists:
        for item in lst:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
    return result


def _pick_canonical(names: list[str]) -> str:
    """Pick the best canonical name from a list of candidates.

    Prefers: longest non-acronym name, then most common.
    """
    if not names:
        return ""
    # Count occurrences
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1

    # Sort by: not-all-upper first (prefer expanded names), then longest, then most frequent
    candidates = sorted(
        counts.keys(),
        key=lambda n: (not n.isupper(), len(n), counts[n]),
        reverse=True,
    )
    return candidates[0]


# ---------------------------------------------------------------------------
# Resolution pipeline
# ---------------------------------------------------------------------------


def resolve_entities(raw: list[RawEntity]) -> list[ResolvedEntity]:
    """Resolve raw entities into deduplicated canonical entities.

    Steps:
    1. Group by (slug, entity_type) — exact dedup
    2. Merge aliases and source docs within each group
    3. Fuzzy-match groups within same type (Levenshtein >= 0.85)
    4. Produce final ResolvedEntity list

    Args:
        raw: List of raw extracted entities.

    Returns:
        List of resolved, deduplicated entities.
    """
    # Step 1: group by (slug, type)
    groups: dict[tuple[str, str], list[RawEntity]] = defaultdict(list)
    for entity in raw:
        slug = _to_slug(entity.name)
        if not slug:
            continue
        groups[(slug, entity.entity_type)].append(entity)

    # Step 2: merge within each group
    merged: dict[tuple[str, str], ResolvedEntity] = {}
    for (slug, etype), members in groups.items():
        names = [m.name for m in members]
        canonical = _pick_canonical(names)
        all_aliases = _merge_lists(*[m.aliases for m in members])
        # Add non-canonical names as aliases
        for n in names:
            if n != canonical and n not in all_aliases:
                all_aliases.append(n)

        all_domains = _merge_lists(*[m.domains for m in members])
        all_docs = _merge_lists(*[m.source_docs for m in members])
        best_conf = max(m.confidence for m in members)
        # Use the longest description
        desc = max((m.description for m in members), key=len, default="")

        merged[(slug, etype)] = ResolvedEntity(
            id=slug,
            canonical_name=canonical,
            entity_type=etype,
            description=desc,
            domains=all_domains,
            source_docs=all_docs,
            aliases=all_aliases,
            confidence=best_conf,
        )

    # Step 3: fuzzy-match within same type
    # Skip fuzzy matching for high-cardinality types where O(n^2) is too slow
    # and fuzzy dedup adds little value (concepts from headings, individual docs).
    _SKIP_FUZZY_TYPES = {"Concept", "Document"}

    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in merged:
        by_type[key[1]].append(key)

    merge_map: dict[tuple[str, str], tuple[str, str]] = {}  # victim -> winner

    for etype, keys in by_type.items():
        if etype in _SKIP_FUZZY_TYPES:
            continue
        slugs = sorted(keys, key=lambda k: k[0])
        # Safety: skip if group is very large (>2000) to avoid long runtime
        if len(slugs) > 2000:
            continue
        for i in range(len(slugs)):
            if slugs[i] in merge_map:
                continue
            for j in range(i + 1, len(slugs)):
                if slugs[j] in merge_map:
                    continue
                sim = _similarity(slugs[i][0], slugs[j][0])
                if sim >= 0.85:
                    # Merge j into i (keep the one with more source docs)
                    a = merged[slugs[i]]
                    b = merged[slugs[j]]
                    if len(b.source_docs) > len(a.source_docs):
                        merge_map[slugs[i]] = slugs[j]
                    else:
                        merge_map[slugs[j]] = slugs[i]

    # Apply merges
    for victim, winner in merge_map.items():
        # Follow chains
        while winner in merge_map:
            winner = merge_map[winner]
        v = merged.pop(victim)
        w = merged[winner]
        w.source_docs = _merge_lists(w.source_docs, v.source_docs)
        w.domains = _merge_lists(w.domains, v.domains)
        w.aliases = _merge_lists(w.aliases, [v.canonical_name], v.aliases)
        w.confidence = max(w.confidence, v.confidence)
        if len(v.description) > len(w.description):
            w.description = v.description

    # Step 4: produce final list, sorted by id
    return sorted(merged.values(), key=lambda e: (e.entity_type, e.id))
