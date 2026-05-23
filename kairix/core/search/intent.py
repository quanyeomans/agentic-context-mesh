"""
Query intent classifier for the kairix hybrid search pipeline.

Classifies a query string into one of seven intent types. Pure function — no I/O,
no external dependencies. Rule-based with defined priority order.

Intent types and their dispatch in hybrid.py:
  KEYWORD        → BM25 + vector via RRF (proper nouns, error codes, file paths, version strings)
  TEMPORAL       → BM25 + vector with date-string rewriting and date-filtered path set (TMP-2)
  ENTITY         → entity graph first, then hybrid (Phase 1b+)
  PROCEDURAL     → BM25 + vector via RRF with procedural path boost
  SEMANTIC       → BM25 + vector via RRF (default for abstract/conceptual queries)
  MULTI_HOP      → QueryPlanner decomposes into sub-queries, each runs hybrid
  ATTRIBUTE_FACT → fact retriever dominates fusion (Plan B-parity Capability #5)
                   — short entity-attribute lookups like "what is X's Y?" or "Y of X?"

Priority order (first match wins):
  TEMPORAL > MULTI_HOP > ATTRIBUTE_FACT > ENTITY > PROCEDURAL > KEYWORD > SEMANTIC

ATTRIBUTE_FACT slots in BEFORE ENTITY so a short attribute lookup
("what is acme's address?") routes to the fact layer rather than the
ENTITY-graph branch (which expects "tell me about ..." style framing).

Failure mode: never raises; returns SEMANTIC on any unexpected input.
"""

import re
from enum import Enum

# ---------------------------------------------------------------------------
# Temporal signals
# ---------------------------------------------------------------------------
_TEMPORAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\blast\s+(week|month|year|quarter|30|7|90|14)\b", re.IGNORECASE),
    re.compile(
        r"\bin\s+(January|February|March|April|May|June|July|August|September|October|November|December)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(yesterday|today|recently|lately|this\s+week|this\s+month)\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+did\b", re.IGNORECASE),
    re.compile(
        r"\bwhat\s+(changed|happened|was\s+done|was\s+completed|was\s+fixed|was\s+shipped)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcompleted\s+on\b", re.IGNORECASE),
    re.compile(r"\bsince\s+(last|the|a\s+few)\b", re.IGNORECASE),
    re.compile(r"\bover\s+the\s+(last|past)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\b.*\bdo\s+(last|this|in)\b", re.IGNORECASE),
    # P6 additions: date-prefixed temporal queries
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date: 2026-03-09
    re.compile(  # "March 2026"
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Entity signals
# ---------------------------------------------------------------------------
_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\btell\s+me\s+about\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+has\b.{1,50}\bdone\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+do\s+we\s+know\s+about\b", re.IGNORECASE),
    re.compile(r"\bwho\s+is\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+is\b.{1,30}\b(doing|working|responsible|role)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Procedural signals
# ---------------------------------------------------------------------------
_PROCEDURAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bhow\s+(to|do\s+I|do\s+we|should\s+I|can\s+I)\b", re.IGNORECASE),
    re.compile(
        r"\bwhat('s|\s+is)\s+the\s+(rule|process|procedure|workflow|standard|convention)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bshould\s+I\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+do\s+I\s+do\s+when\b", re.IGNORECASE),
    re.compile(r"\bstep[s\s]+to\b", re.IGNORECASE),
    re.compile(r"\bwhat('s|\s+is)\s+the\s+best\s+way\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+should\s+I\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Attribute-fact signals — short entity-attribute lookups (Plan B-parity #5)
#
# Targets the canonical "what is X's Y?" / "Y of X?" surface that the
# fact-retriever federation stage owns. Patterns are intentionally tight
# so longer narrative questions ("tell me about how X's Y evolved over
# the project") still route to ENTITY / SEMANTIC.
# ---------------------------------------------------------------------------
_ATTRIBUTE_FACT_PATTERNS: list[re.Pattern[str]] = [
    # "what is X's Y?" / "what's X's Y?" — possessive form, requires apostrophe-s.
    re.compile(r"\bwhat(?:'s|\s+is|\s+are|\s+was|\s+were)\s+\S+'s\s+\w+", re.IGNORECASE),
    # "Y of X?" — short attribute query, ≤6 words, no procedural/multi-hop
    # signals (gated downstream by ``_is_short_attribute_of_query``).
    re.compile(r"^\s*\w+(?:\s+\w+){0,2}\s+of\s+[A-Z][\w'.-]+\s*\??\s*$", re.IGNORECASE),
    # "X's Y?" — bare possessive attribute lookup, ≤4 words.
    re.compile(r"^\s*\S+'s\s+\w+\s*\??\s*$", re.IGNORECASE),
    # "what did X verb?" — single-verb factoid lookup (e.g. "what did Caroline research?").
    # Plan B-parity D2 remediation: LoCoMo single-hop questions of this
    # shape were falling all the way to SEMANTIC. The existing TEMPORAL
    # pattern requires a "do last/this/in" suffix, so this won't shadow it.
    re.compile(r"^\s*what\s+did\s+\S+\s+\w+\s*\??\s*$", re.IGNORECASE),
    # "who is/was X's Y?" — possessive identity lookup (e.g. "who was Caroline's friend?").
    # Plan B-parity D2 remediation: the ENTITY block matches bare "who is"
    # earlier in priority order, so this lookahead must fire BEFORE ENTITY
    # — which it already does (ATTRIBUTE_FACT runs before ENTITY in classify).
    re.compile(r"\bwho(?:'s|\s+is|\s+was|\s+are|\s+were)\s+\S+'s\s+\w+", re.IGNORECASE),
]


def _is_attribute_fact_query(query: str) -> bool:
    """Return True if ``query`` matches an attribute-fact lookup shape."""
    return any(p.search(query) for p in _ATTRIBUTE_FACT_PATTERNS)


# ---------------------------------------------------------------------------
# Multi-hop signals — queries spanning multiple documents/topics
# ---------------------------------------------------------------------------
_MULTI_HOP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\band\s+how\s+does\b", re.IGNORECASE),
    re.compile(r"relates?\s+to", re.IGNORECASE),
    re.compile(r"compared?\s+to", re.IGNORECASE),
    re.compile(r"impact\s+on", re.IGNORECASE),
    re.compile(r"connection\s+between", re.IGNORECASE),
    re.compile(r"relationship\s+between", re.IGNORECASE),
    re.compile(r"how\s+does.{1,40}affect", re.IGNORECASE),
    re.compile(r"both.{1,40}and.{1,40}(strategy|approach|method|framework)", re.IGNORECASE),
    re.compile(r"(positioning|methodology)\s+and\s+(how|why|what)", re.IGNORECASE),
    re.compile(r"link\s+between", re.IGNORECASE),
    re.compile(r"interaction\s+between", re.IGNORECASE),
    # P6-A additions: natural-language multi-hop signals
    re.compile(r"\band\s+why\b", re.IGNORECASE),  # "and why does", "and why do"
    re.compile(r"\btradeoffs?\b", re.IGNORECASE),  # "explain the tradeoffs"
]

# ---------------------------------------------------------------------------
# Keyword signals (proper nouns, codes, paths, versions)
# ---------------------------------------------------------------------------

# File path — forward slash or backslash sequences
_FILE_PATH_RE = re.compile(r"[/\\][a-zA-Z0-9_.\-]+[/\\][a-zA-Z0-9_.\-/\\]+")

# Version string — e.g. v1.2.3, 3.12.0, 1.1.2, 2024-02-01
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(\.\d+)?\b")

# Error code — HTTP codes (4xx/5xx), exception names, ALLCAPS codes, hexadecimal.
# Split into independent compiled patterns to keep each one under the Sonar
# python:S5843 regex-complexity threshold (≤20). Match semantics unchanged:
# a query is treated as carrying an error code when any one of the four
# patterns matches.
_ERROR_CODE_EXCEPTION_RE = re.compile(r"\b[A-Z]{2,}(?:Error|Exception)\b")
_ERROR_CODE_HTTP_RE = re.compile(r"\b[45]\d{2}\b")
_ERROR_CODE_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_ERROR_CODE_ALLCAPS_RE = re.compile(r"\b[A-Z]{3,}[-_][A-Z0-9]{2,}\b")
_ERROR_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _ERROR_CODE_EXCEPTION_RE,
    _ERROR_CODE_HTTP_RE,
    _ERROR_CODE_HEX_RE,
    _ERROR_CODE_ALLCAPS_RE,
)


def _has_error_code(query: str) -> bool:
    """Return True when ``query`` matches any of the error-code patterns."""
    return any(p.search(query) for p in _ERROR_CODE_PATTERNS)


# Title Case heuristic: 2+ consecutive capitalised words, none of which are
# common prepositions or stopwords that appear in natural language headings.
_STOPWORDS = frozenset(
    {
        "The",
        "A",
        "An",
        "In",
        "On",
        "At",
        "For",
        "To",
        "Of",
        "And",
        "Or",
        "But",
        "With",
        "By",
        "From",
        "As",
        "Is",
        "Are",
        "Was",
        "Were",
        "Be",
        "How",
        "What",
        "When",
        "Where",
        "Why",
        "Who",
        "Which",
        "That",
        "This",
        "Do",
        "Does",
        "Did",
        "Has",
        "Have",
        "Had",
        "Will",
        "Would",
        "Should",
        "Could",
        "Can",
        "May",
        "Might",
        "Tell",
        "Me",
        "We",
        "About",
        "Know",
        "Last",
        "Week",
        "Month",
        "Year",
        "Recently",
        "Yesterday",
        "Today",
    }
)

_TITLE_WORD_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+)\b")


def _is_keyword_query(query: str) -> bool:
    """Return True if query looks like a keyword/proper-noun lookup."""
    # File path
    if _FILE_PATH_RE.search(query):
        return True

    # Error code
    if _has_error_code(query):
        return True

    # Version string
    if _VERSION_RE.search(query):
        return True

    # Title Case: 2+ capitalised non-stopword words in a short query
    words = _TITLE_WORD_RE.findall(query)
    non_stop = [w for w in words if w not in _STOPWORDS]
    # Short queries (≤5 words) with ≥2 Title Case non-stopwords → keyword
    total_words = len(query.split())
    if len(non_stop) >= 2 and total_words <= 6:
        return True

    # Very short single Title Case word (3+ chars) with no sentence structure
    if len(non_stop) == 1 and total_words <= 3 and len(non_stop[0]) >= 3:
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class QueryIntent(str, Enum):
    """Intent class for a search query, determining dispatch strategy."""

    KEYWORD = "keyword"
    TEMPORAL = "temporal"
    ENTITY = "entity"
    PROCEDURAL = "procedural"
    SEMANTIC = "semantic"
    MULTI_HOP = "multi_hop"
    ATTRIBUTE_FACT = "attribute_fact"


def _matches_any(patterns: list[re.Pattern[str]], q: str) -> bool:
    return any(p.search(q) for p in patterns)


def _classify_nonempty(q: str) -> QueryIntent:
    """Classify a pre-stripped, non-empty query by priority-ordered pattern checks.

    Priority order: TEMPORAL > MULTI_HOP > ATTRIBUTE_FACT > ENTITY > PROCEDURAL > KEYWORD > SEMANTIC.
    Extracted from ``classify`` to keep the public function under F16's
    cognitive-complexity ceiling — ``classify`` retains the empty-guard
    + try/except safety net, this helper owns the dispatch chain.
    """
    if _matches_any(_TEMPORAL_PATTERNS, q):
        return QueryIntent.TEMPORAL
    if _matches_any(_MULTI_HOP_PATTERNS, q):
        return QueryIntent.MULTI_HOP
    # Attribute-fact slotted BEFORE ENTITY so "what is acme's address?"
    # routes to the fact retriever, not the ENTITY-graph branch (which
    # expects "tell me about ..." narrative framing).
    if _is_attribute_fact_query(q):
        return QueryIntent.ATTRIBUTE_FACT
    if _matches_any(_ENTITY_PATTERNS, q):
        return QueryIntent.ENTITY
    if _matches_any(_PROCEDURAL_PATTERNS, q):
        return QueryIntent.PROCEDURAL
    if _is_keyword_query(q):
        return QueryIntent.KEYWORD
    return QueryIntent.SEMANTIC


def classify(query: str) -> QueryIntent:
    """
    Classify a query string into a QueryIntent.

    Priority order: TEMPORAL > MULTI_HOP > ATTRIBUTE_FACT > ENTITY > PROCEDURAL > KEYWORD > SEMANTIC.
    Returns SEMANTIC on empty or unclassifiable input.
    Never raises.
    """
    try:
        q = query.strip()
        if not q:
            return QueryIntent.SEMANTIC
        return _classify_nonempty(q)
    except Exception:
        return QueryIntent.SEMANTIC
