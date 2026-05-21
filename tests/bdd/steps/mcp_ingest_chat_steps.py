"""Step definitions for mcp_ingest_chat.feature.

Drives ``tool_ingest_chat`` end-to-end with fakes injected from
``tests/fakes.py``. F1-clean: every collaborator (paths, fact_store,
fact_extractor) is passed as a kwarg — no monkeypatching. F13-clean:
scenarios reference agent concepts (transcript, namespace, ingest
response), never implementation symbols.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.tools.ingest_chat import tool_ingest_chat
from kairix.paths import KairixPaths
from tests.fakes import FakeFactExtractor, FakeFactStore

pytestmark = pytest.mark.bdd


@dataclass
class _State:
    """Per-scenario state — fresh on every scenario."""

    document_root: Path
    workspace_root: Path
    db_path: Path
    log_dir: Path
    allowed_namespace: str = ""
    transcript_content: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    fact_store: FakeFactStore = field(default_factory=FakeFactStore)
    fact_extractor: FakeFactExtractor = field(default_factory=FakeFactExtractor)


@pytest.fixture
def _mcp_ingest_state(tmp_path: Path) -> _State:
    return _State(
        document_root=tmp_path / "vault",
        workspace_root=tmp_path / "workspaces",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
    )


def _paths_from(state: _State) -> KairixPaths:
    return KairixPaths(
        document_root=state.document_root,
        db_path=state.db_path,
        log_dir=state.log_dir,
        workspace_root=state.workspace_root,
    )


@given(parsers.parse('the agent is scoped to namespace "{ns}"'))
def _agent_scoped(_mcp_ingest_state: _State, ns: str) -> None:
    _mcp_ingest_state.allowed_namespace = ns


@given(parsers.parse('the agent has a transcript with {n:d} turns about "{subject}"'))
def _transcript(_mcp_ingest_state: _State, n: int, subject: str) -> None:
    lines = [
        json.dumps(
            {
                "conversation_id": "conv-bdd",
                "role": "user" if t % 2 == 0 else "assistant",
                "content": f"turn {t} about {subject}",
            }
        )
        for t in range(n)
    ]
    _mcp_ingest_state.transcript_content = "\n".join(lines) + "\n"


@when(parsers.parse('the agent calls ingest-chat with namespace "{ns}"'))
def _agent_calls_ingest(_mcp_ingest_state: _State, ns: str) -> None:
    _mcp_ingest_state.response = tool_ingest_chat(
        jsonl_content=_mcp_ingest_state.transcript_content,
        conversation_id="conv-bdd",
        namespace=ns,
        allowed_namespace=_mcp_ingest_state.allowed_namespace,
        paths=_paths_from(_mcp_ingest_state),
        fact_store=_mcp_ingest_state.fact_store,
        fact_extractor=_mcp_ingest_state.fact_extractor,
    )


@then(parsers.parse("the ingest response reports {n:d} turns ingested"))
def _then_turns(_mcp_ingest_state: _State, n: int) -> None:
    assert _mcp_ingest_state.response["turns_ingested"] == n


@then(parsers.parse('the ingest response namespace is "{ns}"'))
def _then_namespace(_mcp_ingest_state: _State, ns: str) -> None:
    assert _mcp_ingest_state.response["namespace"] == ns


@then("the ingest response error is empty")
def _then_no_error(_mcp_ingest_state: _State) -> None:
    assert _mcp_ingest_state.response["error"] == ""


@then(parsers.parse('the ingest response error contains "{token}"'))
def _then_error_contains(_mcp_ingest_state: _State, token: str) -> None:
    assert token in _mcp_ingest_state.response["error"], (
        f"expected {token!r} in error, got {_mcp_ingest_state.response['error']!r}"
    )
