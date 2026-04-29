"""Contract: LLMBackend Protocol conformance.

Verifies that AzureOpenAIBackend satisfies the LLMBackend Protocol
structurally (no live API calls made).
"""

import pytest

from kairix.platform.llm.backends import AzureOpenAIBackend
from kairix.platform.llm.protocol import LLMBackend


@pytest.mark.contract
def test_azure_backend_satisfies_llm_protocol():
    """AzureOpenAIBackend must satisfy LLMBackend Protocol at class level."""
    assert issubclass(AzureOpenAIBackend, LLMBackend) or isinstance(AzureOpenAIBackend(), LLMBackend)


@pytest.mark.contract
def test_llm_backend_protocol_has_required_methods():
    """LLMBackend Protocol must declare chat and embed."""
    required = {"chat", "embed"}
    protocol_members = (
        set(LLMBackend.__protocol_attrs__) if hasattr(LLMBackend, "__protocol_attrs__") else set(dir(LLMBackend))
    )
    for method in required:
        assert method in protocol_members, f"LLMBackend missing required method: {method}"


@pytest.mark.contract
def test_fake_llm_satisfies_protocol(fake_llm_backend):
    """FakeLLM test fixture must satisfy LLMBackend Protocol."""
    assert isinstance(fake_llm_backend, LLMBackend)


@pytest.mark.contract
def test_fake_llm_chat_returns_string(fake_llm_backend):
    result = fake_llm_backend.chat([{"role": "user", "content": "hello"}])
    assert isinstance(result, str)


@pytest.mark.contract
def test_fake_llm_embed_returns_float_list(fake_llm_backend):
    result = fake_llm_backend.embed("test text")
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)
    assert len(result) == 1536


@pytest.mark.contract
def test_fake_llm_embed_as_bytes_returns_bytes(fake_llm_backend):
    result = fake_llm_backend.embed_as_bytes("test text")
    assert result is None or isinstance(result, bytes)
