"""Fixtures for wikilinks tests."""
import os
import pytest


@pytest.fixture(autouse=True)
def _set_test_roots(monkeypatch):
    """Set vault/workspace roots before wikilinks module resolves them."""
    monkeypatch.setenv("KAIRIX_VAULT_ROOT", "/tmp/test-vault")
    monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", "/tmp/test-workspaces")

    # Force reimport so module-level variables pick up the new env vars
    import importlib
    import kairix.wikilinks.injector
    importlib.reload(kairix.wikilinks.injector)
