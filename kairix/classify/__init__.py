"""
kairix.classify — Auto-classification of memory writes.

Public API:
  classify_content(content: str, agent: str = "shared") -> ClassificationResult
      Classify content and return routing decision.
      Uses rule-based classifier first; falls back to LLM for ambiguous cases.
"""

from kairix.classify.rules import ClassificationResult, classify_content

__all__ = ["ClassificationResult", "classify_content"]
