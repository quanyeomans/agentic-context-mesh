"""
kairix.mcp.server — MCP server exposing kairix tools to MCP-compatible agents.

Provides five tools:
  search       Hybrid BM25 + vector search over the vault
  entity       Entity lookup from Neo4j
  prep         Context preparation: tiered L0/L1 summary generation
  timeline     Temporal query rewriting + date-aware retrieval
  usage_guide  Return the kairix agent usage guide (self-documentation)

The server uses FastMCP (from the ``mcp`` package). Install via:
    pip install kairix[agents]

Tool functions are pure Python functions importable without FastMCP installed —
import them directly for unit testing or programmatic use.

Design principles:
  - Never raises; returns error dicts on failure so agents can handle gracefully
  - All inputs/outputs are JSON-serialisable primitives
  - Dependencies initialised lazily on first call
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool implementations — pure Python, no mcp dependency
# ---------------------------------------------------------------------------


def tool_search(
    query: str,
    agent: str | None = None,
    scope: str = "shared+agent",
    budget: int = 3000,
) -> dict[str, Any]:
    """
    Run hybrid BM25 + vector search over the vault.

    Args:
        query:  Search query string.
        agent:  Agent name for collection scoping (e.g. "shape", "builder").
        scope:  Collection scope: "shared", "agent", or "shared+agent".
        budget: Token budget cap. Default 3000.

    Returns:
        dict with keys: query, intent, results (list of dicts), total_tokens,
        latency_ms, error.
    """
    try:
        from kairix.search.hybrid import search

        result = search(query=query, agent=agent, scope=scope, budget=budget)
        return {
            "query": result.query,
            "intent": result.intent.value if hasattr(result.intent, "value") else str(result.intent),
            "results": [
                {
                    "path": r.result.path,
                    "score": r.result.boosted_score,
                    "snippet": r.content[:500],
                    "tokens": r.token_estimate,
                }
                for r in result.results
            ],
            "total_tokens": result.total_tokens,
            "latency_ms": result.latency_ms,
            "error": result.error,
        }
    except Exception as exc:
        logger.warning("mcp.search failed: %s", exc)
        return {"query": query, "intent": "", "results": [], "total_tokens": 0, "latency_ms": 0.0, "error": str(exc)}


def tool_entity(
    name: str,
    action: str = "lookup",
) -> dict[str, Any]:
    """
    Entity lookup from Neo4j (primary) or entities.db (fallback).

    Args:
        name:   Entity name or id to look up.
        action: "lookup" (default) — find and return the entity.

    Returns:
        dict with keys: id, name, type, summary, vault_path, error.
        Returns error key when entity not found.
    """
    try:
        # Try Neo4j first
        from kairix.graph.client import get_client

        neo4j = get_client()
        if neo4j.available:
            import re

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            rows = neo4j.cypher(
                "MATCH (n) WHERE n.id = $id OR toLower(n.name) = toLower($name) "
                "RETURN labels(n)[0] AS type, n.id AS id, n.name AS name, "
                "n.summary AS summary, n.vault_path AS vault_path LIMIT 1",
                {"id": slug, "name": name},
            )
            if rows:
                r = rows[0]
                return {
                    "id": r.get("id", ""),
                    "name": r.get("name", ""),
                    "type": r.get("type", ""),
                    "summary": r.get("summary") or "",
                    "vault_path": r.get("vault_path") or "",
                    "error": "",
                }
    except Exception as exc:
        logger.warning("mcp.entity neo4j lookup failed: %s", exc)

    return {"id": "", "name": name, "type": "", "summary": "", "vault_path": "", "error": f"Entity not found: {name}"}


def tool_prep(
    query: str,
    agent: str | None = None,
    tier: str = "l0",
) -> dict[str, Any]:
    """
    Context preparation: generate tiered summaries for the given query.

    Args:
        query:  Query to prepare context for.
        agent:  Agent name for scoping.
        tier:   "l0" (brief, ≤500 tokens) or "l1" (detailed, ≤2000 tokens).

    Returns:
        dict with keys: query, tier, summary, tokens, error.
    """
    try:
        from kairix._azure import chat_completion

        max_tokens = 150 if tier == "l0" else 600
        system = (
            "You are a concise knowledge assistant. Summarise what is known about the topic in 2-3 sentences."
            if tier == "l0"
            else "You are a knowledge assistant. Provide a structured overview of the topic."
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Topic: {query}"},
        ]
        summary = chat_completion(messages, max_tokens=max_tokens)
        return {
            "query": query,
            "tier": tier,
            "summary": summary,
            "tokens": len(summary) // 4,
            "error": "",
        }
    except Exception as exc:
        logger.warning("mcp.prep failed: %s", exc)
        return {"query": query, "tier": tier, "summary": "", "tokens": 0, "error": str(exc)}


def tool_timeline(
    query: str,
    anchor_date: str | None = None,
) -> dict[str, Any]:
    """
    Temporal query rewriting and date-aware retrieval.

    Detects relative temporal expressions ("last week", "yesterday", "Q1")
    and rewrites them with explicit date tokens for improved retrieval.

    Args:
        query:       Query that may contain temporal expressions.
        anchor_date: ISO date string (YYYY-MM-DD) to anchor relative dates.
                     Defaults to today.

    Returns:
        dict with keys: original_query, rewritten_query, is_temporal,
        time_window (dict with start/end), error.
    """
    try:
        from datetime import date as _date

        from kairix.temporal.rewriter import is_relative_temporal, rewrite_temporal_query

        anchor: _date | None = None
        if anchor_date:
            try:
                anchor = _date.fromisoformat(anchor_date)
            except ValueError:
                pass

        is_temporal = is_relative_temporal(query)
        rewritten = rewrite_temporal_query(query=query, reference_date=anchor) if is_temporal else query

        # Extract time window if temporal
        time_window: dict[str, str] = {}
        if is_temporal:
            try:
                from kairix.temporal.rewriter import extract_time_window

                start, end = extract_time_window(query=query, reference_date=anchor)
                if start or end:
                    time_window = {
                        "start": str(start) if start else "",
                        "end": str(end) if end else "",
                    }
            except Exception:
                logger.debug("extract_time_window failed", exc_info=True)

        return {
            "original_query": query,
            "rewritten_query": rewritten if rewritten is not None else query,
            "is_temporal": is_temporal,
            "time_window": time_window,
            "error": "",
        }
    except Exception as exc:
        logger.warning("mcp.timeline failed: %s", exc)
        return {
            "original_query": query,
            "rewritten_query": query,
            "is_temporal": False,
            "time_window": {},
            "error": str(exc),
        }


def tool_usage_guide(topic: str = "") -> dict[str, Any]:
    """
    Return the kairix agent usage guide, or a section of it filtered by topic.

    Use this tool when you are unsure how to use kairix, when a search returns
    unexpected results, or when you want to understand a specific feature.

    Args:
        topic: Optional topic filter (e.g. "temporal", "entity", "troubleshoot",
               "intent", "budget"). Empty string returns the full guide.

    Returns:
        dict with keys: topic, content (markdown string), error.
    """
    try:
        from pathlib import Path

        # Find the guide relative to this file's package root
        guide_path = Path(__file__).parent.parent.parent / "docs" / "agent-usage-guide.md"
        if not guide_path.exists():
            import kairix as _kairix

            guide_path = Path(_kairix.__file__).parent.parent / "docs" / "agent-usage-guide.md"

        if not guide_path.exists():
            return {
                "topic": topic,
                "content": "",
                "error": "Usage guide not found. Run: kairix onboard guide --vault-root <path>",
            }

        full_text = guide_path.read_text(encoding="utf-8")

        if not topic:
            return {"topic": "", "content": full_text, "error": ""}

        # Filter to sections matching the topic (heading-level search)
        topic_lower = topic.lower()
        lines = full_text.splitlines()
        sections: list[str] = []
        in_section = False
        current: list[str] = []

        for line in lines:
            is_heading = line.startswith("## ") or line.startswith("### ")
            if is_heading:
                if in_section and current:
                    sections.append("\n".join(current))
                    current = []
                if topic_lower in line.lower():
                    in_section = True
                    current = [line]
                else:
                    in_section = False
            elif in_section:
                current.append(line)

        if in_section and current:
            sections.append("\n".join(current))

        if not sections:
            # Fallback: search for topic keyword in full text
            matching_lines = [ln for ln in lines if topic_lower in ln.lower()]
            content = "\n".join(matching_lines[:30]) if matching_lines else full_text[:2000]
        else:
            content = "\n\n".join(sections)

        return {"topic": topic, "content": content, "error": ""}

    except Exception as exc:
        logger.warning("mcp.usage_guide failed: %s", exc)
        return {"topic": topic, "content": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# FastMCP server — only constructed when mcp package is available
# ---------------------------------------------------------------------------


def build_server() -> Any:
    """
    Construct and return the FastMCP server with all four tools registered.

    Raises ImportError when the ``mcp`` package is not installed.
    Install via: pip install kairix[agents]
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "The 'mcp' package is required to run the MCP server. Install it with: pip install 'kairix[agents]'"
        ) from exc

    server = FastMCP("kairix")

    @server.tool()
    def search(
        query: str,
        agent: str | None = None,
        scope: str = "shared+agent",
        budget: int = 3000,
    ) -> dict[str, Any]:
        """Hybrid BM25 + vector search over the vault."""
        return tool_search(query=query, agent=agent, scope=scope, budget=budget)

    @server.tool()
    def entity(name: str, action: str = "lookup") -> dict[str, Any]:
        """Entity lookup from Neo4j / entities.db."""
        return tool_entity(name=name, action=action)

    @server.tool()
    def prep(query: str, agent: str | None = None, tier: str = "l0") -> dict[str, Any]:
        """Context preparation: tiered L0/L1 summary generation."""
        return tool_prep(query=query, agent=agent, tier=tier)

    @server.tool()
    def timeline(query: str, anchor_date: str | None = None) -> dict[str, Any]:
        """Temporal query rewriting + date-aware retrieval."""
        return tool_timeline(query=query, anchor_date=anchor_date)

    @server.tool()
    def usage_guide(topic: str = "") -> dict[str, Any]:
        """Return the kairix agent usage guide. Call this when unsure how to use kairix."""
        return tool_usage_guide(topic=topic)

    return server
