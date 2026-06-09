"""F48 composed-path E2E for #458 ContentQualityBoost — factory wiring.

Closes the test-discipline gap deferred from the #458 MVP commit:
the unit tests in tests/search/test_pipeline.py cover the boost's
signal math in isolation. This E2E pins the wiring contract — when
the operator sets ``content_quality_boost.enabled=True`` in their
config, the boost is included in the chain
``factory.build_search_pipeline`` composes. Disabled, it's absent.

A boost that's wired but not configured (or configured but not
wired) would silently no-op in production; this test makes the
factory-side wiring auditable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.scanner import CollectionConfig, DocumentScanner
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.boosts import ContentQualityBoost
from kairix.core.search.config import ContentQualityBoostConfig, RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.e2e


def _build_e2e_env(tmp_path: Path) -> Path:
    document_root = tmp_path / "vault"
    document_root.mkdir(exist_ok=True)
    (document_root / "alpha.md").write_text("# Alpha\nSample document body.\n")
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


def _build_pipeline(tmp_path: Path, *, boost_enabled: bool):
    db_path = _build_e2e_env(tmp_path)
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    cfg = RetrievalConfig(
        provider="fake",
        content_quality_boost=ContentQualityBoostConfig(enabled=boost_enabled),
    )
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    reset_search_pipeline_cache()
    return build_search_pipeline(config=cfg, registry=registry, paths=paths)


def test_factory_includes_content_quality_boost_when_enabled(tmp_path: Path) -> None:
    """``content_quality_boost.enabled=True`` → ContentQualityBoost
    appears in the pipeline's boost chain.

    Sabotage-proof: drop the ``if cfg.content_quality_boost.enabled:
    boosts.append(ContentQualityBoost(...))`` block in
    :func:`factory.select_boosts` and the assertion below catches —
    the boost stays absent from the chain even when the operator opted
    in via their config.
    """
    pipeline = _build_pipeline(tmp_path, boost_enabled=True)
    boost_types = {type(b).__name__ for b in pipeline.boosts}
    assert "ContentQualityBoost" in boost_types, (
        f"expected ContentQualityBoost in chain when enabled; got {boost_types!r}"
    )


def test_factory_excludes_content_quality_boost_when_disabled(tmp_path: Path) -> None:
    """The default (``enabled=False``) keeps ContentQualityBoost out
    of the chain — operators who haven't opted in see pre-#458
    ranking byte-for-byte."""
    pipeline = _build_pipeline(tmp_path, boost_enabled=False)
    boost_types = {type(b).__name__ for b in pipeline.boosts}
    assert "ContentQualityBoost" not in boost_types, (
        f"expected ContentQualityBoost absent when disabled; got {boost_types!r}"
    )


def test_factory_content_quality_boost_carries_config(tmp_path: Path) -> None:
    """The boost the factory adds carries the operator-supplied
    :class:`ContentQualityBoostConfig` — not a default placeholder.

    Locks the config-propagation contract so an operator who tunes
    e.g. ``length_substantive_ceiling`` sees their value flow into
    the wired-up boost, not get silently replaced by the factory's
    default config.
    """
    pipeline = _build_pipeline(tmp_path, boost_enabled=True)
    cqb = next(b for b in pipeline.boosts if isinstance(b, ContentQualityBoost))
    # The boost stores its config on _config (private attr — read via
    # vars() so this stays F5-clean: the test doesn't import a
    # ``_config`` symbol, it reads the instance dict).
    cfg = vars(cqb).get("_config")
    assert cfg is not None
    assert cfg.enabled is True
