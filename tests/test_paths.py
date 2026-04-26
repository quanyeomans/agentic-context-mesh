"""Tests for kairix.paths — centralised path resolution."""

from pathlib import Path

import pytest

from kairix.paths import KairixPaths, clear_cache, document_root


@pytest.fixture(autouse=True)
def _clear_path_cache():
    """Clear the path cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


@pytest.mark.unit
class TestKairixPaths:
    def test_document_root_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "/custom/vault")
        paths = KairixPaths.resolve()
        assert paths.document_root == Path("/custom/vault")

    def test_db_path_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_DB_PATH", "/custom/db/index.sqlite")
        paths = KairixPaths.resolve()
        assert paths.db_path == Path("/custom/db/index.sqlite")

    def test_log_dir_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_LOG_DIR", "/custom/logs")
        paths = KairixPaths.resolve()
        assert paths.log_dir == Path("/custom/logs")

    def test_workspace_root_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", "/custom/workspaces")
        paths = KairixPaths.resolve()
        assert paths.workspace_root == Path("/custom/workspaces")

    def test_defaults_not_data_paths(self, monkeypatch) -> None:
        """Default paths should not contain /data/ (TC-specific)."""
        monkeypatch.delenv("KAIRIX_DOCUMENT_ROOT", raising=False)
        monkeypatch.delenv("KAIRIX_DB_PATH", raising=False)
        monkeypatch.delenv("KAIRIX_LOG_DIR", raising=False)
        monkeypatch.delenv("KAIRIX_WORKSPACE_ROOT", raising=False)
        monkeypatch.delenv("KAIRIX_DOCKER", raising=False)
        paths = KairixPaths.resolve()
        assert "/data/" not in str(paths.document_root)
        assert "/data/" not in str(paths.db_path)

    def test_docker_detection_via_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_DOCKER", "1")
        monkeypatch.delenv("KAIRIX_DOCUMENT_ROOT", raising=False)
        monkeypatch.delenv("KAIRIX_DB_PATH", raising=False)
        paths = KairixPaths.resolve()
        assert str(paths.document_root) == "/data/vault"

    def test_clear_cache_allows_re_resolution(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "/first")
        paths1 = KairixPaths.resolve()
        clear_cache()
        monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "/second")
        paths2 = KairixPaths.resolve()
        assert paths1.document_root != paths2.document_root

    def test_tilde_expansion(self, monkeypatch) -> None:
        monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "~/my-vault")
        paths = KairixPaths.resolve()
        assert "~" not in str(paths.document_root)
        assert str(paths.document_root).endswith("/my-vault")


@pytest.mark.unit
class TestDocumentRootEnvVar:
    def test_document_root_from_env(self, monkeypatch, tmp_path):
        from kairix.paths import clear_cache

        clear_cache()
        monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", str(tmp_path))
        result = document_root()
        assert result == tmp_path
        clear_cache()

    def test_document_root_default_when_unset(self, monkeypatch):
        from kairix.paths import clear_cache

        clear_cache()
        monkeypatch.delenv("KAIRIX_DOCUMENT_ROOT", raising=False)
        monkeypatch.delenv("KAIRIX_DOCKER", raising=False)
        result = document_root()
        assert result == Path.home() / "Documents"
        clear_cache()
