"""Tests for kairix.llm.embed_provider — SDK-based embedding clients."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Create a mock openai module so tests work without the real package installed
_mock_openai = ModuleType("openai")
_mock_openai.AzureOpenAI = MagicMock  # type: ignore[attr-defined]
_mock_openai.OpenAI = MagicMock  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _mock_openai_module(monkeypatch):
    """Ensure openai module is available for import even if not installed."""
    monkeypatch.setitem(sys.modules, "openai", _mock_openai)
    yield
    # Re-import to clear cached provider instances
    if "kairix.llm.embed_provider" in sys.modules:
        del sys.modules["kairix.llm.embed_provider"]


from kairix.llm.embed_provider import (
    AzureEmbedProvider,
    EmbedProvider,
    OpenAIEmbedProvider,
    get_embed_provider,
)


@pytest.mark.unit
class TestEmbedProviderProtocol:
    def test_azure_provider_is_embed_provider(self) -> None:
        provider = AzureEmbedProvider(endpoint="https://test.openai.azure.com", api_key="key")
        assert isinstance(provider, EmbedProvider)

    def test_openai_provider_is_embed_provider(self) -> None:
        provider = OpenAIEmbedProvider(api_key="key")
        assert isinstance(provider, EmbedProvider)


@pytest.mark.unit
class TestAzureEmbedProvider:
    def test_embed_batch_delegates_to_sdk(self) -> None:
        provider = AzureEmbedProvider(endpoint="https://test.openai.azure.com", api_key="key")

        mock_item = MagicMock()
        mock_item.embedding = [0.1, 0.2, 0.3]
        provider.client.embeddings.create.return_value = MagicMock(data=[mock_item])

        result = provider.embed_batch(["hello"], model="text-embedding-3-large", dims=1536)
        assert result == [[0.1, 0.2, 0.3]]

    def test_batch_returns_correct_count(self) -> None:
        provider = AzureEmbedProvider(endpoint="https://test", api_key="key")

        items = [MagicMock(embedding=[float(i)]) for i in range(3)]
        provider.client.embeddings.create.return_value = MagicMock(data=items)

        result = provider.embed_batch(["a", "b", "c"], model="m", dims=3)
        assert len(result) == 3


@pytest.mark.unit
class TestOpenAIEmbedProvider:
    def test_embed_batch_delegates_to_sdk(self) -> None:
        provider = OpenAIEmbedProvider(api_key="key")

        items = [MagicMock(embedding=[1.0, 2.0]), MagicMock(embedding=[3.0, 4.0])]
        provider.client.embeddings.create.return_value = MagicMock(data=items)

        result = provider.embed_batch(["a", "b"], model="text-embedding-3-small", dims=512)
        assert len(result) == 2
        assert result[0] == [1.0, 2.0]


@pytest.mark.unit
class TestGetEmbedProvider:
    def test_returns_azure_when_env_vars_set(self, monkeypatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        provider = get_embed_provider()
        assert isinstance(provider, AzureEmbedProvider)

    def test_falls_back_to_openai(self, monkeypatch) -> None:
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("kairix.secrets.get_secret", return_value=None):
            provider = get_embed_provider()
        assert isinstance(provider, OpenAIEmbedProvider)

    def test_raises_when_no_credentials(self, monkeypatch) -> None:
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("kairix.secrets.get_secret", return_value=None):
            with pytest.raises(OSError, match="No embedding provider"):
                get_embed_provider()
