"""Step definitions for mcp_memory_write.feature (#472).

Drives ``tool_memory_write`` — the MCP surface over the same
``remember`` use case the CLI calls — with deps injected through the
``RememberDeps`` seam. F1-clean: no monkeypatching. F2-clean: no env
vars. F13-clean: scenarios speak in agent/memory language, never
implementation symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.tools.memory_write import tool_memory_write
from kairix.paths import WriteAccessProbe
from kairix.use_cases.remember import RememberDeps

pytestmark = pytest.mark.bdd

_BDD_NOW = datetime(2026, 6, 11, 11, 0, tzinfo=timezone.utc)


@dataclass
class _MemoryWriteState:
    """Per-scenario state — fresh on every scenario."""

    document_root: Path
    db_path: Path
    fallback_root: Path
    config: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    # Models whether immediate search indexing can complete. False mirrors a
    # still-warming kairix: the file is written and the memory is queued for
    # the next indexing pass rather than the write being refused (PLA-257).
    index_ready: bool = True
    # PLA-296 — when True the preferred overlay is read-only, so the write must
    # fall back to the writable data dir instead of crashing.
    overlay_readonly: bool = False


@pytest.fixture
def _memory_write_state(tmp_path: Path) -> _MemoryWriteState:
    return _MemoryWriteState(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
        fallback_root=tmp_path / "data" / "agent-memory",
    )


def _deps_from(state: _MemoryWriteState) -> RememberDeps:
    def _probe(path: str | Path) -> WriteAccessProbe:
        if state.overlay_readonly:
            return WriteAccessProbe(path=Path(path), writable=False, reason="Permission denied", errno_name="EACCES")
        return WriteAccessProbe(path=Path(path), writable=True)

    return RememberDeps(
        config_fn=lambda: state.config,
        document_root_fn=lambda: state.document_root,
        db_path_fn=lambda: state.db_path,
        now_fn=lambda: _BDD_NOW,
        # The fallback path calls the indexer with a trailing ``extra_scan_root``
        # keyword, so the stub accepts **_kw.
        index_fn=lambda _db, _root, _target, _hash, **_kw: state.index_ready,
        memory_fallback_root_fn=lambda: state.fallback_root,
        probe_fn=_probe,
    )


@given("agent-alpha is registered in the team's agent configuration")
def _alpha_registered(_memory_write_state: _MemoryWriteState) -> None:
    _memory_write_state.config = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
            }
        }
    }


@given("kairix has not finished warming up so search indexing is not ready yet")
def _kairix_still_warming(_memory_write_state: _MemoryWriteState) -> None:
    _memory_write_state.index_ready = False


@given("the agent-knowledge overlay is read-only")
def _overlay_readonly(_memory_write_state: _MemoryWriteState) -> None:
    _memory_write_state.overlay_readonly = True


@when(parsers.parse('the agent writes the memory "{content}" for {agent}'))
def _agent_writes_memory(_memory_write_state: _MemoryWriteState, content: str, agent: str) -> None:
    _memory_write_state.response = tool_memory_write(
        agent=agent,
        content=content,
        deps=_deps_from(_memory_write_state),
    )


@then("the memory-write response reports no error")
def _then_no_error(_memory_write_state: _MemoryWriteState) -> None:
    assert _memory_write_state.response["error"] == "", f"unexpected error: {_memory_write_state.response['error']!r}"


@then("the memory-write response names a saved file under agent-alpha's memory area")
def _then_saved_path(_memory_write_state: _MemoryWriteState) -> None:
    saved = Path(_memory_write_state.response["path"])
    assert saved.exists(), f"expected saved memory at {saved}"
    assert saved.parent == _memory_write_state.document_root / "04-Agent-Knowledge" / "agent-alpha"


@then("the memory-write response says the memory is searchable now")
def _then_indexed(_memory_write_state: _MemoryWriteState) -> None:
    assert _memory_write_state.response["indexed"] is True


@then("the memory-write response says the memory is saved and queued for indexing")
def _then_queued_for_indexing(_memory_write_state: _MemoryWriteState) -> None:
    assert _memory_write_state.response["indexed"] is False
    assert "next: run kairix embed" in _memory_write_state.response["detail"]


@then("the memory-write response is an error naming agent-omega")
def _then_error_names_agent(_memory_write_state: _MemoryWriteState) -> None:
    assert _memory_write_state.response["error"] != ""
    assert "agent-omega" in _memory_write_state.response["error"]


@then("the memory-write error tells the operator to add the agent to the configuration")
def _then_error_affordance(_memory_write_state: _MemoryWriteState) -> None:
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in _memory_write_state.response["error"]


@then("no memory file was written")
def _then_no_file(_memory_write_state: _MemoryWriteState) -> None:
    assert _memory_write_state.response["path"] == ""
    assert not _memory_write_state.document_root.exists()


@then("the memory is saved in the writable fallback area")
def _then_saved_in_fallback(_memory_write_state: _MemoryWriteState) -> None:
    saved = Path(_memory_write_state.response["path"])
    assert saved.exists(), f"expected the memory to land in the fallback, missing: {saved}"
    assert saved.parent == _memory_write_state.fallback_root / "agent-alpha"
