"""
Tests for kairix.onboard.check — deployment health checks.

All tests use monkeypatch and tmp_path to isolate environment and filesystem.
No external services required.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kairix.onboard.check import (
    CheckResult,
    check_agent_knowledge_populated,
    check_kairix_on_path,
    check_neo4j_reachable,
    check_secrets_loaded,
    check_vault_root_configured,
    check_vector_search_working,
    check_wrapper_installed,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# check_kairix_on_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kairix_on_path_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: "/opt/openclaw/bin/kairix" if cmd == "kairix" else None)
    result = check_kairix_on_path()
    assert result.ok
    assert "/opt/openclaw/bin/kairix" in result.detail


@pytest.mark.unit
def test_kairix_on_path_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    result = check_kairix_on_path()
    assert not result.ok
    assert result.fix is not None
    assert "PATH" in result.fix


# ---------------------------------------------------------------------------
# check_wrapper_installed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_wrapper_installed_kairix_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    result = check_wrapper_installed()
    assert not result.ok
    assert "not on PATH" in result.detail


@pytest.mark.unit
def test_wrapper_installed_shell_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = tmp_path / "kairix"
    wrapper.write_bytes(b"#!/usr/bin/env bash\nexec python3 $@\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda cmd: str(wrapper))
    result = check_wrapper_installed()
    assert result.ok
    assert "wrapper installed" in result.detail


@pytest.mark.unit
def test_wrapper_installed_raw_python_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "kairix"
    binary.write_bytes(b"#!/path/to/python\n# Python script\n")
    binary.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda cmd: str(binary))
    result = check_wrapper_installed()
    assert not result.ok
    assert "raw Python binary" in result.detail
    assert result.fix is not None
    assert "kairix-wrapper.sh" in result.fix


# ---------------------------------------------------------------------------
# check_secrets_loaded
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_secrets_loaded_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-test-key-12345678")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://myresource.cognitiveservices.azure.com/")
    result = check_secrets_loaded()
    assert result.ok
    assert "sk-test-" in result.detail   # masked key
    assert "myresource" in result.detail


@pytest.mark.unit
def test_secrets_loaded_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.com/")
    result = check_secrets_loaded()
    assert not result.ok
    assert "AZURE_OPENAI_API_KEY" in result.detail
    assert result.fix is not None


@pytest.mark.unit
def test_secrets_loaded_endpoint_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-key")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = check_secrets_loaded()
    assert not result.ok
    assert "AZURE_OPENAI_ENDPOINT" in result.detail


@pytest.mark.unit
def test_secrets_loaded_both_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = check_secrets_loaded()
    assert not result.ok
    assert "AZURE_OPENAI_API_KEY" in result.detail
    assert "AZURE_OPENAI_ENDPOINT" in result.detail


# ---------------------------------------------------------------------------
# check_vault_root_configured
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vault_root_configured_set_and_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create some .md files so the count is > 0
    (tmp_path / "note.md").write_text("# Note")
    monkeypatch.setenv("KAIRIX_VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    result = check_vault_root_configured()
    assert result.ok
    assert str(tmp_path) in result.detail


@pytest.mark.unit
def test_vault_root_configured_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAIRIX_VAULT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    result = check_vault_root_configured()
    assert not result.ok
    assert "not set" in result.detail
    assert result.fix is not None


@pytest.mark.unit
def test_vault_root_configured_dir_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIRIX_VAULT_ROOT", "/nonexistent/vault/path")
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    result = check_vault_root_configured()
    assert not result.ok
    assert "does not exist" in result.detail


# ---------------------------------------------------------------------------
# check_vector_search_working
# ---------------------------------------------------------------------------


def _make_search_result(vec_count: int = 5, bm25_count: int = 3, vec_failed: bool = False) -> MagicMock:
    r = MagicMock()
    r.vec_count = vec_count
    r.bm25_count = bm25_count
    r.vec_failed = vec_failed
    r.results = [MagicMock()] * (vec_count + bm25_count)
    return r


@pytest.mark.unit
def test_vector_search_working_ok() -> None:
    mock_result = _make_search_result(vec_count=4, bm25_count=2)
    with patch("kairix.onboard.check.check_vector_search_working.__module__"), \
         patch("kairix.search.hybrid.search", return_value=mock_result):
        # Call with the patch active
        import kairix.onboard.check as mod
        with patch.object(mod, "check_vector_search_working", wraps=mod.check_vector_search_working):
            # Direct import approach
            pass

    # Patch at the function level directly
    with patch("kairix.onboard.check.__builtins__"):
        pass

    # Simpler: patch the import inside the function
    mock_search = MagicMock(return_value=_make_search_result(vec_count=4, bm25_count=2))
    with patch.dict("sys.modules", {"kairix.search.hybrid": MagicMock(search=mock_search)}):
        result = check_vector_search_working()
    assert result.ok
    assert "vec=4" in result.detail


@pytest.mark.unit
def test_vector_search_working_vec_failed() -> None:
    mock_search = MagicMock(return_value=_make_search_result(vec_count=0, vec_failed=True))
    with patch.dict("sys.modules", {"kairix.search.hybrid": MagicMock(search=mock_search)}):
        result = check_vector_search_working()
    assert not result.ok
    assert "vec_failed=True" in result.detail
    assert result.fix is not None
    assert "secrets_loaded" in result.fix


@pytest.mark.unit
def test_vector_search_working_exception() -> None:
    mock_search = MagicMock(side_effect=RuntimeError("connection refused"))
    with patch.dict("sys.modules", {"kairix.search.hybrid": MagicMock(search=mock_search)}):
        result = check_vector_search_working()
    assert not result.ok
    assert "exception" in result.detail


# ---------------------------------------------------------------------------
# check_neo4j_reachable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_neo4j_reachable_ok() -> None:
    mock_client = MagicMock()
    mock_client.available = True
    mock_client.cypher.return_value = [{"total": 76}]
    mock_get_client = MagicMock(return_value=mock_client)
    with patch.dict("sys.modules", {"kairix.graph.client": MagicMock(get_client=mock_get_client)}):
        result = check_neo4j_reachable()
    assert result.ok
    assert "76" in result.detail


@pytest.mark.unit
def test_neo4j_reachable_unavailable() -> None:
    mock_client = MagicMock()
    mock_client.available = False
    mock_get_client = MagicMock(return_value=mock_client)
    with patch.dict("sys.modules", {"kairix.graph.client": MagicMock(get_client=mock_get_client)}):
        result = check_neo4j_reachable()
    assert not result.ok
    assert "unavailable" in result.detail
    assert result.fix is not None


@pytest.mark.unit
def test_neo4j_reachable_empty_graph() -> None:
    mock_client = MagicMock()
    mock_client.available = True
    mock_client.cypher.return_value = [{"total": 0}]
    mock_get_client = MagicMock(return_value=mock_client)
    with patch.dict("sys.modules", {"kairix.graph.client": MagicMock(get_client=mock_get_client)}):
        result = check_neo4j_reachable()
    assert not result.ok
    assert "empty" in result.detail
    assert "vault crawl" in result.fix


# ---------------------------------------------------------------------------
# check_agent_knowledge_populated
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_agent_knowledge_populated_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory_dir = tmp_path / "shape" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "2026-04-14.md").write_text("# Session notes")
    monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", str(tmp_path))
    result = check_agent_knowledge_populated()
    assert result.ok
    assert "1 files" in result.detail


@pytest.mark.unit
def test_agent_knowledge_populated_missing_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", "/nonexistent/workspaces")
    result = check_agent_knowledge_populated()
    assert not result.ok
    assert result.fix is not None


@pytest.mark.unit
def test_agent_knowledge_populated_no_memory_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIRIX_WORKSPACE_ROOT", str(tmp_path))
    result = check_agent_knowledge_populated()
    assert not result.ok
    assert "No agent memory logs" in result.detail


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_all_checks_returns_list() -> None:
    """run_all_checks returns a list of CheckResult objects."""
    results = run_all_checks()
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(isinstance(r, CheckResult) for r in results)


@pytest.mark.unit
def test_run_all_checks_never_raises() -> None:
    """run_all_checks catches exceptions from individual checks."""
    import kairix.onboard.check as mod

    def exploding_check() -> CheckResult:
        raise RuntimeError("check exploded unexpectedly")

    original = mod.ALL_CHECKS[:]
    mod.ALL_CHECKS.insert(0, exploding_check)
    try:
        results = run_all_checks()
        assert any("exploded" in r.detail for r in results)
    finally:
        mod.ALL_CHECKS[:] = original


@pytest.mark.unit
def test_run_all_checks_result_names_are_unique() -> None:
    """Each check result has a distinct name."""
    results = run_all_checks()
    names = [r.name for r in results]
    assert len(names) == len(set(names)), f"Duplicate names: {names}"
