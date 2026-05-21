"""Unit tests for :mod:`kairix.corpus.ingest` — the Spike C1 unified contract.

Coverage targets every public contract surface and the documented
optional-collaborator branches:

  * SessionPayload / IngestRequest / IngestResult round-trip + immutability
  * Happy-path orchestration with all collaborators wired
  * document_writer=None — facts-only mode
  * embedder=None — no chunk index update
  * consolidation=None vs. wired — facts_superseded counter branch
  * session_metadata propagates to fact_extractor (Stream A Lever A pin)
  * corpus_id stamped as namespace on every emitted fact
  * corpus_id and session_id forwarded to DocumentWriter.write
  * window_turns kwarg drives extractor windowing (incl. ``0`` collapse)
  * skipped_sessions surfaces per-session failures without aborting siblings
  * Frontmatter + body convention (``**Session date:**`` pin)

Several tests carry sabotage-proof comments documenting which mutation
in production code drops them red. Sabotage proofs were executed live
during authorship — the docstring records the exact line mutated, the
observed failure, and the restoration step.

F1: every test uses Fake* implementations from :mod:`tests.fakes`. No
monkeypatching, no internal-attribute reassignment, no env-var
substitution. F8: ``pytestmark`` carries the unit category.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from kairix.core.facts.consolidation import (
    ConsolidationPass,
    default_contradict,
)
from kairix.corpus.ingest import (
    IngestRequest,
    IngestResult,
    SessionPayload,
    ingest_corpus,
)
from tests.fakes import (
    FakeCorpusEmbedder,
    FakeDocumentWriter,
    FakeFactExtractor,
    FakeFactRecord,
    FakeFactStore,
    FakePaths,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers shared across tests — keep test bodies focused on assertions.
# ---------------------------------------------------------------------------


def _turn(role: str, content: str, turn_id: str = "t-1") -> dict[str, Any]:
    """One canonical turn dict — F17 helper to avoid duplicating the literal shape."""
    return {"id": turn_id, "role": role, "content": content}


def _make_session(
    *, session_id: str | None = None, n_turns: int = 3, metadata: dict[str, Any] | None = None
) -> SessionPayload:
    """Build a SessionPayload with ``n_turns`` synthesised user/assistant turns."""
    turns = tuple(
        _turn(
            role="user" if i % 2 == 0 else "assistant",
            content=f"turn {i}",
            turn_id=f"t-{i}",
        )
        for i in range(n_turns)
    )
    return SessionPayload(turns=turns, session_id=session_id, metadata=metadata)


def _fact(fid: str, entity: str = "acme", attribute: str = "ceo", value: str = "alice") -> FakeFactRecord:
    """One fake fact — F17 helper for the common factory shape."""
    return FakeFactRecord(id=fid, entity=entity, attribute=attribute, value=value)


# ---------------------------------------------------------------------------
# Section 1 — value-object shapes (SessionPayload / IngestRequest / IngestResult)
# ---------------------------------------------------------------------------


def test_session_payload_minimal_construction() -> None:
    """SessionPayload accepts turns alone and defaults the optional fields."""
    payload = SessionPayload(turns=(_turn("user", "hi"),))
    assert payload.turns == (_turn("user", "hi"),)
    assert payload.session_id is None
    assert payload.metadata is None


def test_session_payload_is_frozen() -> None:
    """SessionPayload is a frozen dataclass — assignment must raise FrozenInstanceError.

    Sabotage-proof: remove ``frozen=True`` from the dataclass decorator
    (``kairix/corpus/ingest.py:91``). With the mutation in place this
    test reports FAILED — the assignment succeeds and the assertion
    doesn't trip. Restoring ``frozen=True`` returns the test green.
    Verified live during authorship.
    """
    payload = SessionPayload(turns=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.turns = (_turn("user", "x"),)  # type: ignore[misc] — sabotage probe; assignment must raise


def test_ingest_request_default_window_turns_is_four() -> None:
    """IngestRequest defaults ``window_turns`` to 4 per the brief's spec."""
    req = IngestRequest(sessions=(), corpus_id="conv-1")
    assert req.window_turns == 4


def test_ingest_request_is_frozen() -> None:
    """IngestRequest is frozen — corpus_id reassignment must raise."""
    req = IngestRequest(sessions=(), corpus_id="conv-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.corpus_id = "conv-2"  # type: ignore[misc] — sabotage probe; assignment must raise on frozen dataclass


def test_ingest_result_carries_all_documented_fields() -> None:
    """IngestResult instantiates with the full canonical surface area."""
    result = IngestResult(
        corpus_id="conv-1",
        sessions_processed=2,
        turns_ingested=6,
        document_paths=(Path("/x/a.md"), Path("/x/b.md")),
        windows_extracted=2,
        facts_added=4,
        facts_superseded=1,
        chunks_indexed=8,
        skipped_sessions=(),
    )
    assert result.corpus_id == "conv-1"
    assert result.sessions_processed == 2
    assert result.turns_ingested == 6
    assert result.document_paths == (Path("/x/a.md"), Path("/x/b.md"))
    assert result.windows_extracted == 2
    assert result.facts_added == 4
    assert result.facts_superseded == 1
    assert result.chunks_indexed == 8
    assert result.skipped_sessions == ()


# ---------------------------------------------------------------------------
# Section 2 — happy-path orchestration with every collaborator wired.
# ---------------------------------------------------------------------------


def test_happy_path_all_collaborators_wired() -> None:
    """Two sessions, three turns each, every collaborator non-None — counts pin the orchestration."""
    sessions = (_make_session(n_turns=3), _make_session(n_turns=3))
    request = IngestRequest(sessions=sessions, corpus_id="conv-7", window_turns=2)

    fact_store = FakeFactStore()
    extractor = FakeFactExtractor(scripted_facts=[_fact("f-1")])
    writer = FakeDocumentWriter()
    embedder = FakeCorpusEmbedder(scripted_chunks_per_call=[5])
    consolidation = ConsolidationPass(fact_store=fact_store, contradict=default_contradict)

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=fact_store,
        fact_extractor=extractor,
        document_writer=writer,
        embedder=embedder,
        consolidation=consolidation,
    )

    # 2 sessions, 3 turns each, window_turns=2 -> 2 windows per session (2+1) -> 4 total
    assert result.corpus_id == "conv-7"
    assert result.sessions_processed == 2
    assert result.turns_ingested == 6
    assert result.windows_extracted == 4
    assert result.facts_added == 4  # 1 fact per window, 4 windows
    assert result.chunks_indexed == 5
    assert result.skipped_sessions == ()
    assert len(result.document_paths) == 2  # one path per session


def test_document_writer_receives_corpus_id_and_session_id() -> None:
    """corpus_id + session_id forward verbatim into DocumentWriter.write.

    Sabotage-proof: in ``_write_session_document`` change
    ``corpus_id=corpus_id`` to ``corpus_id="other"`` (or drop the kwarg).
    Running this test then reports FAILED on the writes[0]['corpus_id']
    assertion. Restoring the literal returns the test green. Verified
    live during authorship.
    """
    session = SessionPayload(turns=(_turn("user", "hi"),), session_id="s-42")
    request = IngestRequest(sessions=(session,), corpus_id="conv-99")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert len(writer.writes) == 1
    assert writer.writes[0]["corpus_id"] == "conv-99"
    assert writer.writes[0]["session_id"] == "s-42"


def test_document_writer_synthesises_session_id_when_absent() -> None:
    """Missing session_id → orchestrator falls back to ``session-{idx:03d}`` shape."""
    sessions = (_make_session(n_turns=1), _make_session(n_turns=1))
    request = IngestRequest(sessions=sessions, corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert [w["session_id"] for w in writer.writes] == ["session-001", "session-002"]


def test_document_paths_preserve_session_order() -> None:
    """IngestResult.document_paths is in the same order as the input sessions."""
    sessions = (_make_session(session_id="s-a"), _make_session(session_id="s-b"), _make_session(session_id="s-c"))
    request = IngestRequest(sessions=sessions, corpus_id="conv-1")
    writer = FakeDocumentWriter()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    stems = [p.stem for p in result.document_paths]
    assert stems == ["s-a", "s-b", "s-c"]


# ---------------------------------------------------------------------------
# Section 3 — optional-collaborator opt-out branches.
# ---------------------------------------------------------------------------


def test_document_writer_none_no_writes_facts_still_extracted() -> None:
    """``document_writer=None`` → no markdown written; facts still flow.

    Sabotage-proof: drop the ``if document_writer is not None`` guard in
    ``_process_session`` so the writer is called unconditionally. With
    document_writer=None this then raises AttributeError on
    ``None.write(...)``. Restoring the guard returns the test green.
    Verified live during authorship.
    """
    sessions = (_make_session(n_turns=2),)
    request = IngestRequest(sessions=sessions, corpus_id="conv-1", window_turns=2)
    extractor = FakeFactExtractor(scripted_facts=[_fact("f-1")])
    fact_store = FakeFactStore()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=fact_store,
        fact_extractor=extractor,
        document_writer=None,
    )

    assert result.document_paths == ()
    assert result.facts_added == 1
    assert len(extractor.calls) == 1


def test_embedder_none_chunks_indexed_zero() -> None:
    """``embedder=None`` → chunks_indexed stays at 0 regardless of doc paths."""
    request = IngestRequest(sessions=(_make_session(n_turns=2),), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
        embedder=None,
    )

    assert result.chunks_indexed == 0
    assert len(result.document_paths) == 1  # writer still ran


def test_embedder_called_once_with_all_document_paths() -> None:
    """The embedder receives the full document_paths tuple in one call.

    Sabotage-proof: change ``embedder.embed(tuple(document_paths))`` to
    ``embedder.embed(())`` in ``_maybe_embed``. Running this test then
    reports FAILED on the calls[0] length assertion. Restoring returns
    the test green. Verified live during authorship.
    """
    sessions = (_make_session(session_id="s-1"), _make_session(session_id="s-2"))
    request = IngestRequest(sessions=sessions, corpus_id="conv-1")
    writer = FakeDocumentWriter()
    embedder = FakeCorpusEmbedder(scripted_chunks_per_call=[7])

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
        embedder=embedder,
    )

    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 2
    assert result.chunks_indexed == 7


def test_consolidation_none_no_supersession() -> None:
    """``consolidation=None`` → facts_superseded=0 even with duplicate facts in store."""
    sessions = (_make_session(n_turns=2),)
    fact_store = FakeFactStore()
    fact_store.add(_fact("prior", value="bob"))  # pre-existing
    extractor = FakeFactExtractor(scripted_facts=[_fact("new", value="charlie")])
    request = IngestRequest(sessions=sessions, corpus_id="conv-1", window_turns=2)

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=fact_store,
        fact_extractor=extractor,
        consolidation=None,
    )

    assert result.facts_superseded == 0
    assert result.facts_added == 1


def test_consolidation_wired_supersedes_contradicting_facts() -> None:
    """``consolidation=ConsolidationPass(...)`` → contradicting priors get marked superseded.

    Default contradict callable returns ``update`` when values differ.
    The store starts with one fact about (acme, ceo, bob); the
    extractor emits a new fact about (acme, ceo, charlie). Stamped
    with the same corpus_id namespace, the consolidation pass marks
    the prior superseded.

    Sabotage-proof: in ``_process_session`` change
    ``hasattr(stamped, "namespace")`` to ``False``. Running this test
    then reports FAILED on facts_superseded == 1. Restoring returns
    the test green. Verified live during authorship.
    """
    sessions = (_make_session(n_turns=1),)
    fact_store = FakeFactStore()
    # Pre-existing fact with the same corpus namespace ("conv-1") so the
    # consolidation pass finds it as a conflict candidate.
    prior = FakeFactRecord(id="prior", entity="acme", attribute="ceo", value="bob", namespace="conv-1")
    fact_store.add(prior)
    extractor = FakeFactExtractor(scripted_facts=[_fact("new", value="charlie")])
    consolidation = ConsolidationPass(fact_store=fact_store, contradict=default_contradict)
    request = IngestRequest(sessions=sessions, corpus_id="conv-1", window_turns=1)

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=fact_store,
        fact_extractor=extractor,
        consolidation=consolidation,
    )

    assert result.facts_added == 1
    assert result.facts_superseded == 1


# ---------------------------------------------------------------------------
# Section 4 — Stream A Lever A: session_metadata propagation.
# ---------------------------------------------------------------------------


def test_session_metadata_propagates_to_fact_extractor() -> None:
    """``SessionPayload.metadata`` flows verbatim into ``extract(session_metadata=...)``.

    THE Lever A test — closing the SuiteRunner regression (C1 §1c) at
    the orchestrator layer. If the orchestrator drops the kwarg, every
    fact downstream loses its temporal anchor and LoCoMo recall
    halves.

    Sabotage-proof: in ``_process_session`` drop the
    ``session_metadata=session.metadata`` kwarg on the extractor call
    (replace with ``session_metadata=None``). Running this test then
    reports FAILED — the calls[0]['session_metadata'] is None. Restoring
    returns the test green. Verified live during authorship.
    """
    metadata = {"date_time": "2026-05-21 14:00", "session_id": "s-42"}
    session = SessionPayload(turns=(_turn("user", "hi"),), metadata=metadata)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    extractor = FakeFactExtractor(scripted_facts=[_fact("f-1")])

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert len(extractor.calls) == 1
    assert extractor.calls[0]["session_metadata"] == metadata


def test_session_metadata_none_passes_none_through() -> None:
    """Missing metadata → extractor sees session_metadata=None (no fabrication)."""
    session = SessionPayload(turns=(_turn("user", "hi"),))
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    extractor = FakeFactExtractor(scripted_facts=[])

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert extractor.calls[0]["session_metadata"] is None


def test_session_date_pin_appears_in_rendered_body() -> None:
    """Metadata with ``date_time`` → body carries the ``**Session date:**`` pin.

    Stream A Lever A's body convention — keeps the temporal anchor
    visible to the retrieval-side LLM even when the chunker drops
    frontmatter.
    """
    metadata = {"date_time": "2026-05-21 14:00"}
    session = SessionPayload(turns=(_turn("user", "hi"),), metadata=metadata)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert "**Session date:** 2026-05-21 14:00" in writer.writes[0]["rendered_body"]


def test_frontmatter_carries_date_time_when_metadata_has_it() -> None:
    """date_time round-trips into the frontmatter dict passed to the writer."""
    metadata = {"date_time": "2026-05-21 14:00"}
    session = SessionPayload(turns=(_turn("user", "hi"),), metadata=metadata)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert writer.writes[0]["frontmatter"]["date_time"] == "2026-05-21 14:00"


def test_frontmatter_omits_date_time_when_metadata_lacks_it() -> None:
    """No date_time in metadata → frontmatter has no date_time key."""
    session = SessionPayload(turns=(_turn("user", "hi"),))
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert "date_time" not in writer.writes[0]["frontmatter"]


# ---------------------------------------------------------------------------
# Section 5 — corpus_id IS the namespace.
# ---------------------------------------------------------------------------


def test_corpus_id_stamped_as_namespace_on_emitted_facts() -> None:
    """Every fact reaching ``fact_store.add`` carries namespace=corpus_id.

    Sabotage-proof: in ``_process_session`` replace
    ``_apply_namespace(fact, request.corpus_id)`` with
    ``_apply_namespace(fact, "shared")``. Running this test then
    reports FAILED — the stored fact's namespace is "shared", not
    "conv-special". Restoring returns the test green. Verified live
    during authorship.
    """
    sessions = (_make_session(n_turns=1),)
    extractor = FakeFactExtractor(
        scripted_facts=[
            FakeFactRecord(id="f-1", entity="x", attribute="y", value="z", namespace="default"),
        ]
    )
    fact_store = FakeFactStore()
    request = IngestRequest(sessions=sessions, corpus_id="conv-special", window_turns=1)

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=fact_store,
        fact_extractor=extractor,
    )

    stored = fact_store._facts["f-1"]
    assert stored.namespace == "conv-special"


# ---------------------------------------------------------------------------
# Section 6 — window_turns kwarg behaviour.
# ---------------------------------------------------------------------------


def test_window_turns_drives_extractor_windowing() -> None:
    """``window_turns=1`` over 3 turns → 3 extractor calls."""
    session = _make_session(n_turns=3)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1", window_turns=1)
    extractor = FakeFactExtractor()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert len(extractor.calls) == 3
    assert result.windows_extracted == 3


def test_window_turns_zero_collapses_to_one_window() -> None:
    """``window_turns=0`` → extractor sees the whole session in one call."""
    session = _make_session(n_turns=5)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1", window_turns=0)
    extractor = FakeFactExtractor()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert len(extractor.calls) == 1
    assert len(extractor.calls[0]["turns"]) == 5
    assert result.windows_extracted == 1


def test_zero_turn_session_does_not_invoke_extractor() -> None:
    """Empty session → no extractor call (defensive: no extractor crash on []."""
    session = SessionPayload(turns=())
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    extractor = FakeFactExtractor()

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert extractor.calls == []
    assert result.windows_extracted == 0
    assert result.turns_ingested == 0


# ---------------------------------------------------------------------------
# Section 7 — skipped_sessions: per-session error isolation.
# ---------------------------------------------------------------------------


class _RaisingFactExtractor:
    """Extractor that raises for ``raise_on_call``-th invocation (0-indexed)."""

    def __init__(self, raise_on_call: int) -> None:
        self._raise_on = raise_on_call
        self.calls = 0

    def extract(self, *, turns: list[dict[str, Any]], **_kwargs: Any) -> list[Any]:
        if self.calls == self._raise_on:
            self.calls += 1
            raise RuntimeError("kaboom — synthetic per-session failure")
        self.calls += 1
        return []


def test_skipped_sessions_records_indexes_for_failures() -> None:
    """One of three sessions raises → ``skipped_sessions=(1,)``, others continue.

    Sabotage-proof: remove the ``try / except`` in ``ingest_corpus``'s
    per-session loop. Running this test then reports FAILED — the
    extractor exception propagates out and the call raises before
    asserting. Restoring the guard returns the test green. Verified
    live during authorship.
    """
    sessions = (
        _make_session(n_turns=1, session_id="s-0"),
        _make_session(n_turns=1, session_id="s-1"),
        _make_session(n_turns=1, session_id="s-2"),
    )
    request = IngestRequest(sessions=sessions, corpus_id="conv-1", window_turns=1)
    extractor = _RaisingFactExtractor(raise_on_call=1)

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=extractor,
    )

    assert result.skipped_sessions == (1,)
    assert result.sessions_processed == 3  # all three reached the loop


def test_empty_request_yields_trivial_result() -> None:
    """IngestRequest with no sessions → zeros everywhere, no collaborator calls."""
    request = IngestRequest(sessions=(), corpus_id="conv-empty")
    writer = FakeDocumentWriter()
    embedder = FakeCorpusEmbedder(scripted_chunks_per_call=[99])  # would surface if invoked

    result = ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
        embedder=embedder,
    )

    assert result.corpus_id == "conv-empty"
    assert result.sessions_processed == 0
    assert result.turns_ingested == 0
    assert result.document_paths == ()
    assert result.windows_extracted == 0
    assert result.facts_added == 0
    assert result.facts_superseded == 0
    # Embedder still called once with empty tuple — the Protocol accepts ().
    assert embedder.calls == [()]
    assert result.chunks_indexed == 99
    assert writer.writes == []


# ---------------------------------------------------------------------------
# Section 8 — body-rendering edge cases.
# ---------------------------------------------------------------------------


def test_body_falls_back_to_speaker_text_keys() -> None:
    """Turns missing ``role``/``content`` use ``speaker``/``text`` as fallback.

    Covers the LoCoMo native turn shape (speaker / text) so adapters
    don't have to translate before constructing SessionPayload.
    """
    locomo_turn = {"speaker": "alice", "text": "hello there"}
    session = SessionPayload(turns=(locomo_turn,))
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert "**alice**: hello there" in writer.writes[0]["rendered_body"]


def test_body_handles_completely_missing_role_and_content() -> None:
    """Turn with neither role nor content → ``**unknown**:`` line (no crash)."""
    session = SessionPayload(turns=({},))
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert "**unknown**:" in writer.writes[0]["rendered_body"]


def test_metadata_evidence_at_treated_as_session_date_alias() -> None:
    """``metadata['evidence_at']`` falls back as the date pin source.

    Some adapters carry the date under ``evidence_at`` rather than
    ``date_time``; the orchestrator accepts either alias so neither
    side has to translate.
    """
    metadata = {"evidence_at": "2026-05-21"}
    session = SessionPayload(turns=(_turn("user", "hi"),), metadata=metadata)
    request = IngestRequest(sessions=(session,), corpus_id="conv-1")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    assert "**Session date:** 2026-05-21" in writer.writes[0]["rendered_body"]


def test_frontmatter_always_carries_turn_count_and_corpus_id() -> None:
    """Frontmatter unconditionally surfaces corpus_id + turn_count + session_id."""
    session = SessionPayload(turns=tuple(_turn("user", f"t-{i}") for i in range(4)), session_id="s-z")
    request = IngestRequest(sessions=(session,), corpus_id="conv-meta")
    writer = FakeDocumentWriter()

    ingest_corpus(
        request,
        paths=FakePaths(),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        document_writer=writer,
    )

    fm = writer.writes[0]["frontmatter"]
    assert fm["corpus_id"] == "conv-meta"
    assert fm["session_id"] == "s-z"
    assert fm["turn_count"] == 4
    assert "ingested_at" in fm  # ISO timestamp; we don't pin its value
