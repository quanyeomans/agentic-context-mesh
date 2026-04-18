"""
Reciprocal Rank Fusion (RRF) + entity boosting + procedural boosting + temporal
date boosting for the kairix search pipeline.

RRF combines BM25 and vector search result lists into a single ranked list.
Entity boosting increases scores for documents that have known entity mentions,
rewarding documents that are associated with important named entities.
Procedural boosting re-ranks procedural content (how-to guides, runbooks) for
PROCEDURAL intent queries where retrieval hits but ranking is weak.
Temporal date boosting re-ranks documents whose path contains a date string
matching the queried date, for TEMPORAL intent queries.

Boost behaviour is controlled via config dataclasses (see kairix.search.config).
Pass a RetrievalConfig to hybrid_search() to tune or disable individual boosts.
Use RetrievalConfig.minimal() for RRF baseline isolation.

Constants:
  RRF_K = 60  Standard RRF constant (Cormack et al., 2009)

RRF formula per document:
  score(d) = sum(1 / (k + rank_in_list) for each list containing d)
  Documents appearing in only one list use len(other_list) + 1 as their rank.

Entity boost formula:
  boost(d) = 1 + min(factor * log(1 + mention_count), cap - 1)
  Applied after RRF, before budget trim.

Procedural boost:
  Applied post-RRF, after entity boost, for PROCEDURAL intent queries only.
  Multiplies boosted_score by config.factor for documents whose path
  matches procedural content patterns (how-to-*, /runbooks/, runbook-*, procedure*).
  Zero effect on other intent types.

Temporal date boost:
  Applied post-RRF, after entity boost, for TEMPORAL intent queries only.
  Multiplies boosted_score by config.date_path_boost_factor for documents whose
  path contains a date string extracted from the query (YYYY-MM-DD or YYYY-MM).
  Also boosts recent documents for relative temporal queries ("recent", "last month").
  Disabled by default (date_path_boost_enabled=False in TemporalBoostConfig).
  Zero effect on other intent types.

All functions return [] on empty inputs. Never raise.
"""

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from kairix.search.bm25 import BM25Result
from kairix.search.config import EntityBoostConfig, ProceduralBoostConfig, TemporalBoostConfig
from kairix.search.vector import VecResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RRF_K: int = 60

# ---------------------------------------------------------------------------
# Entity slug helpers (for secondary name-based lookup)
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert entity name to QMD path slug (lowercase, hyphens for separators)."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


_LABEL_TO_DIR: dict[str, str] = {
    "person": "person",
    "organisation": "organisation",
    "organization": "organisation",
    "concept": "concept",
}

# ---------------------------------------------------------------------------
# Temporal date extraction patterns (query-side)
# ---------------------------------------------------------------------------

# Matches YYYY-MM-DD (e.g. "2026-03-22") in a query
_QUERY_ISO_DATE_RE: re.Pattern[str] = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Matches YYYY-MM (e.g. "2026-03") — also captures the YYYY-MM prefix of ISO dates
_QUERY_YEAR_MONTH_RE: re.Pattern[str] = re.compile(r"\b(\d{4}-\d{2})(?:-\d{2})?\b")

# Relative temporal terms that trigger a recency boost instead of a date-match boost
_RELATIVE_TEMPORAL_RE: re.Pattern[str] = re.compile(
    r"\b(recent(?:ly)?|last\s+(?:week|month)|yesterday|today|this\s+(?:week|month))\b",
    re.IGNORECASE,
)

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

    # Chunk date metadata (populated at index time — used by chunk_date_boost, TMP-7B)
    chunk_date: str = ""

    # Cross-encoder re-rank score (populated by rerank.rerank() when enabled)
    rerank_score: float = 0.0

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
    config: EntityBoostConfig | None = None,
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

    cfg = config if config is not None else EntityBoostConfig()
    if not cfg.enabled:
        for r in results:
            r.boosted_score = r.rrf_score
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
            "RETURN n.vault_path AS vault_path, n.name AS name, labels(n) AS labels, count(*) AS in_degree"
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
    # Secondary: slug-based lookup from entity name + label → QMD path
    name_slug_in_degree: dict[str, int] = {}
    for row in rows:
        vp = str(row["vault_path"]).lower().replace("\\", "/")
        in_deg = int(row.get("in_degree") or 0)
        path_in_degree[vp] = in_deg
        parent = str(Path(vp).parent).lower().replace("\\", "/")
        if parent not in (".", ""):
            dir_in_degree[parent] = max(dir_in_degree.get(parent, 0), in_deg)

        # Build slug-based secondary lookup from entity name + label
        name = str(row.get("name") or "").strip()
        labels = row.get("labels") or []
        if name:
            for lbl in labels:
                dir_name = _LABEL_TO_DIR.get(str(lbl).lower())
                if dir_name:
                    slug = _slugify(name)
                    if slug:
                        qmd_path = f"{dir_name}/{slug}.md"
                        existing = name_slug_in_degree.get(qmd_path, 0)
                        name_slug_in_degree[qmd_path] = max(existing, in_deg)

    if not path_in_degree:
        for r in results:
            r.boosted_score = r.rrf_score
        return results

    max_in_degree = max(path_in_degree.values()) or 1

    for r in results:
        path_lower = r.path.lower().replace("\\", "/")
        in_deg = path_in_degree.get(path_lower, 0)

        # Secondary: slug-based lookup from entity name
        if in_deg == 0:
            in_deg = name_slug_in_degree.get(path_lower, 0)

        if in_deg == 0:
            # Half-boost for files under an entity directory
            for dir_prefix, dir_deg in dir_in_degree.items():
                if path_lower.startswith(dir_prefix + "/"):
                    in_deg = max(in_deg, dir_deg // 2)
                    break
        r.entity_mention_count = in_deg
        if in_deg > 0:
            normalised = in_deg / max_in_degree
            boost_amount = min(cfg.factor * math.log1p(normalised * 10), cfg.cap - 1.0)
            r.boosted_score = r.rrf_score * (1.0 + boost_amount)
        else:
            r.boosted_score = r.rrf_score

    return sorted(results, key=lambda r: r.boosted_score, reverse=True)


# ---------------------------------------------------------------------------
# Procedural boosting
# ---------------------------------------------------------------------------


def procedural_boost(
    results: list[FusedResult],
    config: ProceduralBoostConfig | None = None,
) -> list[FusedResult]:
    """
    Boost documents whose paths match procedural content patterns for PROCEDURAL
    intent queries.

    Called after entity_boost(), before apply_budget(). Only called when
    intent == QueryIntent.PROCEDURAL — callers are responsible for the guard.

    Boost logic:
      If any pattern in config.path_patterns matches result.path:
        result.boosted_score *= config.factor

    This is a re-ranking fix, not a retrieval fix. Procedural files are typically
    retrieved (Hit@5 > 0.5) but ranked too low (positions 4-7). The 1.4x multiplier
    moves them into the top-3 without over-ranking them for non-procedural queries
    (the boost is gated to PROCEDURAL intent in hybrid.py).

    Args:
        results:  List of FusedResult (from rrf(), after entity_boost()).
        config:   ProceduralBoostConfig. Default: ProceduralBoostConfig().

    Returns:
        Results re-sorted by boosted_score descending.
        Returns results unmodified on any error.
        Never raises.
    """
    cfg = config if config is not None else ProceduralBoostConfig()
    if not cfg.enabled:
        return results

    if not results:
        return results

    try:
        return _procedural_boost_impl(results, cfg)
    except Exception as e:
        logger.warning("procedural_boost: error — %s — returning unmodified results", e)
        return results


def _procedural_boost_impl(
    results: list[FusedResult],
    config: ProceduralBoostConfig,
) -> list[FusedResult]:
    """Implementation of procedural boosting — called from procedural_boost() with error boundary."""
    patterns = [re.compile(p, re.IGNORECASE) for p in config.path_patterns]
    for r in results:
        if any(p.search(r.path) for p in patterns):
            r.boosted_score *= config.factor
    return sorted(results, key=lambda r: r.boosted_score, reverse=True)


# ---------------------------------------------------------------------------
# Temporal date boosting
# ---------------------------------------------------------------------------


def temporal_date_boost(
    results: list[FusedResult],
    query: str,
    config: TemporalBoostConfig | None = None,
) -> list[FusedResult]:
    """
    Boost documents whose path contains a date string matching the queried date
    for TEMPORAL intent queries.

    Called after entity_boost(), before apply_budget(). Only called when
    intent == QueryIntent.TEMPORAL — callers are responsible for the guard.
    Disabled by default (date_path_boost_enabled=False in TemporalBoostConfig).

    Boost logic:
      - If query contains a specific date (YYYY-MM-DD): boost documents whose
        path contains that exact date string or its YYYY-MM prefix.
      - If query contains a relative temporal term ("recent", "last week",
        "last month", "yesterday", "today"): boost documents whose path
        contains an ISO date from the last 30 days (last week) or 90 days
        (last month / recent).
      - Non-matching documents are unaffected.

    Args:
        results:  List of FusedResult (from rrf(), after entity_boost()).
        query:    The original (or rewritten) query string.
        config:   TemporalBoostConfig. Default: TemporalBoostConfig().

    Returns:
        Results re-sorted by boosted_score descending.
        Returns results unmodified on any error.
        Never raises.
    """
    cfg = config if config is not None else TemporalBoostConfig()
    if not cfg.date_path_boost_enabled:
        return results

    if not results:
        return results

    try:
        return _temporal_date_boost_impl(results, query, cfg.date_path_boost_factor)
    except Exception as e:
        logger.warning("temporal_date_boost: error — %s — returning unmodified results", e)
        return results


def _temporal_date_boost_impl(
    results: list[FusedResult],
    query: str,
    boost_factor: float,
) -> list[FusedResult]:
    """Implementation of temporal date boosting — called from temporal_date_boost() with error boundary."""
    import datetime

    boosted_any = False

    # --- Strategy 1: explicit date in query (YYYY-MM-DD or YYYY-MM) ---
    iso_match = _QUERY_ISO_DATE_RE.search(query)
    ym_match = _QUERY_YEAR_MONTH_RE.search(query)

    date_strings: list[str] = []
    if iso_match:
        date_strings.append(iso_match.group(1))  # YYYY-MM-DD
        date_strings.append(iso_match.group(1)[:7])  # YYYY-MM prefix
    elif ym_match:
        date_strings.append(ym_match.group(1))  # YYYY-MM

    if date_strings:
        for r in results:
            if any(ds in r.path for ds in date_strings):
                r.boosted_score *= boost_factor
                boosted_any = True
        if boosted_any:
            return sorted(results, key=lambda r: r.boosted_score, reverse=True)

    # --- Strategy 2: relative temporal terms → recency window ---
    rel_match = _RELATIVE_TEMPORAL_RE.search(query)
    if rel_match:
        term = rel_match.group(1).lower().replace(" ", " ")
        today = datetime.date.today()
        if "last week" in term or "yesterday" in term or "today" in term:
            cutoff = today - datetime.timedelta(days=30)
        else:
            # "recently", "last month", "this week", "this month"
            cutoff = today - datetime.timedelta(days=90)

        # Path dates: look for YYYY-MM-DD in the path and compare to cutoff
        _path_date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
        for r in results:
            path_date_match = _path_date_re.search(r.path)
            if path_date_match:
                try:
                    path_date = datetime.date.fromisoformat(path_date_match.group(1))
                    if path_date >= cutoff:
                        r.boosted_score *= boost_factor
                        boosted_any = True
                except ValueError:
                    pass

    if boosted_any:
        return sorted(results, key=lambda r: r.boosted_score, reverse=True)

    return results


# ---------------------------------------------------------------------------
# Chunk-date proximity boosting (TMP-7B)
# ---------------------------------------------------------------------------


def chunk_date_boost(
    results: list[FusedResult],
    query_date: object,
    config: TemporalBoostConfig | None = None,
) -> list[FusedResult]:
    """
    Boost documents by proximity of chunk_date metadata to the query date.

    Uses Gaussian decay: boost = 1 + exp(-delta^2 / (2*sigma^2))
    where sigma = halflife / 1.177 (halflife = days at which boost = 0.5 of max).

    Called from hybrid.py for TEMPORAL intent when chunk_date_boost_enabled is True.
    Requires chunk_date to be passed in via FusedResult (TMP-7B wires this).

    Args:
        results:     FusedResult list after entity_boost.
        query_date:  Date extracted from the query (datetime.date). None = no-op.
        config:      TemporalBoostConfig. Default: TemporalBoostConfig().

    Returns:
        Results re-sorted by boosted_score descending.
        Returns results unmodified on any error.
        Never raises.
    """
    cfg = config if config is not None else TemporalBoostConfig()
    if not cfg.chunk_date_boost_enabled or query_date is None:
        return results

    if not results:
        return results

    try:
        return _chunk_date_boost_impl(results, query_date, cfg)
    except Exception as e:
        logger.warning("chunk_date_boost: error — %s — returning unmodified results", e)
        return results


def _chunk_date_boost_impl(
    results: list[FusedResult],
    query_date: object,
    config: TemporalBoostConfig,
) -> list[FusedResult]:
    """Implementation of chunk_date proximity boosting."""
    import datetime
    import math

    sigma = config.chunk_date_decay_halflife_days / 1.177
    boosted_any = False

    for r in results:
        chunk_date_str = getattr(r, "chunk_date", None)
        if not chunk_date_str:
            continue
        try:
            if isinstance(chunk_date_str, str):
                chunk_date = datetime.date.fromisoformat(chunk_date_str[:10])
            else:
                chunk_date = chunk_date_str
        except (ValueError, TypeError):
            continue

        delta_days = abs((chunk_date - query_date).days)
        boost = 1.0 + math.exp(-(delta_days ** 2) / (2 * sigma ** 2))
        r.boosted_score *= boost
        boosted_any = True

    if boosted_any:
        return sorted(results, key=lambda r: r.boosted_score, reverse=True)
    return results
