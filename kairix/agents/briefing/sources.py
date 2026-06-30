"""
Individual source fetchers for the briefing pipeline.

Each fetcher is independent and safe to run concurrently.
All functions return strings (may be empty on failure) and never raise.

Each fetcher accepts an optional ``memory_dirs`` / ``document_root`` test
seam so tests can pass a tmp_path-rooted layout without monkeypatching
the kairix.paths helpers. Production callers leave them ``None`` and the
fetchers resolve via :func:`kairix.core.agents.scope.get_agent_scope`
(which honours the ``agents:`` block in ``kairix.config.yaml``).

The memory-reading fetchers (``fetch_memory_logs`` /
``fetch_recent_memory``) iterate every surface returned by
:meth:`AgentScope.memory_paths` — multi-surface agents must not silently
drop content from any configured location.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

# build_search_pipeline is imported at module load (not lazily inside
# fetch_hybrid_search) so the factory's per-config memoisation kicks in
# on every brief invocation. With the lazy ``from kairix.core.factory
# import ...`` pattern, the symbol lookup ran each call but the actual
# rebuild was guarded by ``_PIPELINE_CACHE``; the original sin wasn't
# the import latency itself but the fact that with concurrent first
# calls (#396 W-B Commit 1) two threads could race a fresh rebuild.
# Hoisting the import keeps the call path tight: cache hit → <1ms
# memory lookup, cache miss → exactly one build under the cache lock.
from kairix.agents.briefing._source_caches import (
    BriefSourceCache,
    get_brief_source_cache,
    reset_brief_source_cache,
)
from kairix.core.factory import build_search_pipeline
from kairix.text import truncate_to_tokens

if TYPE_CHECKING:
    from kairix.core.search.pipeline import SearchPipeline

# Re-export the brief-source cache accessors + class so tests + the
# probe-caches CLI can reach them without an underscore-prefixed
# import (F5: no internal-name imports in tests).
__all__ = [
    "BriefSourceCache",
    "fetch_entity_stub",
    "fetch_hybrid_search",
    "fetch_knowledge_rules",
    "fetch_memory_logs",
    "fetch_memory_logs_via_scope",
    "fetch_recent_decisions",
    "fetch_recent_memory",
    "get_brief_source_cache",
    "reset_brief_source_cache",
    "resolve_memory_dirs",
]

# Cache keys for the slow-moving fetchers — one logical name per fetcher
# so the cache's ``(source_name, agent)`` tuple stays unambiguous. The two
# time-sensitive fetchers (memory_logs, recent_memory) deliberately do NOT
# cache (PLA-267): they surface today's pending/blocked items, and a 1h TTL
# would cap the freshness of the very thing the brief exists to show.
_CACHE_KEY_ENTITY_STUB = "entity_stub"
_CACHE_KEY_KNOWLEDGE_RULES = "knowledge_rules"
_CACHE_KEY_RECENT_DECISIONS = "recent_decisions"

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


def resolve_memory_dirs(agent: str, config: dict[str, object] | None = None) -> list[Path]:
    """Return every surface path declared by the agent's :class:`AgentScope`.

    Wraps :func:`kairix.core.agents.scope.get_agent_scope` so the brief
    source fetchers don't depend on the scope module's import path more
    than once. ``config`` is the test seam — production callers leave it
    None and the scope loader reads ``kairix.config.yaml`` itself.

    Returns an empty list when scope resolution raises (missing
    ``agents:`` and ``agent_defaults:`` blocks — the operator hasn't
    onboarded the agent yet); the caller logs the empty-result.
    """
    from kairix.core.agents.scope import get_agent_scope

    effective_config = _load_config() if config is None else config
    try:
        scope = get_agent_scope(agent, config=effective_config)
    except ValueError as exc:
        logger.warning(
            "sources: no AgentScope for %r — %s. Add an agents.%s entry "
            "or an agent_defaults block in kairix.config.yaml.",
            agent,
            exc,
            agent,
        )
        return []
    return list(scope.memory_paths())


def _load_config() -> dict[str, object] | None:
    """Read ``kairix.config.yaml`` as a top-level dict, or None on missing.

    Thin alias over :func:`kairix.paths.load_top_level_config` so the
    sources module doesn't have to spell out the env-var + yaml machinery.
    """
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def fetch_memory_logs(
    agent: str,
    max_tokens: int = 500,
    memory_dirs: Iterable[Path] | None = None,
    config: dict[str, object] | None = None,
) -> str:
    """
    Fetch last 7 days of memory log files for agent across every configured surface.

    Extracts items tagged [pending], [blocked], [action:], and summaries.
    Returns empty string on failure.

    Test seams:
      * ``memory_dirs`` — pass an explicit iterable of Paths to bypass
        scope resolution entirely (the simplest seam for tests with
        a single tmp_path-rooted directory).
      * ``config`` — pass a parsed ``kairix.config.yaml`` dict to drive
        AgentScope resolution against an inline config instead of the
        on-disk file. Useful when the test wants to exercise the
        synthesis / multi-surface paths through the public surface.

    When both seams are ``None`` (production path) surfaces are resolved
    via :meth:`kairix.core.agents.scope.AgentScope.memory_paths` using
    the on-disk ``kairix.config.yaml``.

    NOT cached (PLA-267): this source surfaces today's
    ``[pending]``/``[blocked]``/TODO items, so it reads fresh on every
    call. Caching it for 1h capped the freshness of the very items the
    brief exists to show — a line added minutes ago stayed invisible for
    up to an hour. (The slow-moving sources — entity stub, knowledge
    rules, recent decisions — still cache.)
    """
    if memory_dirs is not None:
        return _fetch_memory_logs_impl(agent, max_tokens, list(memory_dirs))
    return fetch_memory_logs_via_scope(agent, max_tokens, config=config)


def fetch_memory_logs_via_scope(
    agent: str,
    max_tokens: int,
    config: dict[str, object] | None,
) -> str:
    """Resolve the agent's memory surfaces via AgentScope, then read them.

    Public symbol (F5-clean) so tests can drive the scope-driven path
    without reaching into underscore-prefixed helpers. The brief
    pipeline uses this directly when it wants to surface a missing-
    memory note; the cached ``fetch_memory_logs`` delegates here for
    production reads.
    """
    dirs = resolve_memory_dirs(agent, config=config)
    if not dirs:
        return ""
    return _fetch_memory_logs_impl(agent, max_tokens, dirs)


def _collect_last_seven_days_lines(memory_dir: Path) -> list[str]:
    """Read last-7-days tagged lines from a single surface.

    Extracted from ``_fetch_memory_logs_impl`` so the parent's per-surface
    loop stays under F16's cognitive-complexity ceiling — three nested
    loops (per surface x per day x per line) push the parent over 15.
    """
    today = date.today()
    out: list[str] = []
    for days_back in range(7):
        day = today - timedelta(days=days_back)
        path = memory_dir / f"{day.isoformat()}.md"
        if path.exists():
            out.extend(_extract_tagged_lines(path, day.isoformat()))
    return out


def _fetch_memory_logs_impl(agent: str, max_tokens: int, memory_dirs: list[Path]) -> str:
    """Cache-free implementation of :func:`fetch_memory_logs`.

    Iterates every surface in ``memory_dirs`` and collects last-7-days
    tagged lines from each. Surfaces that don't exist are logged once
    (so operators see the missing directory) but do not abort the read
    — a multi-surface agent with one missing workspace dir still gets
    a useful brief from its memory dir.
    """
    try:
        lines: list[str] = []
        for memory_dir in memory_dirs:
            if not memory_dir.exists():
                logger.warning(
                    "sources: memory dir not found for agent %r at %s — create it with: mkdir -p %s",
                    agent,
                    memory_dir,
                    memory_dir,
                )
                continue
            lines.extend(_collect_last_seven_days_lines(memory_dir))

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


def fetch_recent_memory(
    agent: str,
    max_tokens: int = 300,
    memory_dirs: Iterable[Path] | None = None,
) -> str:
    """
    Fetch today's and yesterday's memory files for agent (full content)
    across every configured surface.

    Returns empty string on failure.

    ``memory_dirs`` is the test seam; production callers leave it None
    and surfaces are resolved via
    :meth:`kairix.core.agents.scope.AgentScope.memory_paths`.

    NOT cached (PLA-267): today's + yesterday's memory is the freshest,
    most time-sensitive source, so it reads fresh on every call rather
    than serving a value cached for up to an hour.
    """
    if memory_dirs is not None:
        return _fetch_recent_memory_impl(agent, max_tokens, list(memory_dirs))
    return _fetch_recent_memory_via_scope(agent, max_tokens, config=None)


def _fetch_recent_memory_via_scope(
    agent: str,
    max_tokens: int,
    config: dict[str, object] | None,
) -> str:
    """Production read path — resolve surfaces via AgentScope then read."""
    dirs = resolve_memory_dirs(agent, config=config)
    if not dirs:
        return ""
    return _fetch_recent_memory_impl(agent, max_tokens, dirs)


def _fetch_recent_memory_impl(agent: str, max_tokens: int, memory_dirs: list[Path]) -> str:
    """Cache-free implementation of :func:`fetch_recent_memory`.

    Iterates every surface; missing dirs are skipped silently here (the
    log-once happens in ``fetch_memory_logs``'s impl — duplicating the
    warning at two source fetchers spams operator logs).
    """
    try:
        parts: list[str] = []
        for memory_dir in memory_dirs:
            if not memory_dir.exists():
                continue
            parts.extend(_collect_recent_memory_sections(memory_dir))

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

    Cached for 1h per ``(entity_stub, agent)``; explicit ``document_root=``
    overrides bypass the cache.
    """
    if document_root is None:
        cache = get_brief_source_cache()
        cached = cache.get(_CACHE_KEY_ENTITY_STUB, agent)
        if cached is not None:
            return cached
        result = _fetch_entity_stub_impl(agent, max_tokens, document_root)
        cache.put(_CACHE_KEY_ENTITY_STUB, agent, result)
        return result
    return _fetch_entity_stub_impl(agent, max_tokens, document_root)


def _fetch_entity_stub_impl(agent: str, max_tokens: int, document_root: Path | None) -> str:
    """Cache-free implementation of :func:`fetch_entity_stub`."""
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

    Cached for 1h per ``(knowledge_rules, agent)``; explicit ``document_root=``
    overrides bypass the cache.
    """
    if document_root is None:
        cache = get_brief_source_cache()
        cached = cache.get(_CACHE_KEY_KNOWLEDGE_RULES, agent)
        if cached is not None:
            return cached
        result = _fetch_knowledge_rules_impl(agent, max_tokens, document_root)
        cache.put(_CACHE_KEY_KNOWLEDGE_RULES, agent, result)
        return result
    return _fetch_knowledge_rules_impl(agent, max_tokens, document_root)


def _fetch_knowledge_rules_impl(agent: str, max_tokens: int, document_root: Path | None) -> str:
    """Cache-free implementation of :func:`fetch_knowledge_rules`."""
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

    Cached for 1h per ``(recent_decisions, agent)``; explicit ``document_root=``
    overrides bypass the cache.
    """
    if document_root is None:
        cache = get_brief_source_cache()
        cached = cache.get(_CACHE_KEY_RECENT_DECISIONS, agent)
        if cached is not None:
            return cached
        result = _fetch_recent_decisions_impl(agent, max_tokens, document_root)
        cache.put(_CACHE_KEY_RECENT_DECISIONS, agent, result)
        return result
    return _fetch_recent_decisions_impl(agent, max_tokens, document_root)


def _fetch_recent_decisions_impl(agent: str, max_tokens: int, document_root: Path | None) -> str:
    """Cache-free implementation of :func:`fetch_recent_decisions`."""
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
# Source 6: Hybrid search driven by real agent signal (PLA-264)
# ---------------------------------------------------------------------------

# Actionable-work markers we lift out of the cheap fan-out sources to seed
# the retrieval query. Matched case-insensitively as substrings — the same
# convention ``_MEMORY_LOG_TAGS`` uses for the memory-log scan.
_FOCUS_TAGS = ("[pending]", "[blocked]", "[action:", "todo")

# Caps so the derived query stays a focused signal, not the whole memory
# log: at most N distinct work-items, truncated to a small token budget
# before it hits the embedder.
_MAX_FOCUS_LINES = 8
_FOCUS_QUERY_MAX_TOKENS = 80

# Leading bookkeeping markup we strip so the embedded query carries the
# work-item text ("ship the connector refactor"), not the markers.
_LEADING_DATE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}\]\s*")
_LEADING_STATUS_RE = re.compile(r"^\[(?:pending|blocked)\]\s*", re.IGNORECASE)
_LEADING_TODO_RE = re.compile(r"^todo\b:?\s*", re.IGNORECASE)


def _clean_focus_line(line: str) -> str:
    """Reduce one tagged memory line to its searchable work-item text.

    Drops the leading ``[YYYY-MM-DD]`` day label that ``fetch_memory_logs``
    prefixes, then the ``[pending]``/``[blocked]`` status tag and any
    ``TODO`` marker, so the query embeds the actual task rather than the
    bookkeeping markup.
    """
    text = _LEADING_DATE_RE.sub("", line.strip())
    text = _LEADING_STATUS_RE.sub("", text)
    text = _LEADING_TODO_RE.sub("", text)
    return text.strip(" -*").strip()


def _line_is_focus(line_lower: str) -> bool:
    """True when a lowercased line carries one of the actionable markers."""
    return any(tag in line_lower for tag in _FOCUS_TAGS)


def _extend_focus_from_block(block: str, seen: set[str], out: list[str]) -> None:
    """Append the distinct actionable lines in ``block`` to ``out``."""
    for raw in block.splitlines():
        if not _line_is_focus(raw.strip().lower()):
            continue
        cleaned = _clean_focus_line(raw)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)


def _extract_focus_lines(signals: Iterable[str]) -> list[str]:
    """Pull distinct ``[pending]``/``[blocked]``/``[action:]``/TODO items.

    Scans every signal block (memory logs, recent memory, entity stub) and
    returns the cleaned work-item lines, de-duplicated and capped at
    :data:`_MAX_FOCUS_LINES`.
    """
    seen: set[str] = set()
    out: list[str] = []
    for block in signals:
        _extend_focus_from_block(block, seen, out)
    return out[:_MAX_FOCUS_LINES]


def _collect_focus_signals(agent: str) -> list[str]:
    """Re-read the cheap fan-out sources to seed the retrieval query.

    Returns the non-empty text blocks the brief's other sources already
    produce for this agent — memory logs, recent memory, and the entity
    stub. memory_logs + recent_memory read fresh each call (PLA-267 — they
    are the time-sensitive sources and are no longer cached); the entity
    stub is cached for 1h, so its read here is a cache hit when the
    concurrent fan-out has already run it.
    """
    blocks: list[str] = []
    for block in (fetch_memory_logs(agent), fetch_recent_memory(agent), fetch_entity_stub(agent)):
        if block:
            blocks.append(block)
    return blocks


def _build_focus_query(agent: str, focus_signals: Iterable[str] | None) -> str:
    """Build the retrieval query from real agent signal, not the bare name.

    ``focus_signals`` is the injection seam — pass the fan-out's already-
    fetched memory text directly. Production callers leave it ``None`` and
    the signal is collected via :func:`_collect_focus_signals`.

    Falls back to the agent name only when there is genuinely no actionable
    signal yet (a freshly onboarded agent with no memory) — that keeps a
    non-empty query rather than searching for the empty string.
    """
    signals = list(focus_signals) if focus_signals is not None else _collect_focus_signals(agent)
    focus_lines = _extract_focus_lines(signals)
    if not focus_lines:
        return agent
    return truncate_to_tokens(" ".join(focus_lines), _FOCUS_QUERY_MAX_TOKENS)


def fetch_hybrid_search(
    agent: str,
    max_tokens: int = 600,
    *,
    pipeline: SearchPipeline | None = None,
    focus_signals: Iterable[str] | None = None,
) -> str:
    """
    Run hybrid search seeded by the agent's real work signal (PLA-264).

    The query is derived from the ``[pending]``/``[blocked]``/``[action:]``/
    ``TODO`` lines already surfaced by the cheap fan-out sources (memory
    logs + recent memory + entity stub) — searching for those concrete
    work-items returns docs relevant to what the agent is actually doing,
    instead of the degenerate "search for the string 'builder'" that merely
    matched docs mentioning the agent name. The ``agent=`` / ``scope=``
    arguments are kept for collection scoping.

    Test seams (production callers leave both ``None``):
      * ``pipeline`` — inject a search pipeline (e.g. ``FakeSearchPipeline``)
        instead of constructing the production one via
        :func:`build_search_pipeline`.
      * ``focus_signals`` — pass the fan-out signal blocks directly rather
        than re-reading them through the cached source fetchers.

    Returns empty string on failure.

    ``build_search_pipeline`` is imported at module load (not lazily
    inside this function) so the factory's process-shared
    ``_PIPELINE_CACHE`` is honoured: the first brief call pays the 2.3s
    construction; every subsequent call observes the cached pipeline
    instance and short-circuits in <1ms. The race-resistance of that
    cache is locked by #396 W-B Commit 1's double-checked locking.
    """
    try:
        search_pipeline = build_search_pipeline() if pipeline is None else pipeline
        query = _build_focus_query(agent, focus_signals)
        result = search_pipeline.search(query=query, agent=agent, scope="shared+agent", budget=max_tokens * 2)

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
