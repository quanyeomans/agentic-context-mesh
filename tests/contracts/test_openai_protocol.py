"""Contract test for the OpenAI-direct provider plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeProvider` configured
with ``name="openai"``) AND the real implementation
(:class:`kairix.providers.openai.OpenAIProvider`) through the same
:class:`~kairix.providers.Provider` Protocol assertions. F43 requires this
pairing — without it the canonical fake can drift away from the real wire
(or vice versa) and the production path silently diverges from what
BDD / unit tests measure.

Both factories return Protocol-conformant ``Provider`` instances; no
network I/O is performed. The real plugin is constructed with a
hand-rolled in-memory transport client that mirrors the OpenAI-SDK
surface (``embeddings.create`` and ``chat.completions.create``) — the
contract assertions check shape (typed return values, ``name`` attribute,
callable methods), not delivery latency.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.providers import Provider, ProviderHealth
from kairix.providers.openai import PROVIDER_NAME, OpenAIProvider
from tests.fakes import FakeProvider


class _StubEmbeddings:
    """In-memory stand-in for the OpenAI SDK ``embeddings`` surface."""

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


class _StubOpenAIClient:
    """Minimal transport stub satisfying what :class:`OpenAIProvider` consumes."""

    def __init__(self) -> None:
        self.embeddings = _StubEmbeddings()
        self.chat = _StubChat()


def _fake_factory() -> Provider:
    return FakeProvider(name=PROVIDER_NAME, dim=3, chat_reply="ok")


def _real_factory() -> Provider:
    creds = Credentials(
        api_key="openai-test-key",  # pragma: allowlist secret — test fixture
        endpoint="https://api.openai.com/v1",
        model="text-embedding-3-large",
        dims=3,
    )
    return OpenAIProvider(credentials=creds, transport_client=_StubOpenAIClient())


_FACTORIES: list[tuple[str, Callable[[], Provider]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_openai_provider_satisfies_provider_protocol(name: str, factory: Callable[[], Provider]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: deleting ``embed_batch`` from :class:`OpenAIProvider`
    flips the real-impl isinstance check to False; same for the fake.
    """
    provider = factory()
    assert isinstance(provider, Provider), f"{name!r} factory output is not a Provider"
    assert provider.name == PROVIDER_NAME


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_openai_provider_exposes_protocol_callables(name: str, factory: Callable[[], Provider]) -> None:
    """Required Protocol methods are all present and callable.

    Sabotage-proof: removing ``dimension`` from either impl makes the
    ``callable(...)`` assertion fail.
    """
    provider = factory()
    for attr in ("embed_batch", "chat", "dimension", "healthcheck"):
        assert callable(getattr(provider, attr)), f"{name!r} missing callable {attr!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_openai_provider_dimension_returns_positive_int(name: str, factory: Callable[[], Provider]) -> None:
    """``dimension()`` returns a positive integer (vector width).

    Sabotage-proof: returning ``None`` or ``-1`` from either impl
    flunks the assertion.
    """
    provider = factory()
    dim = provider.dimension()
    assert isinstance(dim, int) and dim > 0, f"{name!r} returned non-positive dim: {dim!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_openai_provider_healthcheck_returns_provider_health(name: str, factory: Callable[[], Provider]) -> None:
    """``healthcheck()`` returns a :class:`ProviderHealth` record on both impls.

    Sabotage-proof: returning a bare bool from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    health = provider.healthcheck()
    assert isinstance(health, ProviderHealth), f"{name!r} returned non-ProviderHealth: {health!r}"
    assert isinstance(health.ok, bool)
