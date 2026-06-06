"""Contract test for the Anthropic provider plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeProvider` configured
with ``name="anthropic"``) AND the real implementation
(:class:`kairix.providers.anthropic.AnthropicProvider`) through the same
:class:`~kairix.providers.Provider` Protocol assertions. F43 requires
this pairing — without it the canonical fake can drift away from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Anthropic is the chat-only outlier: its ``embed_batch`` raises
:class:`~kairix.providers.EmbedNotSupported` because Anthropic ships no
embedding surface. The Protocol shape only requires the method exists
and is callable — behavioural semantics live in the per-plugin unit
tests (``tests/providers/anthropic/test_provider.py``).

Both factories return Protocol-conformant ``Provider`` instances; no
network I/O is performed. The real plugin is constructed with a
hand-rolled in-memory transport client mirroring the official
``anthropic`` SDK surface (``messages.create``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.providers import Provider, ProviderHealth
from kairix.providers.anthropic import PROVIDER_NAME, AnthropicProvider
from tests.fakes import FakeProvider


class _StubMessages:
    def create(self, **_kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])


class _StubAnthropicClient:
    """Minimal transport stub satisfying what :class:`AnthropicProvider` consumes."""

    def __init__(self) -> None:
        self.messages = _StubMessages()


def _fake_factory() -> Provider:
    # Anthropic is chat-only in production; the fake still satisfies the
    # full Protocol surface for shape-equivalence purposes.
    return FakeProvider(name=PROVIDER_NAME, dim=1, chat_reply="ok")


def _real_factory() -> Provider:
    creds = Credentials(
        api_key="anthropic-test-key",  # pragma: allowlist secret — test fixture
        endpoint="https://api.anthropic.com",
        model="claude-3-5-sonnet-20241022",
        dims=0,
    )
    return AnthropicProvider(credentials=creds, transport_client=_StubAnthropicClient())


_FACTORIES: list[tuple[str, Callable[[], Provider]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_anthropic_provider_satisfies_provider_protocol(name: str, factory: Callable[[], Provider]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: deleting ``chat`` from :class:`AnthropicProvider`
    flips the real-impl isinstance check to False; same for the fake.
    """
    provider = factory()
    assert isinstance(provider, Provider), f"{name!r} factory output is not a Provider"
    assert provider.name == PROVIDER_NAME


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_anthropic_provider_exposes_protocol_callables(name: str, factory: Callable[[], Provider]) -> None:
    """Required Protocol methods are all present and callable.

    Anthropic is chat-only, so ``embed_batch`` exists (per the Protocol
    shape) even though calling it raises :class:`EmbedNotSupported`.

    Sabotage-proof: removing any of these methods from either impl
    makes the ``callable(...)`` assertion fail.
    """
    provider = factory()
    for attr in ("embed_batch", "chat", "dimension", "healthcheck"):
        assert callable(getattr(provider, attr)), f"{name!r} missing callable {attr!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_anthropic_provider_dimension_returns_int(name: str, factory: Callable[[], Provider]) -> None:
    """``dimension()`` returns an integer (zero is valid for chat-only providers).

    Anthropic ships no embed model, so a non-positive dimension is the
    documented signal that the surface is unavailable; both fake and
    real impls must still return an ``int`` so callers can branch.

    Sabotage-proof: returning ``None`` from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    dim = provider.dimension()
    assert isinstance(dim, int), f"{name!r} returned non-int dim: {dim!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_anthropic_provider_healthcheck_returns_provider_health(name: str, factory: Callable[[], Provider]) -> None:
    """``healthcheck()`` returns a :class:`ProviderHealth` record on both impls.

    Sabotage-proof: returning a bare bool from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    health = provider.healthcheck()
    assert isinstance(health, ProviderHealth), f"{name!r} returned non-ProviderHealth: {health!r}"
    assert isinstance(health.ok, bool)
