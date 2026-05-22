"""Per-directory fixtures for end-to-end composed-path tests.

F48 tests construct ``build_search_pipeline(paths=FakePaths(...))`` against
a tmp-path SQLite. The factory's process-lifetime cache
(``kairix.core.factory._PIPELINE_CACHE``, keyed by ``RetrievalConfig``)
would hold the test's tmp-bound pipeline past test teardown, leaking the
now-deleted tmpdir into any later test that constructs a pipeline with
the same default config.

Mirrors the autouse reset pattern from ``tests/core/conftest.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_search_pipeline_cache() -> None:
    """Each E2E test starts and ends with a clean factory cache."""
    from kairix.core.factory import reset_search_pipeline_cache

    reset_search_pipeline_cache()
    yield
    reset_search_pipeline_cache()
