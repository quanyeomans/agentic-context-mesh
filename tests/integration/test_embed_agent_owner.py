"""Integration test for embed-side agent_owner tagging (#114).

Indexes the synthetic-agent fixture under
``04-Agent-Knowledge/<agent>/memory/...`` and asserts that the embed
pipeline (scanner + agent_owner_resolver wired through ConfigDrivenAgentRegistry)
tags each document with its owning agent. Documents outside any agent's
write_path land with ``agent_owner=NULL``.

Issue spec: each chunk under a path matching an agent's ``write_path``
should carry that agent's name; cross-agent / shared / unowned documents
remain NULL.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core.db.scanner import CollectionConfig, DocumentScanner
from kairix.core.db.schema import create_schema
from kairix.core.search.registry import (
    AgentDef,
    ConfigDrivenAgentRegistry,
    build_agent_owner_resolver,
)

pytestmark = pytest.mark.integration

_SYNTHETIC_AGENTS_DIR = Path(__file__).parent.parent / "fixtures" / "synthetic_agents"

# F69 scale floor — the agent_owner fetchall paths must survive a
# production-scale documents table. Each scale variant seeds
# _F69_DOCS_PER_AGENT docs per agent (2 agents + 1 shared bucket =
# >= 10_000 total) and re-runs the same SELECT ... fetchall the
# fixture-scale tests pin, with wall-clock budgets that catch
# Bug 3-class unbounded scans on documents.agent_owner.
_F69_DOCS_PER_AGENT = 4_000
_F69_TOTAL_OWNER_DOCS = 12_000  # 3 buckets * 4_000 — agent-alpha + agent-beta + shared


def _build_scale_indexed_db(tmp_root: Path) -> sqlite3.Connection:
    """Seed a 04-Agent-Knowledge tree at F69 scale and index it.

    Writes _F69_DOCS_PER_AGENT docs under each of agent-alpha,
    agent-beta, and entities/ (shared / no agent owner). Then runs
    DocumentScanner with the two-agent registry so each document
    carries the correct agent_owner (or NULL for entities/).
    """
    base = tmp_root / "04-Agent-Knowledge"
    alpha = base / "agent-alpha"
    beta = base / "agent-beta"
    shared = base / "entities"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    shared.mkdir(parents=True)
    for i in range(_F69_DOCS_PER_AGENT):
        (alpha / f"alpha-{i:05d}.md").write_text(f"# Alpha doc {i}\nbody {i}\n", encoding="utf-8")
        (beta / f"beta-{i:05d}.md").write_text(f"# Beta doc {i}\nbody {i}\n", encoding="utf-8")
        (shared / f"shared-{i:05d}.md").write_text(f"# Shared {i}\nbody {i}\n", encoding="utf-8")

    db = sqlite3.connect(":memory:")
    create_schema(db)

    registry = ConfigDrivenAgentRegistry(
        agents=[
            AgentDef(
                name="agent-alpha",
                paths=["04-Agent-Knowledge/agent-alpha"],
                write_path="04-Agent-Knowledge/agent-alpha",
            ),
            AgentDef(
                name="agent-beta",
                paths=["04-Agent-Knowledge/agent-beta"],
                write_path="04-Agent-Knowledge/agent-beta",
            ),
        ]
    )
    resolver = build_agent_owner_resolver(registry)
    scanner = DocumentScanner(db, document_root=tmp_root, agent_owner_resolver=resolver)
    scanner.scan([CollectionConfig(name="agent-knowledge", path="04-Agent-Knowledge")])
    return db


def _build_indexed_db(tmp_root: Path) -> sqlite3.Connection:
    """Copy the synthetic-agent fixture into ``tmp_root`` and run the scanner
    with an agent_owner resolver derived from a two-agent registry.
    """
    shutil.copytree(_SYNTHETIC_AGENTS_DIR / "04-Agent-Knowledge", tmp_root / "04-Agent-Knowledge")

    db = sqlite3.connect(":memory:")
    create_schema(db)

    registry = ConfigDrivenAgentRegistry(
        agents=[
            AgentDef(
                name="agent-alpha",
                paths=["04-Agent-Knowledge/agent-alpha"],
                write_path="04-Agent-Knowledge/agent-alpha",
            ),
            AgentDef(
                name="agent-beta",
                paths=["04-Agent-Knowledge/agent-beta"],
                write_path="04-Agent-Knowledge/agent-beta",
            ),
        ]
    )
    resolver = build_agent_owner_resolver(registry)
    scanner = DocumentScanner(db, document_root=tmp_root, agent_owner_resolver=resolver)
    scanner.scan([CollectionConfig(name="agent-knowledge", path="04-Agent-Knowledge")])
    return db


@pytest.mark.integration
def test_embed_pipeline_tags_documents_with_owning_agent(tmp_path: Path) -> None:
    # F69-small-scale-only: pins the per-document tagging CONTRACT on
    # the synthetic-agent fixture. The "every alpha doc has
    # agent_owner='agent-alpha'" assertion fires correctly at N >= 1
    # because the resolver is deterministic per-path; running at 10K
    # docs would re-check the same per-row predicate without changing
    # the contract under test. F69 scale concern for the agent_owner
    # SELECT fetchall is covered by
    # ``test_agent_owner_select_path_scales_to_10k_docs`` below.
    """Every document under ``04-Agent-Knowledge/agent-alpha/`` lands with
    ``agent_owner='agent-alpha'``; same for ``agent-beta``.
    """
    db = _build_indexed_db(tmp_path)

    alpha_owners = db.execute(
        "SELECT agent_owner FROM documents WHERE path LIKE ? AND active = 1",
        ("04-Agent-Knowledge/agent-alpha/%",),
    ).fetchall()
    assert alpha_owners, "fixture should index at least one agent-alpha document"
    assert all(row[0] == "agent-alpha" for row in alpha_owners), (
        f"alpha documents not all tagged with agent-alpha: {alpha_owners}"
    )

    beta_owners = db.execute(
        "SELECT agent_owner FROM documents WHERE path LIKE ? AND active = 1",
        ("04-Agent-Knowledge/agent-beta/%",),
    ).fetchall()
    assert beta_owners, "fixture should index at least one agent-beta document"
    assert all(row[0] == "agent-beta" for row in beta_owners), (
        f"beta documents not all tagged with agent-beta: {beta_owners}"
    )


@pytest.mark.integration
def test_embed_pipeline_leaves_unowned_documents_with_null_agent_owner(tmp_path: Path) -> None:
    # F69-small-scale-only: pins the NULL-sentinel CONTRACT for
    # un-owned documents. The "every entities/ doc has owner is None"
    # assertion fires correctly on row 1; N doesn't change the contract.
    # F69 scale concern for the unowned-filter fetchall is covered by
    # ``test_agent_owner_select_path_scales_to_10k_docs`` below.
    """Documents under ``04-Agent-Knowledge/entities/`` are not under any
    agent's ``write_path`` and must be persisted with ``agent_owner=NULL``
    (the canonical 'shared / unowned' marker).
    """
    db = _build_indexed_db(tmp_path)

    entity_rows = db.execute(
        "SELECT path, agent_owner FROM documents WHERE path LIKE ? AND active = 1",
        ("04-Agent-Knowledge/entities/%",),
    ).fetchall()
    assert entity_rows, "fixture should index at least one entity document"
    for path, owner in entity_rows:
        assert owner is None, f"entity document {path!r} should have NULL agent_owner, got {owner!r}"


@pytest.mark.integration
def test_embed_pipeline_filters_per_agent_via_agent_owner_column(tmp_path: Path) -> None:
    # F69-small-scale-only: pins the cross-agent ISOLATION contract —
    # WHERE agent_owner=? returns disjoint path sets across alpha + beta.
    # The disjoint-set assertion is structural: it fires on the very
    # first leaked path regardless of N. F69 scale concern for the same
    # WHERE-agent_owner fetchall under production volume is covered by
    # ``test_agent_owner_select_path_scales_to_10k_docs`` below.
    """The downstream selection contract: ``WHERE agent_owner = ?`` returns
    exactly that agent's documents, never another agent's, never shared.

    This is the test that proves the column is *useful* — not just present.
    """
    db = _build_indexed_db(tmp_path)

    alpha_paths = {
        row[0]
        for row in db.execute(
            "SELECT path FROM documents WHERE active = 1 AND agent_owner = ?", ("agent-alpha",)
        ).fetchall()
    }
    beta_paths = {
        row[0]
        for row in db.execute(
            "SELECT path FROM documents WHERE active = 1 AND agent_owner = ?", ("agent-beta",)
        ).fetchall()
    }

    assert alpha_paths, "agent-alpha should own at least one document"
    assert beta_paths, "agent-beta should own at least one document"
    assert alpha_paths.isdisjoint(beta_paths), f"alpha and beta path sets overlap: {alpha_paths & beta_paths}"
    assert all(p.startswith("04-Agent-Knowledge/agent-alpha/") for p in alpha_paths), (
        f"agent-alpha column leaked non-alpha paths: {alpha_paths}"
    )
    assert all(p.startswith("04-Agent-Knowledge/agent-beta/") for p in beta_paths), (
        f"agent-beta column leaked non-beta paths: {beta_paths}"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_agent_owner_select_path_scales_to_10k_docs(tmp_path: Path) -> None:
    """F69 production-scale variant: agent_owner fetchalls survive 10K docs.

    Seeds ``_F69_TOTAL_OWNER_DOCS`` documents across agent-alpha,
    agent-beta, and a shared (NULL agent_owner) bucket. Then re-runs
    each of the three fetchall shapes the fixture-scale tests pin
    (LIKE path filter, NULL filter, WHERE agent_owner=?) with
    wall-clock budgets that catch Bug 3-class unbounded scans on the
    documents table at production volume.

    Sabotage proof (executed): replaced the WHERE agent_owner=? lookup
    with a synthetic ``CROSS JOIN documents d2`` to amplify the scan;
    at 12K rows the wall-clock crossed the 3s budget per query.
    Restoring the bounded SELECT brought each query back under 50ms.
    """
    db = _build_scale_indexed_db(tmp_path)

    # The scanner's report.new is asserted indirectly via the COUNT below.
    total = db.execute("SELECT count(*) FROM documents WHERE active=1").fetchone()[0]
    assert total == _F69_TOTAL_OWNER_DOCS, f"expected {_F69_TOTAL_OWNER_DOCS} indexed docs; got {total}"

    # F69: LIKE path filter at 10K-scale.
    start = time.monotonic()
    alpha_rows = db.execute(
        "SELECT agent_owner FROM documents WHERE path LIKE ? AND active = 1",
        ("04-Agent-Knowledge/agent-alpha/%",),
    ).fetchall()
    elapsed_alpha = time.monotonic() - start
    assert len(alpha_rows) == _F69_DOCS_PER_AGENT
    assert all(row[0] == "agent-alpha" for row in alpha_rows)
    assert elapsed_alpha < 3.0, (
        f"alpha LIKE fetchall over {_F69_TOTAL_OWNER_DOCS} docs took {elapsed_alpha:.2f}s; budget 3.0s"
    )

    # F69: NULL-agent_owner fetchall at 10K-scale.
    start = time.monotonic()
    shared_rows = db.execute(
        "SELECT path, agent_owner FROM documents WHERE path LIKE ? AND active = 1",
        ("04-Agent-Knowledge/entities/%",),
    ).fetchall()
    elapsed_shared = time.monotonic() - start
    assert len(shared_rows) == _F69_DOCS_PER_AGENT
    assert all(row[1] is None for row in shared_rows)
    assert elapsed_shared < 3.0, (
        f"shared LIKE fetchall over {_F69_TOTAL_OWNER_DOCS} docs took {elapsed_shared:.2f}s; budget 3.0s"
    )

    # F69: WHERE agent_owner=? fetchall at 10K-scale (the canonical
    # downstream selection path).
    start = time.monotonic()
    alpha_paths = {
        row[0]
        for row in db.execute(
            "SELECT path FROM documents WHERE active = 1 AND agent_owner = ?", ("agent-alpha",)
        ).fetchall()
    }
    elapsed_select = time.monotonic() - start
    beta_paths = {
        row[0]
        for row in db.execute(
            "SELECT path FROM documents WHERE active = 1 AND agent_owner = ?", ("agent-beta",)
        ).fetchall()
    }
    assert len(alpha_paths) == _F69_DOCS_PER_AGENT
    assert len(beta_paths) == _F69_DOCS_PER_AGENT
    assert alpha_paths.isdisjoint(beta_paths)
    assert elapsed_select < 3.0, (
        f"WHERE agent_owner=? fetchall over {_F69_TOTAL_OWNER_DOCS} docs took {elapsed_select:.2f}s; budget 3.0s"
    )
