"""Contract test for the legacy Azure-OpenAI provider plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeProvider` configured
with ``name="azure_legacy"``) AND the real implementation
(:class:`kairix.providers.azure_legacy.AzureLegacyProvider`) through the
same :class:`~kairix.providers.Provider` Protocol assertions. F43
requires this pairing — without it the canonical fake can drift away
from the real wire (or vice versa) and the production path silently
diverges from what BDD / unit tests measure.

Both factories return Protocol-conformant ``Provider`` instances; no
network I/O is performed. The real plugin is constructed with a
hand-rolled in-memory transport client that mirrors the AzureOpenAI
SDK surface (``embeddings.create`` and ``chat.completions.create``) and
a legacy ``<resource>.openai.azure.com`` endpoint — the contract
assertions check shape (typed return values, ``name`` attribute,
callable methods), not delivery latency.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.providers import Provider, ProviderHealth
from kairix.providers.azure_legacy import PROVIDER_NAME, AzureLegacyProvider
from tests.fakes import FakeProvider


class _StubEmbeddings:
    def create(self, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])


class _StubChatCompletions:
    def create(self, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


class _StubChat:
    def __init__(self) -> None:
        self.completions = _StubChatCompletions()


class _StubAzureClient:
    """Minimal transport stub satisfying what :class:`AzureLegacyProvider` consumes."""

    def __init__(self) -> None:
        self.embeddings = _StubEmbeddings()
        self.chat = _StubChat()


def _fake_factory() -> Provider:
    return FakeProvider(name=PROVIDER_NAME, dim=3, chat_reply="ok")


def _real_factory() -> Provider:
    creds = Credentials(
        api_key="azure-legacy-test-key",  # pragma: allowlist secret — test fixture
        endpoint="https://example.openai.azure.com",
        model="text-embedding-3-large",
        dims=3,
    )
    return AzureLegacyProvider(
        credentials=creds,
        api_version="2024-02-01",
        transport_client=_StubAzureClient(),
    )


_FACTORIES: list[tuple[str, Callable[[], Provider]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_azure_legacy_provider_satisfies_provider_protocol(name: str, factory: Callable[[], Provider]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: deleting ``embed_batch`` from
    :class:`AzureLegacyProvider` flips the real-impl isinstance check to
    False; same for the fake.
    """
    provider = factory()
    assert isinstance(provider, Provider), f"{name!r} factory output is not a Provider"
    assert provider.name == PROVIDER_NAME


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_azure_legacy_provider_exposes_protocol_callables(name: str, factory: Callable[[], Provider]) -> None:
    """Required Protocol methods are all present and callable.

    Sabotage-proof: removing any of these methods from either impl
    makes the ``callable(...)`` assertion fail.
    """
    provider = factory()
    for attr in ("embed_batch", "chat", "dimension", "healthcheck"):
        assert callable(getattr(provider, attr)), f"{name!r} missing callable {attr!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_azure_legacy_provider_dimension_returns_positive_int(name: str, factory: Callable[[], Provider]) -> None:
    """``dimension()`` returns a positive integer (vector width).

    Sabotage-proof: returning ``None`` or ``-1`` from either impl
    flunks the assertion.
    """
    provider = factory()
    dim = provider.dimension()
    assert isinstance(dim, int) and dim > 0, f"{name!r} returned non-positive dim: {dim!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_azure_legacy_provider_healthcheck_returns_provider_health(name: str, factory: Callable[[], Provider]) -> None:
    """``healthcheck()`` returns a :class:`ProviderHealth` record on both impls.

    Sabotage-proof: returning a bare bool from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    health = provider.healthcheck()
    assert isinstance(health, ProviderHealth), f"{name!r} returned non-ProviderHealth: {health!r}"
    assert isinstance(health.ok, bool)
