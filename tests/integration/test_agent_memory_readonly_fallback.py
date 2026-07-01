"""Outcome tests — agent writes survive a read-only document root (PLA-296).

On the stock compose the document root mounts read-only and only
``04-Agent-Knowledge`` is a separate writable overlay. When that overlay is
also read-only for the agent's uid, both ``memory_write`` and ``ingest_chat``
used to die with EACCES. These outcome tests drive the composed production path
against a REAL read-only ``04-Agent-Knowledge`` and prove the write now falls
back to the writable data dir AND stays BM25-searchable:

  - ``memory_write`` (MCP handler) → the memory lands in the fallback and a BM25
    MATCH over the real index finds it immediately;
  - ``ingest_chat`` (MCP handler) → the conversation lands in the namespaced
    fallback and a BM25 MATCH finds it.

Both use the real index step (``RememberDeps`` / ``ingest_chat`` defaults) and a
real ``probe_write_access`` — only the document root, db path, and fallback root
are pinned under ``tmp_path`` (F2-clean, no ``KAIRIX_*`` env).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from kairix.agents.mcp.tools.ingest_chat import tool_ingest_chat
from kairix.agents.mcp.tools.memory_write import tool_memory_write
from kairix.use_cases.remember import RememberDeps
from tests.fakes import FakeFactExtractor, FakeFactStore, FakePaths

pytestmark = pytest.mark.integration


def _require_enforced_readonly_or_skip(directory: Path) -> None:
    """Strip write perms from ``directory``; skip when the platform won't enforce it."""
    if os.geteuid() == 0:
        pytest.skip("read-only perms are not enforced as root")
    os.chmod(directory, 0o500)
    probe = directory / ".probe-write"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return
    probe.unlink()
    os.chmod(directory, 0o700)
    pytest.skip("filesystem does not enforce read-only directory permissions")


def _bm25_hit(db_path: Path, term: str) -> bool:
    """True when a BM25 MATCH for ``term`` finds a row in the real index."""
    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(
            "SELECT 1 FROM documents_fts WHERE documents_fts MATCH ? LIMIT 1",
            (term,),
        ).fetchone()
    finally:
        db.close()
    return row is not None


_CONFIG = {
    "agents": {
        "agent-alpha": {
            "harness": "claude-code",
            "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
        }
    }
}


def test_memory_write_falls_back_and_is_searchable(tmp_path: Path) -> None:
    """memory_write on a read-only overlay → memory saved in the fallback and
    found by BM25 immediately (the #677 acceptance path).

    Sabotage (executed): reverted ``remember`` to write straight to the
    preferred dir → the write failed with WriteFailed and the ``error == ""``
    assertion failed; restored.
    """
    doc_root = tmp_path / "vault"
    agent_dir = doc_root / "04-Agent-Knowledge" / "agent-alpha"
    agent_dir.mkdir(parents=True)
    _require_enforced_readonly_or_skip(agent_dir)
    fallback_root = tmp_path / "data" / "agent-memory"
    db_path = tmp_path / "index.sqlite"

    try:
        response = tool_memory_write(
            agent="agent-alpha",
            content="decision: adopt the quokka-lighthouse rollout cadence",
            kind="decision",
            deps=RememberDeps(
                config_fn=lambda: _CONFIG,
                document_root_fn=lambda: doc_root,
                db_path_fn=lambda: db_path,
                memory_fallback_root_fn=lambda: fallback_root,
            ),
        )
    finally:
        os.chmod(agent_dir, 0o700)

    assert response["error"] == "", f"expected fallback success, got: {response}"
    written = Path(response["path"])
    assert written.parent == fallback_root / "agent-alpha", f"memory should land in the fallback, got {written}"
    assert written.exists()
    assert response["indexed"] is True
    assert _bm25_hit(db_path, "quokka"), "BM25 must find the fallback memory immediately"


def test_ingest_chat_falls_back_and_is_searchable(tmp_path: Path) -> None:
    """ingest_chat on a read-only overlay → conversation saved in the namespaced
    fallback and found by BM25 (the #677 acceptance path for conversations).

    Sabotage: drop the fallback index step in ``ingest_chat`` → the conversation
    is written but the BM25 assertion fails (worker never scans the fallback).
    """
    doc_root = tmp_path / "vault"
    ak_dir = doc_root / "04-Agent-Knowledge"
    ak_dir.mkdir(parents=True)
    _require_enforced_readonly_or_skip(ak_dir)
    fallback_root = tmp_path / "data" / "agent-memory"
    db_path = tmp_path / "index.sqlite"
    paths = FakePaths(document_root=doc_root, db_path=db_path, workspace_root=tmp_path / "ws")

    jsonl = '{"conversation_id": "conv-xyz", "role": "user", "content": "remember the marsupial-beacon-tempo plan"}\n'
    try:
        out = tool_ingest_chat(
            jsonl_content=jsonl,
            conversation_id="conv-xyz",
            namespace="engagement-alpha",
            allowed_namespace="engagement-alpha",
            paths=paths,
            fact_store=FakeFactStore(),
            fact_extractor=FakeFactExtractor(),
            no_extract=True,
            memory_fallback_root=fallback_root,
        )
    finally:
        os.chmod(ak_dir, 0o700)

    assert out["error"] == "", f"expected fallback success, got: {out}"
    conv_file = fallback_root / "engagement-alpha" / "conversations" / "conv-xyz.md"
    assert conv_file.exists(), f"conversation should land in the namespaced fallback, got missing {conv_file}"
    assert _bm25_hit(db_path, "marsupial"), "BM25 must find the fallback conversation"
