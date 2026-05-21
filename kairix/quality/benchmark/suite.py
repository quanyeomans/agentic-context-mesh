"""
Benchmark suite loader and validator.

Loads YAML suite files and validates them against the kairix index.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kairix.quality.eval.constants import CATEGORY_ALIASES, CATEGORY_WEIGHTS

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset(CATEGORY_WEIGHTS.keys()) | frozenset(CATEGORY_ALIASES.keys())
VALID_SCORE_METHODS = frozenset({"exact", "fuzzy", "llm", "classification", "ndcg"})


@dataclass
class BenchmarkCase:
    """One row in a benchmark suite YAML.

    Routing-boundary fields (``scope``, ``collection``, ``agent``)
    constrain WHERE retrieval looks for evidence. They are NOT
    permission enforcement — kairix's benchmark suite validates routing
    behaviour, not RBAC. See Gap 7 in
    ``/tmp/spike-C3-scope-rbac-flow.md`` for the gap to real RBAC.
    Operators wiring access-control semantics into a deployment must
    layer those at the transport/auth boundary; the suite YAML cannot
    assert them.

    LLM-judge scoring fields (``expected_answer``,
    ``expected_answer_keywords``) declare what a correct answer looks
    like. The scorer registry (P3) consumes them when ``score_method``
    selects an LLM-judged path; suites with only ``gold_titles`` /
    ``gold_paths`` continue to score on retrieval-correctness alone.

    Per-case overrides win over the suite-level ``default_*`` keys in
    ``meta`` whenever both are present; this lets a single suite mix
    multi-agent routing assertions with per-query exceptions.
    """

    id: str
    category: str  # recall|temporal|entity|conceptual|multi_hop|procedural|classification
    query: str
    gold_path: str | None
    score_method: str  # exact|fuzzy|llm|classification|ndcg
    notes: str | None = None
    expected_type: str | None = None  # for classification score_method
    gold_paths: list[dict] | None = None  # for ndcg: [{path, relevance}] graded relevance 0-2 (path-based)
    gold_title: str | None = None  # stable note title for exact/fuzzy cases (path-agnostic)
    gold_titles: list[dict] | None = None  # for ndcg: [{title, relevance}] graded relevance 0-2 (title-based)
    agent: str | None = None  # per-case agent override (builder|shape|consultant|…)
    # ------------------------------------------------------------------
    # P4 extensions — LLM-judge scoring + routing-boundary overrides
    # ------------------------------------------------------------------
    expected_answer: str | None = None
    """Reference answer text for LLM-judge scoring (P3 scorer registry
    will consume). Used alongside or instead of ``gold_titles`` when
    the scoring path is ``score_method: llm`` and the judge wants a
    full-answer match. Optional — when None, the judge falls back to
    keyword / retrieval-only signal."""
    expected_answer_keywords: list[str] | None = None
    """Looser keyword list for LLM-judge scoring. Compatible with
    ``expected_answer`` — when both are present, the judge may use
    keywords as a fallback signal when full-text matching is too strict.
    Optional."""
    scope: str | None = None
    """Per-query scope override parsed downstream via
    ``Scope.parse(...)``. Valid values: ``shared``, ``agent``,
    ``shared+agent``, ``all-agents``, ``everything``. None means
    'inherit the suite-level default and ultimately the retrieval-time
    fallback (shared+agent)'. Routing-boundary control — NOT a permission
    check; see class docstring."""
    collection: str | None = None
    """Per-query collection filter. When set, short-circuits
    ``CollectionResolver`` per spike-C3 §2 — retrieval reaches only this
    collection regardless of ``scope``/``agent``. Routing-boundary
    control — NOT a permission check."""
    expected_zero_results: bool | None = None
    """When True, the case asserts the retrieval pipeline must return
    zero matches (routing-boundary probe, e.g. 'this query against
    agent-X's scope must not surface shared-Y's note'). Reserved for
    P5/P6 scorer wiring; declarative-only in P4."""


@dataclass
class BenchmarkSuite:
    """Loaded suite — meta dict plus typed cases.

    The ``meta`` dict is intentionally permissive. Suite-level
    declarative keys consumed today and by P3+ scorers:

    * ``default_collection`` — auto-scope target for ``kairix benchmark
      run`` (already wired in ``kairix/quality/benchmark/cli.py``).
    * ``default_scope`` (P4) — fallback scope when a case omits its
      own. Routing-boundary control; see ``BenchmarkCase`` docstring.
    * ``default_agent`` (P4) — fallback agent identity used when a
      case omits its own ``agent``.
    * ``focus_areas`` (P4) — list of free-form labels recording which
      capabilities the suite covers (e.g. ``[recall, entity, scope]``).
      Declarative only; used by reporters to colour-code summaries.

    Unknown keys round-trip untouched so suites can encode runner-side
    metadata (description, version, created) without schema churn.
    """

    meta: dict
    cases: list[BenchmarkCase] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def resolve_suite_path(suite_arg: str, root: Path | None = None) -> Path:
    """Resolve a `--suite` argument to a concrete YAML path.

    ``suite_arg`` may be:
      - An explicit filesystem path (used directly when it exists).
      - A bundle name (e.g. ``reflib``) — searches ``root`` for the
        highest gold version matching ``<name>-gold-v*.yaml``, then
        falls back to ``<name>.yaml``.

    ``root`` defaults to ``kairix.paths.bundled_suites_root()`` for
    production callers; tests pass an explicit ``tmp_path`` to avoid
    env-var monkeypatching (F2).

    The bundle-name shortcut is the user-facing UX from #222 — running
    ``kairix benchmark run reflib`` no longer requires hunting for the
    in-image suite path.
    """
    p = Path(suite_arg)
    if p.exists():
        return p

    if root is None:
        from kairix.paths import bundled_suites_root

        root = bundled_suites_root()

    if root.is_dir():
        gold = sorted(
            root.glob(f"{suite_arg}-gold-v*.yaml"),
            key=lambda x: x.name,
            reverse=True,
        )
        if gold:
            return gold[0]

        fallback = root / f"{suite_arg}.yaml"
        if fallback.exists():
            return fallback

    raise FileNotFoundError(
        f"Suite '{suite_arg}' not found. Tried path lookup and bundled "
        f"resolution in {root}. Run 'kairix benchmark list' to see available bundled suites."
    )


def list_bundled_suites(root: Path | None = None) -> list[dict]:
    """Return metadata for each bundled suite for the ``list`` subcommand.

    Returns: list of dicts with keys ``name``, ``path``, ``default_collection``,
    ``n_cases``, ``description``. Missing fields are ``None``.

    ``root`` defaults to ``kairix.paths.bundled_suites_root()``; tests
    pass an explicit path for hermetic resolution.
    """
    if root is None:
        from kairix.paths import bundled_suites_root

        root = bundled_suites_root()

    if not root.is_dir():
        return []

    out: list[dict] = []
    for yaml_path in sorted(root.glob("*.yaml")):
        try:
            raw = load_yaml_file(yaml_path)
        except (FileNotFoundError, ValueError):
            continue
        meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
        out.append(
            {
                "name": meta.get("name") or yaml_path.stem,
                "path": str(yaml_path),
                "default_collection": meta.get("default_collection"),
                "n_cases": len(raw.get("cases", [])) if isinstance(raw, dict) else 0,
                "description": meta.get("description"),
            }
        )
    return out


def load_yaml_file(path: Path) -> dict:
    """Read a YAML file, raise on parse error or unexpected type."""
    if not path.exists():
        raise FileNotFoundError(f"Suite file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ValueError(f"Suite file must be a YAML mapping, got {type(raw).__name__}")

    return raw


def validate_meta_and_cases_structure(raw: dict, _path: str) -> tuple[dict, list[dict], list[str]]:
    """Validate root dict has valid meta and cases. Returns (meta, raw_cases, errors).

    ``_path`` is accepted for caller-side symmetry with ``load_suite``/CLI
    error messages, which thread the source path through; this validator
    only inspects the parsed ``raw`` mapping.
    """
    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError("'meta' must be a mapping")

    raw_cases = raw.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ValueError("'cases' must be a list")

    return meta, raw_cases, []


def validate_required_fields(case_id: str | None, case: dict, i: int, errors: list[str]) -> None:
    """Check id, category, query, score_method are present and valid."""
    if not case_id:
        errors.append(f"Case [{i}]: missing required field 'id'")

    category = case.get("category")
    if not category:
        errors.append(f"Case [{i}] ({case_id}): missing required field 'category'")
    elif category not in VALID_CATEGORIES:
        errors.append(
            f"Case [{i}] ({case_id}): invalid category {category!r}; must be one of {sorted(VALID_CATEGORIES)}"
        )

    if not case.get("query"):
        errors.append(f"Case [{i}] ({case_id}): missing required field 'query'")

    score_method = case.get("score_method")
    if not score_method:
        errors.append(f"Case [{i}] ({case_id}): missing required field 'score_method'")
    elif score_method not in VALID_SCORE_METHODS:
        errors.append(
            f"Case [{i}] ({case_id}): invalid score_method {score_method!r}; "
            f"must be one of {sorted(VALID_SCORE_METHODS)}"
        )


def validate_gold_titles_structure(
    gold_titles: list[dict] | None, case_id: str | None, i: int, errors: list[str]
) -> None:
    """Validate each gold_titles entry has title (str) and relevance (int 0-2)."""
    if not gold_titles or not isinstance(gold_titles, list):
        return

    for j, gt in enumerate(gold_titles):
        if not isinstance(gt, dict):
            errors.append(f"Case [{i}] ({case_id}): gold_titles[{j}] must be a mapping")
        elif "title" not in gt:
            errors.append(f"Case [{i}] ({case_id}): gold_titles[{j}] missing required field 'title'")
        elif "relevance" not in gt:
            errors.append(f"Case [{i}] ({case_id}): gold_titles[{j}] missing required field 'relevance'")
        elif gt["relevance"] not in (0, 1, 2):
            errors.append(f"Case [{i}] ({case_id}): gold_titles[{j}] relevance must be 0, 1, or 2")


_VALID_SCOPE_VALUES = frozenset({"shared", "agent", "shared+agent", "all-agents", "everything"})


_P4_SIMPLE_TYPE_FIELDS: tuple[tuple[str, type, str, str], ...] = (
    (
        "expected_answer",
        str,
        "a string",
        'fix: quote the value in YAML, e.g. expected_answer: "..."',
    ),
    (
        "expected_answer_keywords",
        list,
        "a list",
        "fix: use YAML list syntax, e.g. expected_answer_keywords: [foo, bar]",
    ),
    (
        "collection",
        str,
        "a string",
        'fix: quote the value, e.g. collection: "reference-library"',
    ),
    (
        "expected_zero_results",
        bool,
        "true|false",
        "fix: use a YAML boolean.",
    ),
)


def _validate_p4_scope(raw_case: dict, case_id: str | None, i: int, errors: list[str]) -> None:
    scope = raw_case.get("scope")
    if scope is None:
        return
    if not isinstance(scope, str):
        errors.append(
            f"Case [{i}] ({case_id}): 'scope' must be a string when set; fix: use one of {sorted(_VALID_SCOPE_VALUES)}"
        )
    elif scope not in _VALID_SCOPE_VALUES:
        errors.append(
            f"Case [{i}] ({case_id}): 'scope' must be one of {sorted(_VALID_SCOPE_VALUES)}; "
            f"got {scope!r}. fix: update the suite YAML to a valid scope."
        )


def validate_p4_field_types(raw_case: dict, case_id: str | None, i: int, errors: list[str]) -> None:
    """Validate the P4 optional fields have the right shape when set.

    All P4 fields are optional; this validator only fires when a key is
    present and its value type is wrong. Backwards compat: a suite that
    omits every P4 key produces zero errors.

    Checked fields:
      * ``expected_answer`` — must be a string when set.
      * ``expected_answer_keywords`` — must be a list of strings when set.
      * ``scope`` — must be one of the valid Scope strings when set.
      * ``collection`` — must be a string when set.
      * ``expected_zero_results`` — must be a bool when set.
    """
    for key, expected_type, type_label, fix_hint in _P4_SIMPLE_TYPE_FIELDS:
        value = raw_case.get(key)
        if value is None or isinstance(value, expected_type):
            continue
        errors.append(f"Case [{i}] ({case_id}): {key!r} must be {type_label} when set; {fix_hint}")
    _validate_p4_scope(raw_case, case_id, i, errors)


def validate_recall_gold_requirement(
    category: str | None,
    gold_path: str | None,
    gold_paths: list[dict] | None,
    gold_title: str | None,
    gold_titles: list[dict] | None,
    case_id: str | None,
    i: int,
    errors: list[str],
) -> None:
    """Check recall cases have at least one gold reference."""
    if category == "recall" and not gold_path and not gold_paths and not gold_title and not gold_titles:
        if not errors:
            errors.append(f"Case [{i}] ({case_id}): recall cases must have gold_path, gold_title, or a gold list")


def derive_gold_path_from_gold_lists(
    gold_path: str | None,
    gold_paths: list[dict] | None,
    gold_title: str | None,
    gold_titles: list[dict] | None,
) -> str | None:
    """Derive best gold_path from gold lists for backwards compat.

    Priority: explicit gold_path > highest-relevance gold_paths entry
    > highest-relevance gold_titles entry > gold_title.
    """
    if gold_path:
        return gold_path

    if gold_paths and isinstance(gold_paths, list):
        # _coerce_gold_list allows non-dict scalars through; filter to dicts here
        # so the relevance-based max doesn't AttributeError on a string item.
        dict_paths = [g for g in gold_paths if isinstance(g, dict)]
        best = max(dict_paths, key=lambda g: g.get("relevance", 0), default=None)
        if best:
            return best.get("path")
    elif gold_titles and isinstance(gold_titles, list):
        dict_titles = [g for g in gold_titles if isinstance(g, dict)]
        best_t = max(dict_titles, key=lambda g: g.get("relevance", 0), default=None)
        if best_t:
            return best_t.get("title")  # title as path-equivalent for display
    elif gold_title:
        return gold_title

    return None


def _coerce_gold_list(items: list[dict] | None, ref_field: str) -> list[dict] | None:
    """Coerce the ref-field of each item in a gold list to ``str``.

    PyYAML parses unquoted ISO-shaped values like ``2026-04-07`` as
    ``datetime.date`` objects. Downstream scoring calls ``.endswith(".md")``
    on these refs, which raises AttributeError on date objects. Coerce here
    at the suite-load boundary so scoring sees only strings.
    """
    if not isinstance(items, list):
        return None
    coerced: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            coerced.append(item)
            continue
        new_item = dict(item)
        if ref_field in new_item and new_item[ref_field] is not None:
            new_item[ref_field] = str(new_item[ref_field])
        coerced.append(new_item)
    return coerced


@dataclass
class _CaseFields:
    """Bag of raw parsed fields for one suite case.

    Exists only to keep ``build_benchmark_case`` under F16's
    cognitive-complexity ceiling — twelve positional args was already
    over the readability cliff and the P4 additions push it further.
    Loader populates the bag from the raw YAML mapping; the builder
    consumes it. The dataclass is internal (leading underscore) and
    NOT part of the suite-loading public surface.
    """

    case_id: str | None
    category: str | None
    query: str | None
    gold_path: str | None
    score_method: str | None
    notes: str | None
    expected_type: str | None
    gold_paths: list[dict] | None
    gold_title: str | None
    gold_titles: list[dict] | None
    case_agent: str | None
    expected_answer: str | None = None
    expected_answer_keywords: list[str] | None = None
    scope: str | None = None
    collection: str | None = None
    expected_zero_results: bool | None = None


def _coerce_str_list(items: Any) -> list[str] | None:
    """Coerce a YAML-list-of-anything to ``list[str] | None``.

    Returns None when ``items`` is not a list; otherwise stringifies
    each element. Keeps the keyword list resilient against YAML
    quirks (unquoted ISO dates, ints) without forcing operators to
    quote every string in a long list.
    """
    if not isinstance(items, list):
        return None
    return [str(x) for x in items]


def build_benchmark_case(i: int, fields: _CaseFields) -> BenchmarkCase:
    """Construct a BenchmarkCase from validated raw fields."""
    return BenchmarkCase(
        id=str(fields.case_id) if fields.case_id else f"case_{i}",
        category=str(fields.category) if fields.category else "",
        query=str(fields.query) if fields.query else "",
        gold_path=str(fields.gold_path) if fields.gold_path else None,
        score_method=str(fields.score_method) if fields.score_method else "",
        notes=str(fields.notes) if fields.notes else None,
        expected_type=str(fields.expected_type) if fields.expected_type else None,
        gold_paths=_coerce_gold_list(fields.gold_paths, "path"),
        gold_title=str(fields.gold_title) if fields.gold_title else None,
        gold_titles=_coerce_gold_list(fields.gold_titles, "title"),
        agent=str(fields.case_agent) if fields.case_agent else None,
        expected_answer=str(fields.expected_answer) if fields.expected_answer else None,
        expected_answer_keywords=_coerce_str_list(fields.expected_answer_keywords),
        scope=str(fields.scope) if fields.scope else None,
        collection=str(fields.collection) if fields.collection else None,
        expected_zero_results=(
            bool(fields.expected_zero_results) if fields.expected_zero_results is not None else None
        ),
    )


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
    raw = load_yaml_file(Path(path))
    meta, raw_cases, errors = validate_meta_and_cases_structure(raw, path)

    cases: list[BenchmarkCase] = []

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
        gold_paths = raw_case.get("gold_paths")
        gold_title = raw_case.get("gold_title")
        gold_titles = raw_case.get("gold_titles")
        case_agent = raw_case.get("agent")

        validate_required_fields(case_id, raw_case, i, errors)
        validate_gold_titles_structure(gold_titles, case_id, i, errors)
        validate_recall_gold_requirement(category, gold_path, gold_paths, gold_title, gold_titles, case_id, i, errors)
        validate_p4_field_types(raw_case, case_id, i, errors)

        gold_path = derive_gold_path_from_gold_lists(gold_path, gold_paths, gold_title, gold_titles)

        if not errors or (case_id and category and query and score_method):
            fields = _CaseFields(
                case_id=case_id,
                category=category,
                query=query,
                gold_path=gold_path,
                score_method=score_method,
                notes=notes,
                expected_type=expected_type,
                gold_paths=gold_paths,
                gold_title=gold_title,
                gold_titles=gold_titles,
                case_agent=case_agent,
                expected_answer=raw_case.get("expected_answer"),
                expected_answer_keywords=raw_case.get("expected_answer_keywords"),
                scope=raw_case.get("scope"),
                collection=raw_case.get("collection"),
                expected_zero_results=raw_case.get("expected_zero_results"),
            )
            cases.append(build_benchmark_case(i, fields))

    if errors:
        raise ValueError(f"Suite schema errors in {path}:\n" + "\n".join(f"  - {e}" for e in errors))

    return BenchmarkSuite(meta=meta, cases=cases)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _check_duplicate_gold_paths(path_based_recall: list[tuple[str, str]]) -> list[str]:
    """Emit one error per duplicate (case-insensitive) gold_path."""
    errors: list[str] = []
    seen_gold: dict[str, str] = {}
    for case_id, gp in path_based_recall:
        gp_lower = gp.lower()
        if gp_lower in seen_gold:
            errors.append(f"Duplicate gold_path: {gp!r} used by both {seen_gold[gp_lower]!r} and {case_id!r}")
        else:
            seen_gold[gp_lower] = case_id
    return errors


def _check_gold_paths_in_index(
    path_based_recall: list[tuple[str, str]],
    db: sqlite3.Connection,
) -> list[str]:
    """Emit one error per recall case whose ``gold_path`` is missing from the index."""
    return [
        f"Case {case_id!r}: gold_path {gp!r} not found in kairix index"
        for case_id, gp in path_based_recall
        if not _gold_path_in_index(db, gp)
    ]


def _check_duplicate_gold_titles(suite: BenchmarkSuite) -> list[str]:
    """Emit one error per duplicate ``gold_title`` across recall cases."""
    errors: list[str] = []
    seen_titles: dict[str, str] = {}
    for case in suite.cases:
        if not (case.gold_title and case.category == "recall"):
            continue
        title_lower = case.gold_title.lower()
        if title_lower in seen_titles:
            errors.append(
                f"Duplicate gold_title: {case.gold_title!r} used by both {seen_titles[title_lower]!r} and {case.id!r}"
            )
        else:
            seen_titles[title_lower] = case.id
    return errors


def validate_suite(
    suite: BenchmarkSuite,
    db: sqlite3.Connection,
) -> list[str]:
    """
    Validate a benchmark suite against the kairix index.

    Checks:
    - Gold paths for recall cases exist in the index (case-insensitive)
    - No duplicate gold paths across the suite

    Args:
        suite: The BenchmarkSuite to validate.
        db:    Open sqlite3.Connection to the kairix index.

    Returns:
        List of error strings. Empty list means all checks passed. Callers
        decide whether to treat any non-empty result as a hard failure or a
        warning (see ``kairix.quality.benchmark.cli.cmd_run`` for the
        warning path and ``cmd_validate`` for the strict-error path).
    """
    path_based_recall: list[tuple[str, str]] = [
        (case.id, case.gold_path)
        for case in suite.cases
        if case.gold_path and case.category == "recall" and not case.gold_title and not case.gold_titles
    ]

    errors: list[str] = []
    errors.extend(_check_duplicate_gold_paths(path_based_recall))
    errors.extend(_check_gold_paths_in_index(path_based_recall, db))
    errors.extend(_check_duplicate_gold_titles(suite))
    return errors


def _gold_path_in_index(db: sqlite3.Connection, gold_path: str) -> bool:
    """
    Check whether a gold path exists in the kairix index (case-insensitive substring match).

    kairix stores paths as document-root-relative paths like:
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
