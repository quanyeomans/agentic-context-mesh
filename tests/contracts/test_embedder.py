"""Contract: EmbedderProtocol conformance."""
import pytest
from kairix.contracts.embed import EmbedderProtocol


@pytest.mark.contract
def test_fake_llm_backend_satisfies_embedder_protocol(fake_llm_backend):
    assert isinstance(fake_llm_backend, EmbedderProtocol)


@pytest.mark.contract
def test_embedder_embed_returns_correct_dim(fake_llm_backend):
    vec = fake_llm_backend.embed("hello world")
    assert len(vec) == fake_llm_backend.dimension()


@pytest.mark.contract
def test_embedder_embed_bytes_length(fake_llm_backend):
    import struct
    b = fake_llm_backend.embed_as_bytes("hello")
    assert b is not None
    dim = len(struct.unpack(f"{len(b) // 4}f", b))
    assert dim == 1536


@pytest.mark.contract
def test_embedder_deterministic(fake_llm_backend):
    v1 = fake_llm_backend.embed("same text")
    v2 = fake_llm_backend.embed("same text")
    assert v1 == v2


@pytest.mark.contract
def test_embedder_different_inputs_differ(fake_llm_backend):
    v1 = fake_llm_backend.embed("text one")
    v2 = fake_llm_backend.embed("text two")
    assert v1 != v2
