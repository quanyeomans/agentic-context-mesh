"""Unit tests for the capability corpus builder (Feeder 1).

Sabotage-proof log (executed mutate -> fail -> restore):

* ``test_build_capability_chunk_shape`` — changed ``source_uri`` in
  ``build_capability_chunk`` from ``f"capability://kairix/{name}"`` to a wrong
  literal ``"capability://WRONG/x"``; ran the test -> FAILED on the
  ``chunk.source_uri == "capability://kairix/search"`` assertion; restored the
  line -> PASS.
"""

import pytest

pytestmark = pytest.mark.unit


def test_cap_when_to_use_emitted_only_when_set():
    # Drives the `_cap(..., when_to_use=)` extension through its public surface,
    # `tool_capabilities()` (F5: no private-helper imports). A row that supplies
    # when_to_use carries the key with that text; a minimal row omits it entirely
    # (the "emit only when non-empty" branch keeps existing entries unchanged).
    from kairix.agents.mcp.server import tool_capabilities

    by_name = {c["name"]: c for c in tool_capabilities()["capabilities"]}
    assert by_name["search"]["when_to_use"].startswith("Call before answering")
    # `warm` is a minimal diagnostic row with no when_to_use seeded.
    assert "when_to_use" not in by_name["warm"]


def test_catalogue_rows_carry_when_to_use_for_core_caps():
    from kairix.agents.mcp.server import tool_capabilities

    by_name = {c["name"]: c for c in tool_capabilities()["capabilities"]}
    # Core retrieval/synthesis caps must advertise when to reach for them.
    for name in ("search", "research", "contradict"):
        assert by_name[name].get("when_to_use", "").strip(), f"{name} missing when_to_use"


def test_build_capability_chunk_shape():
    from kairix.knowledge.capabilities.builder import build_capability_chunk

    chunk = build_capability_chunk(
        name="search",
        kind="tool",
        surface="both",
        when_to_use="Call before answering a factual question.",
        description="Hybrid search: BM25 + vector via RRF",
        mcp_tool="search",
        cli="kairix search",
        category="retrieval",
        tick_iso="2026-06-20T00:00:00+00:00",
    )
    assert chunk.source_uri == "capability://kairix/search"
    assert chunk.sensitivity == "internal"
    assert chunk.source_modified_at == "2026-06-20T00:00:00+00:00"
    # Retrieval document must contain the name, trigger text, and invocation tokens
    assert "search" in chunk.text
    assert "Call before answering" in chunk.text
    assert "kairix search" in chunk.text
    assert chunk.content_hash  # non-empty sha256


def test_catalogue_builder_maps_caps_to_chunks():
    from kairix.knowledge.capabilities.builder import CapabilityCatalogueBuilder

    fake_caps = [
        {
            "name": "search",
            "mcp_tool": "search",
            "cli": "kairix search",
            "category": "retrieval",
            "when_to_use": "Find prior work.",
        },
        {
            "name": "doctor",
            "mcp_tool": None,
            "cli": "kairix doctor",
            "category": "diagnostic",
        },  # no when_to_use, CLI-only
    ]
    builder = CapabilityCatalogueBuilder(
        catalogue_fn=lambda: fake_caps,
        now_fn=lambda: "2026-06-20T00:00:00+00:00",
    )
    chunks = builder.build_chunks()
    by_uri = {c.source_uri: c for c in chunks}
    assert by_uri["capability://kairix/search"].metadata["surface"] == "both"
    assert by_uri["capability://kairix/doctor"].metadata["surface"] == "cli"
    # A cap with an MCP tool renders the invocation token into the retrieval doc;
    # a CLI-only cap (mcp_tool=None -> falls back to "") renders no mcp line.
    assert "mcp tool: search" in by_uri["capability://kairix/search"].text
    assert "mcp tool:" not in by_uri["capability://kairix/doctor"].text


def test_default_builder_uses_real_catalogue_and_clock():
    # F86 via the PUBLIC surface (F5): a no-arg CapabilityCatalogueBuilder wires
    # the real `_default_catalogue` (tool_capabilities) + `_default_now` defaults.
    # Calling build_chunks() executes both DI-default seams without importing
    # their private names. Each chunk's source_modified_at is the ISO stamp the
    # clock seam produced.
    from datetime import datetime

    from kairix.knowledge.capabilities.builder import CapabilityCatalogueBuilder

    chunks = CapabilityCatalogueBuilder().build_chunks()
    assert chunks  # the real catalogue is non-empty
    assert all(c.source_uri.startswith("capability://kairix/") for c in chunks)
    # The default clock seam produced a tz-aware ISO-8601 stamp on every chunk.
    assert datetime.fromisoformat(chunks[0].source_modified_at).tzinfo is not None


def test_mcp_only_capability_maps_to_mcp_surface():
    # Drives the `_surface_for` "mcp" branch through the public builder: a cap
    # with an MCP tool but no CLI invocation renders surface == "mcp".
    from kairix.knowledge.capabilities.builder import CapabilityCatalogueBuilder

    builder = CapabilityCatalogueBuilder(
        catalogue_fn=lambda: [{"name": "mcp_only", "mcp_tool": "mcp_only", "cli": "", "category": "agent"}],
        now_fn=lambda: "2026-06-20T00:00:00+00:00",
    )
    chunk = builder.build_chunks()[0]
    assert chunk.metadata["surface"] == "mcp"


class _FakeWriter:
    """Capture-only chunk writer — records the chunks and reports the count."""

    def __init__(self) -> None:
        self.upserted: list = []

    def upsert(self, chunks) -> int:
        self.upserted = list(chunks)
        return len(self.upserted)


class _FakeVecIndex:
    """Capture-only usearch stand-in — records add_vectors + save calls."""

    def __init__(self) -> None:
        self.added: list = []
        self.saved = False

    def add_vectors(self, hash_seqs, vectors) -> int:
        self.added = list(zip(hash_seqs, vectors, strict=False))
        return len(vectors)

    def save(self) -> None:
        self.saved = True


def _one_cap_builder():
    from kairix.knowledge.capabilities.builder import CapabilityCatalogueBuilder

    return CapabilityCatalogueBuilder(
        catalogue_fn=lambda: [
            {"name": "search", "mcp_tool": "search", "cli": "kairix search", "category": "retrieval"}
        ],
        now_fn=lambda: "2026-06-20T00:00:00+00:00",
    )


def test_build_corpus_embeds_when_vectors_present():
    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    index = _FakeVecIndex()
    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [[0.5, 0.5] for _ in texts],
        vec_index_fn=lambda: index,
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 1
    assert result.embedded == 1
    assert result.error == ""
    assert index.saved is True
    # The vec index received exactly one (hash_seq, vector) pair for the one cap.
    assert len(index.added) == 1


def test_build_corpus_stays_bm25_only_on_vector_count_mismatch():
    # Pins the guard's count-mismatch limb: when embed_batch returns a vector
    # count that does not match the chunk count, the vec step is skipped entirely
    # (embedded == 0) and the index is never touched — BM25-only.
    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    index = _FakeVecIndex()
    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),  # one cap
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [[0.5, 0.5], [0.6, 0.6]],  # 2 vectors, 1 chunk
        vec_index_fn=lambda: index,
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 1
    assert result.embedded == 0
    assert result.error == ""
    assert index.saved is False
    assert index.added == []


def test_build_corpus_stays_bm25_only_on_zero_dim_vectors():
    # Pins the guard's zero-dim limb: a matching count of EMPTY vectors must not
    # be pushed to the index — stay BM25-only (embedded == 0).
    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    index = _FakeVecIndex()
    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),  # one cap
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [[] for _ in texts],  # one zero-dim vector
        vec_index_fn=lambda: index,
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 1
    assert result.embedded == 0
    assert result.error == ""
    assert index.saved is False


def test_build_corpus_vec_leg_failure_keeps_bm25_write_count():
    # T1 deferred minor: a vec-index-unavailable failure must NOT mask the
    # successful BM25 write count. The embed step produces non-empty vectors so
    # the vec branch is reached, then the vec index raises — the BM25 `written`
    # count is still reported truthfully (1), embedded stays 0, error stays "".
    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    class _BoomVecIndex:
        def add_vectors(self, hash_seqs, vectors) -> int:
            raise RuntimeError("vec index is read-only")

        def save(self) -> None:  # pragma: no cover - never reached (add raises first)
            raise AssertionError("save must not run when add_vectors raised")

    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [[0.5, 0.5] for _ in texts],
        vec_index_fn=lambda: _BoomVecIndex(),
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 1, "BM25 write count must survive a vec-leg failure"
    assert result.embedded == 0
    assert result.error == "", "a vec-leg failure must degrade, not surface as a build error"


def test_build_corpus_vec_leg_failure_logs_traceback(caplog):
    # The vec-leg isolation logs the swallowed failure WITH exc_info=True so the
    # vec-index error's stack trace reaches the logs. Pins exc_info against a
    # mutation that would silence it (True -> False).
    import logging

    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    class _BoomVecIndex:
        def add_vectors(self, hash_seqs, vectors) -> int:
            raise RuntimeError("vec index is read-only")

        def save(self) -> None:  # pragma: no cover - never reached
            raise AssertionError("save must not run")

    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [[0.5, 0.5] for _ in texts],
        vec_index_fn=lambda: _BoomVecIndex(),
    )
    with caplog.at_level(logging.WARNING):
        build_capability_corpus(object(), deps=deps)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "vec leg unavailable" in r.getMessage()]
    assert warnings, "expected a vec-leg-unavailable WARNING record"
    assert warnings[0].exc_info is not None  # traceback captured (exc_info=True)
    assert warnings[0].exc_info[0] is RuntimeError


def test_build_corpus_empty_catalogue_reports_error():
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )

    deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(catalogue_fn=lambda: [], now_fn=lambda: "x"),
        chunk_writer_fn=lambda _db: _FakeWriter(),
        embed_batch_fn=lambda texts: [],
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 0
    assert result.error == "no capabilities to index"


def test_build_corpus_never_raises_on_writer_failure():
    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    def _boom(_db):
        raise RuntimeError("disk full")

    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),
        chunk_writer_fn=_boom,
        embed_batch_fn=lambda texts: [],
    )
    result = build_capability_corpus(object(), deps=deps)
    assert result.written == 0
    assert "RuntimeError" in result.error
    assert "disk full" in result.error


def test_build_corpus_warning_carries_traceback(caplog):
    # The never-raise guard logs the swallowed exception WITH exc_info=True so the
    # stack trace reaches the logs (not just type+message on .error). Pins the
    # traceback against a mutation that would silence it (exc_info True -> False).
    import logging

    from kairix.knowledge.capabilities.builder import CapabilityCorpusDeps, build_capability_corpus

    def _boom(_db):
        raise RuntimeError("disk full")

    deps = CapabilityCorpusDeps(
        builder=_one_cap_builder(),
        chunk_writer_fn=_boom,
        embed_batch_fn=lambda texts: [],
    )
    with caplog.at_level(logging.WARNING):
        result = build_capability_corpus(object(), deps=deps)

    assert "RuntimeError" in result.error
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "build_capability_corpus failed" in r.getMessage()
    ]
    assert warnings, "expected a never-raise WARNING record"
    assert warnings[0].exc_info is not None  # traceback captured (exc_info=True)
    assert warnings[0].exc_info[0] is RuntimeError


def test_build_corpus_default_deps_drive_writer_and_embed_seams():
    # F86 via the PUBLIC surface (F5): build_capability_corpus(db) with a default
    # (uninjected) CapabilityCorpusDeps drives the `_default_chunk_writer` seam
    # (the real F61 writer lands every kairix cap into the BM25 store) and then
    # the `_default_embed_batch` seam (which resolves the provider config). In a
    # test env without provider creds the embed seam raises; the never-raise
    # contract catches it and surfaces `.error` while the BM25 write stands.
    import sqlite3

    from kairix.core.db.schema import create_schema
    from kairix.knowledge.capabilities.builder import build_capability_corpus

    db = sqlite3.connect(":memory:")
    create_schema(db)
    result = build_capability_corpus(db)  # no deps -> all DI defaults
    db.commit()
    written = db.execute("SELECT count(*) FROM documents WHERE collection = ?", ("capabilities",)).fetchone()[0]
    db.close()

    # The real catalogue wrote every cap through the default chunk writer.
    assert written >= 1
    # The embed seam ran; with no provider creds it surfaces via .error (never
    # raises). On a host WITH a working provider it embeds cleanly instead.
    assert result.error == "" or result.embedded == 0


def test_build_corpus_default_vec_index_seam_executes():
    # F86 via the PUBLIC surface (F5): inject only the embed seam (a public dep)
    # with non-empty vectors so the vec branch is reached, leaving vec_index_fn
    # at its `_default_vec_index` default. The canonical opener returns None when
    # worker vec writes are disabled (the test-env default), so the seam body
    # runs and the subsequent add_vectors-on-None is caught by the never-raise
    # contract -> the BM25 write still lands (written == 1).
    import sqlite3

    from kairix.core.db.schema import create_schema
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )

    db = sqlite3.connect(":memory:")
    create_schema(db)
    deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(
            catalogue_fn=lambda: [
                {"name": "search", "mcp_tool": "search", "cli": "kairix search", "category": "retrieval"}
            ],
            now_fn=lambda: "2026-06-20T00:00:00+00:00",
        ),
        embed_batch_fn=lambda texts: [[0.5, 0.5] for _ in texts],
        # vec_index_fn left at the default _default_vec_index seam.
    )
    build_capability_corpus(db, deps=deps)
    db.commit()
    written = db.execute("SELECT count(*) FROM documents WHERE collection = ?", ("capabilities",)).fetchone()[0]
    db.close()
    assert written == 1  # BM25 write landed before the vec seam ran
