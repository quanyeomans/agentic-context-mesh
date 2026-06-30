"""Chunk-expansion use case — pull a hit's neighbouring chunks (PLA-268).

After a search / recall hit an agent holds a matched chunk plus its typed
``source_uri`` + ``seq`` (PLA-270), but to read the *surrounding* context it
previously had to re-ingest the whole document. This use case is that
missing read path: given ``source_uri`` + ``seq`` it walks outward from the
matched chunk — the preceding and following chunks — accumulating their text
up to a token budget, and returns the ordered window.

The retrieval backbone is :meth:`DocumentRepository.get_by_path` keyed by the
chunk path ``<source_uri>#<seq>`` — the same key the chunk writer enumerates
(see ``kairix.worker._SqliteChunkWriter.upsert``). There is no second
chunk-fetch implementation: ``run_expand`` calls ``get_by_path`` per
neighbour, which is exactly the by-key lookup PLA-268 wires for the first time.

Shared by CLI (``kairix expand``) and MCP (``tool_expand``) so both surfaces
stay aligned (the CLI/MCP feature-parity contract, #168). Never raises —
failures populate :attr:`ExpandOutput.error` (the use-case never-raise
contract the parity invariants enforce).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.core.protocols import SourceRef
from kairix.text import estimate_tokens

logger = logging.getLogger(__name__)

# Token-budget bounds — clamp the caller's request so a pathological budget
# can't drive an unbounded outward walk over a huge document.
_DEFAULT_TOKEN_BUDGET = 2000
_MAX_TOKEN_BUDGET = 32000
_TOKEN_BUDGET_FLOOR = 1

# Envelope key constants (F17) — each key is read at the writer
# (``expand_output_to_envelope``) AND the reader (``ExpandOutput.from_envelope``
# / ``ExpandedChunk.from_envelope``); the duplicated literal trips Sonar
# S1192 unless extracted to one source of truth.
_KEY_CHUNKS = "chunks"
_KEY_SOURCE_URI = "source_uri"
_KEY_TOTAL_TOKENS = "total_tokens"
_KEY_MATCHED_SEQ = "matched_seq"
_KEY_IS_MATCH = "is_match"
_KEY_COLLECTION = "collection"


def _chunk_path(source_uri: str, seq: int) -> str:
    """Build the chunk's by-key path ``<source_uri>#<seq>``.

    The single source of the chunk-key format — the same key the chunk
    writer enumerates (``kairix.worker._SqliteChunkWriter.upsert``). Keeping
    it in one place means the expand read path can't drift from the write
    path's key format.
    """
    return f"{source_uri}#{seq}"


def _default_get_chunk(path: str) -> dict[str, Any] | None:
    """Production chunk reader — wires ``SQLiteDocumentRepository.get_by_path``.

    The by-key retrieval backbone (PLA-268): looks a chunk up by its
    ``<source_uri>#<seq>`` path in the default worker index and returns the
    document row dict (``path`` / ``collection`` / ``title`` / ``hash`` /
    ``content``), or ``None`` when no active chunk is stored at that key.
    """
    from kairix.core.db.repository import SQLiteDocumentRepository
    from kairix.paths import db_path

    return SQLiteDocumentRepository(db_path()).get_by_path(path)


@dataclass(frozen=True)
class ExpandDeps:
    """Injectable dependencies for :func:`run_expand`.

    ``get_chunk`` is the by-key chunk-retrieval seam — a callable taking a
    chunk path (``<source_uri>#<seq>``) and returning the document row dict
    or ``None``. Production callers leave ``deps`` None and the default
    factory wires :meth:`SQLiteDocumentRepository.get_by_path`; tests inject
    ``ExpandDeps(get_chunk=FakeDocumentRepository(...).get_by_path)`` so the
    neighbour-walking + budget logic is the property under test, not SQLite.

    F6-clean: a non-Optional field with a ``default_factory`` (the canonical
    Deps shape, mirroring :class:`kairix.use_cases.research.ResearchDeps`) —
    never a ``*_fn=None`` test-only kwarg.
    """

    get_chunk: Callable[[str], dict[str, Any] | None] = field(default_factory=lambda: _default_get_chunk)


@dataclass(frozen=True)
class ExpandedChunk:
    """One chunk in the expansion window — an agent-facing result row.

    Carries the shared :class:`SourceRef` breadcrumb (PLA-274 / F97) via the
    :meth:`source_ref` accessor, so an agent can cite or re-open the exact
    chunk it read uniformly with every other surface.

    Attributes:
        path: The chunk's ``documents.path`` (``<source_uri>#<seq>``).
        seq: The 0-based chunk sequence index within the document.
        text: The chunk's full text (``content.doc``).
        tokens: Estimated token count of ``text``.
        title: Document title, when known.
        collection: The collection the chunk lives in, when known.
        source_uri: The canonical resolvable breadcrumb the window is keyed on.
        is_match: True for the chunk the hit pointed at; False for neighbours.
    """

    path: str
    seq: int
    text: str
    tokens: int
    title: str = ""
    collection: str = ""
    source_uri: str = ""
    is_match: bool = False

    def source_ref(self) -> SourceRef:
        """Return the shared :class:`SourceRef` breadcrumb for this chunk (F97).

        Built through :meth:`SourceRef.of` so the source_uri→path fallback and
        the non-paged locator derivation apply uniformly across every surface.
        """
        return SourceRef.of(
            path=self.path,
            source_uri=self.source_uri,
            title=self.title or None,
            collection=self.collection or None,
        )

    @classmethod
    def from_envelope(cls, data: dict[str, Any]) -> ExpandedChunk:
        """Rebuild an ``ExpandedChunk`` from one per-chunk envelope dict."""
        return cls(
            path=str(data.get("path", "")),
            seq=int(data.get("seq", 0) or 0),
            text=str(data.get("text", "")),
            tokens=int(data.get("tokens", 0) or 0),
            title=str(data.get("title", "")),
            collection=str(data.get(_KEY_COLLECTION, "")),
            source_uri=str(data.get(_KEY_SOURCE_URI, "") or ""),
            is_match=bool(data.get(_KEY_IS_MATCH, False)),
        )

    def to_envelope(self) -> dict[str, Any]:
        """Project to the per-chunk dict callers receive.

        Carries the flat structural fields AND the nested ``source_ref``
        breadcrumb so the round-trip is lossless (PLA-274).
        """
        return {
            "path": self.path,
            "seq": self.seq,
            "text": self.text,
            "tokens": self.tokens,
            "title": self.title,
            _KEY_COLLECTION: self.collection,
            _KEY_SOURCE_URI: self.source_uri,
            _KEY_IS_MATCH: self.is_match,
            "source_ref": self.source_ref().to_envelope(),
        }


@dataclass(frozen=True)
class ExpandOutput:
    """Outcome of one :func:`run_expand` invocation.

    Attributes:
        source_uri: The caller's source_uri, unchanged.
        matched_seq: The seq the caller asked to expand around.
        chunks: The expansion window — the matched chunk plus neighbours,
            ordered by ascending ``seq``, fitting within the token budget.
            Empty on error.
        total_tokens: Sum of token estimates across ``chunks``.
        error: Empty on success; an actionable message on a miss or
            structured ``"<Class>: <msg>"`` on a top-level failure.
    """

    source_uri: str
    matched_seq: int | None = None
    chunks: list[ExpandedChunk] = field(default_factory=list)
    total_tokens: int = 0
    error: str = ""

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> ExpandOutput:
        """Rebuild an ``ExpandOutput`` from the dict ``expand_output_to_envelope`` emits."""
        raw_chunks = envelope.get(_KEY_CHUNKS, []) or []
        raw_seq = envelope.get(_KEY_MATCHED_SEQ)
        return cls(
            source_uri=str(envelope.get(_KEY_SOURCE_URI, "")),
            matched_seq=int(raw_seq) if isinstance(raw_seq, int) else None,
            chunks=[ExpandedChunk.from_envelope(c) for c in raw_chunks],
            total_tokens=int(envelope.get(_KEY_TOTAL_TOKENS, 0) or 0),
            error=str(envelope.get("error", "")),
        )


def _row_to_chunk(row: dict[str, Any], *, source_uri: str, seq: int, is_match: bool) -> ExpandedChunk:
    """Map one ``get_by_path`` row into an :class:`ExpandedChunk`."""
    text = str(row.get("content", "") or "")
    return ExpandedChunk(
        path=str(row.get("path") or _chunk_path(source_uri, seq)),
        seq=seq,
        text=text,
        tokens=estimate_tokens(text),
        title=str(row.get("title") or ""),
        collection=str(row.get(_KEY_COLLECTION) or ""),
        source_uri=source_uri,
        is_match=is_match,
    )


def _fetch_neighbour(
    get_chunk: Callable[[str], dict[str, Any] | None],
    *,
    source_uri: str,
    seq: int,
) -> ExpandedChunk | None:
    """Fetch one neighbour chunk by key, or ``None`` when none is stored."""
    row = get_chunk(_chunk_path(source_uri, seq))
    if row is None:
        return None
    return _row_to_chunk(row, source_uri=source_uri, seq=seq, is_match=False)


def _collect_window(
    get_chunk: Callable[[str], dict[str, Any] | None],
    *,
    source_uri: str,
    matched: ExpandedChunk,
    budget: int,
) -> tuple[list[ExpandedChunk], int]:
    """Walk outward from ``matched`` collecting neighbours within ``budget``.

    Expands symmetrically (``seq-1``, ``seq+1``, ``seq-2``, ``seq+2`` …) so
    the window stays centred on the hit. Each side stops independently the
    first time a neighbour is absent (document edge) or would push the
    running token total over ``budget``. Returns the chunks ordered by
    ascending ``seq`` plus the total token estimate.
    """
    collected: dict[int, ExpandedChunk] = {matched.seq: matched}
    total = matched.tokens
    # Per-side activity flags. The ``nseq < 0`` guard below is the single
    # source of the document's lower edge — when the match is chunk 0 the
    # first backward step (seq-1) is negative and deactivates the low side.
    active = {-1: True, 1: True}
    offset = 1
    while any(active.values()):
        for sign in (-1, 1):
            if not active[sign]:
                continue
            nseq = matched.seq + sign * offset
            if nseq < 0:
                active[sign] = False
                continue
            chunk = _fetch_neighbour(get_chunk, source_uri=source_uri, seq=nseq)
            if chunk is None or total + chunk.tokens > budget:
                active[sign] = False
                continue
            collected[nseq] = chunk
            total += chunk.tokens
        offset += 1
    ordered = [collected[key] for key in sorted(collected)]
    return ordered, total


def run_expand(
    source_uri: str,
    seq: int,
    *,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    deps: ExpandDeps | None = None,
) -> ExpandOutput:
    """Return the matched chunk plus its neighbours within a token budget.

    Never raises — failures populate :attr:`ExpandOutput.error`.

    Args:
        source_uri: The hit's canonical breadcrumb (``SearchHit.source_uri``).
        seq: The hit's 0-based chunk sequence index (``SearchHit.seq``).
        token_budget: Soft cap on the total tokens of the returned window
            (clamped to ``[1, 32000]``). The matched chunk is always
            included even if it alone exceeds the budget.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or ExpandDeps()
    budget = min(max(_TOKEN_BUDGET_FLOOR, token_budget), _MAX_TOKEN_BUDGET)

    if not source_uri:
        return ExpandOutput(
            source_uri=source_uri,
            matched_seq=seq,
            error="expand: source_uri is required. fix: pass the hit's source_uri.",
        )
    if seq < 0:
        return ExpandOutput(
            source_uri=source_uri,
            matched_seq=seq,
            error=f"expand: seq must be >= 0; got {seq}. fix: pass the hit's 0-based seq.",
        )

    try:
        matched_row = d.get_chunk(_chunk_path(source_uri, seq))
        if matched_row is None:
            return ExpandOutput(
                source_uri=source_uri,
                matched_seq=seq,
                error=(
                    f"expand: no chunk stored at {_chunk_path(source_uri, seq)}. "
                    "fix: re-run search for a current source_uri + seq."
                ),
            )
        matched = _row_to_chunk(matched_row, source_uri=source_uri, seq=seq, is_match=True)
        chunks, total = _collect_window(d.get_chunk, source_uri=source_uri, matched=matched, budget=budget)
        return ExpandOutput(source_uri=source_uri, matched_seq=seq, chunks=chunks, total_tokens=total)
    except Exception as exc:
        logger.warning("run_expand failed: %s", exc, exc_info=True)
        return ExpandOutput(source_uri=source_uri, matched_seq=seq, error=f"{type(exc).__name__}: {exc}")


def expand_output_to_envelope(out: ExpandOutput) -> dict[str, Any]:
    """Project an ``ExpandOutput`` to the JSON envelope CLI + MCP callers receive."""
    return {
        _KEY_SOURCE_URI: out.source_uri,
        _KEY_MATCHED_SEQ: out.matched_seq,
        _KEY_CHUNKS: [c.to_envelope() for c in out.chunks],
        _KEY_TOTAL_TOKENS: out.total_tokens,
        "error": out.error,
    }


# ---------------------------------------------------------------------------
# CLI adapter — ``kairix expand`` (dispatched from kairix.cli.COMMANDS)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix expand",
        description=(
            "Expand a search hit to its neighbouring chunks. Pass the hit's "
            "source_uri + seq; expand returns the matched chunk plus the "
            "preceding and following chunks within a token budget — so you "
            "read surrounding context without re-ingesting the whole document."
        ),
    )
    parser.add_argument("source_uri", help="The hit's canonical source_uri (SearchHit.source_uri)")
    parser.add_argument("seq", type=int, help="The hit's 0-based chunk sequence index (SearchHit.seq)")
    parser.add_argument(
        "--token-budget",
        dest="token_budget",
        type=int,
        default=_DEFAULT_TOKEN_BUDGET,
        help=f"Soft cap on total tokens in the returned window (default: {_DEFAULT_TOKEN_BUDGET})",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Read chunks from this SQLite index instead of the default worker index (operator / test seam).",
    )
    return parser


def _deps_from_args(args: argparse.Namespace) -> ExpandDeps | None:
    """Build override deps from the F30 ``--db-path`` subprocess seam, or None.

    Wires the canonical ``SQLiteDocumentRepository.get_by_path`` backbone at
    the operator-supplied index path; ``None`` lets ``run_expand`` resolve
    the default worker index.
    """
    if args.db_path:
        from kairix.core.db.repository import SQLiteDocumentRepository

        repo = SQLiteDocumentRepository(Path(args.db_path))
        return ExpandDeps(get_chunk=repo.get_by_path)
    return None


def _format_text(out: ExpandOutput) -> str:
    """Render an ``ExpandOutput`` as the human-readable text the CLI prints."""
    lines = [f"source_uri: {out.source_uri}", f"matched_seq: {out.matched_seq}"]
    if out.error:
        lines.append(f"Error: {out.error}")
        return "\n".join(lines) + "\n"
    lines.append(f"chunks: {len(out.chunks)} returned | {out.total_tokens} tokens")
    lines.append("")
    for chunk in out.chunks:
        marker = " (match)" if chunk.is_match else ""
        lines.append(f"[seq {chunk.seq}]{marker} {chunk.path} · {chunk.tokens} tokens")
        if chunk.text:
            lines.append(f"   {chunk.text}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(
    argv: list[str] | None = None,
    *,
    deps: ExpandDeps | None = None,
    out: Any = None,
    err: Any = None,
) -> int:
    """CLI entry point for ``kairix expand``. Returns 0 on success, 1 on error.

    ``deps`` is the in-process test seam; ``--db-path`` is the F30 subprocess
    seam. Both default to the production worker index.
    """
    args = build_parser().parse_args(argv)
    out_sink = out if out is not None else sys.stdout
    err_sink = err if err is not None else sys.stderr

    effective_deps = deps if deps is not None else _deps_from_args(args)
    result = run_expand(args.source_uri, args.seq, token_budget=args.token_budget, deps=effective_deps)

    if args.as_json:
        out_sink.write(json.dumps(expand_output_to_envelope(result), indent=2) + "\n")
    elif not result.error:
        out_sink.write(_format_text(result))

    if result.error:
        err_sink.write(f"kairix expand: {result.error}\n")
        return 1
    return 0


__all__ = [
    "ExpandDeps",
    "ExpandOutput",
    "ExpandedChunk",
    "expand_output_to_envelope",
    "main",
    "run_expand",
]


if __name__ == "__main__":
    sys.exit(main())
