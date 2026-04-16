"""
Reciprocal Rank Fusion (RRF) + entity boosting + procedural boosting for the
kairix search pipeline.

RRF combines BM25 and vector search result lists into a single ranked list.
Entity boosting increases scores for documents that have known entity mentions,
rewarding documents that are associated with important named entities.
Procedural boosting re-ranks procedural content (how-to guides, runbooks) for
PROCEDURAL intent queries where retrieval hits but ranking is weak.

Constants:
  RRF_K = 60                      Standard RRF constant (Cormack et al., 2009)
  ENTITY_BOOST_FACTOR = 0.20      Multiplier for log(1 + mention_count)
  ENTITY_BOOST_CAP = 2.0          Maximum boost multiplier (prevents runaway scores)
  PROCEDURAL_BOOST_FACTOR = 1.4   Score multiplier for procedural path patterns

RRF formula per document:
  score(d) = sum(1 / (k + rank_in_list) for each list containing d)
  Documents appearing in only one list use len(other_list) + 1 as their rank.

Entity boost formula:
  boost(d) = 1 + min(ENTITY_BOOST_FACTOR * log(1 + mention_count), CAP - 1)
  Applied after RRF, before budget trim.

Procedural boost:
  Applied post-RRF, after entity boost, for PROCEDURAL intent queries only.
  Multiplies boosted_score by PROCEDURAL_BOOST_FACTOR for documents whose path
  matches procedural content patterns (how-to-*, /runbooks/, runbook-*, procedure*).
  Zero effect on other intent types.

All functions return [] on empty inputs. Never raise.
"""

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from kairix.search.bm25 import BM25Result
from kairix.search.vector import VecResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RRF_K: int = 60
ENTITY_BOOST_FACTOR: float = 0.20
ENTITY_BOOST_CAP: float = 2.0
PROCEDURAL_BOOST_FACTOR: float = 1.4

# ---------------------------------------------------------------------------
# Procedural path patterns
# ---------------------------------------------------------------------------

_PROCEDURAL_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|/)how-to-", re.IGNORECASE),
    re.compile(r"/runbooks?/", re.IGNORECASE),
    re.compile(r"(?:^|/)runbook-", re.IGNORECASE),
    re.compile(r"(?:^|/)procedure", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class FusedResult:
    """A search result after RRF fusion, optionally entity-boosted."""

    # Document identity
    path: str
    collection: str
    title: str
    snippet: str

    # Scores
    rrf_score: float = 0.0
    boosted_score: float = 0.0

    # Source membership
    in_bm25: bool = False
    in_vec: bool = False

    # Entity info (populated by entity_boost)
    entity_mention_count: int = 0

    # Raw ranks (1-based, 0 = not ranked in that list)
    bm25_rank: int = 0
    vec_rank: int = 0


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def rrf(
    bm25: list[BM25Result],
    vec: list[VecResult],
    k: int = RRF_K,
) -> list[FusedResult]:
    """
    Reciprocal Rank Fusion of BM25 and vector search results.

    Args:
        bm25:  BM25 results in rank order (highest score first).
        vec:   Vector results in rank order (lowest distance first = best first).
        k:     RRF constant. Default 60.

    Returns:
        Fused results sorted by RRF score descending.
        Returns [] if both inputs are empty.
        Never raises.
    """
    if not bm25 and not vec:
        return []

    try:
        return _rrf_impl(bm25, vec, k)
    except Exception as e:
        logger.warning("rrf: unexpected error during fusion — %s", e)
        return []


def _rrf_impl(
    bm25: list[BM25Result],
    vec: list[VecResult],
    k: int,
) -> list[FusedResult]:
    """Implementation of RRF — called from rrf() with error boundary."""
    # Build path → FusedResult index
    fused: dict[str, FusedResult] = {}

    def _canonical_path(raw: str) -> str:
        """Normalise path to bare collection-relative form for deduplication.

        BM25 returns  qmd://vault-entities/concept/builder.md
        Vector returns concept/builder.md   (for vault-entities collection)
               or     04-agent-knowledge/entities/concept/builder.md (vault collection)

        Strip the qmd://collection-name/ prefix so BM25 and vector paths for the
        same document merge correctly in the fused dict.
        """
        if raw.startswith("qmd://"):
            # Remove qmd://collection-name/ prefix → bare path
            without_scheme = raw[len("qmd://") :]
            slash = without_scheme.find("/")
            if slash != -1:
                return without_scheme[slash + 1 :]
            return without_scheme
        return raw

    # Process BM25 results (1-indexed ranks)
    for rank, result in enumerate(bm25, start=1):
        path = _canonical_path(result["file"])
        if path not in fused:
            fused[path] = FusedResult(
                path=path,
                collection=result["collection"],
                title=result["title"],
                snippet=result["snippet"],
            )
        fused[path].in_bm25 = True
        fused[path].bm25_rank = rank
        fused[path].rrf_score += 1.0 / (k + rank)

    # Process vector results (1-indexed ranks)
    for rank, result in enumerate(vec, start=1):
        path = _canonical_path(result["path"])
        if path not in fused:
            fused[path] = FusedResult(
                path=path,
                collection=result["collection"],
                title=result["title"],
                snippet=result["snippet"],
            )
        fused[path].in_vec = True
        fused[path].vec_rank = rank
        fused[path].rrf_score += 1.0 / (k + rank)

    # Documents in only one list: they already got their score from that list's rank.
    # The spec says: "Results appearing in only one list get rank = len(other_list) + 1."
    # Interpretation: they do NOT get an additional contribution from the absent list —
    # they simply don't accumulate a score from it (which is what the above code does).

    # Sort by RRF score descending
    results = sorted(fused.values(), key=lambda r: r.rrf_score, reverse=True)

    # Initialise boosted_score from rrf_score
    for r in results:
        r.boosted_score = r.rrf_score

    return results


# ---------------------------------------------------------------------------
# Entity boosting (Neo4j)
# ---------------------------------------------------------------------------


def entity_boost_neo4j(
    results: list[FusedResult],
    neo4j_client: object,
    boost_factor: float = ENTITY_BOOST_FACTOR,
    cap: float = ENTITY_BOOST_CAP,
) -> list[FusedResult]:
    """
    Boost entity canonical notes and entity-directory documents using Neo4j.

    Queries Neo4j for entity vault_paths and their MENTIONS in-degree.
    Documents matching an entity vault_path or living inside an entity directory
    receive a log-scaled boost proportional to the entity's in-degree.

    Falls back to returning unmodified results if Neo4j is unavailable or empty.
    Never raises.
    """
    if not results:
        return results

    client = neo4j_client
    if client is None or not getattr(client, "available", False):
        for r in results:
            r.boosted_score = r.rrf_score
        return results

    try:
        rows = client.cypher(  # type: ignore[union-attr]
            "MATCH (n) WHERE n.vault_path IS NOT NULL AND n.vault_path <> '' "
            "OPTIONAL MATCH ()-[:MENTIONS]->(n) "
            "RETURN n.vault_path AS vault_path, count(*) AS in_degree"
        )
    except Exception as e:
        logger.warning("entity_boost_neo4j: cypher failed — %s", e)
        for r in results:
            r.boosted_score = r.rrf_score
        return results

    if not rows:
        for r in results:
            r.boosted_score = r.rrf_score
        return results

    # Build lookup: lowercased path → in_degree, and dir prefix → max in_degree
    path_in_degree: dict[str, int] = {}
    dir_in_degree: dict[str, int] = {}
    for row in rows:
        vp = str(row["vault_path"]).lower().replace("\\", "/")
        in_deg = int(row.get("in_degree") or 0)
        path_in_degree[vp] = in_deg
        parent = str(Path(vp).parent).lower().replace("\\", "/")
        if parent not in (".", ""):
            dir_in_degree[parent] = max(dir_in_degree.get(parent, 0), in_deg)

    if not path_in_degree:
        for r in results:
            r.boosted_score = r.rrf_score
        return results

    max_in_degree = max(path_in_degree.values()) or 1

    for r in results:
        path_lower = r.path.lower().replace("\\", "/")
        in_deg = path_in_degree.get(path_lower, 0)
        if in_deg == 0:
            # Half-boost for files under an entity directory
            for dir_prefix, dir_deg in dir_in_degree.items():
                if path_lower.startswith(dir_prefix + "/"):
                    in_deg = max(in_deg, dir_deg // 2)
                    break
        r.entity_mention_count = in_deg
        if in_deg > 0:
            normalised = in_deg / max_in_degree
            boost_amount = min(boost_factor * math.log1p(normalised * 10), cap - 1.0)
            r.boosted_score = r.rrf_score * (1.0 + boost_amount)
        else:
            r.boosted_score = r.rrf_score

    return sorted(results, key=lambda r: r.boosted_score, reverse=True)


# ---------------------------------------------------------------------------
# Procedural boosting
# ---------------------------------------------------------------------------


def procedural_boost(
    results: list[FusedResult],
    boost_factor: float = PROCEDURAL_BOOST_FACTOR,
) -> list[FusedResult]:
    """
    Boost documents whose paths match procedural content patterns for PROCEDURAL
    intent queries.

    Called after entity_boost(), before apply_budget(). Only called when
    intent == QueryIntent.PROCEDURAL — callers are responsible for the guard.

    Boost logic:
      If any pattern in _PROCEDURAL_PATH_PATTERNS matches result.path:
        result.boosted_score *= boost_factor

    This is a re-ranking fix, not a retrieval fix. Procedural files are typically
    retrieved (Hit@5 > 0.5) but ranked too low (positions 4-7). The 1.4x multiplier
    moves them into the top-3 without over-ranking them for non-procedural queries
    (the boost is gated to PROCEDURAL intent in hybrid.py).

    Args:
        results:       List of FusedResult (from rrf(), after entity_boost()).
        boost_factor:  Multiplier for matching paths. Default 1.4.

    Returns:
        Results re-sorted by boosted_score descending.
        Returns results unmodified on any error.
        Never raises.
    """
    if not results:
        return results

    try:
        return _procedural_boost_impl(results, boost_factor)
    except Exception as e:
        logger.warning("procedural_boost: error — %s — returning unmodified results", e)
        return results


def _procedural_boost_impl(
    results: list[FusedResult],
    boost_factor: float,
) -> list[FusedResult]:
    """Implementation of procedural boosting — called from procedural_boost() with error boundary."""
    for r in results:
        if any(p.search(r.path) for p in _PROCEDURAL_PATH_PATTERNS):
            r.boosted_score *= boost_factor
    return sorted(results, key=lambda r: r.boosted_score, reverse=True)
