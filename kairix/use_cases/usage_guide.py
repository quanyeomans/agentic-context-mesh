"""Usage-guide use case — markdown-section retrieval shared by CLI and MCP.

Phase 3f of the CLI/MCP feature parity initiative (#168). Pre-Phase-3f
``mcp__usage_guide`` was MCP-only — operators couldn't read the agent
usage guide from a shell. This module wraps the existing topic-section
extractor in a use case so both surfaces share the same call shape and
result structure.

The CLI surface also addresses dogfood CONN-2 (deployment-step gap):
operators can now run ``kairix usage-guide`` to onboard themselves
without booting the MCP server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The bundled-guide package and filenames (#466). The guide ships as
# package data under ``kairix/agents/usage_guide/data/`` so it lands in
# both the wheel and the Docker image and is immune to the
# ``/var/lib/kairix`` named-volume shadow — the only copy used to live at
# the repo root under ``docs/``, which the image never carried, so the
# MCP tool returned ``UsageGuideNotFound`` on stock production deploys.
_GUIDE_PACKAGE = "kairix.agents.usage_guide"
_GUIDE_RESOURCE = "data/agent-usage-guide.md"


def _bundled_guide_path(resource: str) -> Path:
    """Return the on-disk path of a bundled usage-guide data file.

    Resolves via :func:`importlib.resources.files` so it works from both a
    source checkout and an installed wheel — no ``__file__``-based path
    lookups that break inside the shadow-mounted image. The returned path
    is deterministic whether or not the file is present; callers gate on
    :meth:`Path.exists`, so a missing file surfaces as the
    ``UsageGuideNotFound`` envelope rather than a raised exception.
    """
    # ``files(...)`` returns a Traversable; for a real package dir this is a
    # concrete filesystem path, which the callers read via
    # ``Path.read_text``. ``str(...)`` yields that path.
    return Path(str(resources.files(_GUIDE_PACKAGE).joinpath(resource)))


def _default_resolve_guide(guide_path: Path | None) -> Path:
    """Resolve the usage-guide markdown file path.

    Production resolution order:
      1. explicit ``guide_path`` override (the test/operator seam)
      2. the bundled package-data copy (shadow-immune, ships in the image)

    The bundled copy under ``kairix/agents/usage_guide/data/`` is the
    single source of truth — it lands in both the wheel and the Docker
    image and is immune to the ``/var/lib/kairix`` named-volume shadow
    (#466). No ``docs/`` fallback: the repo-root ``docs/`` copy is a stub
    pointer that never ships in the image, so resolving it would
    reintroduce the very split-brain #466 fixed.
    """
    if guide_path is not None:
        return guide_path
    return _bundled_guide_path(_GUIDE_RESOURCE)


# Topic keys that route to a dedicated docs file rather than the main
# guide's heading-section filter. Agents asking for ``mcp-latency`` want
# the per-tool p50/p99 table + task-budget formula, which lives in its
# own doc so the main guide stays short and the operations runbook can
# link directly to it. Add new entries here when a topic deserves its
# own file rather than a sub-section of the main guide.
_DEDICATED_TOPIC_DOCS: dict[str, str] = {
    "mcp-latency": "MCP-LATENCY-EXPECTATIONS.md",
}


def _resolve_dedicated_topic_path(topic: str) -> Path | None:
    """Return the on-disk path for a dedicated-topic markdown file, or None.

    Topics in :data:`_DEDICATED_TOPIC_DOCS` ship as package data under
    ``kairix/agents/usage_guide/data/`` next to the main guide — the same
    bundled, shadow-immune location :func:`_default_resolve_guide` uses.
    Returns ``None`` when the topic key isn't dedicated; callers fall
    through to the heading-section filter.
    """
    filename = _DEDICATED_TOPIC_DOCS.get(topic.lower())
    if filename is None:
        return None
    return _bundled_guide_path(f"data/{filename}")


@dataclass(frozen=True)
class UsageGuideOutput:
    """Outcome of one ``run_usage_guide`` invocation.

    Attributes:
        topic: The caller's topic filter (empty string returns the full guide).
        content: Markdown content. Full guide when ``topic == ""``;
            otherwise concatenated sections whose headings mention the
            topic. Falls back to a keyword-line search when no heading
            matches; first 2000 chars of the guide when no lines match.
        error: Empty on success; an operator-actionable message when
            the guide file is missing; ``"<Class>: <msg>"`` on
            unexpected failure.
    """

    topic: str = ""
    content: str = ""
    error: str = ""


@dataclass(frozen=True)
class UsageGuideDeps:
    """Injectable dependencies for ``run_usage_guide``.

    Non-Optional field wired to the production resolver via
    ``default_factory`` — eliminates the ``Optional[Callable]`` mypy
    regression class flagged in #204. Tests construct
    ``UsageGuideDeps(resolve_guide_fn=fake)`` with explicit overrides;
    ``UsageGuideDeps()`` resolves to ``_default_resolve_guide``.
    """

    resolve_guide_fn: Callable[[Path | None], Path] = field(default_factory=lambda: _default_resolve_guide)


def _collect_matching_sections(lines: list[str], topic_lower: str) -> list[str]:
    """Walk the file line-by-line and emit each section whose heading matches topic."""
    sections: list[str] = []
    current: list[str] = []
    in_section = False

    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            if in_section and current:
                sections.append("\n".join(current))
            current = []
            in_section = topic_lower in line.lower()
            if in_section:
                current.append(line)
        elif in_section:
            current.append(line)

    if in_section and current:
        sections.append("\n".join(current))
    return sections


def extract_topic_sections(full_text: str, topic_lower: str) -> str:
    """Return the concatenated markdown sections whose heading mentions the topic.

    Sections are demarcated by ``##`` / ``###`` headings. Falls back to a
    keyword search across all lines when no heading matches; falls back
    again to the first 2000 chars of the guide when no lines match.

    Public so CLI tests can pin the section-extraction contract directly.
    """
    lines = full_text.splitlines()
    sections = _collect_matching_sections(lines, topic_lower)
    if sections:
        return "\n\n".join(sections)
    matching_lines = [ln for ln in lines if topic_lower in ln.lower()]
    return "\n".join(matching_lines[:30]) if matching_lines else full_text[:2000]


def run_usage_guide(
    topic: str = "",
    *,
    guide_path: Path | None = None,
    deps: UsageGuideDeps | None = None,
) -> UsageGuideOutput:
    """Read the usage guide and optionally filter by topic.

    Never raises — failures populate ``UsageGuideOutput.error``.

    Args:
        topic: Optional topic filter (case-insensitive). Empty string
            returns the full guide.
        guide_path: Explicit path to the guide markdown file. When
            omitted, the use case resolves the production location.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or UsageGuideDeps()
    resolve = d.resolve_guide_fn

    try:
        # Dedicated-topic routing: topics in _DEDICATED_TOPIC_DOCS resolve
        # to their own markdown file under docs/agents/ rather than a
        # heading slice of the main guide. The full file is returned
        # verbatim. Tests opt out by passing an explicit guide_path
        # (the caller-supplied path always wins).
        dedicated_path = _resolve_dedicated_topic_path(topic) if guide_path is None and topic else None
        resolved = resolve(dedicated_path if dedicated_path is not None else guide_path)
        if not resolved.exists():
            return UsageGuideOutput(
                topic=topic,
                error="UsageGuideNotFound: run 'kairix onboard guide --document-root <path>' to install it",
            )

        full_text = resolved.read_text(encoding="utf-8")
        if not topic:
            return UsageGuideOutput(content=full_text)
        if dedicated_path is not None:
            return UsageGuideOutput(topic=topic, content=full_text)

        return UsageGuideOutput(topic=topic, content=extract_topic_sections(full_text, topic.lower()))
    except Exception as exc:
        logger.warning("run_usage_guide failed: %s", exc, exc_info=True)
        return UsageGuideOutput(topic=topic, error=f"{type(exc).__name__}: {exc}")


def usage_guide_output_to_envelope(out: UsageGuideOutput) -> dict[str, Any]:
    """Project a ``UsageGuideOutput`` to the JSON envelope MCP callers receive."""
    return {
        "topic": out.topic,
        "content": out.content,
        "error": out.error,
    }
