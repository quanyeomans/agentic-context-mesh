"""
Backwards-compat verification: kairix.vault.crawler still re-exports everything.

The canonical tests live in tests/store/test_crawler.py. This file only verifies
that the old import path (kairix.vault.crawler) continues to work via the shim.
"""

from __future__ import annotations

import warnings

import pytest


@pytest.mark.unit
def test_vault_crawler_shim_exports_crawl() -> None:
    """from kairix.vault.crawler import crawl still works via compat shim."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.crawler import crawl
    assert callable(crawl)


@pytest.mark.unit
def test_vault_crawler_shim_exports_crawl_report() -> None:
    """from kairix.vault.crawler import CrawlReport still works via compat shim."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.crawler import CrawlReport
    r = CrawlReport(vault_root="/test", dry_run=True)
    assert r.ok is True


@pytest.mark.unit
def test_vault_crawler_shim_exports_helpers() -> None:
    """Private helpers are re-exported for test compatibility."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.crawler import _as_list, _parse_frontmatter, _to_display_name, _to_slug  # noqa: F401
    assert _to_slug("Hello World") == "hello-world"
    assert _to_display_name("hello-world") == "Hello World"
    assert _as_list(None) == []


@pytest.mark.unit
def test_vault_crawler_shim_emits_deprecation_warning() -> None:
    """Importing from kairix.vault.crawler should emit a DeprecationWarning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import importlib

        import kairix.vault.crawler

        importlib.reload(kairix.vault.crawler)
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "kairix.store" in str(dep_warnings[0].message) or "deprecated" in str(dep_warnings[0].message).lower()
