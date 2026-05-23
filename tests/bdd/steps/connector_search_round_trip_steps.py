"""Step definitions for connector_search_round_trip.feature.

Pins the IM-6 cutover regression: chunks written via the connector
framework's ``_SqliteChunkWriter`` must also land in ``documents_fts``
so BM25 retrieval finds them.

Drives the real production composition through
``ConnectorPipeline.run_batch`` with a canonical ``FakeSourceConnector``
+ ``FakeExtractor`` — F46-clean (steps reach the production pipeline,
not a hand-rolled stub).

F1-clean: no @patch / module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.bdd

_COLLECTION = "obsidian-rt"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _Ctx:
    db: sqlite3.Connection | None = None
    bronze_root: Path | None = None
    bm25_results: list[tuple[str, ...]] = field(default_factory=list)
    docs_count: int | None = None
    fts_count: int | None = None


@pytest.fixture
def connector_rt_ctx(tmp_path: Path) -> _Ctx:
    ctx = _Ctx()
    ctx.db = sqlite3.connect(":memory:")
    create_schema(ctx.db, dims=4)
    ctx.bronze_root = tmp_path / "bronze"
    ctx.bronze_root.mkdir()
    return ctx


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse('the connector framework\'s chunk writer persists a chunk with text "{text}"'))
def _given_chunk_persisted(connector_rt_ctx: _Ctx, text: str) -> None:
    assert connector_rt_ctx.db is not None
    assert connector_rt_ctx.bronze_root is not None
    fake = FakeSourceConnector(
        name=_COLLECTION,
        events=[ChangeEvent(op="created", item_id="note.md", modified_at=_now())],
        content={"note.md": text.encode("utf-8")},
    )
    pipeline = build_connector_pipeline(
        db=connector_rt_ctx.db,
        bronze_root=connector_rt_ctx.bronze_root,
        collection=_COLLECTION,
    )
    pipeline.run_batch(fake, FakeExtractor())
    connector_rt_ctx.db.commit()


@given(parsers.parse("the connector framework writes {n:d} chunks across {n2:d} source files"))
def _given_n_chunks(connector_rt_ctx: _Ctx, n: int, n2: int) -> None:
    assert n == n2, "this scenario expects 1 chunk per file"
    assert connector_rt_ctx.db is not None
    assert connector_rt_ctx.bronze_root is not None
    fake = FakeSourceConnector(
        name=_COLLECTION,
        events=[ChangeEvent(op="created", item_id=f"file-{i}.md", modified_at=_now()) for i in range(n)],
        content={f"file-{i}.md": f"file {i} content body".encode() for i in range(n)},
    )
    pipeline = build_connector_pipeline(
        db=connector_rt_ctx.db,
        bronze_root=connector_rt_ctx.bronze_root,
        collection=_COLLECTION,
    )
    pipeline.run_batch(fake, FakeExtractor())


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse('BM25 searches for "{term}"'))
def _when_bm25(connector_rt_ctx: _Ctx, term: str) -> None:
    assert connector_rt_ctx.db is not None
    rows = list(
        connector_rt_ctx.db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = ?",
            (term, _COLLECTION),
        )
    )
    connector_rt_ctx.bm25_results = rows


@when("the connector batch commits")
def _when_batch_commits(connector_rt_ctx: _Ctx) -> None:
    assert connector_rt_ctx.db is not None
    connector_rt_ctx.db.commit()
    connector_rt_ctx.docs_count = connector_rt_ctx.db.execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND active = 1",
        (_COLLECTION,),
    ).fetchone()[0]
    connector_rt_ctx.fts_count = connector_rt_ctx.db.execute(
        "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.collection = ?",
        (_COLLECTION,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("at least one result returns the chunk")
def _then_one_result(connector_rt_ctx: _Ctx) -> None:
    assert len(connector_rt_ctx.bm25_results) >= 1, (
        f"BM25 returned 0 hits — the IM-6 FTS-gap regression has returned. results={connector_rt_ctx.bm25_results!r}"
    )


@then(
    parsers.parse(
        "the count of active documents in the connector's collection equals the count of FTS rows for that collection"
    )
)
def _then_counts_match(connector_rt_ctx: _Ctx) -> None:
    assert connector_rt_ctx.docs_count is not None
    assert connector_rt_ctx.fts_count is not None
    assert connector_rt_ctx.docs_count == connector_rt_ctx.fts_count > 0, (
        f"docs={connector_rt_ctx.docs_count} fts={connector_rt_ctx.fts_count} — the IM-6 cutover failure mode is back."
    )
