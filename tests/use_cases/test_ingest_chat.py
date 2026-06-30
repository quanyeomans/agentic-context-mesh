"""Unit + contract tests for ``kairix.use_cases.ingest_chat``.

Every test is sabotage-proven (mutate prod → fail → restore → pass).
The test corpus exercises the documented surface:

- Markdown is written under ``document_root/04-Agent-Knowledge/conversations/<cid>.md``
- File content includes the role + content of each turn
- Result counts match the input shape
- Empty + malformed JSONL is tolerated (warnings, not exceptions)
- Re-ingest is idempotent on file content
- ``--no-extract`` short-circuits the extractor path entirely
- Default mode calls the extractor once per window
- ``namespace`` flows from kwarg through to persisted FactRecord
- Window arithmetic: ``12 turns + window=5 → 3 windows of 5/5/2``

F1: zero monkeypatches, zero internal-attribute reassignment — every
collaborator is a fake injected via the kwargs surface.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from kairix.paths import KairixPaths
from kairix.use_cases.ingest_chat import IngestChatResult, ingest_chat, main
from tests.fakes import FakeFactExtractor, FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> KairixPaths:
    """Construct a KairixPaths pinned to tmp_path — never reads env."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _conversations_dir(document_root: Path) -> Path:
    """The writable agent-knowledge submount conversations land under (PLA-275)."""
    return document_root / "04-Agent-Knowledge" / "conversations"


def _write_jsonl(path: Path, turns: list[dict[str, object]]) -> None:
    """Write ``turns`` as a JSONL transcript at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def _turn(cid: str, idx: int, role: str = "user", content: str | None = None) -> dict[str, object]:
    """Build a minimal valid turn dict for the JSONL corpus."""
    return {
        "conversation_id": cid,
        "role": role,
        "content": content if content is not None else f"turn {idx} of {cid}",
        "timestamp": f"2026-05-20T00:00:{idx:02d}Z",
    }


# ---------------------------------------------------------------------------
# Happy-path: markdown written, counts correct
# ---------------------------------------------------------------------------


def test_writes_markdown_under_conversations_dir(tmp_path: Path) -> None:
    """Sabotage-proof: change the subdir literal ``"conversations"`` to
    ``"chats"`` in the use case and this test fails because the expected
    path no longer exists on disk."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0), _turn("c1", 1, role="assistant")])

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    written = _conversations_dir(tmp_path / "vault") / "c1.md"
    assert written.exists()


def test_markdown_body_includes_role_and_content(tmp_path: Path) -> None:
    """Sabotage-proof: change ``**{role}**`` to ``{role}:`` in the
    rendered body and this assertion fails on the substring match."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            _turn("c1", 0, role="user", content="hello there"),
            _turn("c1", 1, role="assistant", content="oh hi"),
        ],
    )

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    body = (_conversations_dir(tmp_path / "vault") / "c1.md").read_text(encoding="utf-8")
    assert "**user**: hello there" in body
    assert "**assistant**: oh hi" in body


def test_result_counts_match_input_shape(tmp_path: Path) -> None:
    """Sabotage-proof: mis-count ``turns_ingested`` (return
    ``len(turns) - 1``) and the equality fails."""
    transcript = tmp_path / "t.jsonl"
    turns = [_turn("c1", i) for i in range(3)] + [_turn("c2", i) for i in range(2)]
    _write_jsonl(transcript, turns)

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert isinstance(result, IngestChatResult)
    assert result.turns_ingested == 5
    assert result.conversations_processed == 2


# ---------------------------------------------------------------------------
# Empty / malformed input
# ---------------------------------------------------------------------------


def test_empty_jsonl_returns_zero_counts(tmp_path: Path) -> None:
    """Sabotage-proof: raise on empty file and this test fails because
    a result dataclass never gets returned."""
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("", encoding="utf-8")

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert result == IngestChatResult(0, 0, 0, 0, 0)


def test_malformed_line_logs_warning_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: change ``logger.warning`` to ``raise`` and
    pytest.raises would be needed; this test asserts no exception."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "not-json\n" + json.dumps(_turn("c1", 0)) + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="kairix.use_cases.ingest_chat"):
        result = ingest_chat(
            transcript,
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=FakeFactExtractor(),
        )

    assert result.turns_ingested == 1
    assert any("malformed jsonl line" in record.message for record in caplog.records)


def test_missing_required_field_skips_turn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Turns missing ``role`` or ``content`` skip with a warning.

    Note: ``conversation_id`` is NO LONGER a required field — it falls
    back to the JSONL filename stem (operator-friendly: one file = one
    conversation is a natural convention). Only ``role`` and ``content``
    are still mandatory.

    Sabotage-proof: remove the missing-field guard from ``_parse_turn``
    and a KeyError fires deeper in the pipeline.
    """
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"conversation_id": "c1", "content": "no role"}) + "\n" + json.dumps(_turn("c1", 0)) + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="kairix.use_cases.ingest_chat"):
        result = ingest_chat(
            transcript,
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=FakeFactExtractor(),
        )

    assert result.turns_ingested == 1
    assert any("missing" in record.message for record in caplog.records)


def test_missing_conversation_id_falls_back_to_filename_stem(tmp_path: Path) -> None:
    """Turns without explicit ``conversation_id`` inherit the JSONL filename stem.

    Supports the reference-library/conversations convention where one
    session-NNN.jsonl file is one conversation; per-turn conversation_id
    would be redundant. Sabotage-proof: change the fallback to skip
    instead of inherit and the markdown file under ``session-001/`` won't
    exist.
    """
    transcript = tmp_path / "session-001.jsonl"
    # Turn missing conversation_id but with required role + content.
    transcript.write_text(
        json.dumps({"role": "user", "content": "hello there"}) + "\n",
        encoding="utf-8",
    )

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert result.turns_ingested == 1
    assert result.conversations_processed == 1
    # File written under the filename-stem conversation id
    assert (_conversations_dir(tmp_path / "vault") / "session-001.md").exists()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_re_ingest_does_not_rewrite_unchanged_file(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``_existing_body_matches`` short-circuit
    so the file is always written, and the mtime equality fails."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0), _turn("c1", 1)])
    deps = dict(
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    ingest_chat(transcript, **deps)
    written = _conversations_dir(tmp_path / "vault") / "c1.md"
    first_mtime = written.stat().st_mtime_ns

    ingest_chat(transcript, **deps)
    second_mtime = written.stat().st_mtime_ns

    assert first_mtime == second_mtime


# ---------------------------------------------------------------------------
# Extractor + store coupling
# ---------------------------------------------------------------------------


def test_no_extract_short_circuits_extractor(tmp_path: Path) -> None:
    """Sabotage-proof: remove the ``if no_extract: continue`` branch and
    the extractor's ``calls`` list grows past zero."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(5)])

    extractor = FakeFactExtractor(scripted_facts=[FakeFactRecord(id="f1", entity="x", attribute="y", value="z")])
    store = FakeFactStore()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
        no_extract=True,
    )

    assert extractor.calls == []
    assert result.facts_added == 0
    assert result.windows_extracted == 0
    # FakeFactStore.search via empty query returns []; assert no records persisted.
    assert store.search("z") == []


def test_default_mode_calls_extractor_once_per_window(tmp_path: Path) -> None:
    """Sabotage-proof: change the windowing step from ``range(0, n, size)``
    to ``range(0, n - size + 1)`` and the call count exceeds expected."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(10)])

    extractor = FakeFactExtractor()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
        window_turns=5,
    )

    assert len(extractor.calls) == 2  # 10 turns / 5 per window


def test_each_emitted_fact_added_to_store(tmp_path: Path) -> None:
    """Sabotage-proof: drop ``fact_store.add(fact)`` from the loop and
    the persisted-id set is empty.

    Each fact uses a distinct ``attribute`` so the ingest-time
    consolidation pass (Capability #4) doesn't classify them as
    conflicting and supersede the earlier ones.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(5)])

    facts = [FakeFactRecord(id=f"f-{i}", entity="X", attribute=f"attr-{i}", value=f"v{i}") for i in range(3)]
    extractor = FakeFactExtractor(scripted_facts=facts)
    store = FakeFactStore()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
    )

    assert result.facts_added == 3
    persisted_ids: set[str] = set()
    for i in range(3):
        persisted_ids.update(f.id for f in store.find_conflicts(entity="X", attribute=f"attr-{i}"))
    assert persisted_ids == {"f-0", "f-1", "f-2"}


def test_namespace_flows_through_to_persisted_facts(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``_apply_provenance`` call from the use
    case and persisted facts carry the default ``"shared"`` namespace
    instead of the requested ``"eng-x"``."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    facts = [FakeFactRecord(id="f-1", entity="X", attribute="y", value="v")]
    extractor = FakeFactExtractor(scripted_facts=facts)
    store = FakeFactStore()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
        namespace="eng-x",
    )

    persisted = store.find_conflicts(entity="X", attribute="y", namespace="eng-x")
    assert len(persisted) == 1
    assert persisted[0].namespace == "eng-x"


def test_window_arithmetic_for_uneven_split(tmp_path: Path) -> None:
    """Sabotage-proof: replace the windowing loop with ``range(0, n - size, size)``
    and the trailing partial window is dropped — the count drops to 2."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(12)])

    extractor = FakeFactExtractor()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
        window_turns=5,
    )

    # 12 turns / window=5 → 3 windows (5 + 5 + 2)
    assert result.windows_extracted == 3
    assert len(extractor.calls) == 3
    assert [len(call["turns"]) for call in extractor.calls] == [5, 5, 2]


# ---------------------------------------------------------------------------
# Markdown frontmatter shape
# ---------------------------------------------------------------------------


def test_markdown_frontmatter_carries_turn_count(tmp_path: Path) -> None:
    """Sabotage-proof: omit ``turn_count`` from the rendered frontmatter
    and the regex assertion fails."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(4)])

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    body = (_conversations_dir(tmp_path / "vault") / "c1.md").read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "conversation_id: c1" in body
    assert "turn_count: 4" in body


# ---------------------------------------------------------------------------
# CLI surface — main() drives the use case
# ---------------------------------------------------------------------------


def test_cli_main_runs_use_case_with_injected_deps(tmp_path: Path) -> None:
    """Sabotage-proof: break the kwarg threading in ``main()`` (e.g.
    drop ``paths=resolved_paths``) and the use case fails to find
    the document root we wired."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0), _turn("c1", 1)])

    out = io.StringIO()
    err = io.StringIO()
    exit_code = main(
        [str(transcript), "--no-extract"],
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert exit_code == 0
    assert (_conversations_dir(tmp_path / "vault") / "c1.md").exists()
    assert "kairix ingest-chat: complete" in out.getvalue()


def test_cli_main_emits_json_when_flag_set(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``--json`` branch and the captured
    stdout starts with the human-readable banner instead."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    out = io.StringIO()
    main(
        [str(transcript), "--no-extract", "--json"],
        out=out,
        err=io.StringIO(),
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    payload = json.loads(out.getvalue())
    assert payload == {
        "turns_ingested": 1,
        "conversations_processed": 1,
        "facts_added": 0,
        "windows_extracted": 0,
        "facts_superseded": 0,
    }


def test_cli_main_resolves_production_sqlite_fact_store(tmp_path: Path) -> None:
    """Production path: ``fact_store=None`` resolves to a real SQLiteFactStore.

    Capability #3 (SQLiteFactStore) landed before Capability #1; the
    production-resolution path now wires the real store. This test
    pins that wiring — the CLI succeeds end-to-end against a real
    SQLite-backed store with ``--no-extract``.

    Sabotage-proof: rename ``SQLiteFactStore`` in ``kairix.core.facts``
    and the production-resolution import raises ImportError → exit
    code becomes 2 → this assertion fails.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    err = io.StringIO()
    exit_code = main(
        [str(transcript), "--no-extract"],
        out=io.StringIO(),
        err=err,
        paths=_paths(tmp_path),
        fact_store=None,  # forces production-resolution path → real SQLiteFactStore
        fact_extractor=FakeFactExtractor(),
    )

    assert exit_code == 0, f"production resolution should succeed; stderr={err.getvalue()!r}"
    assert (_conversations_dir(tmp_path / "vault") / "c1.md").exists(), "ingest must write the conversation file"


def test_cli_main_argparse_window_turns_default(tmp_path: Path) -> None:
    """Sabotage-proof: change the argparse default away from 5 and
    the extractor call count diverges from 1 (5-turn window for 5 turns)."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(5)])

    extractor = FakeFactExtractor()
    main(
        [str(transcript)],
        out=io.StringIO(),
        err=io.StringIO(),
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert len(extractor.calls) == 1


# ---------------------------------------------------------------------------
# Cross-cutting: namespace default is "shared"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Defensive branches — coverage for skip / fallback / null-extractor paths
# ---------------------------------------------------------------------------


def test_blank_line_in_jsonl_is_skipped(tmp_path: Path) -> None:
    """Sabotage-proof: remove the ``if not line: return None`` guard in
    ``_parse_turn`` and the empty line raises JSONDecodeError instead."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n" + json.dumps(_turn("c1", 0)) + "\n", encoding="utf-8")

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )
    assert result.turns_ingested == 1


def test_non_object_jsonl_line_is_skipped(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``isinstance(obj, dict)`` guard and the
    list literal explodes deeper in the pipeline."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "[1, 2, 3]\n" + json.dumps(_turn("c1", 0)) + "\n",
        encoding="utf-8",
    )

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )
    assert result.turns_ingested == 1


def test_window_turns_zero_collapses_to_one_window(tmp_path: Path) -> None:
    """Sabotage-proof: remove the ``size <= 0`` guard in ``_window`` and
    ``range(0, n, 0)`` raises ValueError."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(3)])

    extractor = FakeFactExtractor()
    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
        window_turns=0,
    )
    assert result.windows_extracted == 1
    assert len(extractor.calls) == 1


def test_null_extractor_used_under_no_extract_flag(tmp_path: Path) -> None:
    """Sabotage-proof: make ``_NullFactExtractor.extract`` raise and
    the test fails because the ``--no-extract`` default-deps path
    can't ingest.

    Capability #2 made the production default the LLM-backed
    extractor (resolved via :func:`kairix.platform.llm.get_default_backend`).
    The null extractor only fires when ``--no-extract`` is passed —
    the operator's documented "chunks-only" mode.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0), _turn("c1", 1)])

    out = io.StringIO()
    exit_code = main(
        [str(transcript), "--no-extract"],
        out=out,
        err=io.StringIO(),
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        # fact_extractor=None — exercises the _NullFactExtractor default branch
        fact_extractor=None,
    )

    assert exit_code == 0
    assert "facts added:             0" in out.getvalue()


def test_strip_frontmatter_passes_through_non_frontmatter_text(tmp_path: Path) -> None:
    """Sabotage-proof: remove the early-return for text without a
    leading ``---\\n`` and the idempotency hash diverges on plain text."""
    # We exercise the strip helper indirectly: re-ingesting a conversation
    # where the existing file was hand-written WITHOUT frontmatter still
    # writes the new content (no false-match on the body hash).
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    # Pre-seed a file with NO frontmatter at the target path.
    target_dir = _conversations_dir(tmp_path / "vault")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "c1.md"
    target.write_text("plain body, no frontmatter\n", encoding="utf-8")

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    # The plain pre-existing body doesn't match the new frontmatter+body
    # hash, so the file gets overwritten.
    assert "**user**: turn 0 of c1" in target.read_text(encoding="utf-8")


def test_provenance_stamped_even_when_namespace_already_matches(tmp_path: Path) -> None:
    """``_apply_provenance`` reconstructs the fact to stamp the breadcrumb
    even when the namespace already matches — the source_uri must be applied
    regardless, and the deterministic ``id`` is preserved on reconstruction.

    Sabotage-proof (executed): drop the ``source_uri=conversation_source_uri``
    kwarg from the ``_apply_provenance`` call in
    ``_extract_facts_for_conversation`` → ``source_uri`` stays ``None`` and
    the source_uri assertion below fails.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    fact_in = FakeFactRecord(id="stable-id", entity="X", attribute="y", value="v", namespace="shared")
    extractor = FakeFactExtractor(scripted_facts=[fact_in])
    store = FakeFactStore()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
        namespace="shared",
    )

    persisted = store.find_conflicts(entity="X", attribute="y", namespace="shared")
    assert persisted[0].id == "stable-id"
    assert persisted[0].source_uri == "04-Agent-Knowledge/conversations/c1.md"


def test_conversation_breadcrumb_stamped_on_persisted_facts(tmp_path: Path) -> None:
    """An ingested conversation's facts carry the resolvable breadcrumb:
    ``conversation_id`` + the conversation document ``source_uri`` that an
    agent can re-open to verify the fact (PLA-261).

    Sabotage-proof (executed): drop the ``conversation_id=cid`` kwarg from
    the ``_extract_facts_for_conversation`` call in ``ingest_chat`` →
    ``conversation_id`` stays ``None`` and the assertion below fails.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("sess-7", 0)])

    facts = [FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    extractor = FakeFactExtractor(scripted_facts=facts)
    store = FakeFactStore()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
    )

    persisted = store.find_conflicts(entity="Alice", attribute="role")[0]
    assert persisted.conversation_id == "sess-7"
    assert persisted.source_uri == "04-Agent-Knowledge/conversations/sess-7.md"


def test_apply_provenance_returns_unchanged_when_fact_lacks_namespace(tmp_path: Path) -> None:
    """Sabotage-proof: remove the ``hasattr(fact, "namespace")`` guard and
    the use case crashes when the extractor returns minimal duck-typed facts."""

    class _DuckFact:
        """A FactRecord-shaped object missing the ``namespace`` attribute."""

        id = "duck-1"
        entity = "X"
        attribute = "y"
        value = "v"
        confidence = 0.5
        source_turn_ids: tuple[str, ...] = ()
        extracted_at = "2026-05-20T00:00:00Z"
        superseded_by = None
        # NOTE: deliberately no ``namespace`` attribute.

    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    extractor = FakeFactExtractor(scripted_facts=[_DuckFact()])
    store = FakeFactStore()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
    )
    # The duck fact reached the store unmodified (the missing-namespace
    # guard in _apply_provenance returned the original).
    assert result.facts_added == 1


def test_namespace_defaults_to_shared(tmp_path: Path) -> None:
    """Sabotage-proof: change the default kwarg to e.g. ``"eng-default"``
    and the assertion on ``namespace == 'shared'`` fails."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0)])

    facts = [FakeFactRecord(id="f-1", entity="X", attribute="y", value="v")]
    extractor = FakeFactExtractor(scripted_facts=facts)
    store = FakeFactStore()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
    )

    persisted = store.find_conflicts(entity="X", attribute="y", namespace="shared")
    assert len(persisted) == 1


# ---------------------------------------------------------------------------
# Capability #4 — ingest-time consolidation
# ---------------------------------------------------------------------------


def test_two_ingests_supersede_prior_fact_on_conflict(tmp_path: Path) -> None:
    """Two consecutive ingests of the same entity+attribute with different
    values → one live fact + one superseded.

    Sabotage-proof: drop the ``resolved_consolidation.process(fact_to_add)``
    call from ``ingest_chat`` and both facts stay live — this assertion
    on live-count fails.
    """
    transcript_a = tmp_path / "a.jsonl"
    transcript_b = tmp_path / "b.jsonl"
    _write_jsonl(transcript_a, [_turn("c1", 0)])
    _write_jsonl(transcript_b, [_turn("c2", 0)])

    store = FakeFactStore()
    # First ingest: agent-alpha=single
    extractor_a = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-a", entity="agent-alpha", attribute="status", value="single")]
    )
    ingest_chat(
        transcript_a,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_a,
    )
    # Second ingest: agent-alpha=married — should supersede f-a.
    extractor_b = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-b", entity="agent-alpha", attribute="status", value="married")]
    )
    result = ingest_chat(
        transcript_b,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_b,
    )

    live = store.find_conflicts(entity="agent-alpha", attribute="status")
    live_ids = {f.id for f in live}
    assert live_ids == {"f-b"}  # f-a no longer live
    assert result.facts_superseded == 1


def test_two_ingests_same_value_coexist(tmp_path: Path) -> None:
    """Two consecutive ingests of the same entity+attribute with the
    *same* value → both live, no supersession.

    Sabotage-proof: change the ``"same"`` branch of ``default_contradict``
    to return ``"update"`` and the supersession count rises above zero.
    """
    transcript_a = tmp_path / "a.jsonl"
    transcript_b = tmp_path / "b.jsonl"
    _write_jsonl(transcript_a, [_turn("c1", 0)])
    _write_jsonl(transcript_b, [_turn("c2", 0)])

    store = FakeFactStore()
    extractor_a = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-a", entity="agent-alpha", attribute="status", value="single")]
    )
    ingest_chat(
        transcript_a,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_a,
    )
    extractor_b = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-b", entity="agent-alpha", attribute="status", value="single")]
    )
    result = ingest_chat(
        transcript_b,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_b,
    )

    live = store.find_conflicts(entity="agent-alpha", attribute="status")
    live_ids = {f.id for f in live}
    assert live_ids == {"f-a", "f-b"}
    assert result.facts_superseded == 0


def test_injected_consolidation_pass_is_used(tmp_path: Path) -> None:
    """A caller-injected :class:`ConsolidationPass` replaces the default.

    Sabotage-proof: hard-code the default pass instead of honouring the
    ``consolidation`` kwarg and this test's scripted callable (always
    ``"contradiction"``) is bypassed — the same-valued prior would
    coexist instead of being superseded.
    """
    from kairix.core.facts import ConsolidationPass  # public surface

    transcript_a = tmp_path / "a.jsonl"
    transcript_b = tmp_path / "b.jsonl"
    _write_jsonl(transcript_a, [_turn("c1", 0)])
    _write_jsonl(transcript_b, [_turn("c2", 0)])

    store = FakeFactStore()
    extractor_a = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-a", entity="X", attribute="status", value="same-value")]
    )
    ingest_chat(
        transcript_a,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_a,
    )

    # Inject a pass whose callable always supersedes — even when values match.
    def always_contradict(_prior: object, _new: object) -> str:
        return "contradiction"

    aggressive_pass = ConsolidationPass(fact_store=store, contradict=always_contradict)

    extractor_b = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-b", entity="X", attribute="status", value="same-value")]
    )
    result = ingest_chat(
        transcript_b,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor_b,
        consolidation=aggressive_pass,
    )

    live = store.find_conflicts(entity="X", attribute="status")
    live_ids = {f.id for f in live}
    assert live_ids == {"f-b"}
    assert result.facts_superseded == 1


# ---------------------------------------------------------------------------
# Stream A Lever A — session_metadata propagation
# ---------------------------------------------------------------------------


def test_session_metadata_kwarg_flows_to_extractor(tmp_path: Path) -> None:
    """session_metadata passed to ingest_chat reaches the extractor.

    Sabotage-proof: drop the ``session_metadata=resolved_metadata`` kwarg
    from the ``fact_extractor.extract`` call in ``ingest_chat`` and this
    test fails because the extractor's recorded call no longer carries
    the dict the test passed in.

    Manual sabotage run (2026-05-21): removed the kwarg; the
    ``FakeFactExtractor.calls[0]["session_metadata"]`` value flipped to
    ``None``, the equality check failed, pytest exit=1. Restored.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1), _turn("c1", 2)])
    extractor = FakeFactExtractor()
    metadata = {"date_time": "2023-05-04 14:30", "session_id": "s-12"}

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
        session_metadata=metadata,
    )

    # extract() is called once per window; the metadata must flow on
    # every call.
    assert len(extractor.calls) == 1
    assert extractor.calls[0]["session_metadata"] == metadata


def test_session_metadata_sidecar_is_picked_up(tmp_path: Path) -> None:
    """A ``<transcript>.metadata.json`` sidecar is auto-loaded.

    Sabotage-proof: remove the ``_read_sidecar_metadata`` fallback in
    ``ingest_chat`` and this test fails because the extractor receives
    ``session_metadata=None``.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    sidecar = tmp_path / "session-001.jsonl.metadata.json"
    sidecar.write_text(json.dumps({"date_time": "2023-05-04"}), encoding="utf-8")
    extractor = FakeFactExtractor()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert extractor.calls[0]["session_metadata"] == {"date_time": "2023-05-04"}


def test_session_date_appears_in_markdown_body_and_frontmatter(tmp_path: Path) -> None:
    """Session date is embedded in both the YAML frontmatter and body.

    Sabotage-proof: drop the ``**Session date:** ...`` line from
    ``_render_markdown`` and this test fails because the body assertion
    flips. Same for the frontmatter ``date_time:`` line.

    Closes the 54% of LoCoMo cat=2 (temporal) misses spike A1
    categorised as "(c) Date missing" — the chunker emits markdown
    with a session-date anchor so retrieval reaches LLM context with
    a calendar reference.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1, content="we shipped the feature")])

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        session_metadata={"date_time": "2023-05-04 14:30"},
    )

    written = (_conversations_dir(tmp_path / "vault") / "c1.md").read_text(encoding="utf-8")
    assert "date_time: 2023-05-04 14:30" in written  # frontmatter
    assert "**Session date:** 2023-05-04 14:30" in written  # body anchor


def test_no_metadata_omits_date_lines(tmp_path: Path) -> None:
    """When session_metadata is absent, no date line leaks into output.

    Sabotage-proof: hardcode ``date_time = "1970-01-01"`` in
    ``_extract_session_date_time`` and this test fails because the
    bare-no-metadata path leaks a sentinel date.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    written = (_conversations_dir(tmp_path / "vault") / "c1.md").read_text(encoding="utf-8")
    assert "Session date:" not in written
    assert "date_time:" not in written


def test_cli_metadata_flag_loads_json_sidecar(tmp_path: Path) -> None:
    """The CLI ``--metadata`` flag loads a JSON dict and threads it through.

    Sabotage-proof: drop ``session_metadata=session_metadata`` from the
    ``ingest_chat`` call in ``main`` and the extractor sees
    ``session_metadata=None`` — this test then fails on the
    ``FakeFactExtractor.calls[0]["session_metadata"]`` equality.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"date_time": "2023-05-04"}), encoding="utf-8")
    extractor = FakeFactExtractor()
    out = io.StringIO()
    err = io.StringIO()

    rc = main(
        [str(transcript), "--metadata", str(metadata_path)],
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert rc == 0
    assert extractor.calls[0]["session_metadata"] == {"date_time": "2023-05-04"}


def test_cli_metadata_flag_missing_path_warns_and_continues(tmp_path: Path) -> None:
    """``--metadata`` pointing at a missing file warns on stderr but proceeds.

    Sabotage-proof: change ``return None`` in ``_load_metadata_arg`` to
    ``raise FileNotFoundError`` and this test fails because the CLI
    exits non-zero instead of completing the ingest.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    missing = tmp_path / "nope.json"
    out = io.StringIO()
    err = io.StringIO()

    rc = main(
        [str(transcript), "--metadata", str(missing)],
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert rc == 0
    assert "does not exist" in err.getvalue()


def test_cli_metadata_flag_malformed_json_warns_and_continues(tmp_path: Path) -> None:
    """``--metadata`` pointing at malformed JSON warns and falls back.

    Sabotage-proof: drop the ``except json.JSONDecodeError`` branch in
    ``_load_metadata_arg`` and this test fails because the CLI raises
    instead of completing.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json{", encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()

    rc = main(
        [str(transcript), "--metadata", str(bad)],
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert rc == 0
    assert "failed to parse --metadata" in err.getvalue()


def test_cli_metadata_flag_non_object_warns_and_continues(tmp_path: Path) -> None:
    """``--metadata`` JSON that isn't a dict warns and falls back.

    Sabotage-proof: drop the ``isinstance(parsed, dict)`` check in
    ``_load_metadata_arg`` and this test fails because the array gets
    threaded through to the extractor as ``session_metadata=[...]``
    and the extractor's downstream attribute access raises.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    arr = tmp_path / "arr.json"
    arr.write_text(json.dumps(["not a dict"]), encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()

    rc = main(
        [str(transcript), "--metadata", str(arr)],
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert rc == 0
    assert "must contain a JSON object" in err.getvalue()


def test_session_metadata_kwarg_overrides_sidecar(tmp_path: Path) -> None:
    """Explicit ``session_metadata=`` kwarg wins over sidecar auto-discovery.

    Sabotage-proof: change ``if session_metadata is not None`` to
    ``if False`` in ``ingest_chat`` and this test fails because the
    sidecar's date wins and the assertion flips.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    sidecar = tmp_path / "session-001.jsonl.metadata.json"
    sidecar.write_text(json.dumps({"date_time": "1970-01-01"}), encoding="utf-8")
    extractor = FakeFactExtractor()

    ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
        session_metadata={"date_time": "2023-05-04"},
    )

    assert extractor.calls[0]["session_metadata"] == {"date_time": "2023-05-04"}


def test_malformed_sidecar_metadata_warns_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed sidecar JSON is logged + ingest proceeds with no metadata.

    Sabotage-proof: drop the ``except (OSError, json.JSONDecodeError)``
    branch in ``_read_sidecar_metadata`` and this test fails because
    ingest raises instead of logging + returning None.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    sidecar = tmp_path / "session-001.jsonl.metadata.json"
    sidecar.write_text("not json", encoding="utf-8")
    extractor = FakeFactExtractor()

    with caplog.at_level(logging.WARNING, logger="kairix.use_cases.ingest_chat"):
        ingest_chat(
            transcript,
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=extractor,
        )

    assert any("malformed metadata sidecar" in rec.message for rec in caplog.records)
    assert extractor.calls[0]["session_metadata"] is None


def test_non_object_sidecar_metadata_warns_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Sidecar JSON that isn't a dict is rejected with a warning.

    Sabotage-proof: drop the ``isinstance(parsed, dict)`` guard in
    ``_read_sidecar_metadata`` and this test fails because the array
    gets threaded through as ``session_metadata=[...]``.
    """
    transcript = tmp_path / "session-001.jsonl"
    _write_jsonl(transcript, [_turn("c1", 1)])
    sidecar = tmp_path / "session-001.jsonl.metadata.json"
    sidecar.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    extractor = FakeFactExtractor()

    with caplog.at_level(logging.WARNING, logger="kairix.use_cases.ingest_chat"):
        ingest_chat(
            transcript,
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=extractor,
        )

    assert any("not a JSON object" in rec.message for rec in caplog.records)
    assert extractor.calls[0]["session_metadata"] is None
