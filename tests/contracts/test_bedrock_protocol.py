"""Contract test for the AWS Bedrock provider plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeProvider` configured
with ``name="bedrock"``) AND the real implementation
(:class:`kairix.providers.bedrock.BedrockProvider`) through the same
:class:`~kairix.providers.Provider` Protocol assertions. F43 requires
this pairing — without it the canonical fake can drift away from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Both factories return Protocol-conformant ``Provider`` instances; no
network I/O or boto3 SigV4 happens. The real plugin is constructed
with a hand-rolled in-memory transport client mirroring the boto3
``bedrock-runtime`` surface (``invoke_model``) — the contract
assertions check shape (typed return values, ``name`` attribute,
callable methods), not delivery latency.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from kairix.providers import Provider, ProviderHealth
from kairix.providers.bedrock import PROVIDER_NAME, BedrockCredentials, BedrockProvider
from tests.fakes import FakeProvider


class _StreamingBody:
    """Stand-in for botocore's ``StreamingBody`` — exposes ``.read()``."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _StubBedrockClient:
    """Minimal boto3-shaped stub returning canned Titan embed payloads."""

    def invoke_model(
        self,
        *,
        modelId: str,  # noqa: N803 — boto3 method signature pinned by AWS SDK
        body: bytes,
        contentType: str,  # noqa: N803 — boto3 method signature pinned by AWS SDK
        accept: str,
    ) -> dict[str, Any]:
        del modelId, body, contentType, accept
        payload = json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode("utf-8")
        return {"body": _StreamingBody(payload), "contentType": "application/json"}


def _fake_factory() -> Provider:
    return FakeProvider(name=PROVIDER_NAME, dim=3, chat_reply="ok")


def _real_factory() -> Provider:
    creds = BedrockCredentials(
        access_key_id="AKIA-TEST",  # pragma: allowlist secret — test fixture
        secret_access_key="bedrock-test-secret",  # pragma: allowlist secret — test fixture
        region="us-east-1",
        embed_model_id="amazon.titan-embed-text-v2:0",
        chat_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        dims=3,
    )
    return BedrockProvider(credentials=creds, transport_client=_StubBedrockClient())


_FACTORIES: list[tuple[str, Callable[[], Provider]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_bedrock_provider_satisfies_provider_protocol(name: str, factory: Callable[[], Provider]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: deleting ``embed_batch`` from :class:`BedrockProvider`
    flips the real-impl isinstance check to False; same for the fake.
    """
    provider = factory()
    assert isinstance(provider, Provider), f"{name!r} factory output is not a Provider"
    assert provider.name == PROVIDER_NAME


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_bedrock_provider_exposes_protocol_callables(name: str, factory: Callable[[], Provider]) -> None:
    """Required Protocol methods are all present and callable.

    Sabotage-proof: removing any of these methods from either impl
    makes the ``callable(...)`` assertion fail.
    """
    provider = factory()
    for attr in ("embed_batch", "chat", "dimension", "healthcheck"):
        assert callable(getattr(provider, attr)), f"{name!r} missing callable {attr!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_bedrock_provider_dimension_returns_positive_int(name: str, factory: Callable[[], Provider]) -> None:
    """``dimension()`` returns a positive integer (vector width).

    Sabotage-proof: returning ``None`` or ``-1`` from either impl
    flunks the assertion.
    """
    provider = factory()
    dim = provider.dimension()
    assert isinstance(dim, int) and dim > 0, f"{name!r} returned non-positive dim: {dim!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_bedrock_provider_healthcheck_returns_provider_health(name: str, factory: Callable[[], Provider]) -> None:
    """``healthcheck()`` returns a :class:`ProviderHealth` record on both impls.

    Sabotage-proof: returning a bare bool from either impl flunks the
    isinstance assertion.
    """
    provider = factory()
    health = provider.healthcheck()
    assert isinstance(health, ProviderHealth), f"{name!r} returned non-ProviderHealth: {health!r}"
    assert isinstance(health.ok, bool)
