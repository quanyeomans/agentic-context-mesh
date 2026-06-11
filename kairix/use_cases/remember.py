"""Remember use case — save a memory for an agent and make it findable now (#472).

One implementation, two surfaces:

- ``kairix remember <agent> <content...>`` (CLI — :func:`main` below)
- ``memory_write`` MCP tool (``kairix/agents/mcp/tools/memory_write.py``)

Both call :func:`remember`, which:

1. validates the agent against the config-driven allowlist
   (:func:`kairix.core.classify.router.valid_agents` — configured
   ``agents:`` names union the legacy built-in set);
2. classifies the content with the rule classifier (advisory — the
   classification is reported, never blocks the write);
3. writes a markdown file named ``YYYY-MM-DD-<slug>.md`` under the
   agent's write surface (:meth:`AgentScope.writable_path`, resolved
   beneath the document root when relative);
4. indexes the new file immediately through the same document-scan +
   FTS-rebuild step the embed pipeline and worker run, so BM25 search
   finds it now rather than at the next worker tick. Vector embedding
   stays out-of-band — the next ``kairix embed`` / worker tick picks
   the document up as pending.

The use case never raises: every failure mode is reported through the
``error`` field on :class:`RememberResult` so the MCP surface can hand
agents a structured envelope and the CLI can map it to exit code 1.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger(__name__)

__all__ = [
    "VALID_KINDS",
    "RememberDeps",
    "RememberResult",
    "main",
    "remember",
]

VALID_KINDS: tuple[str, ...] = ("note", "decision", "fact")

# Conventional per-agent memory directory under the document root — the
# fallback write surface when no agent scope resolves from config.
_AGENT_KNOWLEDGE_DIR = "04-Agent-Knowledge"


# ---------------------------------------------------------------------------
# Result + deps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RememberResult:
    """Outcome of one :func:`remember` call.

    Attributes:
        path: Absolute path of the written markdown file ("" on failure).
        agent: Agent the memory was written for.
        kind: One of :data:`VALID_KINDS`.
        classified_as: Rule-classifier type (e.g. ``semantic-decision``)
            or ``"unknown"`` when no rule matched.
        indexed: True when the file is already searchable via BM25.
        error: "" on success; an F21-actionable message on failure.
        detail: Supplementary guidance (e.g. the re-index affordance
            when ``indexed`` is False).
    """

    path: str
    agent: str
    kind: str
    classified_as: str
    indexed: bool
    error: str = ""
    detail: str = ""


def _real_config() -> dict[str, object] | None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def _real_document_root() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import document_root

    return document_root()


def _real_db_path() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db import get_db_path

    return get_db_path()


def _real_now() -> datetime:  # pragma: no cover  # lazy-import DI-default delegation
    return datetime.now(timezone.utc)


def _real_classify(
    content: str, *, agent: str, config: dict[str, object] | None
) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.classify.rules import classify_content

    return classify_content(content, agent=agent, config=config)


def _index_via_scan(db_path: Path, document_root: Path, content_hash: str) -> bool:
    """Production immediate-index step — the canonical scan + FTS rebuild.

    Runs :func:`kairix.core.embed.use_cases.default_scan_documents` (the
    exact step ``kairix embed`` and the worker run) against ``document_root``,
    then reports whether a document with ``content_hash`` is active in the
    store. True means BM25 search finds the new memory now; the vector leg
    follows at the next embed run, which sees the document as pending.
    """
    from kairix.core.db import open_db
    from kairix.core.db.schema import create_schema
    from kairix.core.embed.use_cases import UseCaseDeps, default_scan_documents

    db = open_db(db_path)
    try:
        create_schema(db)
        diagnostics: list[str] = []
        default_scan_documents(
            db,
            diagnostics,
            deps=UseCaseDeps(document_root_fn=lambda: document_root),
        )
        row = db.execute(
            "SELECT 1 FROM documents WHERE hash = ? AND active = 1 LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None
    finally:
        db.close()


@dataclass(frozen=True)
class RememberDeps:
    """Injection seam for :func:`remember`.

    Production callers leave every field unset; the defaults wire the
    canonical kairix implementations lazily. Tests construct
    ``RememberDeps(config_fn=lambda: {...}, document_root_fn=lambda: tmp, ...)``
    and drive every branch without monkey-patching (F1) or env vars (F2).
    """

    config_fn: Callable[[], dict[str, object] | None] = field(default_factory=lambda: _real_config)
    document_root_fn: Callable[[], Path] = field(default_factory=lambda: _real_document_root)
    db_path_fn: Callable[[], Path] = field(default_factory=lambda: _real_db_path)
    now_fn: Callable[[], datetime] = field(default_factory=lambda: _real_now)
    classify_fn: Callable[..., Any] = field(default_factory=lambda: _real_classify)
    index_fn: Callable[[Path, Path, str], bool] = field(default_factory=lambda: _index_via_scan)


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------


def _failure(agent: str, kind: str, error: str) -> RememberResult:
    """Build the no-write failure envelope."""
    return RememberResult(path="", agent=agent, kind=kind, classified_as="", indexed=False, error=error)


def _resolve_write_dir(agent: str, config: dict[str, object] | None, document_root: Path) -> Path:
    """Resolve the agent's write surface, beneath the document root.

    Routes through :func:`kairix.core.agents.scope.get_agent_scope` →
    :meth:`AgentScope.writable_path`. Falls back to the conventional
    ``<document-root>/04-Agent-Knowledge/<agent>`` layout when the scope
    cannot resolve (e.g. a legacy list-shaped ``agents:`` block, or a
    scope with no surfaces) — the write must not fail on config shape.
    """
    from kairix.core.agents.scope import get_agent_scope

    try:
        scope = get_agent_scope(agent, config=config, document_root=document_root)
        write_dir = Path(scope.writable_path())
    except ValueError:
        write_dir = document_root / _AGENT_KNOWLEDGE_DIR / agent
    if not write_dir.is_absolute():
        write_dir = document_root / write_dir
    return write_dir


def _build_target_path(write_dir: Path, date_str: str, content: str, kind: str) -> Path:
    """Compose ``YYYY-MM-DD-<slug>.md`` under ``write_dir``, dodging collisions."""
    from kairix.utils import slugify

    slug = slugify(" ".join(content.split()[:8]))[:48].rstrip("-") or kind
    target = write_dir / f"{date_str}-{slug}.md"
    counter = 2
    while target.exists():
        target = write_dir / f"{date_str}-{slug}-{counter}.md"
        counter += 1
    return target


def _render_markdown(agent: str, kind: str, classified_as: str, created: datetime, content: str) -> str:
    """Render the memory as markdown with a small provenance frontmatter."""
    return (
        "---\n"
        f"agent: {agent}\n"
        f"kind: {kind}\n"
        f"classified_as: {classified_as}\n"
        f"created: {created.isoformat()}\n"
        "---\n"
        "\n"
        f"{content.strip()}\n"
    )


def _classify_advisory(content: str, agent: str, config: dict[str, object] | None, d: RememberDeps) -> str:
    """Run the rule classifier; classification is advisory and never blocks."""
    try:
        result = d.classify_fn(content, agent=agent, config=config)
        return str(getattr(result, "type", "unknown") or "unknown")
    except (ValueError, KeyError) as exc:
        logger.warning("remember: classification failed — %s", exc)
        return "unknown"


def remember(
    agent: str,
    content: str,
    kind: str = "note",
    *,
    deps: RememberDeps | None = None,
) -> RememberResult:
    """Save ``content`` as a memory for ``agent`` and index it for search.

    Never raises — every failure mode lands in ``RememberResult.error``
    with F21 ``fix:`` / ``next:`` guidance so both surfaces (CLI + MCP)
    hand callers an actionable envelope.
    """
    d = deps if deps is not None else RememberDeps()

    if kind not in VALID_KINDS:
        return _failure(
            agent,
            kind,
            f"InvalidKind: {kind!r}. Must be one of: {', '.join(VALID_KINDS)}. "
            f"fix: pass one of those kinds (default is note). next: re-run with --kind note.",
        )
    if not content or not content.strip():
        return _failure(
            agent,
            kind,
            "EmptyContent: nothing to remember. "
            'fix: pass the memory text after the agent name. next: kairix remember <agent> "<text>".',
        )

    config = d.config_fn()

    from kairix.core.classify.router import invalid_agent_message, valid_agents

    allowed = valid_agents(config)
    if agent not in allowed:
        return _failure(agent, kind, f"InvalidAgent: {invalid_agent_message(agent, allowed)}")

    classified_as = _classify_advisory(content, agent, config, d)

    document_root = d.document_root_fn()
    write_dir = _resolve_write_dir(agent, config, document_root)
    now = d.now_fn()
    target = _build_target_path(write_dir, now.date().isoformat(), content, kind)
    body = _render_markdown(agent, kind, classified_as, now, content)

    try:
        write_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("remember: failed to write %s — %s", target, exc)
        return _failure(
            agent,
            kind,
            f"WriteFailed: {type(exc).__name__} writing under {write_dir}. "
            f"fix: check the directory exists and is writable. "
            f"next: kairix doctor agent --name {agent}.",
        )

    from kairix.knowledge.reflib.dedup import hash_content

    indexed = False
    detail = ""
    try:
        indexed = d.index_fn(d.db_path_fn(), document_root, hash_content(body))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("remember: immediate indexing failed — %s", exc)
    if not indexed:
        detail = "memory saved but not yet searchable. next: run kairix embed (or wait for the worker tick)."

    return RememberResult(
        path=str(target),
        agent=agent,
        kind=kind,
        classified_as=classified_as,
        indexed=indexed,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# CLI surface — kairix remember
# ---------------------------------------------------------------------------


def _build_parser() -> Any:
    """Argparse for ``kairix remember <agent> <content...> [--kind] [--json]``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="kairix remember",
        description=(
            "Save a memory for an agent. Writes a dated markdown file to the "
            "agent's memory area and indexes it so search finds it right away."
        ),
    )
    parser.add_argument("agent", help="Agent name — must be in the config agents: block or the built-in set.")
    parser.add_argument("content", nargs="+", help="The memory text to save.")
    parser.add_argument(
        "--kind",
        choices=VALID_KINDS,
        default="note",
        help="What kind of memory this is (default: note).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the structured JSON envelope instead of the human-readable line.",
    )
    parser.add_argument(
        "--document-root",
        default=None,
        help=(
            "Override the document root for this invocation. Matches the "
            "canonical pattern in ``kairix bootstrap --document-root``; "
            "enables F30 subprocess outcome tests to drive a tmp knowledge "
            "store without touching the process environment (F2-clean)."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override the index database path for this invocation (F30 subprocess seam).",
    )
    return parser


def _format_human(result: RememberResult) -> str:
    """Human-readable success line for the default (non-``--json``) output."""
    if result.indexed:
        index_line = "true (searchable now)"
    else:
        index_line = f"false ({result.detail})" if result.detail else "false"
    return (
        f"Remembered for {result.agent}: {result.path}\n"
        f"  kind:          {result.kind}\n"
        f"  classified as: {result.classified_as}\n"
        f"  indexed:       {index_line}\n"
    )


def _deps_from_args(args: Any) -> RememberDeps | None:
    """Build override deps from the F30 subprocess seams, or None."""
    overrides: dict[str, Any] = {}
    if args.document_root:
        root = Path(args.document_root)
        overrides["document_root_fn"] = lambda: root
    if args.db_path:
        dbp = Path(args.db_path)
        overrides["db_path_fn"] = lambda: dbp
    if not overrides:
        return None
    return RememberDeps(**overrides)


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    deps: RememberDeps | None = None,
) -> int:
    """CLI entry point for ``kairix remember``. Returns 0 on success, 1 on error.

    ``deps`` is the in-process test seam; the ``--document-root`` /
    ``--db-path`` flags are the F30 subprocess seams. In-process ``deps``
    wins when both are supplied.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    out_sink = out if out is not None else sys.stdout
    err_sink = err if err is not None else sys.stderr

    effective_deps = deps if deps is not None else _deps_from_args(args)

    result = remember(args.agent, " ".join(args.content), kind=args.kind, deps=effective_deps)

    if args.as_json:
        out_sink.write(json.dumps(dataclasses.asdict(result), indent=2) + "\n")
    elif not result.error:
        out_sink.write(_format_human(result))

    if result.error:
        err_sink.write(f"kairix remember: {result.error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
