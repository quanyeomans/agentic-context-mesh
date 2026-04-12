"""
LLM judge for ambiguous memory write classification.

Uses GPT-4o-mini to classify content that doesn't match rule-based patterns.
Falls back gracefully: returns confidence=0.0 and needs_confirmation=True on failure.
"""

from __future__ import annotations

import json
import logging

from mnemosyne.llm import get_default_backend as _get_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a memory-write classifier for an AI agent system. Given a piece of content, classify it into exactly one type.

Types:
- episodic: A session log entry, timestamped activity record, or diary-style note.
  Example: "## 09:15\nFixed the RRF path dedup bug in mnemosyne/search/rrf.py"
- procedural-rule: A normative constraint, rule, or standing instruction.
  Example: "Never write credentials to disk. Always fetch from Key Vault at runtime."
- procedural-pattern: A workflow, how-to guide, or step-by-step pattern.
  Example: "Pattern: To add a new entity stub: Step 1: create file. Step 2: embed."
- semantic-decision: A recorded decision, ADR, or rationale for a choice.
  Example: "We decided to use GPT-4o-mini for synthesis. Rationale: cheaper and fast."
- semantic-fact: An infrastructure or configuration fact (IPs, versions, endpoints, specs).
  Example: "Azure endpoint: https://xxx.openai.azure.com/ — version: 2024-02-01"
- entity: A profile or description of a named entity (person, org, project, concept).
  Example: "Alice Chen: CTO of Acme Corp. Based in Singapore."

Return JSON only:
{"type": "<type>", "confidence": <0.0-1.0>, "reason": "<brief explanation>"}
"""


def classify_with_llm(content: str, agent: str = "shared") -> ClassificationResult:  # noqa: F821
    """
    Use GPT-4o-mini to classify ambiguous content.

    Args:
        content: Content string to classify.
        agent:   Agent name (used for routing after classification).

    Returns:
        ClassificationResult. On failure, returns confidence=0.0 and needs_confirmation=True.
    """
    from mnemosyne.classify.router import resolve_target_path
    from mnemosyne.classify.rules import VALID_AGENTS, ClassificationResult

    # Validate agent
    valid = VALID_AGENTS | {"shared"}
    if agent not in valid:
        raise ValueError(f"Invalid agent {agent!r}. Must be one of: {sorted(valid)}")

    if not content or not content.strip():
        return ClassificationResult(
            type="unknown",
            target_path="",
            confidence=0.0,
            reason="empty content",
            needs_confirmation=True,
        )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Classify this content:\n\n{content[:2000]}"},
    ]

    try:
        raw = _get_llm().chat(messages, max_tokens=200)
        if not raw:
            raise ValueError("empty response from LLM")

        # Parse JSON — handle code fence wrapping
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last code fence lines
            inner = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
            text = inner.strip()

        parsed = json.loads(text)
        classification_type = parsed.get("type", "unknown")
        confidence = float(parsed.get("confidence", 0.5))
        reason = parsed.get("reason", "LLM classification")

        # Resolve target path
        try:
            target_path = resolve_target_path(agent, classification_type)
        except (ValueError, KeyError):
            target_path = ""

        return ClassificationResult(
            type=classification_type,
            target_path=target_path,
            confidence=confidence,
            reason=reason,
            needs_confirmation=confidence < 0.70,
        )

    except Exception as e:
        logger.warning("judge: LLM classification failed — %s", e)
        return ClassificationResult(
            type="unknown",
            target_path="",
            confidence=0.0,
            reason=f"LLM classification failed: {e}",
            needs_confirmation=True,
        )
