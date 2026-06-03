"""
Individual source fetchers for the briefing pipeline.

Each fetcher is independent and safe to run concurrently.
All functions return strings (may be empty on failure) and never raise.

Each fetcher accepts an optional ``memory_dir`` / ``document_root`` Path
override so tests can pass a tmp_path-rooted layout without monkeypatching
the kairix.paths helpers. Production callers leave them ``None`` and the
helpers resolve via ``kairix.paths``.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

# build_search_pipeline is imported at module load (not lazily inside
# fetch_hybrid_search) so the factory's per-config memoisation kicks in
# on every brief invocation. With the lazy ``from kairix.core.factory
# import ...`` pattern, the symbol lookup ran each call but the actual
# rebuild was guarded by ``_PIPELINE_CACHE``; the original sin wasn't
# the import latency itself but the fact that with concurrent first
# calls (#396 W-B Commit 1) two threads could race a fresh rebuild.
# Hoisting the import keeps the call path tight: cache hit → <1ms
# memory lookup, cache miss → exactly one build under the cache lock.
from kairix.core.factory import build_search_pipeline
from kairix.text import truncate_to_tokens

logger = logging.getLogger(__name__)

# F17 — agent-knowledge directory name is referenced from three fetchers; one constant
# keeps the layout in a single edit site.
_AGENT_KNOWLEDGE_DIR = "04-Agent-Knowledge"


# ---------------------------------------------------------------------------
# Source 1: Recent memory log files (last 7 days)
# ---------------------------------------------------------------------------


_MEMORY_LOG_TAGS = ("[pending]", "[blocked]", "[action:", "todo", "## ")


def _extract_tagged_lines(path: Path, day_label: str) -> list[str]:
    """Read one memory log file; return labelled lines matching the tag set.

    Returns ``[]`` on any read failure (logged). Extracted from
    ``fetch_memory_logs`` so the per-day file processing doesn't have to
    live in a try/except inside a for-loop inside another try/except —
    that triple nesting pushed the parent over F16's ceiling.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("sources: error reading memory log %s — %s", path, e)
        return []
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if any(tag in stripped.lower() for tag in _MEMORY_LOG_TAGS):
            out.append(f"[{day_label}] {stripped}")
    return out


def fetch_memory_logs(agent: str, max_tokens: int = 500, memory_dir: Path | None = None) -> str:
    """
    Fetch last 7 days of memory log files for agent.

    Extracts items tagged [pending], [blocked], [action:], and summaries.
    Returns empty string on failure.
    """
    try:
        if memory_dir is None:
            from kairix.paths import agent_memory_path

            memory_dir = agent_memory_path(agent)
        if not memory_dir.exists():
            logger.warning(
                "sources: memory dir not found for agent %r at %s — create it with: mkdir -p %s",
                agent,
                memory_dir,
                memory_dir,
            )
            return ""

        today = date.today()
        lines: list[str] = []
        for days_back in range(7):
            day = today - timedelta(days=days_back)
            path = memory_dir / f"{day.isoformat()}.md"
            if path.exists():
                lines.extend(_extract_tagged_lines(path, day.isoformat()))

        if not lines:
            return ""

        result = "\n".join(lines)
        return truncate_to_tokens(result, max_tokens)

    except Exception as e:
        logger.warning("sources: fetch_memory_logs failed for %r — %s", agent, e)
        return ""


# ---------------------------------------------------------------------------
# Source 2: Today's + yesterday's memory files (full content)
# ---------------------------------------------------------------------------


def _read_memory_day(path: Path, day_label: str) -> str | None:
    """Read one memory file as a headed section; return None on read failure.

    Extracted from ``fetch_recent_memory`` to keep the parent under F16's
    cognitive-complexity ceiling — the per-day exists/try/read block
    inside an outer try/except triple-nested above 15.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("sources: error reading %s — %s", path, e)
        return None
    return f"### {day_label}\n{content}"


def _collect_recent_memory_sections(memory_dir: Path) -> list[str]:
    """Return today's + yesterday's labelled memory sections, skipping read errors.

    Extracted from ``fetch_recent_memory`` to keep the public function
    under F16's cognitive-complexity ceiling — once the outer try/except
    + memory_dir resolution + non-empty guard live in the parent, the
    per-day collection still tipped above 15.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    parts: list[str] = []
    for day in [today, yesterday]:
        path = memory_dir / f"{day.isoformat()}.md"
        if path.exists():
            section = _read_memory_day(path, day.isoformat())
            if section is not None:
                parts.append(section)
    return parts


def fetch_recent_memory(agent: str, max_tokens: int = 300, memory_dir: Path | None = None) -> str:
    """
    Fetch today's and yesterday's memory files for agent (full content).
    Returns empty string on failure.
    """
    try:
        if memory_dir is None:
            from kairix.paths import agent_memory_path

            memory_dir = agent_memory_path(agent)
        if not memory_dir.exists():
            return ""

        parts = _collect_recent_memory_sections(memory_dir)
        if not parts:
            return ""

        combined = "\n\n".join(parts)
        return truncate_to_tokens(combined, max_tokens)

    except Exception as e:
        logger.warning("sources: fetch_recent_memory failed for %r — %s", agent, e)
        return ""


# ---------------------------------------------------------------------------
# Source 3: Entity stub for agent
# ---------------------------------------------------------------------------


def _resolve_document_root(document_root: Path | None) -> Path:
    if document_root is not None:
        return document_root
    from kairix.paths import document_root as _document_root

    return _document_root()


def fetch_entity_stub(agent: str, max_tokens: int = 400, document_root: Path | None = None) -> str:
    """
    Fetch the agent's own entity stub from vault-entities.
    Returns empty string on failure.
    """
    try:
        root = _resolve_document_root(document_root)
        # Try agent-specific entity stub (concept type)
        candidate_paths = [
            root / _AGENT_KNOWLEDGE_DIR / "entities" / "concept" / f"{agent}.md",
            root / _AGENT_KNOWLEDGE_DIR / "entities" / "agent" / f"{agent}.md",
            root / _AGENT_KNOWLEDGE_DIR / "entities" / "person" / f"{agent}.md",
        ]

        for path in candidate_paths:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    return truncate_to_tokens(content, max_tokens)
                except Exception as e:
                    logger.warning("sources: error reading entity stub %s — %s", path, e)

        logger.debug("sources: no entity stub found for agent %r", agent)
        return ""

    except Exception as e:
        logger.warning("sources: fetch_entity_stub failed for %r — %s", agent, e)
        return ""


# ---------------------------------------------------------------------------
# Source 4: Agent knowledge rules
# ---------------------------------------------------------------------------


def _read_rules_file(path: Path) -> str | None:
    """Read one rules.md file as a labelled section; return None on read failure.

    Extracted from ``fetch_knowledge_rules`` for the same F16 reason as
    ``_read_memory_day``.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("sources: error reading rules %s — %s", path, e)
        return None
    return f"### Rules from {path.parent.name}/rules.md\n{content}"


def fetch_knowledge_rules(agent: str, max_tokens: int = 300, document_root: Path | None = None) -> str:
    """
    Fetch rules/constraints from agent's knowledge collection.
    Returns empty string on failure.
    """
    try:
        root = _resolve_document_root(document_root)
        rules_paths = [
            root / _AGENT_KNOWLEDGE_DIR / agent / "rules.md",
            root / _AGENT_KNOWLEDGE_DIR / "shared" / "rules.md",
        ]

        parts: list[str] = []
        for path in rules_paths:
            if path.exists():
                section = _read_rules_file(path)
                if section is not None:
                    parts.append(section)

        if not parts:
            return ""

        combined = "\n\n".join(parts)
        return truncate_to_tokens(combined, max_tokens)

    except Exception as e:
        logger.warning("sources: fetch_knowledge_rules failed for %r — %s", agent, e)
        return ""


# ---------------------------------------------------------------------------
# Source 5: Recent decisions (last 30 days)
# ---------------------------------------------------------------------------


def _read_decisions_file(path: Path) -> str | None:
    """Read decisions.md trimmed to the last ~30 days (last 3000 chars); None on failure.

    Extracted from ``fetch_recent_decisions`` for the same F16 reason as
    ``_read_memory_day``.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("sources: error reading decisions.md — %s", e)
        return None
    # Take last 30 days worth — heuristic: last 3000 chars
    if len(content) > 3000:
        content = content[-3000:]
    return f"### decisions.md\n{content}"


def fetch_recent_decisions(agent: str, max_tokens: int = 400, document_root: Path | None = None) -> str:
    """
    Fetch decisions from last 30 days from decisions.md.
    Returns empty string on failure.
    """
    try:
        root = _resolve_document_root(document_root)
        parts: list[str] = []

        decisions_path = root / _AGENT_KNOWLEDGE_DIR / agent / "decisions.md"
        if decisions_path.exists():
            section = _read_decisions_file(decisions_path)
            if section is not None:
                parts.append(section)

        if not parts:
            return ""

        combined = "\n\n".join(parts)
        return truncate_to_tokens(combined, max_tokens)

    except Exception as e:
        logger.warning("sources: fetch_recent_decisions failed for %r — %s", agent, e)
        return ""


# ---------------------------------------------------------------------------
# Source 6: Hybrid search on agent name
# ---------------------------------------------------------------------------


def fetch_hybrid_search(agent: str, max_tokens: int = 600) -> str:
    """
    Run hybrid search on agent name to get top 5 relevant chunks.
    Returns empty string on failure.

    ``build_search_pipeline`` is imported at module load (not lazily
    inside this function) so the factory's process-shared
    ``_PIPELINE_CACHE`` is honoured: the first brief call pays the 2.3s
    construction; every subsequent call observes the cached pipeline
    instance and short-circuits in <1ms. The race-resistance of that
    cache is locked by #396 W-B Commit 1's double-checked locking.
    """
    try:
        _pipeline = build_search_pipeline()
        result = _pipeline.search(query=agent, agent=agent, scope="shared+agent", budget=max_tokens * 2)

        if not result.results:
            return ""

        chunks: list[str] = []
        for item in result.results[:5]:
            path = getattr(item.result, "path", "unknown")
            content = getattr(item, "content", "")[:400]
            chunks.append(f"**{path}**\n{content}")

        combined = "\n\n---\n\n".join(chunks)
        return truncate_to_tokens(combined, max_tokens)

    except Exception as e:
        logger.warning("sources: fetch_hybrid_search failed for %r — %s", agent, e)
        return ""
