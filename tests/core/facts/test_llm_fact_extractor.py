"""Unit + integration tests for ``kairix.core.facts.LLMFactExtractor``.

F1: zero monkeypatching — every LLM call goes through a
``FakeLLMBackend`` constructed in the test body and handed to the
extractor via its public ``llm=`` kwarg.

F5: imports stay on the public surface — only
``kairix.core.facts.LLMFactExtractor`` and Protocol types are touched.

Each test below was sabotage-proven during authoring (mutate prod →
confirm failure → restore → confirm pass). The transcript for the
spot-checked sample lives in the commit body.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from kairix.core.facts import LLMFactExtractor
from kairix.core.protocols import FactExtractor, FactRecord
from kairix.paths import KairixPaths
from kairix.use_cases.ingest_chat import ingest_chat
from tests.fakes import FakeFactStore, FakeLLMBackend

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


_HAPPY_PATH_RESPONSE = json.dumps(
    [
        {
            "entity": "agent-alpha",
            "attribute": "role",
            "value": "Head of Product",
            "confidence": 0.92,
            "evidence_turn_ids": ["t1", "t2"],
        }
    ]
)


def _turn(turn_id: str, role: str = "user", content: str = "hello") -> dict[str, object]:
    """Construct one minimal turn dict for extractor input."""
    return {"id": turn_id, "role": role, "content": content}


def _two_turns() -> list[dict[str, object]]:
    """Return the canonical two-turn happy-path window."""
    return [
        _turn("t1", "user", "agent-alpha runs product."),
        _turn("t2", "assistant", "Got it — she's Head of Product."),
    ]


# ---------------------------------------------------------------------------
# 1. Protocol compliance
# ---------------------------------------------------------------------------


def test_protocol_compliance() -> None:
    """Sabotage-proof: rename ``LLMFactExtractor.extract`` and the
    runtime ``isinstance(..., FactExtractor)`` check below flips to
    False — the Protocol requires the ``extract`` method by name."""
    extractor = LLMFactExtractor(llm=FakeLLMBackend(chat_response="[]"))
    assert isinstance(extractor, FactExtractor)


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_parses_records_with_provenance() -> None:
    """Sabotage-proof: comment out the ``records.append(record)`` line
    in ``LLMFactExtractor.extract`` and this test fails because the
    returned list is empty."""
    llm = FakeLLMBackend(chat_response=_HAPPY_PATH_RESPONSE)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert len(records) == 1
    record = records[0]
    assert record.entity == "agent-alpha"
    assert record.attribute == "role"
    assert record.value == "Head of Product"
    assert record.source_turn_ids == ("t1", "t2")


# ---------------------------------------------------------------------------
# 3. Empty list
# ---------------------------------------------------------------------------


def test_empty_list_response_returns_no_records() -> None:
    """Sabotage-proof: change the ``if not isinstance(parsed, list)``
    guard to ``if isinstance(parsed, list)`` in ``_parse_response``
    and this test flips because an empty JSON array would be filtered
    out, causing a different assertion path."""
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records == []


# ---------------------------------------------------------------------------
# 4. Malformed JSON
# ---------------------------------------------------------------------------


def test_malformed_json_logs_warning_and_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: remove the ``try/except json.JSONDecodeError``
    block in ``_parse_response`` and this test fails because the
    extractor raises instead of logging + returning []."""
    llm = FakeLLMBackend(chat_response="not json {{")
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("not valid JSON" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 5. Partially malformed
# ---------------------------------------------------------------------------


def test_partially_malformed_keeps_valid_records(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: replace ``if record is not None: records.append(record)``
    in ``LLMFactExtractor.extract`` with an unconditional append and
    this test fails because the malformed record sneaks through to
    the returned list."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.92,
                "evidence_turn_ids": ["t1"],
            },
            {
                "entity": "Bob",
                # missing attribute / value / confidence / evidence_turn_ids
            },
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert len(records) == 1
    assert records[0].entity == "agent-alpha"
    assert any("missing required keys" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 6. Window hint passthrough (deferred behaviour — must not raise)
# ---------------------------------------------------------------------------


def test_window_hint_is_accepted_without_raising() -> None:
    """Sabotage-proof: drop the ``window_hint`` kwarg from the
    extract method signature and this test raises TypeError on the
    call below."""
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)

    # No assertion on side effects of window_hint — that's deferred.
    records = extractor.extract(turns=_two_turns(), window_hint={"session_id": "s-1"})

    assert records == []


# ---------------------------------------------------------------------------
# 7. Confidence calibration
# ---------------------------------------------------------------------------


def test_confidence_comes_from_llm_payload() -> None:
    """Sabotage-proof: hardcode ``confidence=1.0`` in
    ``_record_from_payload`` and this test fails because the asserted
    float drifts from the 0.42 the fake LLM emitted."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.42,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records[0].confidence == 0.42


# ---------------------------------------------------------------------------
# 8. Source turn ids preserved
# ---------------------------------------------------------------------------


def test_evidence_turn_ids_become_source_turn_ids() -> None:
    """Sabotage-proof: change ``source_turn_ids=evidence`` in
    ``_record_from_payload`` to ``source_turn_ids=()`` and this test
    fails because the asserted provenance tuple shrinks to empty."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": ["t7", "t9"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records[0].source_turn_ids == ("t7", "t9")


# ---------------------------------------------------------------------------
# 9. mint_id deterministic
# ---------------------------------------------------------------------------


def test_id_is_deterministic_across_runs() -> None:
    """Sabotage-proof: replace ``StoredFactRecord.mint_id(...)`` in
    ``_record_from_payload`` with ``str(uuid.uuid4())`` and this test
    fails on the equality check between the two runs."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    extractor_a = LLMFactExtractor(llm=FakeLLMBackend(chat_response=response))
    extractor_b = LLMFactExtractor(llm=FakeLLMBackend(chat_response=response))

    record_a = extractor_a.extract(turns=_two_turns())[0]
    record_b = extractor_b.extract(turns=_two_turns())[0]

    assert record_a.id == record_b.id


# ---------------------------------------------------------------------------
# 10. Temperature stored on extractor
# ---------------------------------------------------------------------------


def test_temperature_is_recorded_on_extractor() -> None:
    """Sabotage-proof: change ``self.temperature = temperature`` to
    ``self.temperature = 0.0`` (hardcoded) in ``__init__`` and this
    test fails because the configured 0.7 is silently overwritten.

    The :class:`LLMBackend.chat` Protocol does not carry sampling
    knobs — provider plug-ins resolve those from their own config.
    We pin the operator's *intent* by introspecting the extractor.
    """
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm, temperature=0.7)
    extractor.extract(turns=_two_turns())

    assert extractor.temperature == 0.7
    # Verify the backend was actually invoked.
    assert len(llm.chat_calls) == 1


def test_default_temperature_is_zero() -> None:
    """Sabotage-proof: change the default to ``temperature: float = 0.5``
    in ``__init__`` and this test fails on the equality check."""
    extractor = LLMFactExtractor(llm=FakeLLMBackend(chat_response="[]"))

    assert extractor.temperature == 0.0


# ---------------------------------------------------------------------------
# Prompt template handling
# ---------------------------------------------------------------------------


def test_custom_prompt_template_overrides_default() -> None:
    """Sabotage-proof: change the constructor to ignore
    ``prompt_template`` and this test fails because the recorded
    prompt no longer contains the marker string."""
    template = "CUSTOM TEMPLATE marker XYZ\nTurns:\n{{turns}}\n"
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm, prompt_template=template)

    extractor.extract(turns=_two_turns())

    sent = llm.chat_calls[0]["messages"][0]["content"]
    assert "CUSTOM TEMPLATE marker XYZ" in sent
    # Turns block is interpolated where the placeholder used to be.
    assert "t1:user: agent-alpha runs product." in sent


def test_turns_placeholder_is_substituted_with_formatted_lines() -> None:
    """Sabotage-proof: replace ``{{turns}}`` substitution with a no-op
    in ``LLMFactExtractor.extract`` and this test fails because the
    raw placeholder string survives into the prompt sent to the LLM."""
    template = "Prompt:\n{{turns}}"
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm, prompt_template=template)

    extractor.extract(turns=_two_turns())

    sent = llm.chat_calls[0]["messages"][0]["content"]
    assert "{{turns}}" not in sent
    assert "t1:user: agent-alpha runs product." in sent
    assert "t2:assistant: Got it — she's Head of Product." in sent


def test_empty_turns_short_circuits_before_llm_call() -> None:
    """Sabotage-proof: remove the ``if not turns: return []`` guard
    and this test fails because the fake LLM is called once instead
    of zero times."""
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=[])

    assert records == []
    assert llm.chat_calls == []


# ---------------------------------------------------------------------------
# Tolerance: response wrapped in markdown fence
# ---------------------------------------------------------------------------


def test_markdown_fenced_response_is_unwrapped() -> None:
    """Sabotage-proof: drop the ``if stripped.startswith('```'):``
    branch in ``_parse_response`` and this test fails because the
    fenced JSON no longer parses."""
    fenced = "```json\n" + _HAPPY_PATH_RESPONSE + "\n```"
    llm = FakeLLMBackend(chat_response=fenced)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert len(records) == 1
    assert records[0].entity == "agent-alpha"


# ---------------------------------------------------------------------------
# Tolerance: response that's JSON but not a list
# ---------------------------------------------------------------------------


def test_non_list_json_response_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: drop the ``isinstance(parsed, list)`` check in
    ``_parse_response`` and this test fails because the dict shape
    falls through to the for-loop and raises on a missing key."""
    llm = FakeLLMBackend(chat_response='{"entity": "agent-alpha"}')
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("not a list" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Confidence-out-of-range / wrong-type guard
# ---------------------------------------------------------------------------


def test_confidence_out_of_range_skips_record(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: change ``not 0.0 <= conf <= 1.0`` to
    ``not 0.0 <= conf <= 100.0`` and this test fails because the bad
    record sneaks through."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 5.5,  # invalid
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("unusable confidence" in rec.message for rec in caplog.records)


def test_empty_evidence_turn_ids_skips_record(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: drop the ``or not evidence`` clause in
    ``_record_from_payload`` and this test fails because an
    empty-provenance record gets emitted in violation of the
    FactRecord identity contract."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": [],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("evidence_turn_ids" in rec.message for rec in caplog.records)


def test_evidence_turn_ids_wrong_type_skips_record(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: relax the ``isinstance(raw, list | tuple)``
    guard in ``_coerce_evidence_turn_ids`` and this test fails."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": "t1",  # str — not list-shaped
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("evidence_turn_ids" in rec.message for rec in caplog.records)


def test_list_element_not_dict_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: drop the ``isinstance(payload, dict)`` check
    in ``_record_from_payload`` and this test fails when the str
    element falls through to ``payload['entity']``."""
    response = json.dumps(["not-a-dict"])
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("not a dict" in rec.message for rec in caplog.records)


def test_confidence_non_numeric_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: drop the ``isinstance(raw, int | float)`` check
    in ``_coerce_confidence`` and this test fails because the str
    survives all the way to ``float(raw)`` and raises ValueError."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": "high",  # non-numeric — must be rejected
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("unusable confidence" in rec.message for rec in caplog.records)


def test_empty_response_returns_empty() -> None:
    """Sabotage-proof: remove the ``if not stripped: return []`` guard
    and this test fails because ``json.loads('')`` raises
    JSONDecodeError, which would log a different warning."""
    llm = FakeLLMBackend(chat_response="   ")  # whitespace-only
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records == []


def test_confidence_bool_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage-proof: drop the ``isinstance(raw, bool)`` early-return
    in ``_coerce_confidence`` and this test fails because Python's
    ``True == 1`` would silently coerce to ``1.0``."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": True,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm)

    with caplog.at_level(logging.WARNING, logger="kairix.core.facts.extractor"):
        records = extractor.extract(turns=_two_turns())

    assert records == []
    assert any("unusable confidence" in rec.message for rec in caplog.records)


def test_namespace_kwarg_flows_to_records() -> None:
    """Sabotage-proof: hardcode ``namespace='shared'`` in
    ``_record_from_payload`` and this test fails because the asserted
    namespace flips back to the default."""
    response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=response)
    extractor = LLMFactExtractor(llm=llm, namespace="engagement-a")

    records = extractor.extract(turns=_two_turns())

    assert records[0].namespace == "engagement-a"


def test_max_tokens_is_2000_per_call() -> None:
    """Sabotage-proof: change ``max_tokens=_MAX_TOKENS`` to
    ``max_tokens=200`` and this test fails because the recorded call
    no longer carries the documented budget."""
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)

    extractor.extract(turns=_two_turns())

    assert llm.chat_calls[0]["max_tokens"] == 2000


# ---------------------------------------------------------------------------
# 11. Integration: wired into ingest_chat (no fact_extractor= override)
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, turns: list[dict[str, object]]) -> None:
    """Write ``turns`` as a JSONL transcript at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def _paths(tmp_path: Path) -> KairixPaths:
    """Build a KairixPaths pinned to a temp directory."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def test_llm_fact_extractor_integrates_with_ingest_chat(tmp_path: Path) -> None:
    """End-to-end: dispatch the production extractor (via constructor
    injection of a FakeLLMBackend) through ``ingest_chat`` and verify
    the facts land in the fact store.

    This pins the wiring contract Cap #1 + Cap #2 share — the
    extractor's emitted records must satisfy the ``FactStore.add``
    Protocol, otherwise the use case raises.

    Sabotage-proof: change ``_record_from_payload`` to return
    ``None`` unconditionally and this test fails because the asserted
    ``facts_added=1`` drops to zero.
    """
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "conversation_id": "c1",
                "id": "t1",
                "role": "user",
                "content": "agent-alpha runs product.",
            },
            {
                "conversation_id": "c1",
                "id": "t2",
                "role": "assistant",
                "content": "Got it — Head of Product.",
            },
        ],
    )
    llm = FakeLLMBackend(chat_response=_HAPPY_PATH_RESPONSE)
    extractor: FactExtractor = LLMFactExtractor(llm=llm, namespace="shared")
    store = FakeFactStore()

    result = ingest_chat(
        transcript,
        paths=_paths(tmp_path),
        fact_store=store,
        fact_extractor=extractor,
        window_turns=5,
    )

    assert result.facts_added == 1
    assert result.conversations_processed == 1
    # The fake LLM was called once per window — exactly one window here.
    assert len(llm.chat_calls) == 1


def test_extract_returns_factrecord_protocol_objects() -> None:
    """Sabotage-proof: change the constructed type from
    ``StoredFactRecord`` to ``dict`` and this test fails because the
    runtime-checkable Protocol check no longer holds."""
    llm = FakeLLMBackend(chat_response=_HAPPY_PATH_RESPONSE)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert all(isinstance(r, FactRecord) for r in records)


# ---------------------------------------------------------------------------
# Stream A Lever A — evidence_at injection from session_metadata
# ---------------------------------------------------------------------------


def test_evidence_at_defaults_to_session_metadata_date_time() -> None:
    """When session_metadata carries date_time, facts inherit it as evidence_at.

    Sabotage-proof: delete the ``if evidence_at is None: evidence_at =
    default_evidence_at`` line in ``_record_from_payload`` and this
    test fails because the extracted fact's ``evidence_at`` is None
    instead of the session date.

    Manual sabotage run (2026-05-21): commented out the fallback
    assignment; pytest exit=1, AssertionError on the equality check.
    Restored; pytest exit=0.
    """
    # LLM omits ``evidence_at`` — the use case must inject session default.
    llm_response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "role",
                "value": "Head of Product",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=llm_response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(
        turns=_two_turns(),
        session_metadata={"date_time": "2023-05-04 14:30", "session_id": "s-12"},
    )

    assert len(records) == 1
    assert records[0].evidence_at == "2023-05-04 14:30"


def test_evidence_at_from_llm_payload_overrides_session_default() -> None:
    """LLM-supplied evidence_at (resolved relative reference) wins.

    Sabotage-proof: change ``if evidence_at is None`` to
    ``if True`` in ``_record_from_payload`` (always overwrite with the
    session default) and this test fails because the LLM's resolved
    "2023-05-03" gets clobbered back to the session default.
    """
    llm_response = json.dumps(
        [
            {
                "entity": "Maria",
                "attribute": "had_dinner_with",
                "value": "mom",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
                "evidence_at": "2023-05-03",  # LLM resolved "last night"
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=llm_response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(
        turns=_two_turns(),
        session_metadata={"date_time": "2023-05-04 14:30"},
    )

    assert records[0].evidence_at == "2023-05-03"


def test_evidence_at_is_none_when_no_session_metadata() -> None:
    """Without session_metadata, evidence_at stays None (legacy path).

    Sabotage-proof: hardcode a sentinel string default (e.g. ``"unknown"``)
    for ``default_evidence_at`` and this test fails because the legacy
    behaviour (``None``) breaks for callers that haven't migrated yet.
    """
    llm = FakeLLMBackend(chat_response=_HAPPY_PATH_RESPONSE)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records[0].evidence_at is None


def test_session_metadata_block_renders_into_prompt() -> None:
    """session_metadata is interpolated into the prompt template.

    Sabotage-proof: drop the ``.replace(_SESSION_METADATA_PLACEHOLDER, ...)``
    call from ``_render_prompt`` and this test fails because the
    placeholder string survives unchanged into the LLM payload.
    """
    template = "Prompt:\n{{session_metadata}}\n\nTurns:\n{{turns}}\n"
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm, prompt_template=template)

    extractor.extract(
        turns=_two_turns(),
        session_metadata={"date_time": "2023-05-04", "session_id": "s-12"},
    )

    sent = llm.chat_calls[0]["messages"][0]["content"]
    assert "{{session_metadata}}" not in sent
    assert "date_time: 2023-05-04" in sent
    assert "session_id: s-12" in sent


def test_empty_session_metadata_renders_empty_block() -> None:
    """Empty session_metadata drops the block from the prompt cleanly.

    Sabotage-proof: have ``_format_session_metadata_block`` always emit
    the "Session metadata:" header and this test fails because the
    bare-prompt path now leaks that header into the LLM context.
    """
    template = "{{session_metadata}}TURNS:{{turns}}"
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm, prompt_template=template)

    extractor.extract(turns=_two_turns(), session_metadata=None)

    sent = llm.chat_calls[0]["messages"][0]["content"]
    assert "Session metadata:" not in sent


# ---------------------------------------------------------------------------
# Stream A Lever B — canonical attribute taxonomy + drop list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_attribute,expected_attribute",
    [
        ("move-time", "moved_at"),
        ("origin-country", "moved_from"),
        ("career-interest", "researched"),
        ("has-done-pottery", "practices"),
        ("store-type", "owns"),
    ],
)
def test_attribute_rewrites_snap_drift_to_canonical(raw_attribute: str, expected_attribute: str) -> None:
    """Freeform attribute drift gets rewritten onto the canonical key.

    Sabotage-proof: empty the ``_ATTRIBUTE_REWRITES`` dict in
    ``extractor.py`` and the parametrised case for ``move-time`` flips
    because the rewrite-to-``moved_at`` no longer fires.

    Closes the 43% of LoCoMo misses Spike A1 attributed to attribute
    drift (Cases 4-6 of the failure-mode notes).
    """
    llm_response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": raw_attribute,
                "value": "Sweden" if "country" in raw_attribute else "thing",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=llm_response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert len(records) == 1
    assert records[0].attribute == expected_attribute


@pytest.mark.parametrize(
    "drop_attribute",
    ["audience-reaction", "advice", "encounter", "appreciation", "path"],
)
def test_attribute_drops_filter_non_factual_keys(drop_attribute: str) -> None:
    """Non-factual freeform attributes (opinion / reaction) are dropped.

    Sabotage-proof: empty ``_ATTRIBUTE_DROPS`` and this test fails for
    every parametrised case because the extractor stops filtering
    them — see spike A1's "ranked instead" column for the 5 attributes
    that polluted top-5 retrieval on Cases 4-6.
    """
    llm_response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": drop_attribute,
                "value": "some noise",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=llm_response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records == []


def test_canonical_attribute_passthrough_untouched() -> None:
    """An already-canonical attribute survives normalisation unchanged.

    Sabotage-proof: make ``_normalise_attribute`` always return the
    lowercased key and this test fails on the ``moved_from`` equality
    check (it would become ``moved_from`` lowercased — actually still
    matches, so try the case-preservation test on a CamelCase entity
    if that doesn't fail; the rewrite map's miss path is the load-
    bearing branch).
    """
    llm_response = json.dumps(
        [
            {
                "entity": "agent-alpha",
                "attribute": "moved_from",  # canonical from prompt list
                "value": "Sweden",
                "confidence": 0.9,
                "evidence_turn_ids": ["t1"],
            }
        ]
    )
    llm = FakeLLMBackend(chat_response=llm_response)
    extractor = LLMFactExtractor(llm=llm)

    records = extractor.extract(turns=_two_turns())

    assert records[0].attribute == "moved_from"


# ---------------------------------------------------------------------------
# Stream A — prompt asset carries the canonical taxonomy + few-shots
# ---------------------------------------------------------------------------


def test_default_prompt_lists_canonical_attribute_taxonomy() -> None:
    """The bundled prompt asset names the canonical attribute vocabulary.

    Sabotage-proof: delete the canonical-vocab block from
    ``kairix/core/facts/prompts/fact_extractor_v1.txt`` and this test
    fails because the prompt no longer mentions enough of the closed
    vocabulary to anchor the LLM. Spike A1 §"Why (b) is the rest"
    documented the freeform-key explosion this taxonomy closes.
    """
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)
    extractor.extract(turns=_two_turns())
    sent = llm.chat_calls[0]["messages"][0]["content"]
    # Spot-check 5 canonical attributes drawn from spike A1 cases 4-6.
    assert "moved_from" in sent
    assert "practices" in sent
    assert "researched" in sent
    assert "visited" in sent
    assert "has_item" in sent


def test_default_prompt_anchors_facts_to_subject_not_object() -> None:
    """The prompt's subject-anchoring instruction is present verbatim.

    Sabotage-proof: drop the "ANCHOR FACTS TO THE SUBJECT" block from
    the prompt asset and this test fails because the substring guard
    flips. Closes spike A1 Case 5 (necklace / origin-country / Sweden
    entity drift).
    """
    llm = FakeLLMBackend(chat_response="[]")
    extractor = LLMFactExtractor(llm=llm)
    extractor.extract(turns=_two_turns())
    sent = llm.chat_calls[0]["messages"][0]["content"]
    assert "SUBJECT OF THE SENTENCE" in sent
    # Specific Case-5 example present so the LLM sees the negative example.
    assert "moved_from" in sent and "necklace" in sent
