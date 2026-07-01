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
4. indexes the new file immediately and incrementally — only the file
   just written is upserted (reusing the scanner's per-file processing)
   and the FTS index is updated for that one document, so BM25 search
   finds it now rather than at the next worker tick, WITHOUT re-reading
   or re-hashing the rest of the document tree (PLA-258). Vector
   embedding stays out-of-band — the next ``kairix embed`` / worker tick
   picks the document up as pending.

The use case never raises: every failure mode is reported through the
``error`` field on :class:`RememberResult` so the MCP surface can hand
agents a structured envelope and the CLI can map it to exit code 1.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import logging
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from kairix.paths import WriteAccessProbe
from kairix.use_cases.agent_memory_sink import (
    agent_memory_fallback_root,
    index_agent_file,
    resolve_writable_memory_dir,
)

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


def _real_memory_fallback_root() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    return agent_memory_fallback_root()


def _real_probe_write_access(path: str | Path) -> WriteAccessProbe:  # pragma: no cover  # lazy-import DI seam
    from kairix.paths import probe_write_access

    return probe_write_access(path)


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
    # ``index_fn`` is called with a trailing ``extra_scan_root=`` keyword ONLY
    # on the read-only-overlay fallback path (PLA-296); the common path calls it
    # positionally with four args, so existing four-arg test doubles still fit.
    index_fn: Callable[..., bool] = field(default_factory=lambda: index_agent_file)
    # PLA-296 — the writable data-dir base + probe seam that let a memory write
    # fall back off a read-only 04-Agent-Knowledge overlay instead of crashing.
    memory_fallback_root_fn: Callable[[], Path] = field(default_factory=lambda: _real_memory_fallback_root)
    probe_fn: Callable[[str | Path], WriteAccessProbe] = field(default_factory=lambda: _real_probe_write_access)


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


def _in_root_error(agent: str, write_dir: Path, document_root: Path) -> str:
    """Return an F21 error when ``write_dir`` escapes the scanned root, else ``""``.

    The document scanner only indexes files beneath ``document_root``, so a
    memory written to a surface that resolves OUTSIDE it would be saved but
    never indexed — permanently unsearchable, with no later ``kairix embed``
    able to rescue it (the scanner never walks there). Reuse the canonical
    :func:`kairix.paths.confine_to` confinement guard to detect the escape
    (absolute config surface, ``..`` traversal, or symlink-out) and reject
    BEFORE writing rather than leave a silent orphan behind (PLA-259).
    """
    from kairix.paths import PathTraversalError, confine_to

    try:
        confine_to(document_root, write_dir)
    except PathTraversalError:
        return (
            f"MemoryUnreachable: the memory surface for agent {agent!r} ({write_dir}) "
            f"resolves outside the scanned knowledge store ({document_root}); a memory "
            f"saved there is never indexed, so search can't find it. "
            f"fix: point the agent's memory surface at a path inside the knowledge store "
            f"(for example 04-Agent-Knowledge/{agent} under {document_root}). "
            f"next: edit the agent's surfaces in kairix.config.yaml, then re-run "
            f"kairix doctor agent --name {agent}."
        )
    return ""


def _write_failed_error(agent: str, write_dir: Path, exc: OSError) -> str:
    """Build the F21 ``WriteFailed`` envelope naming the path + permission + fix.

    Reuses :func:`kairix.paths.write_access_fix_hint` so the live write
    failure speaks the same remediation language as the ``doctor`` preflight:
    a read-only mount (``EROFS``) and a wrong-ownership directory (``EACCES``)
    each get their concrete fix instead of an opaque ``OSError`` (PLA-259).
    """
    from kairix.paths import write_access_fix_hint

    errno_name = errno.errorcode.get(exc.errno or 0, "")
    reason = exc.strerror or type(exc).__name__
    detail = f"{reason} [{errno_name}]" if errno_name else reason
    return (
        f"WriteFailed: cannot write under {write_dir} — {detail}. "
        f"{write_access_fix_hint(errno_name)}. "
        f"next: kairix doctor agent --name {agent}."
    )


def _index_written(
    d: RememberDeps,
    document_root: Path,
    target: Path,
    content_hash: str,
    scan_root: Path | None,
) -> bool:
    """Run the immediate index; register the fallback scan root when set (PLA-296).

    ``scan_root`` is non-None ONLY on the read-only-overlay fallback path, so
    the common path calls ``index_fn`` with the four positional args a stock
    four-arg test double expects; the fallback path adds the ``extra_scan_root``
    keyword so the data-dir write is indexed even though it sits outside the
    document root. Indexing failures are swallowed to ``False`` — a
    saved-but-unindexed memory is queued for the next embed rather than lost.
    """
    db_path = d.db_path_fn()
    try:
        if scan_root is not None:
            return bool(d.index_fn(db_path, document_root, target, content_hash, extra_scan_root=scan_root))
        return bool(d.index_fn(db_path, document_root, target, content_hash))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("remember: immediate indexing failed — %s", exc)
        return False


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
    preferred_dir = _resolve_write_dir(agent, config, document_root)

    in_root_error = _in_root_error(agent, preferred_dir, document_root)
    if in_root_error:
        return _failure(agent, kind, in_root_error)

    # PLA-296 — prefer the ADR-017 04-Agent-Knowledge overlay, but when it is
    # read-only for our uid fall back to the writable data dir so the memory is
    # never lost on a stock deploy. ``fallback_root / agent`` keeps per-agent
    # isolation on the fallback surface (F44/F80).
    fallback_root = d.memory_fallback_root_fn()
    resolved = resolve_writable_memory_dir(
        preferred_dir,
        fallback_root / agent,
        label=f"agent {agent!r}",
        fallback_scan_root=fallback_root,
        probe_fn=d.probe_fn,
    )
    write_dir = resolved.write_dir

    now = d.now_fn()
    target = _build_target_path(write_dir, now.date().isoformat(), content, kind)
    body = _render_markdown(agent, kind, classified_as, now, content)

    try:
        write_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("remember: failed to write %s — %s", target, exc)
        return _failure(agent, kind, _write_failed_error(agent, write_dir, exc))

    from kairix.knowledge.reflib.dedup import hash_content

    indexed = _index_written(d, document_root, target, hash_content(body), resolved.scan_root)
    detail = ""
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
