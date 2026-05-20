"""Unit tests for ``tool_ingest_chat`` — the agent-driven ingest MCP tool.

These tests pin the contract from the agent's point of view:

  - Happy path returns the IngestChatResult counts plus the agent's
    ``namespace`` and ``conversation_id`` echoed back.
  - Cross-engagement namespace calls are rejected with a structured
    envelope (no facts written, no markdown written, no exception).
  - Empty inputs are rejected before any I/O.
  - Failures inside the use case are caught and surfaced via ``error``.

Every test carries a ``# Sabotage:`` note describing a concrete change
to the production code that would falsify the test.

F1-clean: every collaborator (paths, fact_store, fact_extractor) is
passed through the constructor seam — no monkeypatching, no
attribute-reassignment, no ``@patch``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.agents.mcp.tools.ingest_chat import (
    ERROR_CROSS_ENGAGEMENT,
    ERROR_INVALID_INPUT,
    tool_ingest_chat,
)
from kairix.paths import KairixPaths
from tests.fakes import FakeFactExtractor, FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.unit


def _paths(tmp_path: Path) -> KairixPaths:
    """Per-test KairixPaths pinned under ``tmp_path`` (hermetic)."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _jsonl(conversation_id: str, n_turns: int) -> str:
    """Compose ``n_turns`` JSONL lines for ``conversation_id``."""
    return (
        "\n".join(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "role": "user" if t % 2 == 0 else "assistant",
                    "content": f"turn {t}",
                }
            )
            for t in range(n_turns)
        )
        + "\n"
    )


def test_happy_path_returns_counts_and_writes_markdown(tmp_path: Path) -> None:
    """A well-formed call writes the conversation chunk and reports the counts.

    Sabotage: remove the ``ingest_chat(...)`` call from tool_ingest_chat and
    return a hand-rolled dict → markdown file would not exist on disk and
    the ``Path.exists()`` assertion below fails.
    """
    paths = _paths(tmp_path)
    store = FakeFactStore()
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    )

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-01", n_turns=5),
        conversation_id="conv-01",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )

    assert out["error"] == ""
    assert out["namespace"] == "engagement-alpha"
    assert out["conversation_id"] == "conv-01"
    assert out["turns_ingested"] == 5
    assert out["facts_added"] >= 1  # one scripted fact per window; 5 turns → 1 window
    assert out["windows_extracted"] == 1
    # Markdown chunk was actually persisted to disk.
    chunk = paths.document_root / "conversations" / "conv-01.md"
    assert chunk.exists(), f"expected markdown at {chunk}"


def test_cross_engagement_namespace_is_rejected(tmp_path: Path) -> None:
    """A namespace that doesn't match ``allowed_namespace`` is rejected.

    Sabotage: drop the ``if namespace != allowed_namespace:`` check from
    tool_ingest_chat → the call proceeds, facts get written, and the
    ``store._facts == {}`` assertion below fails.
    """
    paths = _paths(tmp_path)
    store = FakeFactStore()
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    )

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-01", n_turns=5),
        conversation_id="conv-01",
        namespace="engagement-beta",  # different from allowed
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )

    assert out["error"] == ERROR_CROSS_ENGAGEMENT
    assert out["turns_ingested"] == 0
    assert out["facts_added"] == 0
    # No side effects: store untouched, no markdown directory created.
    assert store._facts == {}
    assert not (paths.document_root / "conversations").exists()


def test_empty_jsonl_content_is_rejected(tmp_path: Path) -> None:
    """An empty ``jsonl_content`` short-circuits before any I/O.

    Sabotage: remove the ``if not jsonl_content:`` guard → the tool writes
    an empty tmp file, ingest_chat runs against zero turns, and the
    ``out["error"] == ERROR_INVALID_INPUT`` assertion below fails (the call
    succeeds with error="").
    """
    out = tool_ingest_chat(
        jsonl_content="",
        conversation_id="conv-01",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert out["error"] == ERROR_INVALID_INPUT
    assert out["turns_ingested"] == 0
    # No tmp file was created under the workspace.
    assert not (_paths(tmp_path).workspace_root / "ingest_chat").exists()


def test_empty_conversation_id_is_rejected(tmp_path: Path) -> None:
    """An empty ``conversation_id`` short-circuits before any I/O.

    Sabotage: remove the ``if not conversation_id:`` guard → the tool
    proceeds and either writes a tmp file with a uuid-only name, or the
    use case writes a chunk at conversations/.md. Either way the
    ``out["error"] == ERROR_INVALID_INPUT`` assertion below fails.
    """
    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-01", n_turns=2),
        conversation_id="",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert out["error"] == ERROR_INVALID_INPUT
    assert out["facts_added"] == 0


def test_namespace_is_propagated_to_facts(tmp_path: Path) -> None:
    """The agent's namespace is stamped onto every persisted fact.

    Sabotage: drop the ``namespace=namespace`` kwarg from the
    ``ingest_chat(...)`` call in tool_ingest_chat → facts persist with the
    default ``"shared"`` namespace and the assertion below fails.
    """
    paths = _paths(tmp_path)
    store = FakeFactStore()
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    )

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-02", n_turns=5),
        conversation_id="conv-02",
        namespace="engagement-gamma",
        allowed_namespace="engagement-gamma",
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )

    assert out["error"] == ""
    # The fake fact was stamped with engagement-gamma at persistence time.
    persisted = list(store._facts.values())
    assert persisted, "expected at least one fact persisted"
    assert all(f.namespace == "engagement-gamma" for f in persisted)


def test_no_extract_mode_skips_facts(tmp_path: Path) -> None:
    """``no_extract=True`` writes the chunk but persists zero facts.

    Sabotage: drop the ``no_extract=no_extract`` kwarg from the
    ingest_chat call in tool_ingest_chat → the extractor runs anyway and
    the ``out["facts_added"] == 0`` assertion below fails.
    """
    paths = _paths(tmp_path)
    store = FakeFactStore()
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity="Alice", attribute="role", value="founder")]
    )

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-03", n_turns=5),
        conversation_id="conv-03",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
        no_extract=True,
    )

    assert out["error"] == ""
    assert out["facts_added"] == 0
    assert out["windows_extracted"] == 0
    # Chunk still written even without extraction.
    assert (paths.document_root / "conversations" / "conv-03.md").exists()
