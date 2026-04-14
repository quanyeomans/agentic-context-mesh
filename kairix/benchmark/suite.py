"""
Benchmark suite loader and validator.

Loads YAML suite files and validates them against the QMD index.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset(
    {"recall", "temporal", "entity", "conceptual", "multi_hop", "procedural", "classification"}
)
VALID_SCORE_METHODS = frozenset({"exact", "fuzzy", "llm", "classification", "ndcg"})


@dataclass
class BenchmarkCase:
    id: str
    category: str  # recall|temporal|entity|conceptual|multi_hop|procedural|classification
    query: str
    gold_path: str | None
    score_method: str  # exact|fuzzy|llm|classification|ndcg
    notes: str | None = None
    expected_type: str | None = None  # for classification score_method
    gold_paths: list[dict] | None = None  # for ndcg: [{path, relevance}] graded relevance 0-2


@dataclass
class BenchmarkSuite:
    meta: dict
    cases: list[BenchmarkCase] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_suite(path: str) -> BenchmarkSuite:
    """
    Load a benchmark suite from a YAML file.

    Args:
        path: Path to the suite YAML file.

    Returns:
        BenchmarkSuite parsed from the file.

    Raises:
        ValueError: If the file cannot be parsed or the schema is invalid.
        FileNotFoundError: If the file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Suite file not found: {path}")

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ValueError(f"Suite file must be a YAML mapping, got {type(raw).__name__}")

    # Validate meta
    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError("'meta' must be a mapping")

    # Parse cases
    raw_cases = raw.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ValueError("'cases' must be a list")

    cases: list[BenchmarkCase] = []
    errors: list[str] = []

    for i, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            errors.append(f"Case [{i}]: must be a mapping")
            continue

        case_id = raw_case.get("id")
        category = raw_case.get("category")
        query = raw_case.get("query")
        gold_path = raw_case.get("gold_path")
        score_method = raw_case.get("score_method")
        notes = raw_case.get("notes")
        expected_type = raw_case.get("expected_type")
        gold_paths = raw_case.get("gold_paths")  # list of {path, relevance} for ndcg

        # Required fields
        if not case_id:
            errors.append(f"Case [{i}]: missing required field 'id'")
        if not category:
            errors.append(f"Case [{i}] ({case_id}): missing required field 'category'")
        elif category not in VALID_CATEGORIES:
            errors.append(
                f"Case [{i}] ({case_id}): invalid category {category!r}; must be one of {sorted(VALID_CATEGORIES)}"
            )
        if not query:
            errors.append(f"Case [{i}] ({case_id}): missing required field 'query'")
        if not score_method:
            errors.append(f"Case [{i}] ({case_id}): missing required field 'score_method'")
        elif score_method not in VALID_SCORE_METHODS:
            errors.append(
                f"Case [{i}] ({case_id}): invalid score_method {score_method!r}; "
                f"must be one of {sorted(VALID_SCORE_METHODS)}"
            )

        # For recall cases, gold_path should be set
        if category == "recall" and not gold_path and not errors:
            errors.append(f"Case [{i}] ({case_id}): recall cases must have a gold_path")

        # If gold_paths provided but gold_path is absent, derive from highest-relevance entry
        if gold_paths and isinstance(gold_paths, list) and not gold_path:
            best = max(gold_paths, key=lambda g: g.get("relevance", 0), default=None)
            if best:
                gold_path = best.get("path")

        if not errors or (case_id and category and query and score_method):
            cases.append(
                BenchmarkCase(
                    id=str(case_id) if case_id else f"case_{i}",
                    category=str(category) if category else "",
                    query=str(query) if query else "",
                    gold_path=str(gold_path) if gold_path else None,
                    score_method=str(score_method) if score_method else "",
                    notes=str(notes) if notes else None,
                    expected_type=str(expected_type) if expected_type else None,
                    gold_paths=gold_paths if isinstance(gold_paths, list) else None,
                )
            )

    if errors:
        raise ValueError(f"Suite schema errors in {path}:\n" + "\n".join(f"  - {e}" for e in errors))

    return BenchmarkSuite(meta=meta, cases=cases)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_suite(
    suite: BenchmarkSuite,
    db: sqlite3.Connection,
    strict: bool = True,
) -> list[str]:
    """
    Validate a benchmark suite against the QMD index.

    Checks:
    - Gold paths for recall cases exist in the index (case-insensitive)
    - No duplicate gold paths across the suite

    Args:
        suite:  The BenchmarkSuite to validate.
        db:     Open sqlite3.Connection to the QMD index.
        strict: If True, missing gold paths are errors. If False, they are warnings.

    Returns:
        List of error strings. Empty list means all checks passed.
    """
    errors: list[str] = []

    # Collect all gold paths from recall cases
    gold_paths: list[tuple[str, str]] = [
        (case.id, case.gold_path) for case in suite.cases if case.gold_path and case.category == "recall"
    ]

    # Check for duplicate gold paths
    seen_gold: dict[str, str] = {}
    for case_id, gp in gold_paths:
        gp_lower = gp.lower()
        if gp_lower in seen_gold:
            errors.append(f"Duplicate gold_path: {gp!r} used by both {seen_gold[gp_lower]!r} and {case_id!r}")
        else:
            seen_gold[gp_lower] = case_id

    # Check gold paths exist in the QMD index
    for case_id, gp in gold_paths:
        if not _gold_path_in_index(db, gp):
            msg = f"Case {case_id!r}: gold_path {gp!r} not found in QMD index"
            errors.append(msg)

    return errors


def _gold_path_in_index(db: sqlite3.Connection, gold_path: str) -> bool:
    """
    Check whether a gold path exists in the QMD index (case-insensitive substring match).

    QMD stores paths as full filesystem paths like:
      /data/obsidian-vault/01-projects/...
    or relative paths like:
      01-projects/...

    We match using a LIKE query on the path column.
    """
    # Normalise the gold path for comparison
    # Strip leading path components and match as suffix
    norm = gold_path.lower().replace("\\", "/")

    # Try exact suffix match first
    cursor = db.execute(
        "SELECT 1 FROM documents WHERE lower(path) LIKE ? LIMIT 1",
        (f"%{norm}",),
    )
    if cursor.fetchone():
        return True

    # Try without any leading prefix (just filename portion)
    parts = norm.split("/")
    if len(parts) > 1:
        # Try progressively shorter suffixes
        for n in range(len(parts) - 1, 0, -1):
            suffix = "/".join(parts[n:])
            cursor = db.execute(
                "SELECT 1 FROM documents WHERE lower(path) LIKE ? LIMIT 1",
                (f"%{suffix}",),
            )
            if cursor.fetchone():
                return True

    return False
