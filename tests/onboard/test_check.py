"""
Tests for kairix.onboard.check deployment health checks.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kairix.onboard.check import (
    CheckResult,
    check_document_root_configured,
    check_neo4j_reachable,
    check_secrets_loaded,
    check_wrapper_installed,
    run_all_checks,
)

# ---------------------------------------------------------------------------
# check_wrapper_installed — Docker skip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_wrapper_check_skipped_in_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """In Docker, wrapper_installed check returns ok=True without probing the binary."""
    monkeypatch.setenv("KAIRIX_DOCKER", "1")
    result = check_wrapper_installed()
    assert result.ok is True
    assert "Docker" in result.detail


# ---------------------------------------------------------------------------
# check_neo4j_reachable — fix hint content
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_neo4j_fix_hint_contains_install_script() -> None:
    """fix hint must reference install-neo4j.sh when Neo4j is unavailable."""
    mock_client = MagicMock()
    mock_client.available = False

    with patch("kairix.graph.client.get_client", return_value=mock_client):
        result = check_neo4j_reachable()

    assert not result.ok
    assert result.fix is not None
    assert "install-neo4j.sh" in result.fix
    assert "docker" in result.fix.lower()
    assert "optional" in result.fix.lower()


@pytest.mark.unit
def test_neo4j_fix_hint_contains_docker_run() -> None:
    """fix hint must include a docker run command as a quick-start option."""
    mock_client = MagicMock()
    mock_client.available = False

    with patch("kairix.graph.client.get_client", return_value=mock_client):
        result = check_neo4j_reachable()

    assert result.fix is not None
    assert "neo4j:5-community" in result.fix


@pytest.mark.unit
def test_neo4j_reachable_ok_when_has_nodes() -> None:
    """Returns ok=True when Neo4j is reachable and contains at least one node."""
    mock_client = MagicMock()
    mock_client.available = True
    mock_client.cypher.return_value = [{"total": 42}]

    with patch("kairix.graph.client.get_client", return_value=mock_client):
        result = check_neo4j_reachable()

    assert result.ok
    assert "42" in result.detail


@pytest.mark.unit
def test_neo4j_reachable_fail_when_empty() -> None:
    """Returns ok=False when Neo4j is reachable but empty (document store crawler not run)."""
    mock_client = MagicMock()
    mock_client.available = True
    mock_client.cypher.return_value = [{"total": 0}]

    with patch("kairix.graph.client.get_client", return_value=mock_client):
        result = check_neo4j_reachable()

    assert not result.ok
    assert result.fix is not None


@pytest.mark.unit
def test_neo4j_check_exception_surfaces_as_failed_result() -> None:
    """Exceptions from Neo4j client are caught and returned as a failed CheckResult."""
    with patch("kairix.graph.client.get_client", side_effect=ImportError("neo4j not installed")):
        result = check_neo4j_reachable()

    assert not result.ok
    assert result.fix is not None


# ---------------------------------------------------------------------------
# check_secrets_loaded — two-tier probe
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_secrets_loaded_ok_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key-abc12345")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    result = check_secrets_loaded()
    assert result.ok
    assert "key-abc1" in result.detail  # masked key present


@pytest.mark.unit
def test_secrets_loaded_fail_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("KAIRIX_SECRETS_FILE", raising=False)
    result = check_secrets_loaded()
    assert not result.ok
    assert result.fix is not None


@pytest.mark.unit
def test_secrets_loaded_ok_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tier 2: secrets file with both keys present returns ok=True."""
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)

    secrets_file = tmp_path / "kairix.env"
    secrets_file.write_text("AZURE_OPENAI_API_KEY=test-key\nAZURE_OPENAI_ENDPOINT=https://example.openai.azure.com/\n")
    monkeypatch.setenv("KAIRIX_SECRETS_FILE", str(secrets_file))

    result = check_secrets_loaded()
    assert result.ok
    assert "Secrets file" in result.detail


# ---------------------------------------------------------------------------
# check_document_root_configured (document root configuration check)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_document_root_configured_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    md_file = tmp_path / "note.md"
    md_file.write_text("# test")
    monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", str(tmp_path))
    result = check_document_root_configured()
    assert result.ok
    assert str(tmp_path) in result.detail


@pytest.mark.unit
def test_document_root_configured_missing_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIRIX_DOCUMENT_ROOT", "/nonexistent/path/vault")
    result = check_document_root_configured()
    assert not result.ok
    assert result.fix is not None


@pytest.mark.unit
def test_document_root_configured_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAIRIX_DOCUMENT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    result = check_document_root_configured()
    assert not result.ok
    assert result.fix is not None


# ---------------------------------------------------------------------------
# run_all_checks — structural
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_all_checks_returns_list_of_check_results() -> None:
    """run_all_checks always returns a list of CheckResult objects without raising."""
    results = run_all_checks()
    assert isinstance(results, list)
    assert len(results) > 0
    for r in results:
        assert isinstance(r, CheckResult)
        assert isinstance(r.name, str)
        assert isinstance(r.ok, bool)
        assert isinstance(r.detail, str)
