"""Integration: ingest-chat → facts_about resolvable-breadcrumb round-trip (PLA-261).

Composes the production write path (``kairix.use_cases.ingest_chat`` over a
real :class:`SQLiteFactStore`) with the production read path (the
``facts_about`` tool resolving the SAME SQLite db) and proves the actionable
breadcrumb survives the round-trip: a fact extracted from a conversation comes
back carrying a ``source_uri`` an agent can re-open — not just opaque turn-ids
(the recall→verify→act loop #467 broke).

The fact-extraction step uses ``FakeFactExtractor`` from ``tests/fakes.py``
(no LLM call); EVERYTHING else is composed production code — the ingest use
case, the SQLite-backed store, the read tool. The fact store is resolved by
the tool from ``paths`` (the production-wiring branch), so only the
hermetic summary leg is injected.

Marker rationale (``integration``): multi-component composition over a real
SQLite file under ``tmp_path``; no network, no LLM, no usearch.

Sabotage-proven (executed mutate→fail→restore); transcripts in the commit body.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.agents.mcp.tools.facts_about import tool_facts_about
from kairix.core.facts import SQLiteFactStore
from kairix.paths import KairixPaths
from kairix.use_cases.ingest_chat import ingest_chat
from tests.fakes import FakeDocumentRepository, FakeFactExtractor, FakeFactRecord

pytestmark = pytest.mark.integration


def _paths(tmp_path: Path) -> KairixPaths:
    """KairixPaths pinned under tmp_path — never reads env (F2-clean)."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _write_transcript(path: Path, conversation_id: str) -> None:
    """Write a one-conversation JSONL transcript carrying ``conversation_id``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    turns = [
        {"id": "t1", "conversation_id": conversation_id, "role": "user", "content": "Alice founded the company."},
        {"id": "t2", "conversation_id": conversation_id, "role": "assistant", "content": "Noted — Alice is founder."},
    ]
    path.write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def test_ingested_fact_is_recalled_with_resolvable_source_uri(tmp_path: Path) -> None:
    """Ingest a conversation, then ask ``facts_about`` and confirm the recalled
    fact carries the conversation document's re-openable ``source_uri``.

    Sabotage-proof (executed): drop the ``conversation_id=cid`` /
    ``conversation_source_uri=...`` kwargs from the
    ``_extract_facts_for_conversation`` call in ``ingest_chat`` → the
    persisted fact loses its breadcrumb, ``source_uri`` falls back to
    ``facts://<id>`` and the conversation-path assertion below fails.
    """
    paths = _paths(tmp_path)
    transcript = tmp_path / "alice.jsonl"
    _write_transcript(transcript, conversation_id="alice-chat")

    store = SQLiteFactStore(db_path=paths.db_path)
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    )

    result = ingest_chat(transcript, paths=paths, fact_store=store, fact_extractor=extractor)
    assert result.facts_added == 1

    # Read path: the tool resolves the real SQLiteFactStore from ``paths``
    # (only the hermetic summary leg is injected).
    out = tool_facts_about(entity="Alice", paths=paths, document_repo=FakeDocumentRepository())

    assert out["error"] == ""
    hit = out["hits"][0]
    assert hit["value"] == "founder"
    assert hit["conversation_id"] == "alice-chat"
    assert hit["source_uri"] == "04-Agent-Knowledge/conversations/alice-chat.md"
    # The conversation markdown the breadcrumb points at was actually written.
    assert (paths.document_root / "04-Agent-Knowledge" / "conversations" / "alice-chat.md").is_file()
