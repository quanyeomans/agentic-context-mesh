"""Unit + contract tests for ``kairix.use_cases.ingest_chat``.

Every test is sabotage-proven (mutate prod → fail → restore → pass).
The test corpus exercises the documented surface:

- Markdown is written under ``document_root/conversations/<cid>.md``
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

    written = tmp_path / "vault" / "conversations" / "c1.md"
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

    body = (tmp_path / "vault" / "conversations" / "c1.md").read_text(encoding="utf-8")
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

    assert result == IngestChatResult(0, 0, 0, 0)


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
    """Sabotage-proof: remove the missing-field guard from ``_parse_turn``
    and a KeyError fires deeper in the pipeline."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "no cid"}) + "\n" + json.dumps(_turn("c1", 0)) + "\n",
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
    written = tmp_path / "vault" / "conversations" / "c1.md"
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
    the persisted-id set is empty."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", i) for i in range(5)])

    facts = [FakeFactRecord(id=f"f-{i}", entity="X", attribute="y", value=f"v{i}") for i in range(3)]
    extractor = FakeFactExtractor(scripted_facts=facts)
    store = FakeFactStore()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
    )

    assert result.facts_added == 3
    persisted_ids = {f.id for f in store.find_conflicts(entity="X", attribute="y")}
    assert persisted_ids == {"f-0", "f-1", "f-2"}


def test_namespace_flows_through_to_persisted_facts(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``_apply_namespace`` call from the use
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

    body = (tmp_path / "vault" / "conversations" / "c1.md").read_text(encoding="utf-8")
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
    assert (tmp_path / "vault" / "conversations" / "c1.md").exists()
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
    assert (tmp_path / "vault" / "conversations" / "c1.md").exists(), "ingest must write the conversation file"


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


def test_null_extractor_used_by_cli_default_returns_no_facts(tmp_path: Path) -> None:
    """Sabotage-proof: make ``_NullFactExtractor.extract`` raise and the
    test fails because the default-deps path can't ingest."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_turn("c1", 0), _turn("c1", 1)])

    out = io.StringIO()
    exit_code = main(
        [str(transcript)],
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
    target_dir = tmp_path / "vault" / "conversations"
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


def test_apply_namespace_no_op_when_already_matching(tmp_path: Path) -> None:
    """Sabotage-proof: force ``_apply_namespace`` to always reconstruct
    even when ``current == namespace`` and the FakeFactRecord identity
    diverges (different ``id`` after reconstruction)."""
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


def test_apply_namespace_returns_unchanged_when_fact_lacks_namespace(tmp_path: Path) -> None:
    """Sabotage-proof: remove the ``AttributeError`` guard and the
    use case crashes when the extractor returns minimal duck-typed facts."""

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
    # The duck fact reached the store unmodified (AttributeError branch
    # in _apply_namespace returned the original).
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
