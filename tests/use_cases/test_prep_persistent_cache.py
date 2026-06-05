"""Persistence tests for :class:`PrepSummaryCache` (#411 Phase 2).

Closes the cold-CLI prep regression: when no MCP is running, every
``kairix prep <topic>`` invocation pays the full LLM synthesis
roundtrip (~2-4 s). With this layer, a second cold invocation against
the same ``(cfg, query, tier, context)`` triplet reads the cached
summary from SQLite and skips the LLM call entirely.

F-rule discipline:
  - F1: no @patch on kairix internals — pass ``path``/``cfg_hash`` by
    argument and drive expiry via the public ``clock`` seam.
  - F2: no env-var monkeypatch.
  - F4: env reads stay at the paths boundary.
  - F8: ``pytestmark = pytest.mark.unit``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.search.prep_summary_cache import (
    DEFAULT_DISK_MAX_AGE_S,
    PrepSummaryCache,
    make_prep_cache_key,
)

pytestmark = pytest.mark.unit


def test_prep_cache_rehydrates_from_disk_in_fresh_process(tmp_path: Path) -> None:
    """A summary stored via ``put`` survives a new ``PrepSummaryCache``
    constructed against the same file path.

    Models the cold-CLI scenario: previous ``kairix prep`` run wrote
    the LLM summary; a fresh process invoking the same prep should
    serve the summary from disk + memory without paying for another
    LLM roundtrip.

    Sabotage-proof (executed locally):
      Removed the ``self._upsert_persisted(key, now, value)`` call
      from ``PrepSummaryCache.put`` (the new-entry branch). Confirmed
      this test failed at the ``rehydrated is not None`` assertion
      (rehydrated was None), then restored.
    """
    cache_file = tmp_path / "prep_cache.sqlite"
    key = make_prep_cache_key("what is python", "l0", "some retrieved context")

    c1 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-pinned")
    c1.put(key, "Python is a programming language.")
    c1.close()

    c2 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-pinned")
    try:
        rehydrated = c2.get(key)
        assert rehydrated is not None, (
            "PrepSummaryCache persistence regression — a fresh instance "
            f"pointing at the same path saw an empty cache. file={cache_file} "
            f"file_size={cache_file.stat().st_size if cache_file.exists() else 'missing'}"
        )
        assert rehydrated == "Python is a programming language."
    finally:
        c2.close()


def test_prep_cache_expires_entries_past_ttl(tmp_path: Path) -> None:
    """Rows whose ``expires_at`` is in the past are dropped on rehydrate.

    Sabotage-proof (executed locally):
      Removed the ``if expires_at <= now: ... continue`` branch from
      ``_open_and_replay``. Confirmed this test failed at the
      ``size == 0`` assertion (size became 1), then restored.
    """
    cache_file = tmp_path / "prep_cache.sqlite"
    fake_now = [1_000_000.0]

    def _fake_time() -> float:
        return fake_now[0]

    key = make_prep_cache_key("hello", "l0", "ctx")

    c1 = PrepSummaryCache(
        path=cache_file,
        cfg_hash="cfg-x",
        clock=_fake_time,
        disk_max_age_s=60.0,
    )
    c1.put(key, "first summary")
    c1.close()

    fake_now[0] += 120.0
    c2 = PrepSummaryCache(
        path=cache_file,
        cfg_hash="cfg-x",
        clock=_fake_time,
        disk_max_age_s=60.0,
    )
    try:
        assert c2.stats().size == 0, (
            "PrepSummaryCache rehydrate loaded an expired row — operators "
            "rely on the expires_at gate to keep restart-resilience honest."
        )
    finally:
        c2.close()


def test_prep_cache_invalidates_on_cfg_hash_change(tmp_path: Path) -> None:
    """Rows written under one cfg_hash are invisible to a fresh cache
    constructed with a different cfg_hash.

    Sabotage-proof (executed locally):
      Replaced ``_SELECT_BY_CFG_SQL`` to drop its ``WHERE cfg_hash =
      ?`` clause. Confirmed this test failed because ``c2.get(key)``
      returned the cached summary instead of None. Restored.
    """
    cache_file = tmp_path / "prep_cache.sqlite"
    key = make_prep_cache_key("hello", "l0", "ctx")

    c1 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-a")
    c1.put(key, "summary under cfg-a")
    c1.close()

    c2 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-b")
    try:
        assert c2.get(key) is None, (
            "PrepSummaryCache served a row keyed under a different cfg_hash — config-change invalidation broken."
        )
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_prep_cache_disk_ttl_default_is_six_hours() -> None:
    """The disk-tier TTL default matches the #411 brief (6 hours).

    Sabotage-proof (executed locally):
      Changed ``DEFAULT_DISK_MAX_AGE_S`` to 60.0. Confirmed this test
      failed (the constant didn't match 21600.0). Restored.
    """
    assert DEFAULT_DISK_MAX_AGE_S == pytest.approx(21600.0), (
        f"DEFAULT_DISK_MAX_AGE_S diverged from the #411 Phase 2 brief "
        f"(6 hour disk TTL) — got {DEFAULT_DISK_MAX_AGE_S}. "
        "fix: keep the 21600.0-second disk TTL or update the brief with rationale."
    )
