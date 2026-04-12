"""
Briefing file writer.

Writes the generated briefing to /data/kairix/briefing/<agent>-latest.md.
Creates directory if needed.
Overwrites on each run (ephemeral working memory).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BRIEFING_DIR = Path(os.environ.get("KAIRIX_BRIEFING_DIR", "/data/kairix/briefing"))


def write_briefing(
    agent: str,
    content: str,
    sources_count: int = 0,
    token_estimate: int = 0,
) -> Path:
    """
    Write a briefing to /data/kairix/briefing/<agent>-latest.md.

    Creates the directory if it doesn't exist.
    Overwrites any existing file.

    Args:
        agent:          Agent name.
        content:        Briefing body (markdown, without header).
        sources_count:  Number of sources that contributed.
        token_estimate: Estimated token count of the output.

    Returns:
        Path to the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    _BRIEFING_DIR.mkdir(parents=True, exist_ok=True)

    out_path = _BRIEFING_DIR / f"{agent}-latest.md"

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")

    header = (
        f"# Agent Briefing — {agent} — {date_str}\n"
        f"_Generated: {ts} | Sources: {sources_count} | Tokens: ~{token_estimate}_\n\n"
    )

    full_content = header + content

    try:
        out_path.write_text(full_content, encoding="utf-8")
        logger.info("writer: briefing written to %s (%d bytes)", out_path, len(full_content))
    except OSError as e:
        logger.error("writer: failed to write briefing to %s — %s", out_path, e)
        raise

    return out_path
