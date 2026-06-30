"""
Provider-plugin synthesis for session briefings.

Takes collected context (up to 3000 tokens) and produces a structured
~800-token briefing markdown file.

Falls back gracefully: returns partial briefing with note if the
provider call fails.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an AI session-briefing generator. "
    "You receive raw context gathered from an agent's memory system "
    "and produce a concise, structured session briefing.\n\n"
    "The briefing must be:\n"
    "- <= 800 tokens\n"
    "- Structured markdown with these sections (omit sections where no content)\n"
    "- Human-readable and directly usable at session start\n"
    "- Prioritise actionable items (pending, blocked, decisions) over background info\n"
    "- Be direct and specific - avoid vague generalisations\n\n"
    "Output the briefing in this exact structure:\n"
    "## Pending & Blocked\n"
    "(items tagged [pending], [blocked], or TODO"
    " - if none, write 'No pending items.')\n\n"
    "## Recent Decisions\n"
    "(last 30 days decisions, 3-5 bullet points"
    " - if none, write 'No recent decisions found.')\n\n"
    "## Active Projects\n"
    "(current in-progress work from memory and boards"
    " - if none, write 'No active project data found.')\n\n"
    "## Relevant Context\n"
    "(synthesised key context from search results)\n\n"
    "## Key Constraints\n"
    "(top rules and constraints from knowledge collection)\n\n"
    "Do not add a title or preamble - the header is added by the caller."
)

# Section heading → context source key(s) → empty-section text. When the
# LLM is unavailable (provider unconfigured or the chat call failed), the
# brief surfaces the raw gathered content under these headings — five of
# the six brief sources need no LLM, so an offline brief still carries the
# actionable items instead of empty placeholders (PLA-267).
_OFFLINE_SECTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("## Pending & Blocked", ("memory_logs",), "No pending items."),
    ("## Recent Decisions", ("recent_decisions",), "No recent decisions found."),
    ("## Active Projects", ("recent_memory",), "No active project data found."),
    ("## Relevant Context", ("hybrid_search", "entity_stub"), "No relevant context found."),
    ("## Key Constraints", ("knowledge_rules",), "No constraints found."),
)


def _degrade_to_non_llm_brief(agent: str, context: dict[str, str], reason: str) -> str:
    """Assemble a brief from already-gathered context without the LLM.

    Five of the six brief sources need no LLM call — when synthesis is
    unavailable we still surface that raw content under the standard
    headings, so the agent gets the actual pending/blocked items,
    decisions, and rules rather than empty ``(synthesis unavailable)``
    placeholders. Falls back to :func:`fallback_briefing` only when there
    is genuinely no content to show.
    """
    if not any(value and value.strip() for value in context.values()):
        return fallback_briefing(agent, reason)
    sections: list[str] = []
    for heading, keys, empty_text in _OFFLINE_SECTIONS:
        parts = [context[k].strip() for k in keys if context.get(k) and context[k].strip()]
        rendered = "\n\n".join(parts) if parts else empty_text
        sections.append(f"{heading}\n{rendered}")
    note = f"_Synthesis unavailable ({reason}); showing gathered context directly._"
    return note + "\n\n" + "\n\n".join(sections)


def synthesise(
    agent: str,
    context: dict[str, str],
    max_tokens: int = 800,
    llm_backend=None,
) -> str:
    """
    Synthesise a session briefing via the configured provider plugin.

    Args:
        agent:       Agent name (e.g. "builder").
        context:     Dict mapping source name to content string.
        max_tokens:  Max tokens for the generated briefing.
        llm_backend: Optional LLM backend instance for dependency injection.
                     Defaults to a ``ProviderChatBackend`` over the
                     configured plugin.

    Returns:
        Synthesised briefing markdown string.
        Returns formatted partial briefing with error note on failure.
        Never raises.
    """
    if llm_backend is None:
        from kairix.paths import provider_name
        from kairix.providers import get_provider
        from kairix.transport.embed_service import ProviderChatBackend

        name = provider_name()
        if name is None:
            # No provider configured, but 5/6 sources need no LLM — surface
            # the gathered context rather than an empty brief (PLA-267).
            return _degrade_to_non_llm_brief(
                agent, context, "kairix.config.yaml is missing the required 'provider:' field"
            )
        llm_backend = ProviderChatBackend(get_provider(name))
    chat = llm_backend.chat
    # Build context block
    context_parts: list[str] = []
    for source_name, content in context.items():
        if content and content.strip():
            context_parts.append(f"=== {source_name} ===\n{content}")

    if not context_parts:
        return fallback_briefing(agent, "no context sources available")

    context_block = "\n\n".join(context_parts)

    # Trim to 3000 tokens (rough: ~2300 words)
    words = context_block.split()
    if len(words) > 2300:
        context_block = " ".join(words[:2300]) + "\n... [context truncated for token limit]"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Generate a session briefing for agent: {agent}\n\n"
                f"Context gathered from memory sources:\n\n{context_block}"
            ),
        },
    ]

    try:
        result = chat(messages, max_tokens=max_tokens)
        if not result or not result.strip():
            raise ValueError("empty response from synthesis API")
        return result.strip()
    except Exception as e:
        logger.warning("synthesiser: synthesis API call failed - %s", e)
        # The chat call failed, but the 5 non-LLM sources already gathered
        # their content — degrade to that gathered context instead of an
        # empty placeholder brief (PLA-267). str(e) is acceptable here: it
        # is embedded in a markdown block written to a local file / returned
        # to the agent operator, not an untrusted API client.
        return _degrade_to_non_llm_brief(agent, context, str(e))


def fallback_briefing(agent: str, reason: str) -> str:
    """Generate a minimal fallback briefing when synthesis fails."""
    return (
        "## Pending & Blocked\n"
        "(synthesis unavailable - check memory logs manually)\n\n"
        "## Recent Decisions\n"
        "(synthesis unavailable)\n\n"
        "## Active Projects\n"
        "(synthesis unavailable)\n\n"
        "## Relevant Context\n"
        f"Briefing synthesis failed: {reason}\n\n"
        f"Check `/data/workspaces/{agent}/MEMORY.md` and recent memory logs as fallback.\n\n"
        "## Key Constraints\n"
        "(synthesis unavailable - check knowledge rules manually)\n"
    )
