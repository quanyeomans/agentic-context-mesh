"""Tests for kairix.secrets.probe — canonical-first LLM credential probe (GH #473)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.secrets.probe import llm_credentials_available

pytestmark = pytest.mark.unit


def test_available_via_canonical_env(tmp_path: Path) -> None:
    """KAIRIX_PROVIDER_LLM_API_KEY in env resolves without touching legacy."""
    assert llm_credentials_available(
        env={"KAIRIX_PROVIDER_LLM_API_KEY": "key-canonical"},  # pragma: allowlist secret
        kv_mount=tmp_path,
        legacy_lookup=lambda: None,
    )


def test_available_via_kv_mount(tmp_path: Path) -> None:
    """A CSI-style per-file mount of the canonical KV name resolves."""
    (tmp_path / "kairix-provider-llm-api-key").write_text("key-from-mount")
    assert llm_credentials_available(
        env={},
        kv_mount=tmp_path,
        legacy_lookup=lambda: None,
    )


def test_falls_back_to_legacy_chain(tmp_path: Path) -> None:
    """When canonical resolution misses, the legacy chain still answers (GH #369)."""
    assert llm_credentials_available(
        env={},
        kv_mount=tmp_path,
        legacy_lookup=lambda: "key-legacy",  # pragma: allowlist secret
    )


def test_unavailable_when_both_generations_miss(tmp_path: Path) -> None:
    assert not llm_credentials_available(
        env={},
        kv_mount=tmp_path,
        legacy_lookup=lambda: None,
    )
