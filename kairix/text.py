"""
Canonical token estimation utilities for kairix.

All token-counting code should use these functions rather than
rolling local estimators. The word-count heuristic matches the
OpenAI tokeniser within 10 % for English prose.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate token count. Uses word count * 1.3 (matches OpenAI tokeniser within 10%)."""
    words = len(text.split())
    if words == 0:
        return 0
    return max(1, int(words * 1.3))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    words = text.split()
    target_words = int(max_tokens / 1.3)
    if len(words) <= target_words:
        return text
    return " ".join(words[:target_words]) + " ... [truncated]"
