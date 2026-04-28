"""
Rule-based classifier for kairix memory writes.

Rules in priority order (first match wins):
  1. Starts with ## [HH:MM] or ## \\d{2}:\\d{2}  → episodic
  2. Contains never/always/rule:/constraint:/never do  → procedural-rule
  3. Contains pattern:/workflow:/how to/## Steps/step 1  → procedural-pattern
  4. Contains decided:/decision:/ADR-/we chose/we decided/rationale:  → semantic-decision
  5. Contains IP/endpoint:/version:/vCPU/port pattern  → semantic-fact
  6. No match  → None (triggers LLM judge)

Rule-based must handle ≥80% of realistic cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kairix.classify.router import VALID_AGENTS

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset(
    {"episodic", "procedural-rule", "procedural-pattern", "semantic-decision", "semantic-fact", "entity"}
)


@dataclass
class ClassificationResult:
    type: str  # classification type
    target_path: str  # resolved absolute path
    confidence: float  # 0.0-1.0
    reason: str  # human-readable explanation
    needs_confirmation: bool = False  # True if confidence < 0.70
    extra: dict = field(default_factory=dict)  # optional extra metadata


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Episodic: starts with ## HH:MM (session log header), optional leading whitespace
_RE_EPISODIC = re.compile(r"^\s*##\s+\d{2}:\d{2}", re.MULTILINE)

# Procedural-rule: strong normative language
_RE_PROCEDURAL_RULE_PATTERNS = [
    re.compile(r"\bnever\s+", re.IGNORECASE),
    re.compile(r"\balways\s+", re.IGNORECASE),
    re.compile(r"\brule\s*:", re.IGNORECASE),
    re.compile(r"\bconstraint\s*:", re.IGNORECASE),
    re.compile(r"\bnever\s+do\b", re.IGNORECASE),
]

# Procedural-pattern: structural markers (checked before normative rules)
_RE_PROCEDURAL_PATTERN_STRONG = [
    re.compile(r"^\s*pattern\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*workflow\s*:", re.IGNORECASE | re.MULTILINE),
]
# Procedural-pattern: weaker markers (checked after normative rules)
_RE_PROCEDURAL_PATTERN_WEAK = [
    re.compile(r"\bhow\s+to\b", re.IGNORECASE),
    re.compile(r"^##\s+steps\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bstep\s+1\b", re.IGNORECASE),
]

# Semantic-decision: decision records
_RE_SEMANTIC_DECISION_PATTERNS = [
    re.compile(r"\bdecided\s*:", re.IGNORECASE),
    re.compile(r"\bdecision\s*:", re.IGNORECASE),
    re.compile(r"\bADR-\d+", re.IGNORECASE),
    re.compile(r"\bwe\s+chose\b", re.IGNORECASE),
    re.compile(r"\bwe\s+decided\b", re.IGNORECASE),
    re.compile(r"\brationale\s*:", re.IGNORECASE),
]

# Semantic-fact: infrastructure/config facts
_RE_SEMANTIC_FACT_PATTERNS = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IPv4
    re.compile(r"\bendpoint\s*:", re.IGNORECASE),
    re.compile(r"\bversion\s*:", re.IGNORECASE),
    re.compile(r"\bvCPU\b", re.IGNORECASE),
    re.compile(r"\bport\s+\d{2,5}\b", re.IGNORECASE),  # port 8080
    re.compile(r":\d{4,5}\b"),  # :8080 in URLs
]


# ---------------------------------------------------------------------------
# Rule classifier
# ---------------------------------------------------------------------------


def _matches_any(content: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(content) for p in patterns)


def classify_by_rules(content: str) -> tuple[str | None, str]:
    """
    Apply rule-based classification.

    Returns:
        (classification_type, reason) or (None, "") if no rule matched.
    """
    if not content or not content.strip():
        return None, ""

    # Rule 1: episodic — starts with ## HH:MM (must be at start of a line, MULTILINE)
    if _RE_EPISODIC.search(content):
        return "episodic", "starts with ## HH:MM session-log header"

    # Rule 3a (elevated): procedural-pattern — check explicit structural markers first
    # before normative-language rules, because pattern docs often contain "always/never"
    # as part of their instructions (e.g. "Pattern: X must always be Y. Step 1: ...")
    for pat in _RE_PROCEDURAL_PATTERN_STRONG:
        m = pat.search(content)
        if m:
            matched = m.group(0).strip()
            return "procedural-pattern", f"contains procedural pattern marker: {matched!r}"

    # Rule 2: procedural-rule — normative language
    for pat in _RE_PROCEDURAL_RULE_PATTERNS:
        m = pat.search(content)
        if m:
            matched = m.group(0).strip()
            return "procedural-rule", f"contains normative language: {matched!r}"

    # Rule 3b: remaining procedural-pattern patterns (how-to, ## Steps, step 1)
    for pat in _RE_PROCEDURAL_PATTERN_WEAK:
        m = pat.search(content)
        if m:
            matched = m.group(0).strip()
            return "procedural-pattern", f"contains procedural pattern marker: {matched!r}"

    # Rule 4: semantic-decision — decision records
    for pat in _RE_SEMANTIC_DECISION_PATTERNS:
        m = pat.search(content)
        if m:
            matched = m.group(0).strip()
            return "semantic-decision", f"contains decision marker: {matched!r}"

    # Rule 5: semantic-fact — infra/config facts
    for pat in _RE_SEMANTIC_FACT_PATTERNS:
        m = pat.search(content)
        if m:
            matched = m.group(0).strip()
            return "semantic-fact", f"contains infrastructure/config fact: {matched!r}"

    return None, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_content(
    content: str,
    agent: str = "shared",
) -> ClassificationResult:
    """
    Classify content using rule-based classifier.

    If no rule matches, returns a result with type=None and confidence=0.0
    so the caller can trigger the LLM judge.

    Args:
        content: Raw content string to classify.
        agent:   Agent name for path resolution (used by router). Must be a valid agent.

    Returns:
        ClassificationResult.

    Raises:
        ValueError: If agent is not in VALID_AGENTS.
    """
    if agent not in VALID_AGENTS:
        raise ValueError(f"Invalid agent {agent!r}. Must be one of: {sorted(VALID_AGENTS)}")

    from kairix.classify.router import resolve_target_path

    classification_type, reason = classify_by_rules(content)

    if classification_type is None:
        # No rule matched — caller should use LLM judge
        return ClassificationResult(
            type="unknown",
            target_path="",
            confidence=0.0,
            reason="no rule matched — requires LLM classification",
            needs_confirmation=True,
        )

    # Resolve path
    try:
        target_path = resolve_target_path(agent, classification_type)
    except (ValueError, KeyError) as e:
        target_path = ""
        reason = f"{reason} (path resolution failed: {e})"

    confidence = 0.90  # rule-based matches are high confidence
    needs_confirmation = confidence < 0.70

    return ClassificationResult(
        type=classification_type,
        target_path=target_path,
        confidence=confidence,
        reason=reason,
        needs_confirmation=needs_confirmation,
    )
