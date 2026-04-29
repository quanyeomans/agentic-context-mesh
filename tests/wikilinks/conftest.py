"""Fixtures for wikilinks tests."""

import pytest


@pytest.fixture(autouse=True)
def _set_test_roots(monkeypatch):
    """Set document store/workspace roots before wikilinks module resolves them."""
    monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "/tmp/test-vault")
    monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", "/tmp/test-workspaces")

    # Force reimport so module-level variables pick up the new env vars
    import importlib

    import kairix.knowledge.wikilinks.injector

    importlib.reload(kairix.knowledge.wikilinks.injector)
