"""Unit tests for ``tool_ingest_chat`` — the agent-driven ingest MCP tool.

These tests pin the contract from the agent's point of view:

  - Happy path returns the IngestChatResult counts plus the agent's
    ``namespace`` and ``conversation_id`` echoed back.
  - Cross-engagement namespace calls are rejected with a structured
    envelope (no facts written, no markdown written, no exception).
  - Empty inputs are rejected before any I/O.
  - Failures inside the use case are caught and surfaced via ``error``.
  - Default ``fact_store`` / ``fact_extractor`` resolution paths fire
    when callers omit the DI seams (production wiring branches).
  - tmp-file write failures surface ``IngestFailed`` (covers the OSError
    catch around staging on disk).
  - Use-case errors during ingest are wrapped in ``IngestFailed``
    (covers the broad except around ``ingest_chat(...)``).

Every test carries a ``# Sabotage:`` note describing a concrete change
to the production code that would falsify the test.

F1-clean: every collaborator (paths, fact_store, fact_extractor) is
passed through the constructor seam — no monkeypatching, no
attribute-reassignment, no ``@patch``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.tools.ingest_chat import (
    ERROR_CROSS_ENGAGEMENT,
    ERROR_INGEST_FAILED,
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


def _conversations_dir(document_root: Path) -> Path:
    """The writable agent-knowledge submount conversations land under (PLA-275)."""
    return document_root / "04-Agent-Knowledge" / "conversations"


def _require_enforced_readonly_or_skip(directory: Path) -> None:
    """Strip write perms from ``directory``; skip if the platform won't enforce it.

    Running as root (uid 0) bypasses DAC write checks and some filesystems
    ignore mode bits — in either case a read-only directory can't be
    simulated, so the read-only-write path is unverifiable here. We skip with
    rationale rather than assert a guarantee the platform can't make, mirroring
    the CLAUDE.md /run/secrets read-only-fs lesson.
    """
    os.chmod(directory, 0o500)
    probe = directory / ".probe-write"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return  # read-only is enforced — proceed with the test
    # Write unexpectedly succeeded → the platform cannot enforce read-only here.
    probe.unlink()
    os.chmod(directory, 0o700)
    # F11: platform does not enforce read-only directory perms (root or a
    # mode-blind filesystem); the RO-write branch is unverifiable, so skip.
    pytest.skip("filesystem does not enforce read-only directory permissions; cannot simulate the RO submount")


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
    # Markdown chunk was actually persisted to disk (writable submount).
    chunk = _conversations_dir(paths.document_root) / "conv-01.md"
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
    assert not _conversations_dir(paths.document_root).exists()


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
    assert (_conversations_dir(paths.document_root) / "conv-03.md").exists()


def test_default_fact_store_and_extractor_are_resolved_when_omitted(tmp_path: Path) -> None:
    """When DI kwargs are omitted, the tool resolves production defaults.

    Drives the production-wiring branches — the ``if fact_store is None:``
    / ``if fact_extractor is None:`` blocks that delegate to the shared
    use-case factories ``resolve_production_fact_store`` /
    ``resolve_production_fact_extractor`` (W5c DRY consolidation,
    PLA-324): a default SQLiteFactStore is constructed for the store, and
    a default LLMFactExtractor + backend for the extractor.

    We pass ``no_extract=True`` so the extractor is constructed (covering
    the import + backend init) but never actually
    called — the ingest_chat use case skips ``extract`` when
    ``no_extract`` is set. SQLiteFactStore is also never queried for
    facts in no-extract mode (only chunks are written), so this test
    runs hermetically against the tmp paths without any external
    service.

    Sabotage: remove the ``if fact_store is None:`` block in
    tool_ingest_chat → the call reaches ``ingest_chat(...,
    fact_store=None, ...)`` and the use case raises AttributeError on
    the missing ``add`` method, breaking this test with an unhandled
    exception. Mutate-confirmed.
    """
    paths = _paths(tmp_path)

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-default", n_turns=3),
        conversation_id="conv-default",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        no_extract=True,
        # fact_store and fact_extractor intentionally omitted — DI defaults must fire.
    )

    assert out["error"] == ""
    assert out["facts_added"] == 0  # no_extract=True
    assert out["turns_ingested"] == 3
    assert (_conversations_dir(paths.document_root) / "conv-default.md").exists()


def test_writes_under_writable_agent_knowledge_submount_not_ro_root(tmp_path: Path) -> None:
    """Conversations land under the writable agent-knowledge submount, not the
    bare (read-only on stock compose) document root (PLA-275).

    On the standard compose ``/data/documents`` mounts ``:ro`` while
    ``/data/documents/04-Agent-Knowledge`` is overlaid as a separate writable
    mount (docker-compose.yml). Writing the bare-root ``conversations/`` path
    crashed with ``OSError: Read-only file system``; the chunk must land under
    ``04-Agent-Knowledge/conversations`` so the write succeeds on the deploy.

    Sabotage: revert the use case to ``paths.document_root / "conversations"``
    → the submount assertion fails (chunk absent there) and the bare-root
    negative assertion fails (chunk present at the read-only root).
    """
    paths = _paths(tmp_path)

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-ak", n_turns=3),
        conversation_id="conv-ak",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        no_extract=True,
    )

    assert out["error"] == ""
    submount_chunk = _conversations_dir(paths.document_root) / "conv-ak.md"
    assert submount_chunk.exists(), f"expected chunk under the writable submount at {submount_chunk}"
    bare_root_chunk = paths.document_root / "conversations" / "conv-ak.md"
    assert not bare_root_chunk.exists(), "chunk must NOT be written to the bare read-only document root"


def test_readonly_submount_falls_back_to_writable_data_dir(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A read-only agent-knowledge submount NO LONGER fails — it falls back (PLA-296).

    Strips write perms from ``04-Agent-Knowledge`` so the preferred conversations
    submount is unwritable, but injects a writable ``memory_fallback_root`` under
    ``tmp_path``. The tool must succeed via the data-dir fallback (error == "")
    rather than hand back the old IngestFailed envelope, and log a LOUD warning
    so a genuinely-misconfigured box is still surfaced.

    Sabotage (executed): reverted ``ingest_chat`` to write straight to
    ``agent_conversations_dir`` (dropping ``resolve_writable_memory_dir``) → the
    mkdir raised PermissionError and the success assertion failed; restored.
    """
    paths = _paths(tmp_path)
    ak_dir = paths.document_root / "04-Agent-Knowledge"
    ak_dir.mkdir(parents=True)
    fallback_root = tmp_path / "data" / "agent-memory"
    _require_enforced_readonly_or_skip(ak_dir)

    try:
        with caplog.at_level(logging.WARNING, logger="kairix.paths"):
            out = tool_ingest_chat(
                jsonl_content=_jsonl("conv-ro", n_turns=2),
                conversation_id="conv-ro",
                namespace="engagement-alpha",
                allowed_namespace="engagement-alpha",
                paths=paths,
                fact_store=FakeFactStore(),
                fact_extractor=FakeFactExtractor(),
                no_extract=True,
                memory_fallback_root=fallback_root,
            )
    finally:
        # Restore perms so pytest can clean up tmp_path even if an assertion fails.
        os.chmod(ak_dir, 0o700)

    assert out["error"] == "", f"expected fallback success, got: {out}"
    assert out["turns_ingested"] == 2
    # The conversation landed in the writable, namespace-isolated fallback.
    fallback_file = fallback_root / "engagement-alpha" / "conversations" / "conv-ro.md"
    assert fallback_file.exists(), f"expected fallback conversation at {fallback_file}"
    # A loud WARN records the misconfigured overlay so it is not silently masked.
    warned = [r for r in caplog.records if "not writable" in r.getMessage()]
    assert warned, "expected a WARN naming the non-writable overlay"


def test_readonly_submount_and_fallback_returns_actionable_envelope_not_oserror(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When BOTH the overlay and the fallback are read-only, the tool still yields
    an actionable envelope, not a crash (PLA-296 residual + PLA-275).

    Both the preferred ``04-Agent-Knowledge`` submount and the injected
    ``memory_fallback_root`` are stripped of write perms, so even the fallback
    ``mkdir(...)`` raises ``PermissionError``. The tool must catch it and hand
    the agent an F21-actionable envelope — never let the OSError propagate.

    Sabotage: remove the ``except OSError`` branch around the ``ingest_chat``
    call in ``tool_ingest_chat`` → the PermissionError propagates out of the
    handler and this test fails with the unhandled exception.
    """
    paths = _paths(tmp_path)
    ak_dir = paths.document_root / "04-Agent-Knowledge"
    ak_dir.mkdir(parents=True)
    fallback_root = tmp_path / "data" / "agent-memory"
    fallback_root.mkdir(parents=True)
    _require_enforced_readonly_or_skip(ak_dir)
    _require_enforced_readonly_or_skip(fallback_root)

    try:
        with caplog.at_level(logging.WARNING, logger="kairix.agents.mcp.tools.ingest_chat"):
            out = tool_ingest_chat(
                jsonl_content=_jsonl("conv-ro", n_turns=2),
                conversation_id="conv-ro",
                namespace="engagement-alpha",
                allowed_namespace="engagement-alpha",
                paths=paths,
                fact_store=FakeFactStore(),
                fact_extractor=FakeFactExtractor(),
                no_extract=True,
                memory_fallback_root=fallback_root,
            )
    finally:
        # Restore perms so pytest can clean up tmp_path even if an assertion fails.
        os.chmod(ak_dir, 0o700)
        os.chmod(fallback_root, 0o700)

    assert out["error"] == ERROR_INGEST_FAILED
    # Actionable: F21 fix:/next: markers + names the writable submount.
    assert "fix:" in out["detail"]
    assert "next:" in out["detail"]
    assert "04-Agent-Knowledge" in out["detail"]
    assert out["turns_ingested"] == 0
    assert out["facts_added"] == 0
    # The filesystem error is logged WITH traceback context (exc_info=True) so
    # operators can diagnose the misconfigured mount from the worker logs.
    fs_records = [r for r in caplog.records if "filesystem error" in r.getMessage()]
    assert fs_records, "expected a WARNING log for the filesystem error"
    captured = fs_records[0].exc_info
    assert captured, "the filesystem-error warning must capture exc_info for diagnosis"
    assert issubclass(captured[0], OSError), "captured exc_info must carry the filesystem (OSError) exception"


def test_tmp_jsonl_write_failure_returns_ingestfailed_envelope(tmp_path: Path) -> None:
    """An OSError staging the JSONL on disk is surfaced as IngestFailed.

    Drives lines 189-191 — the OSError catch around ``_write_tmp_jsonl``.
    We force the OSError by making ``workspace_root`` an existing FILE
    rather than a directory; ``tmp_dir.mkdir(parents=True, ...)`` then
    raises NotADirectoryError (an OSError subclass) when it walks into
    the file-as-dir.

    Sabotage: remove the ``try/except OSError`` around
    ``_write_tmp_jsonl(...)`` in tool_ingest_chat → the NotADirectoryError
    propagates out of the tool, this test fails with the unhandled
    exception. Mutate-confirmed against lines 189-191.
    """
    # Create a tmp_path/workspaces FILE (not directory) so mkdir hits an OSError.
    workspaces_as_file = tmp_path / "workspaces"
    workspaces_as_file.write_text("blocking-file", encoding="utf-8")

    paths = KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=workspaces_as_file,
    )

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-fail", n_turns=2),
        conversation_id="conv-fail",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
    )

    assert out["error"] == ERROR_INGEST_FAILED
    assert "stage transcript on disk" in out["detail"]
    assert out["turns_ingested"] == 0
    assert out["facts_added"] == 0


class _RaisingFactExtractor:
    """FactExtractor stub whose ``extract`` raises RuntimeError.

    Drives the broad-except inside ``tool_ingest_chat`` around the
    ``ingest_chat(...)`` use-case call. Defined locally (not in
    tests/fakes.py) to avoid stepping on sibling agents' file scope.
    """

    def __init__(self, message: str = "extractor exploded") -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    def extract(
        self,
        *,
        turns: list[dict[str, Any]],
        window_hint: dict[str, Any] | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> list[Any]:
        self.calls.append(
            {
                "turns": list(turns),
                "window_hint": window_hint,
                "session_metadata": session_metadata,
            }
        )
        raise RuntimeError(self._message)


def test_use_case_failure_is_wrapped_in_ingestfailed_envelope(tmp_path: Path) -> None:
    """A RuntimeError out of the ingest_chat use case is caught and surfaced.

    Drives lines 208-215 — the outer ``try/except`` around the
    ``ingest_chat(...)`` call wraps any OSError/ValueError/RuntimeError
    in the IngestFailed envelope so the agent reads ``error`` without
    seeing a traceback.

    Sabotage: remove the ``try/except`` around the ingest_chat use-case
    call in tool_ingest_chat → the RuntimeError propagates out of the
    tool, this test fails with the unhandled exception. Mutate-confirmed
    against lines 208-210.
    """
    paths = _paths(tmp_path)
    store = FakeFactStore()
    extractor = _RaisingFactExtractor(message="LLM call failed")

    out = tool_ingest_chat(
        jsonl_content=_jsonl("conv-explode", n_turns=5),
        conversation_id="conv-explode",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=store,
        fact_extractor=extractor,
    )

    assert out["error"] == ERROR_INGEST_FAILED
    assert "LLM call failed" in out["detail"]
    assert out["namespace"] == "engagement-alpha"
    assert out["conversation_id"] == "conv-explode"
