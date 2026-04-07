"""
8-step briefing pipeline for Mnemosyne session briefings.

Steps:
  1. Recent memory log files (last 7 days, tagged items)
  2. Today's + yesterday's memory file (full content)
  3. Entity stub for agent
  4. Agent knowledge rules
  5. Recent decisions (last 30 days)
  6. Hybrid search on agent name
  7. GPT-4o-mini synthesis
  8. Write to /data/mnemosyne/briefing/<agent>-latest.md

Steps 1-6 run concurrently. Total context is capped at 3000 tokens with
priority-based truncation (step 6 first, then 5, 4, etc.).

Never raises — returns partial briefing on any failure.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Token caps per source (approximate)
_SOURCE_TOKEN_CAPS: dict[str, int] = {
    "memory_logs": 500,
    "recent_memory": 300,
    "entity_stub": 400,
    "knowledge_rules": 300,
    "recent_decisions": 400,
    "hybrid_search": 600,
}

# Total context budget before truncation (3000 tokens ~ 2300 words)
_TOTAL_CONTEXT_CAP = 3000

# Priority order for truncation when over budget (lowest priority first)
_TRUNCATION_ORDER = [
    "hybrid_search",
    "recent_decisions",
    "knowledge_rules",
    "entity_stub",
    "recent_memory",
    "memory_logs",
]

_WORDS_PER_TOKEN = 1.3


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * _WORDS_PER_TOKEN)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    words = text.split()
    limit_words = int(max_tokens / _WORDS_PER_TOKEN)
    if len(words) <= limit_words:
        return text
    return " ".join(words[:limit_words]) + "\n... [truncated]"


def _run_source(name: str, fn, *args) -> tuple[str, str]:
    """
    Run a source fetcher safely. Returns (name, content).
    Logs warning and returns empty string on any failure.
    """
    try:
        result = fn(*args)
        return name, result or ""
    except Exception as e:
        logger.warning("pipeline: source %r failed — %s", name, e)
        return name, ""


def _trim_context(context: dict[str, str]) -> dict[str, str]:
    """
    Trim context sources if total token estimate exceeds _TOTAL_CONTEXT_CAP.
    Truncates lowest-priority sources first.
    """
    total = sum(_estimate_tokens(v) for v in context.values())
    if total <= _TOTAL_CONTEXT_CAP:
        return context

    trimmed = dict(context)
    for source_name in _TRUNCATION_ORDER:
        if total <= _TOTAL_CONTEXT_CAP:
            break
        if trimmed.get(source_name):
            current = trimmed[source_name]
            current_tokens = _estimate_tokens(current)
            cap = _SOURCE_TOKEN_CAPS.get(source_name, 200)
            if current_tokens > cap // 2:
                # Halve the allocation
                new_cap = max(cap // 2, 50)
                trimmed[source_name] = _truncate_to_tokens(current, new_cap)
                total -= current_tokens - _estimate_tokens(trimmed[source_name])

    return trimmed


def generate_briefing(agent: str) -> str:
    """
    Generate a session briefing for the given agent.

    Runs the full 8-step pipeline:
    1-6: Concurrent source fetching
    7:   GPT-4o-mini synthesis
    8:   Write to file

    Args:
        agent: Agent name (e.g. "builder", "shape").

    Returns:
        Full briefing content (with header). Never raises.
    """
    from mnemosyne.briefing.sources import (
        fetch_entity_stub,
        fetch_hybrid_search,
        fetch_knowledge_rules,
        fetch_memory_logs,
        fetch_recent_decisions,
        fetch_recent_memory,
    )
    from mnemosyne.briefing.synthesiser import synthesise
    from mnemosyne.briefing.writer import write_briefing

    t_start = time.monotonic()
    logger.info("pipeline: generating briefing for agent %r", agent)

    # Steps 1-6: concurrent source fetching
    source_tasks = [
        ("memory_logs", fetch_memory_logs, agent, _SOURCE_TOKEN_CAPS["memory_logs"]),
        ("recent_memory", fetch_recent_memory, agent, _SOURCE_TOKEN_CAPS["recent_memory"]),
        ("entity_stub", fetch_entity_stub, agent, _SOURCE_TOKEN_CAPS["entity_stub"]),
        ("knowledge_rules", fetch_knowledge_rules, agent, _SOURCE_TOKEN_CAPS["knowledge_rules"]),
        ("recent_decisions", fetch_recent_decisions, agent, _SOURCE_TOKEN_CAPS["recent_decisions"]),
        ("hybrid_search", fetch_hybrid_search, agent, _SOURCE_TOKEN_CAPS["hybrid_search"]),
    ]

    context: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map: dict[Future, str] = {}
        for name, fn, *args in source_tasks:
            future = executor.submit(_run_source, name, fn, *args)
            future_map[future] = name

        for future in as_completed(future_map, timeout=25):
            try:
                source_name, content = future.result()
                if content:
                    context[source_name] = content
                    logger.debug(
                        "pipeline: source %r returned %d tokens",
                        source_name,
                        _estimate_tokens(content),
                    )
            except Exception as e:
                name = future_map[future]
                logger.warning("pipeline: source %r future failed — %s", name, e)

    sources_count = len(context)
    logger.info("pipeline: collected %d sources for %r", sources_count, agent)

    # Trim context if over budget
    context = _trim_context(context)

    # Step 7: Synthesise
    briefing_body = synthesise(agent, context, max_tokens=800)

    # Token estimate for output
    token_estimate = _estimate_tokens(briefing_body)

    # Step 8: Write to file
    try:
        out_path = write_briefing(
            agent=agent,
            content=briefing_body,
            sources_count=sources_count,
            token_estimate=token_estimate,
        )
        logger.info(
            "pipeline: briefing written to %s in %.1fs",
            out_path,
            time.monotonic() - t_start,
        )
    except OSError as e:
        logger.error("pipeline: could not write briefing file — %s", e)
        # Return the content anyway

    # Read back what was written (includes header added by writer)
    try:
        from mnemosyne.briefing.writer import _BRIEFING_DIR

        out_path = _BRIEFING_DIR / f"{agent}-latest.md"
        if out_path.exists():
            return out_path.read_text(encoding="utf-8")
    except Exception as _exc:
        logger.debug("pipeline: could not read back briefing file — %s", _exc)

    # Fallback: build content inline
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")
    header = (
        f"# Agent Briefing — {agent} — {date_str}\n"
        f"_Generated: {ts} | Sources: {sources_count} | Tokens: ~{token_estimate}_\n\n"
    )
    return header + briefing_body
