"""
Briefing file writer.

Writes the generated briefing to ``<briefing_dir>/<agent>-latest.md``,
where ``briefing_dir`` resolves lazily through
:func:`kairix.paths.briefing_dir` (``<cache_dir>/briefing`` —
``/var/cache/kairix/briefing`` on FHS containers + service installs,
``~/.cache/kairix/briefing`` for user installs, or the
``KAIRIX_BRIEFING_DIR`` override). The path is resolved at call time, not
at import, so the module imports cleanly on a hardened no-HOME deploy.
Creates the directory if needed. Overwrites on each run (ephemeral
working memory).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def build_briefing_header(agent: str, *, sources_count: int = 0, token_estimate: int = 0) -> str:
    """Render the two-line briefing header (title + generation metadata).

    The header is prepended to the synthesised body both here (the file
    on disk) and in :func:`kairix.agents.briefing.pipeline.generate_briefing`
    (the value returned to the caller). Single-sourced so the two stay in
    lockstep — the pipeline no longer reads the file back to recover it.
    """
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")
    return (
        f"# Agent Briefing — {agent} — {date_str}\n"
        f"_Generated: {ts} | Sources: {sources_count} | Tokens: ~{token_estimate}_\n\n"
    )


def write_briefing(
    agent: str,
    content: str,
    sources_count: int = 0,
    token_estimate: int = 0,
    output_dir: Path | None = None,
) -> Path:
    """
    Write a briefing to ``<briefing_dir>/<agent>-latest.md``.

    Creates the directory if it doesn't exist.
    Overwrites any existing file.

    Args:
        agent:          Agent name.
        content:        Briefing body (markdown, without header).
        sources_count:  Number of sources that contributed.
        token_estimate: Estimated token count of the output.
        output_dir:     Optional override for the briefing output directory.
                        Defaults to :func:`kairix.paths.briefing_dir`.

    Returns:
        Path to the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    if output_dir is not None:
        target_dir = output_dir
    else:
        from kairix.paths import briefing_dir

        target_dir = briefing_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    out_path = target_dir / f"{agent}-latest.md"

    full_content = build_briefing_header(agent, sources_count=sources_count, token_estimate=token_estimate) + content

    try:
        out_path.write_text(
            full_content, encoding="utf-8"
        )  # lgtm — intentional output: briefing files are user-owned documents, not credentials
        logger.info("writer: briefing written to %s (%d bytes)", out_path, len(full_content))
    except OSError:
        logger.exception("writer: failed to write briefing to %s", out_path)
        raise

    return out_path
