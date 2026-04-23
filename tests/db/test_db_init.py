"""Tests for kairix.db — DB path resolution and extension loading."""

from pathlib import Path

import pytest


@pytest.mark.unit
def test_get_db_path_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """KAIRIX_DB_PATH env var takes priority."""
    from kairix.db import get_db_path

    db_file = tmp_path / "custom.sqlite"
    db_file.touch()
    monkeypatch.setenv("KAIRIX_DB_PATH", str(db_file))
    assert get_db_path() == db_file


@pytest.mark.unit
def test_get_db_path_prefers_kairix_over_qmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Kairix DB is preferred over legacy QMD DB."""
    from kairix.db import get_db_path

    monkeypatch.delenv("KAIRIX_DB_PATH", raising=False)
    monkeypatch.delenv("QMD_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Create both
    kairix_db = tmp_path / ".cache" / "kairix" / "index.sqlite"
    qmd_db = tmp_path / ".cache" / "qmd" / "index.sqlite"
    kairix_db.parent.mkdir(parents=True)
    qmd_db.parent.mkdir(parents=True)
    kairix_db.touch()
    qmd_db.touch()

    result = get_db_path()
    assert "kairix" in str(result)
    assert "qmd" not in str(result)


@pytest.mark.unit
def test_get_db_path_falls_back_to_qmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Falls back to QMD DB when kairix DB does not exist."""
    from kairix.db import get_db_path

    monkeypatch.delenv("KAIRIX_DB_PATH", raising=False)
    monkeypatch.delenv("QMD_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Only create QMD
    qmd_db = tmp_path / ".cache" / "qmd" / "index.sqlite"
    qmd_db.parent.mkdir(parents=True)
    qmd_db.touch()

    result = get_db_path()
    assert "qmd" in str(result)


@pytest.mark.unit
def test_get_db_path_returns_default_when_nothing_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Returns default kairix path when no DB exists anywhere."""
    from kairix.db import get_db_path

    monkeypatch.delenv("KAIRIX_DB_PATH", raising=False)
    monkeypatch.delenv("QMD_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = get_db_path()
    assert str(result).endswith("kairix/index.sqlite")
