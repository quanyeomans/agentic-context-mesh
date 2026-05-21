"""Unit tests for :class:`SuiteRunner`'s ``ingest_corpus`` wiring (Spike C1 Phase 3).

Pins the closure of the Lever A regression Spike C1 surfaced:
``SuiteRunner._ingest_sessions`` previously called
``fact_extractor.extract(turns=turns)`` with **no** ``session_metadata``
kwarg — so every reference-library conversational eval was silently
ingesting without session ``date_time`` anchors. This file proves the
runner now routes through :func:`kairix.corpus.ingest.ingest_corpus`
and that session metadata threads end-to-end to the extractor.

Every test in this file is sabotage-proven (mutate prod → fail →
restore → pass). F1-clean — no monkeypatching; collaborators are
:class:`tests.fakes.Fake*` injections only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kairix.core.facts.consolidation import ConsolidationPass, default_contradict
from kairix.paths import KairixPaths
from kairix.quality.eval.suite_runner import SuiteRunner
from tests.fakes import (
    FakeCorpusEmbedder,
    FakeDocumentWriter,
    FakeFactExtractor,
    FakeFactRecord,
    FakeFactStore,
    FakeLLMBackend,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> KairixPaths:
    """KairixPaths anchored at tmp_path — never reads env."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _write_session(path: Path, turns: list[dict[str, Any]]) -> None:
    """Write a session-NNN.jsonl file with the given turn list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def _write_sidecar(path: Path, payload: dict[str, Any]) -> None:
    """Write a sidecar JSON file at ``path`` from a Python dict."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    """Write a generic JSON file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _lay_out_minimal_suite(suite_dir: Path) -> None:
    """Create a minimal-but-valid suite directory under ``suite_dir``."""
    _write_session(
        suite_dir / "session-001.jsonl",
        [
            {"id": "s001-t001", "speaker": "agent-alpha", "content": "starting"},
            {"id": "s001-t002", "speaker": "agent-beta", "content": "ack"},
        ],
    )
    _write_json(
        suite_dir / "ground-truth-queries.json",
        [
            {
                "question": "What did agent-alpha say?",
                "answer": "starting",
                "category": "single-hop",
            }
        ],
    )


def _make_runner(
    *,
    tmp_path: Path,
    scripted_facts: list[Any] | None = None,
    document_writer: Any = None,
    embedder: Any = None,
    consolidation: Any = None,
) -> tuple[SuiteRunner, FakeFactStore, FakeFactExtractor]:
    """Build a SuiteRunner with the requested collaborators wired in."""
    store = FakeFactStore()
    extractor = FakeFactExtractor(scripted_facts=scripted_facts or [])
    llm = FakeLLMBackend(chat_response="1.0")
    runner = SuiteRunner(
        fact_store=store,
        fact_extractor=extractor,
        llm=llm,
        paths=_paths(tmp_path),
        document_writer=document_writer,
        embedder=embedder,
        consolidation=consolidation,
    )
    return runner, store, extractor


# ---------------------------------------------------------------------------
# Section 1 — legacy-parity (None defaults preserve facts-only mode)
# ---------------------------------------------------------------------------


def test_legacy_defaults_run_facts_only_no_document_writes(tmp_path: Path) -> None:
    """All three new kwargs None → behaviour mirrors today's facts-only path.

    Sabotage-proof (EXECUTED): I temporarily removed the
    ``document_writer is not None`` guard in
    ``kairix/corpus/ingest.py:_process_session`` (forced an
    AttributeError from ``writer.write`` when writer is None) and this
    test failed with ``AttributeError: 'NoneType' object has no
    attribute 'write'``. Restoring the guard makes it pass again.
    """
    suite_dir = tmp_path / "engagement-alpha"
    _lay_out_minimal_suite(suite_dir)

    scripted = [
        FakeFactRecord(
            id="f-001",
            entity="agent-alpha",
            attribute="role",
            value="starting",
            namespace="engagement-alpha",
        )
    ]
    runner, store, extractor = _make_runner(tmp_path=tmp_path, scripted_facts=scripted)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    # Extractor was called — exactly one window for the single session.
    assert len(extractor.calls) == 1
    # Fact landed in the store via the recorder forwarding to the inner store.
    hits = store.search("agent-alpha role")
    assert len(hits) == 1
    assert hits[0].record.id == "f-001"


def test_legacy_defaults_no_chunks_indexed_no_supersession(tmp_path: Path) -> None:
    """All three new kwargs None → result reflects pure-extract mode.

    Sabotage-proof (EXECUTED): I changed ``embedder is None`` to
    ``embedder is not None`` in
    ``kairix/corpus/ingest.py:_maybe_embed`` (always call embed even
    when None) — that triggers ``AttributeError: 'NoneType' object has
    no attribute 'embed'`` and this test crashes. Restoring the guard
    returns the test to passing.
    """
    suite_dir = tmp_path / "engagement-alpha"
    _lay_out_minimal_suite(suite_dir)

    scripted = [
        FakeFactRecord(
            id="f-001",
            entity="agent-alpha",
            attribute="role",
            value="lead",
            namespace="engagement-alpha",
        )
    ]
    runner, store, _ = _make_runner(tmp_path=tmp_path, scripted_facts=scripted)
    spec = runner.discover_suite(suite_dir)
    result = runner.run(spec)

    # Run completes without raising — embedder=None must not be called.
    assert result.n_questions == 1
    # The single fact lands in the store; consolidation=None means no
    # supersession path runs (FakeFactStore would refuse the duplicate id
    # but it isn't a duplicate — single fact, single add).
    assert len(store.search("agent-alpha role")) == 1


# ---------------------------------------------------------------------------
# Section 2 — document_writer wiring
# ---------------------------------------------------------------------------


def test_document_writer_invoked_when_wired(tmp_path: Path) -> None:
    """``document_writer`` non-None → ``DocumentWriter.write`` is called per session.

    Sabotage-proof (EXECUTED): I changed
    ``kairix/corpus/ingest.py:_process_session`` to skip the
    ``if document_writer is not None`` block entirely (force-skip the
    write). ``writer.writes`` stayed empty and this test's
    ``len(writer.writes) == 1`` assertion failed. Restoring the call
    returns the test to passing.
    """
    suite_dir = tmp_path / "engagement-gamma"
    _lay_out_minimal_suite(suite_dir)

    writer = FakeDocumentWriter(base_path=tmp_path / "fake-docs")
    runner, _, _ = _make_runner(tmp_path=tmp_path, document_writer=writer)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(writer.writes) == 1
    # corpus_id == suite.name (engagement-gamma) — Lever A namespace anchor.
    assert writer.writes[0]["corpus_id"] == "engagement-gamma"
    # session_id == session_path.stem.
    assert writer.writes[0]["session_id"] == "session-001"


def test_corpus_id_is_suite_name_on_namespace_stamp(tmp_path: Path) -> None:
    """corpus_id == suite.name flows to the namespace stamped on emitted facts.

    Sabotage-proof (EXECUTED): I patched
    ``kairix/corpus/ingest.py:_process_session`` to pass
    ``corpus_id="bogus"`` to ``_apply_namespace`` instead of
    ``request.corpus_id``. The namespace on the stored fact flipped to
    ``"bogus"`` and this test's ``namespace == "engagement-delta"``
    assertion failed. Restoring ``request.corpus_id`` returns it.
    """
    suite_dir = tmp_path / "engagement-delta"
    _lay_out_minimal_suite(suite_dir)

    scripted = [
        FakeFactRecord(
            id="f-001",
            entity="agent-alpha",
            attribute="role",
            value="lead",
            # Default 'shared' namespace — _apply_namespace must overwrite to
            # the corpus_id == suite.name == "engagement-delta".
        )
    ]
    runner, store, _ = _make_runner(tmp_path=tmp_path, scripted_facts=scripted)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    hits = store.search("agent-alpha role", namespace="engagement-delta")
    assert len(hits) == 1
    assert hits[0].record.namespace == "engagement-delta"


# ---------------------------------------------------------------------------
# Section 3 — embedder wiring
# ---------------------------------------------------------------------------


def test_embedder_invoked_with_written_document_paths(tmp_path: Path) -> None:
    """``embedder`` non-None + ``document_writer`` non-None → embed sees the paths.

    Sabotage-proof (EXECUTED): I changed
    ``kairix/corpus/ingest.py:_maybe_embed`` to always pass ``()`` to
    ``embedder.embed`` (drop the document_paths argument). The fake
    captured ``()`` instead of the written path tuple and this test's
    ``len(embedder.calls[0]) == 1`` assertion failed. Restoring the
    pass-through returns it.
    """
    suite_dir = tmp_path / "engagement-epsilon"
    _lay_out_minimal_suite(suite_dir)

    writer = FakeDocumentWriter(base_path=tmp_path / "fake-docs")
    embedder = FakeCorpusEmbedder(scripted_chunks_per_call=[5])
    runner, _, _ = _make_runner(
        tmp_path=tmp_path,
        document_writer=writer,
        embedder=embedder,
    )
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(embedder.calls) == 1
    # The one written document path was forwarded into the embedder.
    assert len(embedder.calls[0]) == 1
    assert embedder.calls[0][0] == tmp_path / "fake-docs" / "engagement-epsilon" / "session-001.md"


# ---------------------------------------------------------------------------
# Section 4 — consolidation wiring
# ---------------------------------------------------------------------------


def test_consolidation_supersedes_contradicting_prior_facts(tmp_path: Path) -> None:
    """``consolidation`` non-None → contradicting priors get marked superseded.

    The store starts with a prior fact ``(agent-alpha, role, junior)``
    in namespace ``engagement-beta``. The extractor then emits
    ``(agent-alpha, role, lead)`` for the same namespace —
    consolidation marks the prior superseded.

    Sabotage-proof (EXECUTED): I changed the SuiteRunner ctor to
    ignore the ``consolidation`` kwarg (force ``self._consolidation =
    None``). The supersession assertion failed because the prior fact
    stayed live. Restoring ``self._consolidation = consolidation``
    returns the test to green.
    """
    suite_dir = tmp_path / "engagement-beta"
    _lay_out_minimal_suite(suite_dir)

    store = FakeFactStore()
    prior = FakeFactRecord(
        id="f-prior",
        entity="agent-alpha",
        attribute="role",
        value="junior",
        namespace="engagement-beta",
    )
    store.add(prior)

    scripted = [
        FakeFactRecord(
            id="f-new",
            entity="agent-alpha",
            attribute="role",
            value="lead",
            namespace="engagement-beta",
        )
    ]
    extractor = FakeFactExtractor(scripted_facts=scripted)
    llm = FakeLLMBackend(chat_response="1.0")
    consolidation = ConsolidationPass(fact_store=store, contradict=default_contradict)

    runner = SuiteRunner(
        fact_store=store,
        fact_extractor=extractor,
        llm=llm,
        paths=_paths(tmp_path),
        consolidation=consolidation,
    )
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    # The prior fact is now superseded.
    assert store._facts["f-prior"].superseded_by == "f-new"
    # The new fact is live.
    assert store._facts["f-new"].superseded_by is None


# ---------------------------------------------------------------------------
# Section 5 — full collaborator set
# ---------------------------------------------------------------------------


def test_full_collaborator_set_all_components_invoked(tmp_path: Path) -> None:
    """All four collaborators wired → all four receive the ingest pass.

    Sabotage-proof (EXECUTED): I changed
    ``kairix/quality/eval/suite_runner.py:_ingest_sessions`` to pass
    ``document_writer=None`` to ``ingest_corpus`` (ignore the
    constructor-injected writer). ``writer.writes`` stayed empty and
    this test's ``len(writer.writes) == 2`` assertion failed.
    Restoring ``document_writer=self._document_writer`` returns it.
    """
    suite_dir = tmp_path / "engagement-zeta"
    # Two sessions so we can verify each collaborator is invoked per session.
    _write_session(
        suite_dir / "session-001.jsonl",
        [{"id": "s001-t001", "speaker": "agent-alpha", "content": "hi"}],
    )
    _write_session(
        suite_dir / "session-002.jsonl",
        [{"id": "s002-t001", "speaker": "agent-beta", "content": "hey"}],
    )
    _write_json(suite_dir / "ground-truth-queries.json", [{"question": "q", "answer": "a"}])

    writer = FakeDocumentWriter(base_path=tmp_path / "fake-docs")
    embedder = FakeCorpusEmbedder(scripted_chunks_per_call=[3])
    store = FakeFactStore()
    consolidation = ConsolidationPass(fact_store=store, contradict=default_contradict)
    extractor = FakeFactExtractor(scripted_facts=[])
    runner = SuiteRunner(
        fact_store=store,
        fact_extractor=extractor,
        llm=FakeLLMBackend(chat_response="1.0"),
        paths=_paths(tmp_path),
        document_writer=writer,
        embedder=embedder,
        consolidation=consolidation,
    )
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    # Two sessions → two writes.
    assert len(writer.writes) == 2
    # Embedder called once after all sessions, receives both written paths.
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 2
    # Extractor was called for each session (1 window each, since both are tiny).
    assert len(extractor.calls) == 2


# ---------------------------------------------------------------------------
# Section 6 — Lever A regression closure (THE headline test)
# ---------------------------------------------------------------------------


def test_session_metadata_threads_to_extractor_calls(tmp_path: Path) -> None:
    """Lever A regression closure: session_metadata reaches extract calls.

    This is the test the Spike C1 brief calls out as the headline
    regression closure. Before this refactor, ``_ingest_sessions``
    called ``extract(turns=turns)`` with **no** ``session_metadata``
    kwarg — every reference-library conversational eval was silently
    ingesting without session ``date_time`` anchors. The corpus-ingest
    primitive plumbs ``session.metadata`` through to the extractor;
    SuiteRunner now uses that primitive.

    Sabotage-proof (EXECUTED): I reverted
    ``kairix/quality/eval/suite_runner.py:_ingest_sessions`` to call
    ``self._fact_extractor.extract(turns=turns)`` directly (legacy
    behaviour) — bypassing ``ingest_corpus``. The
    ``session_metadata`` recorded in ``extractor.calls`` flipped to
    ``None`` and this assertion failed. Restoring the
    ``ingest_corpus`` wiring returns it.
    """
    suite_dir = tmp_path / "engagement-alpha"
    _lay_out_minimal_suite(suite_dir)
    # Sidecar metadata carrying the session date_time.
    _write_sidecar(
        suite_dir / "session-001.jsonl.metadata.json",
        {"date_time": "2026-04-01T10:00:00Z", "session_id": "session-001"},
    )

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    # The extract call MUST carry the sidecar's metadata.
    assert len(extractor.calls) == 1
    metadata = extractor.calls[0]["session_metadata"]
    assert metadata is not None
    assert metadata["date_time"] == "2026-04-01T10:00:00Z"


def test_session_metadata_none_when_no_sidecar(tmp_path: Path) -> None:
    """No sidecar present → ``session_metadata`` is None (not a synthetic dict).

    Sabotage-proof (EXECUTED): I changed
    ``kairix/quality/eval/suite_runner.py:_load_session_metadata`` to
    return ``{}`` (truthy empty dict) instead of ``None``. The
    extractor's ``session_metadata`` argument became ``{}`` and this
    test's ``is None`` assertion failed. Restoring ``return None`` at
    the bottom of the function returns it.
    """
    suite_dir = tmp_path / "engagement-no-sidecar"
    _lay_out_minimal_suite(suite_dir)
    # Deliberately NO sidecar file.

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(extractor.calls) == 1
    assert extractor.calls[0]["session_metadata"] is None


def test_session_metadata_loads_from_md_sidecar_alternate(tmp_path: Path) -> None:
    """LoCoMo-shape ``<stem>.md.metadata.json`` sidecar is also recognised.

    Sabotage-proof (EXECUTED): I dropped the second candidate
    (``.md.metadata.json`` lookup) from
    ``kairix/quality/eval/suite_runner.py:_load_session_metadata``.
    The metadata stayed unloaded and this test's ``date_time``
    assertion failed because session_metadata was None. Restoring the
    second candidate returns it.
    """
    suite_dir = tmp_path / "engagement-md"
    _lay_out_minimal_suite(suite_dir)
    # Sidecar follows the LoCoMo convention: <stem>.md.metadata.json.
    # The session file is session-001.jsonl, stem is "session-001", so the
    # alternate sidecar lives at session-001.md.metadata.json — under
    # the suite directory alongside the session.
    _write_sidecar(
        suite_dir / "session-001.md.metadata.json",
        {"date_time": "2026-05-15T08:30:00Z"},
    )

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(extractor.calls) == 1
    metadata = extractor.calls[0]["session_metadata"]
    assert metadata is not None
    assert metadata["date_time"] == "2026-05-15T08:30:00Z"


def test_session_metadata_jsonl_sidecar_wins_over_md(tmp_path: Path) -> None:
    """When both sidecar shapes exist, ``.jsonl.metadata.json`` wins.

    Resolution order: the canonical reference-library sidecar
    (``<session_path>.metadata.json``) is checked first; the LoCoMo
    fallback only fires when the canonical sidecar is absent.

    Sabotage-proof (EXECUTED): I reversed the candidate tuple in
    ``kairix/quality/eval/suite_runner.py:_load_session_metadata``
    (put ``.md.metadata.json`` first). The wrong sidecar won and this
    test's ``"canonical"`` assertion failed. Restoring the original
    order returns it.
    """
    suite_dir = tmp_path / "engagement-priority"
    _lay_out_minimal_suite(suite_dir)
    _write_sidecar(
        suite_dir / "session-001.jsonl.metadata.json",
        {"date_time": "canonical"},
    )
    _write_sidecar(
        suite_dir / "session-001.md.metadata.json",
        {"date_time": "locomo-fallback"},
    )

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    metadata = extractor.calls[0]["session_metadata"]
    assert metadata is not None
    assert metadata["date_time"] == "canonical"


def test_malformed_sidecar_is_tolerated_metadata_falls_back_to_none(tmp_path: Path) -> None:
    """Malformed sidecar JSON → metadata returns None, run continues.

    Mirrors the fail-soft posture of ``_read_session`` for session
    JSONL — a malformed sidecar logs a warning and the session
    continues with no metadata, rather than crashing the whole suite.

    Sabotage-proof (EXECUTED): I dropped the ``try/except
    json.JSONDecodeError`` block in
    ``kairix/quality/eval/suite_runner.py:_load_session_metadata``.
    The raised exception escaped the loader, crashing the whole run.
    This test's ``runner.run(spec)`` call raised
    ``json.JSONDecodeError`` instead of completing — the assertion
    after never ran. Restoring the try/except returns the test to
    passing.
    """
    suite_dir = tmp_path / "engagement-bad-sidecar"
    _lay_out_minimal_suite(suite_dir)
    (suite_dir / "session-001.jsonl.metadata.json").write_text("not-json{", encoding="utf-8")

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    # Extract still ran; metadata gracefully degraded to None.
    assert len(extractor.calls) == 1
    assert extractor.calls[0]["session_metadata"] is None


def test_sidecar_non_object_json_is_tolerated_returns_none(tmp_path: Path) -> None:
    """Sidecar JSON that isn't an object (e.g. a list) → falls back to None.

    Sabotage-proof (EXECUTED): I changed
    ``kairix/quality/eval/suite_runner.py:_load_session_metadata`` to
    ``return raw`` (return the value unchanged) instead of checking
    ``isinstance(raw, dict)``. The list leaked through to the
    extractor and ``session_metadata["date_time"]`` raised
    ``TypeError: list indices must be integers``. Restoring the
    isinstance gate returns the soft fall-back.
    """
    suite_dir = tmp_path / "engagement-list-sidecar"
    _lay_out_minimal_suite(suite_dir)
    _write_sidecar(
        suite_dir / "session-001.jsonl.metadata.json",
        # Not a dict — a list at the top level.
        ["oops", "should-be-an-object"],  # type: ignore[arg-type] — deliberate shape violation; the test pins the runtime fallback when a sidecar JSON file's root is not a dict
    )

    runner, _, extractor = _make_runner(tmp_path=tmp_path)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(extractor.calls) == 1
    assert extractor.calls[0]["session_metadata"] is None
