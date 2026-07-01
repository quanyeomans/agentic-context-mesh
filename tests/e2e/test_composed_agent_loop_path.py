"""End-to-end composed agent-write-loop path — F48 sibling for PLA-300.

The capstone proof that the *agent write loop* works on a genuinely
read-only document root — the exact standard-deploy shape where it used to
crash with ``OSError: [Errno 30] Read-only file system``. The write-path fix
(PLA-296/298) and the expand anti-dead-end fix (PLA-297) are already merged;
this test wires them together through the real production seams so the loop
is proven end-to-end, not per-layer.

The journey, all on already-merged code:

1. **Fixture self-check** — the ``readonly_deploy`` fixture proves its
   ``04-Agent-Knowledge`` overlay is *genuinely* non-writable before any leg
   runs (and skips cleanly when the platform can't enforce a read-only
   directory), so the proof can never pass vacuously.
2. **Onboard writability probe is accurate** — ``check_agent_memory_writable``
   reports the true state: green when the agent's memory surface is a writable
   submount on the read-only-root deploy, and it does NOT false-green a
   genuinely dead surface.
3. **memory_write persists + is findable** — ``memory_write`` on the read-only
   overlay lands in the writable data-dir fallback and is returned by a
   subsequent composed ``search`` (read-back through ``build_search_pipeline``).
4. **ingest_chat round-trips** — ``ingest_chat`` on the read-only overlay
   persists a conversation to the namespaced fallback and does not crash.
5. **expand never dead-ends** — a composed search hit → ``expand`` by
   ``source_uri`` (seq-less) returns an ordered neighbour window (the PLA-297
   by-prefix path), never a guessed ``#0`` dead-end.

Composition contract (F1/F46/F47/F48): every leg drives the real MCP handlers
/ use cases / factory-built search pipeline with production seams. The only
injected fakes are ``FakeProvider`` (offline embeddings) and the fact
store/extractor for the chunks-only ingest — the write-surface resolution,
the probe, the incremental index, and BM25 read-back are all real.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from kairix.agents.mcp.tools.ingest_chat import tool_ingest_chat
from kairix.agents.mcp.tools.memory_write import tool_memory_write
from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.pipeline import SearchResult
from kairix.platform.onboard.check import (
    AgentMemoryWritableCheckDeps,
    check_agent_memory_writable,
)
from kairix.use_cases.expand import ExpandDeps, run_expand
from kairix.use_cases.remember import RememberDeps
from tests.fakes import (
    FakeFactExtractor,
    FakeFactStore,
    FakePaths,
    FakeProvider,
    FakeProviderRegistry,
)

# Config that makes ``agent-alpha`` a valid agent whose memory surface resolves
# under the (read-only) ``04-Agent-Knowledge`` overlay — the PLA-296 fallback
# then rescues the write. F32-clean: a generic agent name, not a real person.
_CONFIG = {
    "agents": {
        "agent-alpha": {
            "harness": "claude-code",
            "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
        }
    }
}


# ---------------------------------------------------------------------------
# Read-only-deploy fixture + self-check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Deploy:
    """The composed read-only-root deploy shape under test.

    ``overlay`` (``04-Agent-Knowledge``) is genuinely read-only — the exact
    ``:ro`` mount shape PLA-296 rescues. ``db_path``, ``fallback_root`` and
    ``workspace_root`` are on writable tmp, mirroring the compose where the
    index db + data dir are writable while the document tree is not.
    """

    document_root: Path
    overlay: Path
    db_path: Path
    fallback_root: Path
    workspace_root: Path
    logs: Path


@pytest.fixture
def _ro_dirs() -> Iterator[list[Path]]:
    """Track read-only dirs and restore write perms in teardown.

    A ``chmod 0o500`` directory under ``tmp_path`` blocks pytest's own tmp
    cleanup; restoring ``0o700`` on teardown (even on failure) keeps the
    working tree free of debris that whole-tree detector scans would later
    flag as phantom failures.
    """
    dirs: list[Path] = []
    try:
        yield dirs
    finally:
        for directory in dirs:
            with contextlib.suppress(OSError):
                os.chmod(directory, 0o700)


def _make_readonly_or_skip(directory: Path, ro_dirs: list[Path], *, reason: str) -> None:
    """Strip write perms from ``directory``; skip the test when the OS won't enforce it.

    Registers the dir for perm-restoration teardown. Root bypasses mode bits
    and some filesystems (overlayfs, certain CI mounts) don't enforce them —
    in either case a read-only-root proof would pass vacuously, so we skip
    rather than assert against a surface that is secretly writable.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip(f"{reason}: read-only permissions are not enforced for root")
    os.chmod(directory, 0o500)
    ro_dirs.append(directory)
    probe = directory / f".probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return  # good — the surface is genuinely non-writable
    probe.unlink()
    pytest.skip(f"{reason}: this filesystem does not enforce read-only directory permissions")


def _init_index(db_path: Path) -> None:
    """Create a real, empty on-disk SQLite index (schema + FTS)."""
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        create_schema(db)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def readonly_deploy(tmp_path: Path, _ro_dirs: list[Path]) -> _Deploy:
    """Build the standard read-only-root deploy and self-check its non-writability.

    The ``04-Agent-Knowledge`` overlay is made read-only and then *proven*
    non-writable (``_make_readonly_or_skip`` writes-and-fails, or skips when
    the platform can't enforce it). Any leg that consumes this fixture is
    therefore running against a genuinely dead write surface — the PLA-296
    fallback is the only reason a write survives.
    """
    document_root = tmp_path / "documents"
    overlay = document_root / "04-Agent-Knowledge"
    overlay.mkdir(parents=True)
    _make_readonly_or_skip(overlay, _ro_dirs, reason="read-only agent-knowledge overlay")

    db_path = tmp_path / "index.sqlite"
    _init_index(db_path)
    return _Deploy(
        document_root=document_root,
        overlay=overlay,
        db_path=db_path,
        fallback_root=tmp_path / "data" / "agent-memory",
        workspace_root=tmp_path / "workspaces",
        logs=tmp_path / "logs",
    )


def _composed_search(deploy: _Deploy, query: str) -> SearchResult:
    """Search ``deploy``'s real index through the factory-composed pipeline.

    The agent-facing read path: ``build_search_pipeline`` wires the production
    BM25 + vector + fusion stack over the deploy's SQLite index. Offline
    embeddings come from ``FakeProvider`` so the vector leg needs no network;
    BM25 runs against the real FTS index the write path populated.
    """
    reset_search_pipeline_cache()
    cfg = RetrievalConfig(provider="fake")
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    paths = FakePaths(
        document_root=deploy.document_root,
        db_path=deploy.db_path,
        log_dir=deploy.logs,
        workspace_root=deploy.workspace_root,
    )
    pipeline = build_search_pipeline(config=cfg, registry=registry, paths=paths)
    return pipeline.search(query=query, budget=3000)


# ---------------------------------------------------------------------------
# Leg 1 — the fixture self-check is a genuinely non-writable surface
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_readonly_deploy_overlay_is_genuinely_nonwritable(readonly_deploy: _Deploy) -> None:
    """Leg 1 — the deploy's agent-knowledge overlay really rejects writes.

    Without this, every downstream leg could pass on a secretly-writable
    surface and the PLA-296 defect would silently return. The fixture already
    skips when the platform can't enforce read-only perms, so reaching this
    assertion means the surface must reject a direct write.

    Sabotage-proof (executed): drop the ``os.chmod(.., 0o500)`` in
    ``_make_readonly_or_skip`` → the write below succeeds and this assertion
    fails; restored.
    """
    probe = readonly_deploy.overlay / f"selfcheck-{uuid.uuid4().hex}.md"
    with pytest.raises(OSError):
        probe.write_text("this write must not succeed", encoding="utf-8")


# ---------------------------------------------------------------------------
# Leg 2 — onboard writability probe reports the true state
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_onboard_probe_reports_true_writability_state(tmp_path: Path, _ro_dirs: list[Path]) -> None:
    """Leg 2 — ``check_agent_memory_writable`` tells the truth on a RO-root deploy.

    Green when the configured agent's memory surface is a genuinely writable
    submount (the correctly-configured standard deploy where the document root
    is read-only but the agent-knowledge mount is writable); NOT-green when the
    surface is genuinely dead — the probe never false-greens a read-only
    surface just because a runtime fallback exists.

    Both verdicts run the REAL ``probe_write_access`` (the check's default
    ``probe_fn``); only the config + document root are injected.

    Sabotage-proof (executed): force ``_probe_agent_memory_roots`` to treat
    every probe as writable → the dead-surface assertion (``ok is False``)
    fails; restored.
    """
    document_root = tmp_path / "documents"
    document_root.mkdir()
    _make_readonly_or_skip(document_root, _ro_dirs, reason="read-only document root")

    # Correctly-configured deploy: the agent's memory surface is a separate
    # writable mount, even though the document root above is read-only.
    writable_mount = tmp_path / "agent-knowledge-mount" / "agent-alpha"
    writable_mount.mkdir(parents=True)
    green_config = {"agents": {"agent-alpha": {"surfaces": [{"path": str(writable_mount), "label": "memory"}]}}}
    green = check_agent_memory_writable(
        AgentMemoryWritableCheckDeps(
            config_loader=lambda: green_config,
            document_root_fn=lambda: document_root,
        )
    )
    assert green.ok is True, f"writable submount must probe green, got: {green.detail}"

    # Misconfigured deploy: the agent's memory surface is itself read-only.
    dead_mount = tmp_path / "dead-mount" / "agent-alpha"
    dead_mount.mkdir(parents=True)
    _make_readonly_or_skip(dead_mount, _ro_dirs, reason="read-only agent surface")
    dead_config = {"agents": {"agent-alpha": {"surfaces": [{"path": str(dead_mount), "label": "memory"}]}}}
    dead = check_agent_memory_writable(
        AgentMemoryWritableCheckDeps(
            config_loader=lambda: dead_config,
            document_root_fn=lambda: document_root,
        )
    )
    assert dead.ok is False, "a genuinely read-only agent surface must NOT false-green"


# ---------------------------------------------------------------------------
# Leg 3 — memory_write falls back and stays findable via composed search
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_memory_write_persists_to_fallback_and_is_searchable(readonly_deploy: _Deploy) -> None:
    """Leg 3 — ``memory_write`` on a RO overlay lands in the fallback + reads back.

    Drives the real ``memory_write`` MCP handler (real resolve → real
    ``probe_write_access`` → real incremental index) on the read-only overlay,
    then reads the memory back through the factory-composed search pipeline —
    the same path an agent uses to recall.

    Sabotage-proof (executed): revert ``resolve_writable_memory_dir`` to always
    return the preferred (read-only) dir → the write fails with WriteFailed,
    ``error == ""`` fails, and the composed search returns nothing; restored.
    """
    response = tool_memory_write(
        agent="agent-alpha",
        content="decision: adopt the quokka-lighthouse rollout cadence for the beacon migration",
        kind="decision",
        deps=RememberDeps(
            config_fn=lambda: _CONFIG,
            document_root_fn=lambda: readonly_deploy.document_root,
            db_path_fn=lambda: readonly_deploy.db_path,
            memory_fallback_root_fn=lambda: readonly_deploy.fallback_root,
        ),
    )

    assert response["error"] == "", f"memory_write should fall back cleanly, got: {response}"
    written = Path(response["path"])
    assert written.parent == readonly_deploy.fallback_root / "agent-alpha", (
        f"memory must land in the writable data-dir fallback, got {written}"
    )
    assert written.exists()
    assert response["indexed"] is True, "the fallback write must be indexed for immediate recall"

    result = _composed_search(readonly_deploy, "quokka-lighthouse rollout cadence")
    assert result.results, (
        f"composed search returned nothing for the fallback memory — read-back broken. "
        f"error={result.error!r} bm25_count={result.bm25_count}"
    )
    found_paths = [str(getattr(row.result, "path", "")) for row in result.results]
    assert any(written.name in p for p in found_paths), (
        f"composed search returned hits but not the written memory {written.name!r}: {found_paths}"
    )


# ---------------------------------------------------------------------------
# Leg 4 — ingest_chat round-trips to the namespaced fallback
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_ingest_chat_round_trips_on_readonly_root(readonly_deploy: _Deploy) -> None:
    """Leg 4 — ``ingest_chat`` on a RO overlay persists a conversation + reads back.

    Drives the real ``ingest_chat`` MCP handler (chunks-only: the fact
    store/extractor are injected fakes because fact extraction is out of scope
    for this write-loop capstone). The conversation write resolves through the
    same read-only overlay → writable fallback path and is BM25-indexed, then
    recalled through the factory-composed search pipeline.

    Sabotage-proof (executed): drop the ``_index_fallback_conversations`` call
    in ``ingest_chat`` → the conversation is written but the composed search
    read-back returns nothing and the assertion fails; restored.
    """
    jsonl = (
        '{"conversation_id": "conv-capstone", "role": "user", '
        '"content": "we agreed on the marsupial-beacon-tempo rollout plan"}\n'
        '{"conversation_id": "conv-capstone", "role": "assistant", '
        '"content": "noted — the marsupial-beacon-tempo plan is locked in"}\n'
    )
    paths = FakePaths(
        document_root=readonly_deploy.document_root,
        db_path=readonly_deploy.db_path,
        log_dir=readonly_deploy.logs,
        workspace_root=readonly_deploy.workspace_root,
    )

    out = tool_ingest_chat(
        jsonl_content=jsonl,
        conversation_id="conv-capstone",
        namespace="engagement-alpha",
        allowed_namespace="engagement-alpha",
        paths=paths,
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        no_extract=True,
        memory_fallback_root=readonly_deploy.fallback_root,
    )

    assert out["error"] == "", f"ingest_chat should round-trip cleanly, got: {out}"
    assert out["turns_ingested"] == 2, f"both turns should ingest, got: {out}"
    assert out["conversations_processed"] == 1
    conv_file = readonly_deploy.fallback_root / "engagement-alpha" / "conversations" / "conv-capstone.md"
    assert conv_file.exists(), f"conversation must land in the namespaced fallback, missing: {conv_file}"

    result = _composed_search(readonly_deploy, "marsupial-beacon-tempo rollout plan")
    assert result.results, (
        f"composed search returned nothing for the fallback conversation. "
        f"error={result.error!r} bm25_count={result.bm25_count}"
    )
    found_paths = [str(getattr(row.result, "path", "")) for row in result.results]
    assert any("conv-capstone" in p for p in found_paths), (
        f"composed search returned hits but not the ingested conversation: {found_paths}"
    )


# ---------------------------------------------------------------------------
# Leg 5 — expand never dead-ends off a seq-less search hit (PLA-297)
# ---------------------------------------------------------------------------

_EXPAND_URI = "sharepoint://site/capstone-doc"
_EXPAND_CHUNKS = 5
# A distinctive token so BM25 returns exactly the seeded document.
_EXPAND_TERM = "wombat-relay-cascade"


def _seed_chunk_document(db_path: Path) -> None:
    """Seed a real chunked document (``<source_uri>#<seq>`` rows) + its FTS index.

    Mirrors the exact chunk-row shape the worker's ``_SqliteChunkWriter``
    produces, so ``list_chunk_seqs`` resolves the document's real seqs and
    ``get_by_path`` reads each neighbour — the PLA-297 by-prefix backbone.
    """
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        for seq in range(_EXPAND_CHUNKS):
            chunk_hash = f"capstone-hash-{seq}"
            db.execute(
                "INSERT INTO documents (collection, path, hash, source_uri, sensitivity, active) "
                "VALUES (?, ?, ?, ?, 'public', 1)",
                ("team-notes", f"{_EXPAND_URI}#{seq}", chunk_hash, _EXPAND_URI),
            )
            db.execute(
                "INSERT INTO content (hash, doc) VALUES (?, ?)",
                (chunk_hash, f"{_EXPAND_TERM} chunk number {seq} of the capstone document"),
            )
        db.execute("DELETE FROM documents_fts")
        db.execute(
            """
            INSERT INTO documents_fts (rowid, filepath, title, doc)
            SELECT d.id, d.path, d.title, c.doc
            FROM documents d
            JOIN content c ON c.hash = d.hash
            WHERE d.active = 1
            """
        )
        db.commit()
    finally:
        db.close()


@pytest.mark.e2e
def test_expand_never_dead_ends_from_search_hit(tmp_path: Path) -> None:
    """Leg 5 — a seq-less search hit expands to an ordered neighbour window.

    The read-side handoff of the loop: a composed search hit hands an agent a
    ``source_uri`` (its resolvable breadcrumb) but no ``seq`` for a
    document-level hit. ``expand`` resolves the document's real chunks by
    prefix and returns an ordered window — it never dead-ends at a guessed
    ``#0`` (the PLA-297 / F98 anti-dead-end lock).

    Runs the real ``SQLiteDocumentRepository`` backbone (``get_by_path`` +
    ``list_chunk_seqs``) over a real chunked index — no fakes on the read path.

    Sabotage-proof (executed): make ``_expand_by_source_uri`` ignore
    ``list_chunk_seqs`` and hard-anchor at ``seq=0`` guessing → the ordered
    ``[0..4]`` window assertion fails on a non-chunk-0-anchored document;
    restored.
    """
    db_path = tmp_path / "index.sqlite"
    _seed_chunk_document(db_path)

    deploy = _Deploy(
        document_root=tmp_path / "documents",
        overlay=tmp_path / "documents" / "04-Agent-Knowledge",
        db_path=db_path,
        fallback_root=tmp_path / "data",
        workspace_root=tmp_path / "workspaces",
        logs=tmp_path / "logs",
    )
    (tmp_path / "documents").mkdir(parents=True, exist_ok=True)

    # A composed search surfaces the seeded document and hands back its
    # resolvable source_uri — the breadcrumb an agent feeds to expand.
    result = _composed_search(deploy, _EXPAND_TERM)
    assert result.results, f"composed search must surface the seeded document. error={result.error!r}"
    hit_source_uri = str(getattr(result.results[0].result, "source_uri", "") or "")
    assert hit_source_uri == _EXPAND_URI, (
        f"the search hit must carry the document's resolvable source_uri, got {hit_source_uri!r}"
    )

    # Seq-less expand (a doc / section-level hit): must resolve real chunks by
    # prefix and return the ordered window rather than dead-ending.
    repo = SQLiteDocumentRepository(db_path)
    out = run_expand(
        hit_source_uri,
        None,
        token_budget=10_000,
        deps=ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs),
    )

    assert out.error == "", f"seq-less expand must not error, got: {out.error!r}"
    assert [c.seq for c in out.chunks] == [0, 1, 2, 3, 4], (
        f"expand must return the ordered neighbour window, got seqs {[c.seq for c in out.chunks]}"
    )
    assert any(c.is_match for c in out.chunks), "the anchored chunk must be flagged is_match"
    assert _EXPAND_TERM in out.chunks[0].text, "real chunk content must travel through the backbone"
