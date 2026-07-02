"""End-to-end composed production path for enumeration completion (#437, F48 sibling).

Exercises the full composed path that makes "enumerate the whole list" real:

  real connector pipeline (``factory.build_connector_pipeline``)
    → real silver chunking of a bulleted techniques catalogue into many chunks
    → real ``_SqliteChunkWriter`` persisting ``<source_uri>#<seq>`` rows
    → real composed search (``factory.build_search_pipeline`` + FakePaths)
    → the prep use case (``run_prep``) with the REAL ``run_expand`` backbone
      over the on-disk index

The defect (#437): retrieval stops at the top-N snippets, so a source that
lists N techniques returns only a sample. This proves the composed path now
surfaces the COMPLETE catalogue — source-cohesion enumeration completion pulls
the dominant source's every chunk through the same ``<source_uri>#<seq>`` key
the writer enumerates.

The chat seam echoes the LLM context so the assertion reads exactly what
synthesis was grounded in, without a live model.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline, build_search_pipeline, reset_search_pipeline_cache
from kairix.core.protocols import ChangeEvent
from kairix.core.search.config import RetrievalConfig
from kairix.use_cases.expand import ExpandDeps, run_expand
from kairix.use_cases.prep import PrepDeps, reset_prep_summary_cache, run_prep
from tests.fakes import (
    FakeExtractor,
    FakePaths,
    FakeProvider,
    FakeProviderRegistry,
    FakeSourceConnector,
)

pytestmark = pytest.mark.e2e

_COLLECTION = "reference"
_ITEM = "pretotyping-methods.md"
_TECHNIQUES = [
    "Mechanical Turk",
    "Pinocchio",
    "Stripped Tease",
    "Provincial",
    "Fake Door",
    "Pretend-to-Own",
    "Re-label",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_ENUM_QUERY = "pretotyping technique validates market demand"


def _techniques_markdown() -> bytes:
    """A bulleted techniques catalogue big enough to split into many chunks.

    Every bullet carries the recurring phrase in ``_ENUM_QUERY`` so a single
    query retrieves EVERY chunk (not just the heading) — that is what makes the
    top hits cohere on this one source. Each bullet is padded so silver
    chunking (~1000-char target) flushes one technique per chunk, guaranteeing
    the list spans more chunks than the top-5 retrieval slice can hold.
    """
    lines = ["# Pretotyping techniques", ""]
    for name in _TECHNIQUES:
        body = (
            f"{name} is a pretotyping technique that validates real market demand "
            f"before you build the product. Use {name} to run a cheap experiment. "
        ) * 5
        lines.append(f"- {name}: {body.strip()}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _populate_fts(db: sqlite3.Connection) -> None:
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


def _ingest(tmp_path: Path) -> tuple[Path, str]:
    """Ingest the catalogue and return (db_path, source_uri)."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        connector = FakeSourceConnector(
            name=_COLLECTION,
            events=[ChangeEvent(op="created", item_id=_ITEM, modified_at=_now())],
            content={_ITEM: _techniques_markdown()},
        )
        pipeline = build_connector_pipeline(db=db, collection=_COLLECTION)
        result = pipeline.run_batch(connector, FakeExtractor())
        db.commit()
        assert result.processed == 1, f"expected 1 item processed; got {result}"
        _populate_fts(db)
        db.commit()
        rows = db.execute(
            "SELECT source_uri, path FROM documents WHERE collection = ? AND active = 1 ORDER BY path",
            (_COLLECTION,),
        ).fetchall()
    finally:
        db.close()
    source_uri = rows[0][0]
    assert source_uri, f"connector wrote no source_uri; rows={rows!r}"
    assert len(rows) > 5, f"expected the catalogue to chunk past the top-5 slice; got {len(rows)} chunks"
    return db_path, source_uri


def test_composed_prep_enumerates_full_catalogue(tmp_path: Path) -> None:
    """Composed ingest → search → prep surfaces every technique (#437).

    Proves the real wiring: the chunk KEY the writer enumerates
    (``<source_uri>#<seq>``) is the exact key ``run_expand`` reads back, and
    prep's source-cohesion completion pulls the whole catalogue so synthesis
    is grounded in every technique — not just the score-ranked top-5.
    """
    db_path, _source_uri = _ingest(tmp_path)

    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    reset_search_pipeline_cache()
    reset_prep_summary_cache()
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    search = build_search_pipeline(config=RetrievalConfig(provider="fake"), registry=registry, paths=paths)

    repo = SQLiteDocumentRepository(db_path)

    def _search_fn(**kwargs: Any):
        return search.search(**kwargs)

    def _echo_chat(**kwargs: Any) -> str:
        return str(kwargs["messages"][1]["content"])

    def _expand_fn(source_uri: str):
        return run_expand(
            source_uri,
            token_budget=12000,
            deps=ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs),
        )

    deps = PrepDeps(search_fn=_search_fn, chat_fn=_echo_chat, expand_fn=_expand_fn)
    out = run_prep(_ENUM_QUERY, tier="l1", deps=deps)

    assert out.error == "", f"prep errored: {out.error!r}"
    missing = [name for name in _TECHNIQUES if name not in out.summary]
    assert not missing, f"composed prep dropped enumerated techniques: {missing}"
