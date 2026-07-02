"""Contract: CLI ↔ MCP behavioural parity for the memory/write domain (W5c, PLA-324).

The memory/write domain ships two write capabilities, each with two
surfaces that MUST behave identically:

* ``remember`` — ``kairix remember`` (CLI, :func:`kairix.use_cases.remember.main`)
  and the ``memory_write`` MCP tool
  (:func:`kairix.agents.mcp.tools.memory_write.tool_memory_write`).
* ``ingest_chat`` — ``kairix ingest-chat`` (CLI,
  :func:`kairix.use_cases.ingest_chat.main`) and the ``ingest_chat`` MCP tool
  (:func:`kairix.agents.mcp.tools.ingest_chat.tool_ingest_chat`).

This module is the F43-shaped parity proof: a SINGLE parametrized body
runs over ≥2 surface fixtures (CLI + MCP) per capability and asserts the
two surfaces

1. call the SAME use case (structural limb — no re-implemented write in
   either adapter, and the production fact-store/extractor wiring is
   single-sourced after the W5c fold);
2. persist to the SAME writable location — the PLA-296
   ``resolve_writable_memory_dir`` resolver — under the injected root;
3. return the SAME shape / counts.

If any surface drifts from its use case (a re-inlined write, a divergent
envelope, a re-inlined ``SQLiteFactStore(...)`` construction), one of the
parametrized bodies or the cross-surface identity assertions fails.
"""

from __future__ import annotations

import inspect
import io
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kairix.agents.mcp.tools import ingest_chat as mcp_ingest_module
from kairix.agents.mcp.tools import memory_write as memory_write_module
from kairix.agents.mcp.tools.ingest_chat import tool_ingest_chat
from kairix.agents.mcp.tools.memory_write import tool_memory_write
from kairix.paths import KairixPaths
from kairix.use_cases import ingest_chat as ingest_module
from kairix.use_cases import remember as remember_module
from kairix.use_cases.ingest_chat import main as ingest_cli_main
from kairix.use_cases.remember import RememberDeps
from kairix.use_cases.remember import main as remember_cli_main
from tests.fakes import FakeFactExtractor, FakeFactRecord, FakeFactStore

# Shared literals — hoisted to constants so a value appears once (F17).
_AGENT = "agent-alpha"
_NAMESPACE = "engagement-alpha"
_CID = "conv-parity"
_AGENT_KNOWLEDGE = "04-Agent-Knowledge"
_CONVERSATIONS = "conversations"
_CLASSIFIED = "procedural-rule"
_CONTENT = "rule: always check the shared board before starting work"
_N_TURNS = 4
_FIXED_NOW = datetime(2026, 6, 11, 9, 30, tzinfo=timezone.utc)

# Envelope shapes each surface must return.
_REMEMBER_KEYS = {"path", "agent", "kind", "classified_as", "indexed", "error", "detail"}
_INGEST_COUNT_KEYS = (
    "turns_ingested",
    "conversations_processed",
    "facts_added",
    "windows_extracted",
    "facts_superseded",
)

# One surface invoker = (rc, envelope-dict). ``rc`` is the CLI exit code;
# the MCP tools have no exit code, so it is synthesised from ``error`` so
# both fixtures answer the same shape to the parametrized body.
SurfaceResult = tuple[int, dict[str, Any]]


# ---------------------------------------------------------------------------
# remember / memory_write — surface fixtures + shared deps
# ---------------------------------------------------------------------------


def _remember_deps(root: Path, *, indexed: bool = True) -> RememberDeps:
    """Hermetic ``RememberDeps`` pinned under ``root`` (F1/F2-clean).

    The clock, config, classifier, index step and paths are all injected
    so BOTH surfaces run against byte-identical inputs — any envelope
    difference is then attributable to a surface, not to nondeterminism.
    """
    config: dict[str, object] = {
        "agents": {
            _AGENT: {
                "harness": "claude-code",
                "surfaces": [{"path": f"{_AGENT_KNOWLEDGE}/{_AGENT}", "label": "memory"}],
            }
        }
    }
    return RememberDeps(
        config_fn=lambda: config,
        document_root_fn=lambda: root / "vault",
        db_path_fn=lambda: root / "index.sqlite",
        now_fn=lambda: _FIXED_NOW,
        classify_fn=lambda _content, *, agent, config: SimpleNamespace(type=_CLASSIFIED),
        index_fn=lambda *_a, **_k: indexed,
    )


def _remember_via_cli(*, agent: str, content: str, kind: str, deps: RememberDeps) -> SurfaceResult:
    out = io.StringIO()
    rc = remember_cli_main([agent, content, "--kind", kind, "--json"], out=out, err=io.StringIO(), deps=deps)
    return rc, json.loads(out.getvalue())


def _remember_via_mcp(*, agent: str, content: str, kind: str, deps: RememberDeps) -> SurfaceResult:
    env = tool_memory_write(agent=agent, content=content, kind=kind, deps=deps)
    return (1 if env["error"] else 0), env


_REMEMBER_SURFACES = [
    pytest.param(_remember_via_cli, id="cli-remember"),
    pytest.param(_remember_via_mcp, id="mcp-memory_write"),
]


# ---------------------------------------------------------------------------
# ingest_chat — surface fixtures + shared fakes
# ---------------------------------------------------------------------------


def _ingest_fixtures(root: Path) -> tuple[KairixPaths, FakeFactStore, FakeFactExtractor]:
    """Fresh paths + fakes under ``root`` — a fresh store keeps counts deterministic."""
    root.mkdir(parents=True, exist_ok=True)
    paths = KairixPaths(
        document_root=root / "vault",
        db_path=root / "kairix.db",
        log_dir=root / "logs",
        workspace_root=root / "workspaces",
    )
    store = FakeFactStore()
    extractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f-1", entity=_AGENT, attribute="role", value="founder")]
    )
    return paths, store, extractor


def _jsonl(conversation_id: str, n_turns: int = _N_TURNS) -> str:
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


def _ingest_via_cli(
    *, root: Path, paths: KairixPaths, store: FakeFactStore, extractor: FakeFactExtractor
) -> SurfaceResult:
    transcript = root / f"{_CID}.jsonl"
    transcript.write_text(_jsonl(_CID), encoding="utf-8")
    out = io.StringIO()
    rc = ingest_cli_main(
        [str(transcript), "--namespace", _NAMESPACE, "--json"],
        out=out,
        err=io.StringIO(),
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )
    return rc, json.loads(out.getvalue())


def _ingest_via_mcp(
    *, root: Path, paths: KairixPaths, store: FakeFactStore, extractor: FakeFactExtractor
) -> SurfaceResult:
    env = tool_ingest_chat(
        jsonl_content=_jsonl(_CID),
        conversation_id=_CID,
        namespace=_NAMESPACE,
        allowed_namespace=_NAMESPACE,
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )
    return (1 if env["error"] else 0), env


_INGEST_SURFACES = [
    pytest.param(_ingest_via_cli, id="cli-ingest_chat"),
    pytest.param(_ingest_via_mcp, id="mcp-ingest_chat"),
]


# ---------------------------------------------------------------------------
# Behavioural parity — ONE parametrized body per capability over ≥2 fixtures
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("invoke", _REMEMBER_SURFACES)
def test_remember_surface_shares_use_case_location_and_shape(
    invoke: Callable[..., SurfaceResult], tmp_path: Path
) -> None:
    """Both remember surfaces write to the PLA-296 location + return the same shape.

    Sabotage: change ``tool_memory_write`` to pass ``kind="note"`` instead
    of the caller's ``kind`` (a divergence from the ``remember`` use case)
    → the ``mcp-memory_write`` param fails the ``kind == "decision"``
    assertion below. Restored.
    """
    deps = _remember_deps(tmp_path)
    rc, env = invoke(agent=_AGENT, content=_CONTENT, kind="decision", deps=deps)

    assert rc == 0, f"surface failed: {env!r}"
    assert set(env) == _REMEMBER_KEYS, f"envelope shape drift: {sorted(env)}"
    assert env["error"] == ""
    assert env["agent"] == _AGENT
    assert env["kind"] == "decision"
    assert env["classified_as"] == _CLASSIFIED

    written = Path(env["path"])
    assert written.exists(), f"expected memory file at {written}"
    # PLA-296 resolver lands the write in the agent's writable memory dir.
    assert written.parent == tmp_path / "vault" / _AGENT_KNOWLEDGE / _AGENT


@pytest.mark.contract
def test_remember_cli_and_mcp_return_identical_envelope(tmp_path: Path) -> None:
    """Same inputs → CLI and MCP emit byte-identical envelopes (modulo the root).

    Sabotage: make ``tool_memory_write`` return a hand-rolled dict with an
    extra key → the dict-equality assertion below fails. Restored.
    """
    _, env_cli = _remember_via_cli(agent=_AGENT, content=_CONTENT, kind="note", deps=_remember_deps(tmp_path / "cli"))
    _, env_mcp = _remember_via_mcp(agent=_AGENT, content=_CONTENT, kind="note", deps=_remember_deps(tmp_path / "mcp"))

    # The path differs only by the injected root prefix; everything else
    # (including the resolved sub-path + filename) must be identical.
    rel_cli = Path(env_cli.pop("path")).relative_to(tmp_path / "cli")
    rel_mcp = Path(env_mcp.pop("path")).relative_to(tmp_path / "mcp")
    assert rel_cli == rel_mcp, f"write location drift: {rel_cli} vs {rel_mcp}"
    assert env_cli == env_mcp, f"CLI ↔ MCP envelope divergence:\nCLI: {env_cli!r}\nMCP: {env_mcp!r}"


@pytest.mark.contract
@pytest.mark.parametrize("invoke", _INGEST_SURFACES)
def test_ingest_surface_shares_use_case_location_and_counts(
    invoke: Callable[..., SurfaceResult], tmp_path: Path
) -> None:
    """Both ingest surfaces write the conversation to the PLA-296 location + report counts.

    Sabotage: drop the ``ingest_chat(...)`` call from ``tool_ingest_chat``
    and return a hand-rolled counts dict → the markdown file never lands
    and the ``chunk.exists()`` assertion fails for ``mcp-ingest_chat``.
    Restored.
    """
    paths, store, extractor = _ingest_fixtures(tmp_path)
    rc, env = invoke(root=tmp_path, paths=paths, store=store, extractor=extractor)

    assert rc == 0, f"surface failed: {env!r}"
    assert env.get("error", "") == ""
    for key in _INGEST_COUNT_KEYS:
        assert key in env, f"missing count {key!r} on surface envelope: {sorted(env)}"
    assert env["turns_ingested"] == _N_TURNS
    assert env["conversations_processed"] == 1
    assert env["facts_added"] == 1
    assert env["windows_extracted"] == 1
    assert env["facts_superseded"] == 0

    # PLA-296 resolver lands the conversation under the writable submount.
    chunk = paths.document_root / _AGENT_KNOWLEDGE / _CONVERSATIONS / f"{_CID}.md"
    assert chunk.exists(), f"expected conversation markdown at {chunk}"


@pytest.mark.contract
def test_ingest_cli_and_mcp_return_identical_counts(tmp_path: Path) -> None:
    """Same transcript → CLI and MCP report identical IngestChatResult counts.

    Sabotage: give ``tool_ingest_chat`` a different ``window_turns``
    default than the CLI → ``windows_extracted`` diverges and the
    dict-equality assertion below fails. Restored.
    """
    paths_cli, store_cli, extractor_cli = _ingest_fixtures(tmp_path / "cli")
    paths_mcp, store_mcp, extractor_mcp = _ingest_fixtures(tmp_path / "mcp")

    _, env_cli = _ingest_via_cli(root=tmp_path / "cli", paths=paths_cli, store=store_cli, extractor=extractor_cli)
    _, env_mcp = _ingest_via_mcp(root=tmp_path / "mcp", paths=paths_mcp, store=store_mcp, extractor=extractor_mcp)

    counts_cli = {k: env_cli[k] for k in _INGEST_COUNT_KEYS}
    counts_mcp = {k: env_mcp[k] for k in _INGEST_COUNT_KEYS}
    assert counts_cli == counts_mcp, f"count divergence:\nCLI: {counts_cli!r}\nMCP: {counts_mcp!r}"

    # The MCP surface additionally echoes the agent-facing envelope context.
    assert env_mcp["namespace"] == _NAMESPACE
    assert env_mcp["conversation_id"] == _CID
    assert env_mcp["error"] == ""


# ---------------------------------------------------------------------------
# Structural parity — one use case per capability; adapters don't re-implement
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_both_remember_surfaces_call_the_remember_use_case() -> None:
    cli_src = inspect.getsource(remember_module.main)
    mcp_src = inspect.getsource(memory_write_module.tool_memory_write)
    assert "remember(" in cli_src
    assert "remember(" in mcp_src
    # The adapter delegates — it never re-defines the write.
    assert "def remember(" not in mcp_src


@pytest.mark.contract
def test_both_ingest_surfaces_call_the_ingest_chat_use_case() -> None:
    cli_src = inspect.getsource(ingest_module.main)
    mcp_src = inspect.getsource(mcp_ingest_module.tool_ingest_chat)
    assert "ingest_chat(" in cli_src
    assert "ingest_chat(" in mcp_src


@pytest.mark.contract
def test_ingest_production_wiring_is_single_sourced() -> None:
    """W5c fold: neither surface re-inlines the production fact store/extractor.

    Both the CLI and the MCP adapter resolve production dependencies from
    the SAME use-case factories, so the wiring can never drift apart.
    """
    mcp_src = inspect.getsource(mcp_ingest_module.tool_ingest_chat)
    assert "resolve_production_fact_store(" in mcp_src
    assert "resolve_production_fact_extractor(" in mcp_src
    # The adapter must NOT re-construct the production store/extractor itself.
    assert "SQLiteFactStore(" not in mcp_src
    assert "LLMFactExtractor(" not in mcp_src

    cli_src = inspect.getsource(ingest_module.main)
    assert "resolve_production_fact_store(" in cli_src
    assert "resolve_production_fact_extractor(" in cli_src


@pytest.mark.contract
def test_both_use_cases_persist_through_the_pla296_resolver() -> None:
    """Both write paths land through the PLA-296 ``resolve_writable_memory_dir``."""
    assert "resolve_writable_memory_dir(" in inspect.getsource(remember_module)
    assert "resolve_writable_memory_dir(" in inspect.getsource(ingest_module)
