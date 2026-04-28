"""
GPL-inspired automated evaluation suite generation for kairix.

Implements the Generative Pseudo Labeling pipeline (Wang et al. 2022):

  1. sample_documents  — draw representative docs from the kairix SQLite index
  2. generate_queries  — prompt gpt-4o-mini to write queries the doc answers
  3. retrieve          — run hybrid_search for each generated query
  4. judge             — call judge.judge_batch() to grade retrieved docs
  5. build_case        — emit a BenchmarkCase with gold_titles (0/1/2 graded)
  6. enrich_suite      — convert an existing single-gold-path suite to graded

Reference:
  Wang et al. (2022). GPL: Generative Pseudo Labeling for Unsupervised Domain
  Adaptation of Dense Retrieval. NAACL 2022.
  https://arxiv.org/abs/2112.09118

Also provides enrich_suite() for converting existing BM25-biased suites to
title-based graded relevance without regenerating all queries from scratch.
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kairix.quality.eval.judge import (
    JudgeCalibrationError,
    JudgeResult,
    _call_llm,
    calibrate,
    fetch_llm_credentials,
    judge_batch,
)

logger = logging.getLogger(__name__)


# Path to kairix's SQLite index (resolved via kairix.core.db)
def _get_db_path_str() -> str:
    from kairix.core.db import get_db_path

    return str(get_db_path())


# Category target distribution for generate_suite()
_TARGET_DISTRIBUTION: dict[str, float] = {
    "recall": 0.40,
    "temporal": 0.15,
    "entity": 0.15,
    "conceptual": 0.12,
    "multi_hop": 0.10,
    "procedural": 0.08,
}

# Minimum document body length to sample (chars)
_MIN_DOC_LENGTH: int = 200

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GeneratedQuery:
    """A query generated from a source document."""

    query: str
    intent: str  # recall | temporal | entity | conceptual | multi_hop | procedural
    source_doc_path: str
    source_doc_title: str


@dataclass
class GenerationResult:
    """Result of a generate_suite() or enrich_suite() run."""

    output_path: str
    n_generated: int
    n_accepted: int
    n_rejected: int  # no grade-2 doc found
    n_failed: int  # API or retrieval error
    category_counts: dict[str, int]
    calibration_passed: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class EnrichmentResult:
    """Result of an enrich_suite() run."""

    output_path: str
    n_cases: int
    n_enriched: int  # cases that received gold_titles
    n_skipped: int  # cases where no grade-1+ doc was found (kept with existing gold)
    n_failed: int  # cases where retrieval/judge failed
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Document sampling
# ---------------------------------------------------------------------------


def sample_documents(
    db_path: str = _get_db_path_str(),
    n: int = 200,
    collections: list[str] | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """
    Sample documents from the kairix SQLite index.

    Proportionally samples across collections, skipping archived docs and
    very short documents (< _MIN_DOC_LENGTH chars).

    Args:
        db_path:     Path to kairix SQLite database.
        n:           Target number of documents to sample.
        collections: Restrict to these collection names (None = all).
        seed:        Random seed for reproducibility.

    Returns:
        List of dicts with keys: path, title, collection, body (truncated to 2000 chars).
    """
    if seed is not None:
        random.seed(seed)

    try:
        db = sqlite3.connect(db_path, timeout=10.0)
        db.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning("sample_documents: failed to open %r — %s", db_path, e)
        return []

    try:
        if collections:
            placeholders = ",".join("?" * len(collections))
            # safe: placeholders are "?" strings, values bound via params
            rows = db.execute(
                f"""
                SELECT d.path, d.title, d.collection, c.doc
                FROM documents d
                JOIN content c ON c.hash = d.hash
                WHERE d.collection IN ({placeholders})
                  AND lower(d.path) NOT LIKE '%archive%'
                  AND length(c.doc) >= ?
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (*collections, _MIN_DOC_LENGTH, n * 3),  # oversample to allow filtering
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT d.path, d.title, d.collection, c.doc
                FROM documents d
                JOIN content c ON c.hash = d.hash
                WHERE lower(d.path) NOT LIKE '%archive%'
                  AND length(c.doc) >= ?
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (_MIN_DOC_LENGTH, n * 3),
            ).fetchall()
        db.close()
    except Exception as e:
        logger.warning("sample_documents: query error — %s", e)
        try:
            db.close()
        except Exception:  # noqa: S110
            pass
        return []

    docs = []
    for row in rows:
        body = row["doc"] or ""
        # Skip YAML frontmatter
        if body.startswith("---"):
            parts = body.split("---", 2)
            body = parts[2].strip() if len(parts) >= 3 else body
        if len(body) < _MIN_DOC_LENGTH:
            continue
        docs.append(
            {
                "path": row["path"],
                "title": str(row["title"] or Path(row["path"]).stem),
                "collection": row["collection"],
                "body": body[:2000],
            }
        )

    # Shuffle and trim to target n
    random.shuffle(docs)
    return docs[:n]


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def generate_queries(
    doc_title: str,
    doc_body: str,
    n: int = 2,
    categories: list[str] | None = None,
    api_key: str = "",
    endpoint: str = "",
    deployment: str = "gpt-4o-mini",
    source_doc_path: str = "",
) -> list[GeneratedQuery]:
    """
    Generate n retrieval queries that the given document would primarily answer.

    Prompts gpt-4o-mini to write queries that:
    - Would rank this document at position 1 in a well-functioning retrieval system
    - Cover diverse aspects of the document's content
    - Are labelled with the appropriate intent category

    Args:
        doc_title:       Document title (used as identifier).
        doc_body:        Document body text (first 1000 chars used in prompt).
        n:               Number of queries to generate (default: 2).
        categories:      Allowed intent categories (None = all standard categories).
        api_key:         Azure OpenAI API key.
        endpoint:        Azure OpenAI endpoint URL.
        deployment:      Model deployment name.
        source_doc_path: Original path of the document.

    Returns:
        List of GeneratedQuery. Returns [] on any failure (no raise).
    """
    allowed_cats = categories or list(_TARGET_DISTRIBUTION.keys())
    cats_str = ", ".join(allowed_cats)
    snippet = doc_body[:1000].replace("\n", " ")

    prompt = (
        f"You are generating retrieval queries for an information retrieval benchmark.\n\n"
        f"Document title: {doc_title}\n"
        f"Document content (excerpt): {snippet}\n\n"
        f"Write exactly {n} queries that this document would be the primary answer for.\n"
        f"Each query should:\n"
        f"  - Be a natural question or search phrase a user would actually type\n"
        f"  - Be specific enough that this document clearly answers it\n"
        f"  - Cover different aspects of the document's content (not just paraphrasing the title)\n\n"
        f"Label each query with its intent type from: {cats_str}\n\n"
        f"Reply ONLY with JSON array:\n"
        f'[{{"query": "...", "intent": "recall"}}, ...]\n'
        f"No explanation, no markdown, just the JSON array."
    )

    for attempt in range(2):
        try:
            if not api_key or not endpoint:
                raise ValueError("No API credentials")
            content = _call_llm(prompt, api_key, endpoint, deployment)
            # Extract JSON array from response
            arr_match = re.search(r"\[.*\]", content, re.DOTALL)
            if not arr_match:
                raise ValueError(f"No JSON array in response: {content[:200]!r}")
            raw = json.loads(arr_match.group())
            queries = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                q = str(item.get("query", "")).strip()
                intent = str(item.get("intent", "recall")).strip().lower()
                if not q:
                    continue
                if intent not in allowed_cats:
                    intent = "recall"  # default to recall if unknown category
                queries.append(
                    GeneratedQuery(
                        query=q,
                        intent=intent,
                        source_doc_path=source_doc_path,
                        source_doc_title=doc_title,
                    )
                )
            return queries
        except Exception as e:
            if attempt == 0:
                logger.debug("generate_queries: parse failure (attempt 1) for %r — %s", doc_title, e)
            else:
                logger.warning("generate_queries: failed for %r after 2 attempts — %s", doc_title, e)

    return []


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _retrieve(query: str, intent: str, agent: str = "shape") -> tuple[list[str], list[str]]:
    """
    Run hybrid search and return (paths, snippets).
    Returns ([], []) on any failure.
    """
    try:
        from kairix.core.search.hybrid import search

        sr = search(query=query, agent=agent, scope="shared+agent", budget=500_000)
        paths = [b.result.path for b in sr.results]
        snippets = [b.content[:300] for b in sr.results]
        return paths, snippets
    except Exception as e:
        logger.warning("_retrieve: error for query %r — %s", query[:60], e)
        return [], []


# ---------------------------------------------------------------------------
# Case builder
# ---------------------------------------------------------------------------


def build_case(
    query: str,
    intent: str,
    judge_result: JudgeResult,
    paths: list[str],
    snippets: list[str],
    case_id: str,
) -> dict | None:
    """
    Build a benchmark case dict from judge results.

    Accepts only if at least one document received grade 2. The gold_titles
    list includes all documents with grade >= 1.

    Args:
        query:        The search query.
        intent:       The intent category.
        judge_result: Output of judge_batch().
        paths:        Retrieved paths (parallel to judge candidates).
        snippets:     Retrieved snippets.
        case_id:      Case identifier (e.g. "GEN-R001").

    Returns:
        Dict ready for YAML serialisation, or None if no grade-2 doc found.
    """

    grade_2_count = sum(1 for g in judge_result.grades.values() if g == 2)
    if grade_2_count == 0:
        return None

    # Build gold_titles from grades
    gold_titles: list[dict[str, Any]] = []
    for stem, grade in judge_result.grades.items():
        if grade >= 1:
            gold_titles.append({"title": stem, "relevance": grade})

    # Sort by relevance desc for readability
    gold_titles.sort(key=lambda x: -int(x["relevance"]))

    return {
        "id": case_id,
        "category": intent,
        "query": query,
        "score_method": "ndcg",
        "gold_titles": gold_titles,
    }


# ---------------------------------------------------------------------------
# Suite generation
# ---------------------------------------------------------------------------


def generate_suite(
    db_path: str = _get_db_path_str(),
    output_path: str = "suites/generated.yaml",
    n_cases: int = 100,
    categories: list[str] | None = None,
    api_key: str | None = None,
    endpoint: str | None = None,
    deployment: str = "gpt-4o-mini",
    calibrate_first: bool = True,
    seed: int | None = None,
    agent: str = "shape",
) -> GenerationResult:
    """
    Generate a benchmark suite using the GPL pipeline.

    Pipeline: sample docs → generate queries → hybrid retrieve → LLM judge → YAML.

    Args:
        db_path:        Path to kairix SQLite database.
        output_path:    Output YAML file path.
        n_cases:        Target number of accepted cases.
        categories:     Categories to include (None = all). Controls doc sampling
                        and query generation intent labels.
        api_key:        Azure OpenAI API key (None = auto-fetch from env/Key Vault).
        endpoint:       Azure OpenAI endpoint URL (None = auto-fetch).
        deployment:     Model deployment name.
        calibrate_first: Run calibration anchors before generation (default: True).
        seed:           Random seed for reproducibility.
        agent:          Agent name for hybrid search scoping.

    Returns:
        GenerationResult. Never raises — returns partial results on failure.
    """
    errors: list[str] = []

    # Credential resolution
    if api_key is None or endpoint is None:
        fetched_key, fetched_ep, fetched_dep = fetch_llm_credentials()
        api_key = api_key or fetched_key
        endpoint = endpoint or fetched_ep
        if fetched_dep != "gpt-4o-mini" or deployment == "gpt-4o-mini":
            deployment = fetched_dep

    calibration_passed = False
    if calibrate_first:
        try:
            calibration_passed = calibrate(api_key, endpoint, deployment)
        except JudgeCalibrationError as e:
            errors.append(f"Calibration failed: {e}")
            return GenerationResult(
                output_path=output_path,
                n_generated=0,
                n_accepted=0,
                n_rejected=0,
                n_failed=0,
                category_counts={},
                calibration_passed=False,
                errors=errors,
            )
    else:
        calibration_passed = True

    active_cats = categories or list(_TARGET_DISTRIBUTION.keys())

    # Sample documents — oversample to allow for rejection
    docs = sample_documents(db_path=db_path, n=n_cases * 10, collections=None, seed=seed)
    if not docs:
        errors.append("sample_documents: no documents returned — check db_path")
        return GenerationResult(
            output_path=output_path,
            n_generated=0,
            n_accepted=0,
            n_rejected=0,
            n_failed=0,
            category_counts={},
            calibration_passed=calibration_passed,
            errors=errors,
        )

    accepted_cases: list[dict] = []
    n_rejected = 0
    n_failed = 0
    category_counts: dict[str, int] = {cat: 0 for cat in active_cats}

    # ID counters per category
    id_counters: dict[str, int] = {cat: 0 for cat in active_cats}
    _cat_prefix = {
        "recall": "GEN-R",
        "temporal": "GEN-T",
        "entity": "GEN-E",
        "conceptual": "GEN-C",
        "multi_hop": "GEN-M",
        "procedural": "GEN-P",
    }

    for doc in docs:
        if len(accepted_cases) >= n_cases:
            break

        # Generate queries for this document
        queries = generate_queries(
            doc_title=doc["title"],
            doc_body=doc["body"],
            n=2,
            categories=active_cats,
            api_key=api_key,
            endpoint=endpoint,
            deployment=deployment,
            source_doc_path=doc["path"],
        )

        for gq in queries:
            if len(accepted_cases) >= n_cases:
                break

            # Retrieve
            paths, snippets = _retrieve(gq.query, gq.intent, agent=agent)
            if not paths:
                n_failed += 1
                continue

            # Build candidates for judge
            candidates = list(
                zip(
                    [Path(p).stem for p in paths[:10]],
                    [s[:300] for s in snippets[:10]],
                    strict=False,
                )
            )

            # Judge
            result = judge_batch(
                query=gq.query,
                candidates=candidates,
                api_key=api_key,
                endpoint=endpoint,
                deployment=deployment,
            )

            # Build case
            id_counters[gq.intent] = id_counters.get(gq.intent, 0) + 1
            prefix = _cat_prefix.get(gq.intent, "GEN-X")
            case_id = f"{prefix}{id_counters[gq.intent]:03d}"

            case = build_case(
                query=gq.query,
                intent=gq.intent,
                judge_result=result,
                paths=paths,
                snippets=snippets,
                case_id=case_id,
            )
            if case is None:
                n_rejected += 1
                continue

            accepted_cases.append(case)
            category_counts[gq.intent] = category_counts.get(gq.intent, 0) + 1

    n_generated = len(accepted_cases) + n_rejected + n_failed

    # Write output YAML
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    suite_doc = {
        "meta": {
            "version": "1.0",
            "generated_by": "kairix eval generate",
            "n_cases": len(accepted_cases),
            "categories": active_cats,
            "score_method": "ndcg",
        },
        "cases": accepted_cases,
    }

    try:
        with output.open("w", encoding="utf-8") as f:
            yaml.dump(suite_doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except Exception as e:
        errors.append(f"Failed to write {output_path}: {e}")

    return GenerationResult(
        output_path=output_path,
        n_generated=n_generated,
        n_accepted=len(accepted_cases),
        n_rejected=n_rejected,
        n_failed=n_failed,
        category_counts=category_counts,
        calibration_passed=calibration_passed,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Suite enrichment
# ---------------------------------------------------------------------------


def enrich_suite(
    suite_path: str,
    output_path: str,
    db_path: str = _get_db_path_str(),
    api_key: str | None = None,
    endpoint: str | None = None,
    deployment: str = "gpt-4o-mini",
    agent: str = "shape",
) -> EnrichmentResult:
    """
    Enrich an existing suite's cases with graded gold_titles.

    For each case in the input suite:
    1. Run hybrid_search for the case's query
    2. Judge the top-10 retrieved docs with gpt-4o-mini
    3. Replace gold_path with gold_titles (graded 0/1/2)
    4. Preserve all other case fields unchanged

    Cases where no grade-1+ doc is found retain their existing gold information.

    Args:
        suite_path:  Input suite YAML path.
        output_path: Output YAML path (may equal suite_path for in-place update).
        db_path:     kairix SQLite path (not used directly; hybrid_search handles DB).
        api_key:     Azure OpenAI API key (None = auto-fetch).
        endpoint:    Azure OpenAI endpoint URL (None = auto-fetch).
        deployment:  Model deployment name.
        agent:       Agent name for hybrid search scoping.

    Returns:
        EnrichmentResult. Never raises.
    """
    errors: list[str] = []

    # Credential resolution
    if api_key is None or endpoint is None:
        fetched_key, fetched_ep, fetched_dep = fetch_llm_credentials()
        api_key = api_key or fetched_key
        endpoint = endpoint or fetched_ep
        if fetched_dep != "gpt-4o-mini" or deployment == "gpt-4o-mini":
            deployment = fetched_dep

    # Load input suite as raw YAML (preserve all fields)
    try:
        with open(suite_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        errors.append(f"Failed to load {suite_path}: {e}")
        return EnrichmentResult(
            output_path=output_path,
            n_cases=0,
            n_enriched=0,
            n_skipped=0,
            n_failed=0,
            errors=errors,
        )

    raw_cases: list[dict] = raw.get("cases", [])
    n_enriched = 0
    n_skipped = 0
    n_failed = 0

    enriched_cases = []
    for case in raw_cases:
        query = case.get("query", "")
        if not query:
            enriched_cases.append(case)
            n_skipped += 1
            continue

        # Retrieve
        paths, snippets = _retrieve(query, case.get("category", "recall"), agent=agent)
        if not paths:
            n_failed += 1
            enriched_cases.append(case)
            continue

        # Build candidates
        candidates = list(
            zip(
                [Path(p).stem for p in paths[:10]],
                [s[:300] for s in snippets[:10]],
                strict=False,
            )
        )

        # Judge
        result = judge_batch(
            query=query,
            candidates=candidates,
            api_key=api_key,
            endpoint=endpoint,
            deployment=deployment,
        )

        # Check if any grade >= 1
        has_relevant = any(g >= 1 for g in result.grades.values())
        if not has_relevant:
            n_skipped += 1
            enriched_cases.append(case)
            continue

        # Build gold_titles
        gold_titles: list[dict[str, Any]] = [
            {"title": stem, "relevance": grade} for stem, grade in result.grades.items() if grade >= 1
        ]
        gold_titles.sort(key=lambda x: -int(x["relevance"]))

        # Update case: add gold_titles, update score_method, preserve other fields
        updated = dict(case)
        updated["gold_titles"] = gold_titles
        updated["score_method"] = "ndcg"
        # Keep gold_path for backwards compat display; remove old gold_paths list
        updated.pop("gold_paths", None)

        enriched_cases.append(updated)
        n_enriched += 1

    # Write output
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    out_doc = dict(raw)
    out_doc["cases"] = enriched_cases

    try:
        with output.open("w", encoding="utf-8") as f:
            yaml.dump(out_doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except Exception as e:
        errors.append(f"Failed to write {output_path}: {e}")

    return EnrichmentResult(
        output_path=output_path,
        n_cases=len(raw_cases),
        n_enriched=n_enriched,
        n_skipped=n_skipped,
        n_failed=n_failed,
        errors=errors,
    )
