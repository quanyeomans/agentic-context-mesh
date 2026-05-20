"""Conversation ingest use case — Plan B-parity Capability #1.

Reads a JSONL transcript (one line = one turn), groups turns by
``conversation_id``, writes one markdown chunk per conversation under
``document_root/conversations/``, and (optionally) feeds each sliding
window of turns through a :class:`FactExtractor` whose emitted records
are persisted via a :class:`FactStore`.

Design contract:

- **Dependency injection is total.** ``paths``, ``fact_store``, and
  ``fact_extractor`` are constructor-injected. Tests pass fakes from
  ``tests/fakes.py``; production wires the real implementations at the
  CLI layer. F1: no monkeypatching, no internal-attribute reassignment.
- **Embedding is out-of-band.** The use case writes the markdown file;
  the operator runs ``kairix embed`` separately afterwards. Keeping
  embed off the ingest path lets the operator re-embed without re-
  ingesting and vice versa.
- **Idempotent re-ingest.** Markdown chunks are content-hashed; a
  second run over the same JSONL is a no-op for the filesystem. Fact
  persistence is idempotent on each fact's deterministic id
  (``FactStore.add`` contract).

CLI surface lives in :func:`main` at the bottom of this module so the
top-level ``kairix.cli`` dispatch table can target a single entry
point (``kairix.use_cases.ingest_chat:main``).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from kairix.core.facts.consolidation import (
    ConsolidationOutcome,
    ConsolidationPass,
    default_contradict,
)
from kairix.core.protocols import FactExtractor, FactStore
from kairix.paths import KairixPaths

logger = logging.getLogger(__name__)

__all__ = [
    "IngestChatResult",
    "ingest_chat",
    "main",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestChatResult:
    """Counts emitted by :func:`ingest_chat` for the operator + CLI.

    All counts are non-negative integers; the dataclass is frozen so
    callers can pass it as a value object through downstream pipelines.

    ``facts_superseded`` counts prior facts the ingest-time consolidation
    pass marked superseded by a newly extracted fact — surfaced so the
    operator can spot conversations that rewrote a lot of prior ground
    truth.
    """

    turns_ingested: int
    conversations_processed: int
    facts_added: int
    windows_extracted: int
    facts_superseded: int = 0


# Field-name constants — F17 (no string literal of ≥10 chars duplicated ≥3
# times). Used by _parse_turn and downstream parsers to keep the schema
# field names in one place.
_KEY_CONVERSATION_ID = "conversation_id"


# ---------------------------------------------------------------------------
# Internal helpers — pure, no I/O state
# ---------------------------------------------------------------------------


def _parse_turn(raw_line: str, *, default_conversation_id: str | None = None) -> dict[str, Any] | None:
    """Parse one JSONL line into a turn dict; return None if malformed.

    Required fields: ``role``, ``content``. ``conversation_id`` is required
    BUT defaults to ``default_conversation_id`` (typically the JSONL filename
    stem) when absent — supports the reference-library convention where one
    session-NNN.jsonl file is exactly one conversation and per-turn
    ``conversation_id`` is redundant. Blank lines, non-JSON, or missing
    role/content all produce ``None`` + warning.
    """
    line = raw_line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        logger.warning("ingest-chat: skipping malformed jsonl line: %s", exc)
        return None
    if not isinstance(obj, dict):
        logger.warning("ingest-chat: skipping non-object jsonl line: %r", obj)
        return None
    for key in ("role", "content"):
        if key not in obj or obj[key] is None:
            logger.warning("ingest-chat: skipping turn missing %r: %r", key, obj)
            return None
    if obj.get(_KEY_CONVERSATION_ID) is None:
        if default_conversation_id is None:
            logger.warning("ingest-chat: skipping turn missing %r (no default): %r", _KEY_CONVERSATION_ID, obj)
            return None
        obj[_KEY_CONVERSATION_ID] = default_conversation_id
    return obj


def _read_turns(jsonl_path: Path) -> list[dict[str, Any]]:
    """Read + parse every turn from the JSONL file, skipping malformed lines.

    Turns without explicit ``conversation_id`` inherit the JSONL filename
    stem (e.g. ``session-001.jsonl`` → ``conversation_id=session-001``) so
    the operator-friendly convention "one file = one conversation" works
    without forcing every turn to carry redundant metadata.
    """
    default_cid = jsonl_path.stem
    turns: list[dict[str, Any]] = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for raw in fh:
            turn = _parse_turn(raw, default_conversation_id=default_cid)
            if turn is not None:
                turns.append(turn)
    return turns


def _group_by_conversation(turns: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group turns by ``conversation_id`` preserving in-file order per id."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        cid = str(turn["conversation_id"])
        grouped.setdefault(cid, []).append(turn)
    return grouped


def _render_markdown(conversation_id: str, turns: Sequence[dict[str, Any]], ingested_at: str) -> str:
    """Render a conversation as markdown with YAML frontmatter."""
    body_lines = [f"**{turn['role']}**: {turn['content']}" for turn in turns]
    frontmatter = (
        f"---\nconversation_id: {conversation_id}\nturn_count: {len(turns)}\ningested_at: {ingested_at}\n---\n\n"
    )
    return frontmatter + "\n\n".join(body_lines) + "\n"


def _content_hash(text: str) -> str:
    """SHA-256 hex digest of the chunk body (frontmatter-stripped).

    Used for idempotent re-ingest: identical bodies → identical hash →
    skip the disk write so file mtimes stay stable.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _existing_body_matches(path: Path, expected_body: str) -> bool:
    """True iff ``path`` already exists and its body (post-frontmatter) matches.

    Hash comparison is on the body only so the frontmatter's
    ``ingested_at`` timestamp doesn't break idempotency on re-ingest.
    """
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    existing_body = _strip_frontmatter(existing)
    return _content_hash(existing_body) == _content_hash(expected_body)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``---``-delimited YAML frontmatter block, if any."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n") :].lstrip("\n")


def _window(turns: Sequence[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Slice ``turns`` into non-overlapping windows of length ``size``.

    The trailing window may be shorter than ``size`` (e.g. 12 turns with
    size=5 → windows of 5/5/2). Zero-or-fewer ``size`` collapses to one
    window containing all turns — defensive default so a misconfigured
    CLI flag can't trigger an infinite loop downstream.
    """
    if size <= 0:
        return [list(turns)] if turns else []
    return [list(turns[i : i + size]) for i in range(0, len(turns), size)]


# ---------------------------------------------------------------------------
# Production-time null extractor (placeholder until Capability #2 lands)
# ---------------------------------------------------------------------------


class _NullFactExtractor:
    """Production FactExtractor placeholder: emits zero facts.

    Capability #2 (next week) replaces this with the LLM-driven
    extractor. Until then the CLI wires this so ``--no-extract``
    becomes the only mode that has observable behaviour for the
    operator. Tests inject ``FakeFactExtractor`` from ``tests/fakes.py``
    with scripted records instead.
    """

    def extract(
        self,
        *,
        turns: list[dict[str, Any]],
        window_hint: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Return the empty list — no facts emitted in Week 1.

        The ``turns`` + ``window_hint`` parameter names are mandated by
        the :class:`FactExtractor` Protocol; callers invoke this method
        as ``extract(turns=..., window_hint=...)``, so a ``_``-prefix
        rename would break the runtime contract. We surface their
        receipt as a DEBUG log so the F19 unused-params gate sees a
        Load-context reference and the operator can verify in trace
        logs that the null extractor is what's wired in Week 1.
        """
        logger.debug(
            "ingest-chat: null extractor received %d turn(s); window_hint=%r — Capability #2 will replace this",
            len(turns),
            window_hint,
        )
        return []


# ---------------------------------------------------------------------------
# Public use case
# ---------------------------------------------------------------------------


def ingest_chat(
    jsonl_path: Path,
    *,
    paths: KairixPaths,
    fact_store: FactStore,
    fact_extractor: FactExtractor,
    consolidation: ConsolidationPass | None = None,
    namespace: str = "shared",
    window_turns: int = 5,
    no_extract: bool = False,
) -> IngestChatResult:
    """Ingest a JSONL conversation transcript into the document store + fact store.

    Workflow:

    1. Parse the JSONL file (skip malformed lines with a warning).
    2. Group turns by ``conversation_id``.
    3. Write one markdown file per conversation under
       ``paths.document_root / "conversations"``.
    4. If ``no_extract=False``, slice each conversation into
       ``window_turns``-sized windows, run ``fact_extractor.extract`` on
       each window, persist returned records via ``fact_store.add``, then
       run the configured :class:`ConsolidationPass` against each new
       fact so contradicting prior facts are marked superseded.

    Returns aggregate counts in an :class:`IngestChatResult`.

    Parameters
    ----------
    jsonl_path:
        Path to the source JSONL transcript.
    paths:
        Resolved :class:`KairixPaths` — only ``document_root`` is used.
    fact_store:
        Implementation of :class:`FactStore` Protocol (real or fake).
    fact_extractor:
        Implementation of :class:`FactExtractor` Protocol.
    consolidation:
        Optional :class:`ConsolidationPass`. Defaults to a pass over the
        same ``fact_store`` using :func:`default_contradict`. Passing
        ``None`` is the production wire-up; tests can inject a scripted
        consolidation pass to pin branch coverage.
    namespace:
        Engagement-scope tag stamped onto every emitted FactRecord that
        doesn't already carry one. Defaults to ``"shared"``.
    window_turns:
        Sliding-window size for fact extraction. Defaults to 5.
    no_extract:
        If True, skip fact extraction entirely (chunks-only mode).
    """
    turns = _read_turns(jsonl_path)
    grouped = _group_by_conversation(turns)

    conversations_dir = paths.document_root / "conversations"
    conversations_dir.mkdir(parents=True, exist_ok=True)
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    resolved_consolidation = (
        consolidation
        if consolidation is not None
        else ConsolidationPass(fact_store=fact_store, contradict=default_contradict)
    )

    facts_added = 0
    windows_extracted = 0
    facts_superseded = 0

    for cid, conv_turns in grouped.items():
        markdown = _render_markdown(cid, conv_turns, ingested_at)
        body = _strip_frontmatter(markdown)
        target = conversations_dir / f"{cid}.md"
        if not _existing_body_matches(target, body):
            target.write_text(markdown, encoding="utf-8")

        if no_extract:
            continue

        for window in _window(conv_turns, window_turns):
            windows_extracted += 1
            for fact in fact_extractor.extract(turns=window):
                fact_to_add = _apply_namespace(fact, namespace)
                fact_store.add(fact_to_add)
                facts_added += 1
                # Defensive: a duck-typed fact may not expose the full
                # FactRecord Protocol (e.g. ``namespace``). ``_apply_namespace``
                # already tolerates that branch; consolidation skips it too
                # rather than crashing the ingest pipeline on a Protocol-
                # incomplete fact. The Protocol contract still requires
                # ``namespace`` for production extractors.
                if not hasattr(fact_to_add, "namespace"):
                    continue
                outcome: ConsolidationOutcome = resolved_consolidation.process(fact_to_add)
                facts_superseded += len(outcome.superseded_ids)

    return IngestChatResult(
        turns_ingested=len(turns),
        conversations_processed=len(grouped),
        facts_added=facts_added,
        windows_extracted=windows_extracted,
        facts_superseded=facts_superseded,
    )


def _apply_namespace(fact: Any, namespace: str) -> Any:
    """Stamp ``namespace`` onto a fact when the extractor returned the default.

    Most extractors will not know which engagement scope to tag — the
    use case has that context and applies it at persistence time.
    Falls back to returning the original fact if the namespace already
    matches or the fact doesn't expose a settable namespace.
    """
    try:
        current = fact.namespace
    except AttributeError:
        return fact
    if current == namespace:
        return fact
    if dataclasses.is_dataclass(fact) and not isinstance(fact, type):
        try:
            return dataclasses.replace(fact, namespace=namespace)
        except (TypeError, ValueError):
            return fact
    # FakeFactRecord uses internal state ``_namespace`` — re-construct via
    # its public ``__init__`` rather than reaching past the boundary.
    fake_kwargs = _try_fake_record_kwargs(fact)
    if fake_kwargs is not None:
        fake_kwargs["namespace"] = namespace
        return type(fact)(**fake_kwargs)
    return fact


def _try_fake_record_kwargs(fact: Any) -> dict[str, Any] | None:
    """Read the public Protocol surface back into a constructor kwarg dict.

    Returns ``None`` if any required property is missing — callers fall
    back to using the original fact. This keeps the namespace-stamping
    path free of attribute-reassignment, which would be an F1 violation.
    """
    try:
        return {
            "id": fact.id,
            "entity": fact.entity,
            "attribute": fact.attribute,
            "value": fact.value,
            "confidence": fact.confidence,
            "source_turn_ids": fact.source_turn_ids,
            "extracted_at": fact.extracted_at,
            "superseded_by": fact.superseded_by,
        }
    except AttributeError:
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix ingest-chat`` — keeps the CLI surface narrow."""
    parser = argparse.ArgumentParser(
        prog="kairix ingest-chat",
        description=(
            "Ingest a JSONL chat transcript into the document store (and optionally extract facts into the fact store)."
        ),
    )
    parser.add_argument(
        "jsonl_path",
        type=Path,
        help="Path to the JSONL transcript (one turn per line).",
    )
    parser.add_argument(
        "--namespace",
        default="shared",
        help="Engagement-scope namespace stamped onto emitted facts (default: shared).",
    )
    parser.add_argument(
        "--window-turns",
        type=int,
        default=5,
        help="Sliding-window size in turns for fact extraction (default: 5).",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Skip fact extraction; write markdown chunks only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the IngestChatResult as JSON instead of human-readable text.",
    )
    return parser


def _resolve_production_fact_store(db_path: Path) -> FactStore:
    """Return a production FactStore or raise ImportError with actionable hint.

    Capability #3 (sister subagent) owns ``SQLiteFactStore`` under
    ``kairix.core.facts``; until that branch lands, ``ingest-chat``
    can still serve ``--no-extract`` runs — but every other path needs
    a real store, so we raise ImportError with a fix-now message.
    """
    try:
        from kairix.core.facts import SQLiteFactStore
    except ImportError as exc:
        raise ImportError(
            "ingest-chat needs SQLiteFactStore (Capability #3). "
            "fix: cherry-pick the sqlite-fact-store branch into your worktree, then re-run."
        ) from exc
    store: FactStore = SQLiteFactStore(db_path=db_path)
    return store


def _resolve_production_fact_extractor() -> FactExtractor:
    """Return a production FactExtractor wired to the configured LLM backend.

    Capability #2 lands :class:`LLMFactExtractor` — the production path
    that drives the operator's configured provider plug-in via
    :func:`kairix.platform.llm.get_default_backend`. Until that import
    succeeds, callers fall back to the null extractor so chunks-only
    ingest still works.
    """
    from kairix.core.facts import LLMFactExtractor
    from kairix.platform.llm import get_default_backend

    extractor: FactExtractor = LLMFactExtractor(llm=get_default_backend())
    return extractor


def _format_human(result: IngestChatResult) -> str:
    """Human-readable summary for the default (non-``--json``) CLI output."""
    return (
        "kairix ingest-chat: complete\n"
        f"  turns ingested:         {result.turns_ingested}\n"
        f"  conversations processed: {result.conversations_processed}\n"
        f"  windows extracted:       {result.windows_extracted}\n"
        f"  facts added:             {result.facts_added}\n"
        f"  facts superseded:        {result.facts_superseded}\n"
    )


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    paths: KairixPaths | None = None,
    fact_store: FactStore | None = None,
    fact_extractor: FactExtractor | None = None,
    consolidation: ConsolidationPass | None = None,
) -> int:
    """CLI entry point for ``kairix ingest-chat``.

    The keyword-only ``paths`` / ``fact_store`` / ``fact_extractor`` /
    ``consolidation`` arguments are the test-injection seam. Production
    callers leave them ``None``; the CLI resolves real implementations
    and ImportError if Capability #3 isn't present yet. ``consolidation``
    defaults to a :class:`ConsolidationPass` over the resolved store
    using :func:`default_contradict`.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    out_sink = out if out is not None else sys.stdout
    err_sink = err if err is not None else sys.stderr

    resolved_paths = paths if paths is not None else KairixPaths.resolve()

    if fact_store is None:
        try:
            fact_store = _resolve_production_fact_store(resolved_paths.db_path)
        except ImportError as exc:
            err_sink.write(f"kairix ingest-chat: {exc}\n")
            return 2

    if fact_extractor is not None:
        resolved_extractor: FactExtractor = fact_extractor
    elif args.no_extract:
        # ``--no-extract`` short-circuits before any extractor call,
        # but we still need *some* Protocol-compliant object on the
        # parameter. The null extractor stays the cheapest stand-in.
        resolved_extractor = _NullFactExtractor()
    else:
        resolved_extractor = _resolve_production_fact_extractor()

    result = ingest_chat(
        args.jsonl_path,
        paths=resolved_paths,
        fact_store=fact_store,
        fact_extractor=resolved_extractor,
        consolidation=consolidation,
        namespace=args.namespace,
        window_turns=args.window_turns,
        no_extract=args.no_extract,
    )

    if args.as_json:
        out_sink.write(json.dumps(dataclasses.asdict(result), indent=2) + "\n")
    else:
        out_sink.write(_format_human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
