"""
Tests for kairix.secrets — sidecar secrets file loader.

All tests use tmp_path and monkeypatch to isolate env and filesystem state.
No external services required.
"""

from __future__ import annotations

import os

import pytest

from kairix.secrets import load_secrets

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_secrets(tmp_path, content: str) -> str:
    """Write a secrets file and return its path as a string."""
    p = tmp_path / "kairix.env"
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# File absent / empty
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_returns_zero_when_file_absent(tmp_path) -> None:
    count = load_secrets(str(tmp_path / "nonexistent.env"))
    assert count == 0


@pytest.mark.unit
def test_returns_zero_for_empty_file(tmp_path) -> None:
    path = _write_secrets(tmp_path, "")
    assert load_secrets(path) == 0


@pytest.mark.unit
def test_returns_zero_for_comments_only(tmp_path) -> None:
    path = _write_secrets(tmp_path, "# This is a comment\n# Another comment\n")
    assert load_secrets(path) == 0


# ---------------------------------------------------------------------------
# Loading values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loads_single_key_value(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TEST_VAR_ALPHA", raising=False)
    path = _write_secrets(tmp_path, "TEST_VAR_ALPHA=hello\n")
    count = load_secrets(path)
    assert count == 1
    assert os.environ["TEST_VAR_ALPHA"] == "hello"
    monkeypatch.delenv("TEST_VAR_ALPHA")


@pytest.mark.unit
def test_loads_multiple_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SECRET_A", raising=False)
    monkeypatch.delenv("SECRET_B", raising=False)
    path = _write_secrets(tmp_path, "SECRET_A=val1\nSECRET_B=val2\n")
    count = load_secrets(path)
    assert count == 2
    assert os.environ["SECRET_A"] == "val1"
    assert os.environ["SECRET_B"] == "val2"
    monkeypatch.delenv("SECRET_A")
    monkeypatch.delenv("SECRET_B")


@pytest.mark.unit
def test_value_with_equals_sign(tmp_path, monkeypatch) -> None:
    """Values containing '=' are supported (partition splits on first '=' only)."""
    monkeypatch.delenv("URL_VAR", raising=False)
    path = _write_secrets(tmp_path, "URL_VAR=https://example.com/path?foo=bar\n")
    load_secrets(path)
    assert os.environ["URL_VAR"] == "https://example.com/path?foo=bar"
    monkeypatch.delenv("URL_VAR")


@pytest.mark.unit
def test_ignores_blank_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ONLY_VAR", raising=False)
    path = _write_secrets(tmp_path, "\n\nONLY_VAR=yes\n\n")
    count = load_secrets(path)
    assert count == 1
    monkeypatch.delenv("ONLY_VAR")


@pytest.mark.unit
def test_ignores_comment_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REAL_VAR", raising=False)
    content = "# comment\nREAL_VAR=real\n# another comment\n"
    path = _write_secrets(tmp_path, content)
    count = load_secrets(path)
    assert count == 1
    assert os.environ["REAL_VAR"] == "real"
    monkeypatch.delenv("REAL_VAR")


@pytest.mark.unit
def test_ignores_lines_without_equals(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOOD_VAR", raising=False)
    content = "BADLINE\nGOOD_VAR=ok\n"
    path = _write_secrets(tmp_path, content)
    count = load_secrets(path)
    assert count == 1
    monkeypatch.delenv("GOOD_VAR")


# ---------------------------------------------------------------------------
# Priority: existing env vars are NOT overwritten
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_does_not_overwrite_existing_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROTECTED_VAR", "original")
    path = _write_secrets(tmp_path, "PROTECTED_VAR=override\n")
    count = load_secrets(path)
    assert count == 0  # not loaded — already set
    assert os.environ["PROTECTED_VAR"] == "original"


@pytest.mark.unit
def test_partial_load_when_some_already_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALREADY_SET", "existing")
    monkeypatch.delenv("NOT_SET_YET", raising=False)
    content = "ALREADY_SET=new\nNOT_SET_YET=fresh\n"
    path = _write_secrets(tmp_path, content)
    count = load_secrets(path)
    assert count == 1
    assert os.environ["ALREADY_SET"] == "existing"
    assert os.environ["NOT_SET_YET"] == "fresh"
    monkeypatch.delenv("NOT_SET_YET")


# ---------------------------------------------------------------------------
# KAIRIX_SECRETS_FILE env var controls default path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_uses_kairix_secrets_file_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ENV_FROM_FILE", raising=False)
    path = _write_secrets(tmp_path, "ENV_FROM_FILE=loaded\n")
    monkeypatch.setenv("KAIRIX_SECRETS_FILE", path)
    count = load_secrets()  # no explicit path — reads from env var
    assert count == 1
    assert os.environ["ENV_FROM_FILE"] == "loaded"
    monkeypatch.delenv("ENV_FROM_FILE")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_returns_zero_on_permission_error(tmp_path, monkeypatch) -> None:
    """load_secrets should not raise even if the file can't be read."""
    path = _write_secrets(tmp_path, "X=1\n")
    # Make the file unreadable
    import os as _os

    _os.chmod(path, 0o000)
    try:
        count = load_secrets(path)
        assert count == 0
    finally:
        _os.chmod(path, 0o644)


@pytest.mark.unit
def test_idempotent_multiple_calls(tmp_path, monkeypatch) -> None:
    """Calling load_secrets twice is safe — second call adds nothing."""
    monkeypatch.delenv("IDEMPOTENT_VAR", raising=False)
    path = _write_secrets(tmp_path, "IDEMPOTENT_VAR=once\n")
    count1 = load_secrets(path)
    count2 = load_secrets(path)
    assert count1 == 1
    assert count2 == 0  # already set after first call
    assert os.environ["IDEMPOTENT_VAR"] == "once"
    monkeypatch.delenv("IDEMPOTENT_VAR")
