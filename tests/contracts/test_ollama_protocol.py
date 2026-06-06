"""Contract test for the Ollama provider plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeProvider` configured
with ``name="ollama"``) AND the real implementation
(:class:`kairix.providers.ollama.OllamaProvider`) through the same
:class:`~kairix.providers.Provider` Protocol assertions. F43 requires
this pairing — without it the canonical fake can drift away from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Both factories return Protocol-conformant ``Provider`` instances; no
network I/O is performed. The real plugin is constructed with an
:class:`~kairix.providers.ollama.provider.OllamaTransport`-conformant
stub that returns canned ``{"embedding": [...]}`` and
``{"message": {"content": "..."}}`` payloads — the contract assertions
check shape (typed return values, ``name`` attribute, callable methods),
not delivery latency.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.providers import Provider, ProviderHealth
from kairix.providers.ollama import PROVIDER_NAME, OllamaProvider
from tests.fakes import FakeProvider


class _StubOllamaTransport:
    """Minimal ``OllamaTransport`` stub returning canned embed/chat payloads."""

    def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        del json
        if path.endswith("/embeddings"):
            return {"embedding": [0.1, 0.2, 0.3]}
        if path.endswith("/chat"):
            return {"message": {"content": "ok"}}
        return {}


def _fake_factory() -> Provider:
    return FakeProvider(name=PROVIDER_NAME, dim=3, chat_reply="ok")


def _real_factory() -> Provider:
    creds = Credentials(
        api_key="",  # pragma: allowlist secret — Ollama needs no key
        endpoint="http://localhost:11434",
        model="nomic-embed-text",
        dims=3,
    )
    return OllamaProvider(credentials=creds, transport_client=_StubOllamaTransport())


_FACTORIES: list[tuple[str, Callable[[], Provider]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_ollama_provider_satisfies_provider_protocol(name: str, factory: Callable[[], Provider]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: deleting ``embed_batch`` from :class:`OllamaProvider`
    flips the real-impl isinstance check to False; same for the fake.
    """
    provider = factory()
    assert isinstance(provider, Provider), f"{name!r} factory output is not a Provider"
    assert provider.name == PROVIDER_NAME


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_ollama_provider_exposes_protocol_callables(name: str, factory: Callable[[], Provider]) -> None:
    """Required Protocol methods are all present and callable.

    Sabotage-proof: removing any of these methods from either impl
    makes the ``callable(...)`` assertion fail.
    """
    provider = factory()
    for attr in ("embed_batch", "chat", "dimension", "healthcheck"):
        assert callable(getattr(provider, attr)), f"{name!r} missing callable {attr!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_ollama_provider_dimension_returns_positive_int(name: str, factory: Callable[[], Provider]) -> None:
    """``dimension()`` returns a positive integer (vector width).

    Sabotage-proof: returning ``None`` or ``-1`` from either impl
    flunks the assertion.
    """
    provider = factory()
    dim = provider.dimension()
    assert isinstance(dim, int) and dim > 0, f"{name!r} returned non-positive dim: {dim!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_ollama_provider_healthcheck_returns_provider_health(name: str, factory: Callable[[], Provider]) -> None:
    """``healthcheck()`` returns a :class:`ProviderHealth` record on both impls.

    Sabotage-proof: returning a bare bool from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    health = provider.healthcheck()
    assert isinstance(health, ProviderHealth), f"{name!r} returned non-ProviderHealth: {health!r}"
    assert isinstance(health.ok, bool)
