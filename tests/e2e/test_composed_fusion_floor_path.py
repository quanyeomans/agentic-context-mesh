"""F48 composed-path E2E for #455 fusion floor + cross-layer dedup — factory wiring.

Unit tests in tests/search/test_pipeline.py cover the floor + dedup
math via SearchPipeline.search() at unit scope. This E2E pins the
wiring contract — the operator's :class:`RetrievalConfig` values for
``fact_layer_min_floor``, ``chunk_layer_min_floor`` and
``cross_layer_dedup_enabled`` flow through
``factory.build_search_pipeline`` to the constructed pipeline's
``self.config`` (which ``_fuse_with_intent`` reads on every call).

A regression where the factory ignored these knobs would silently
mean the operator's config has no effect on fusion; this test makes
the propagation auditable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.scanner import CollectionConfig, DocumentScanner
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.e2e


def _build_e2e_env(tmp_path: Path) -> Path:
    document_root = tmp_path / "vault"
    document_root.mkdir(exist_ok=True)
    (document_root / "alpha.md").write_text("# Alpha\nSample document.\n")
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db)
    scanner = DocumentScanner(db, document_root=document_root)
    scanner.scan([CollectionConfig(name="vault", path=".")])
    db.commit()
    db.close()
    return db_path


def _build_pipeline(tmp_path: Path, *, cfg: RetrievalConfig):
    db_path = _build_e2e_env(tmp_path)
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    reset_search_pipeline_cache()
    return build_search_pipeline(config=cfg, registry=registry, paths=paths)


def test_fact_layer_min_floor_propagates_through_factory(tmp_path: Path) -> None:
    """``RetrievalConfig.fact_layer_min_floor=0.4`` is preserved on
    ``pipeline.config.fact_layer_min_floor`` so ``_fuse_with_intent``
    reads the operator-supplied floor on every search.

    Sabotage-proof: drop the ``fact_layer_min_floor`` field reference
    in ``_fuse_with_intent`` (revert to ``max_fact = max_fact_raw or
    1.0``) — this test still passes because the *config* propagates,
    but the unit tests in ``test_pipeline.py::test_fact_floor_set_demotes_...``
    fail. The two tests together cover both the wiring + the math.
    """
    cfg = RetrievalConfig(provider="fake", fact_layer_min_floor=0.4)
    pipeline = _build_pipeline(tmp_path, cfg=cfg)
    assert pipeline.config.fact_layer_min_floor == 0.4


def test_chunk_layer_min_floor_propagates_through_factory(tmp_path: Path) -> None:
    """``chunk_layer_min_floor`` propagates symmetrically with the
    fact-side floor."""
    cfg = RetrievalConfig(provider="fake", chunk_layer_min_floor=0.5)
    pipeline = _build_pipeline(tmp_path, cfg=cfg)
    assert pipeline.config.chunk_layer_min_floor == 0.5


def test_cross_layer_dedup_enabled_propagates_through_factory(tmp_path: Path) -> None:
    """The ``cross_layer_dedup_enabled`` flag propagates so
    ``_fuse_with_intent`` enters the dedup branch when the operator
    opts in via their config.

    Sabotage-proof: drop ``cross_layer_dedup_enabled`` from
    :class:`RetrievalConfig` and the assertion below catches at
    AttributeError.
    """
    cfg = RetrievalConfig(provider="fake", cross_layer_dedup_enabled=True)
    pipeline = _build_pipeline(tmp_path, cfg=cfg)
    assert pipeline.config.cross_layer_dedup_enabled is True


def test_fusion_floor_defaults_preserve_pre_455_behaviour(tmp_path: Path) -> None:
    """A default ``RetrievalConfig()`` keeps both floors at 0.0 and
    dedup off — operators who haven't opted in see byte-for-byte
    pre-#455 ranking."""
    cfg = RetrievalConfig(provider="fake")
    pipeline = _build_pipeline(tmp_path, cfg=cfg)
    assert pipeline.config.fact_layer_min_floor == 0.0
    assert pipeline.config.chunk_layer_min_floor == 0.0
    assert pipeline.config.cross_layer_dedup_enabled is False
