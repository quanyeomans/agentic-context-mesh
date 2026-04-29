"""Cross-collection deduplication for reference library normalisation.

Detects exact duplicates (SHA-256) and near-duplicates (Jaccard on shingles).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path


def hash_content(text: str) -> str:
    """SHA-256 hash of text content (after frontmatter strip)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _shingle(text: str, n: int = 3) -> set[str]:
    """Generate character n-gram shingles from text."""
    text = text.lower()
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard_similarity(a: str, b: str, n: int = 3) -> float:
    """Compute Jaccard similarity between two texts using n-gram shingles."""
    shingles_a = _shingle(a, n)
    shingles_b = _shingle(b, n)
    if not shingles_a or not shingles_b:
        return 0.0
    intersection = len(shingles_a & shingles_b)
    union = len(shingles_a | shingles_b)
    return intersection / union if union > 0 else 0.0


def find_exact_duplicates(
    files: list[tuple[Path, str]],
) -> dict[str, list[Path]]:
    """Find files with identical content hashes.

    Args:
        files: List of (path, content_hash) tuples.

    Returns:
        Dict mapping content_hash -> list of duplicate paths (only groups with 2+).
    """
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path, content_hash in files:
        by_hash[content_hash].append(path)
    return {h: paths for h, paths in by_hash.items() if len(paths) > 1}


def find_near_duplicates(
    files: list[tuple[Path, str]],
    threshold: float = 0.9,
) -> list[tuple[Path, Path, float]]:
    """Find files with high content similarity (near-duplicates).

    Uses Jaccard similarity on 3-gram shingles. Only compares files
    within the same filename stem (different collections may have
    files with the same name but different content).

    Args:
        files: List of (path, body_text) tuples.
        threshold: Minimum Jaccard similarity to flag as near-duplicate.

    Returns:
        List of (path_a, path_b, similarity) tuples.
    """
    # Group by filename stem to limit comparison space
    by_stem: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for path, body in files:
        stem = path.stem.lower()
        by_stem[stem].append((path, body))

    duplicates: list[tuple[Path, Path, float]] = []
    for stem_files in by_stem.values():
        if len(stem_files) < 2:
            continue
        for i in range(len(stem_files)):
            for j in range(i + 1, len(stem_files)):
                path_a, body_a = stem_files[i]
                path_b, body_b = stem_files[j]
                sim = jaccard_similarity(body_a, body_b)
                if sim >= threshold:
                    duplicates.append((path_a, path_b, sim))

    return duplicates


def choose_canonical(paths: list[Path], bodies: dict[Path, str] | None = None) -> Path:
    """Choose the canonical file from a group of duplicates.

    Priority:
    1. Prefer files with longer body (more complete)
    2. Prefer files earlier alphabetically by collection name (stable ordering)
    """
    if not paths:
        raise ValueError("Empty path list")
    if len(paths) == 1:
        return paths[0]

    def sort_key(p: Path) -> tuple[int, str]:
        body_len = len(bodies[p]) if bodies and p in bodies else 0
        return (-body_len, str(p))

    return sorted(paths, key=sort_key)[0]
