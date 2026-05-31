"""Contract tests for :class:`kairix.secrets.loader.SecretsResolver`.

Both the production :class:`SecretsLoader` and the test
:class:`tests.fakes.FakeSecretsLoader` must satisfy the Protocol's
runtime shape. The contract pins the get/require return-type contract
(``str | None`` for get, ``str`` for require, ``SecretNotFoundError``
on miss).

F43-style: real implementation + fake implementation drive the same
contract assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.secrets.loader import (
    SecretNotFoundError,
    SecretsLoader,
    SecretsResolver,
)
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.contract


def _make_real() -> SecretsLoader:
    """Build a real loader with a stub legacy chain (returns None always)."""
    return SecretsLoader(env={}, kv_mount=Path("/dev/null"), legacy_chain=lambda _c: None)


def _make_fake() -> FakeSecretsLoader:
    return FakeSecretsLoader()


def _make_real_with_value() -> SecretsLoader:
    return SecretsLoader(
        env={"KAIRIX_CONNECTOR_M365_TENANT_ID": "tenant-x"},
        kv_mount=Path("/dev/null"),
        legacy_chain=lambda _c: None,
    )


def _make_fake_with_value() -> FakeSecretsLoader:
    return FakeSecretsLoader(values={("connector", "m365", None, "tenant-id"): "tenant-x"})


@pytest.mark.parametrize(
    "factory",
    [_make_real, _make_fake],
    ids=["real:SecretsLoader", "fake:FakeSecretsLoader"],
)
def test_loader_satisfies_secrets_resolver_protocol(factory) -> None:
    """Runtime-checkable Protocol shape — both real + fake structurally
    match :class:`SecretsResolver`.
    """
    loader = factory()
    assert isinstance(loader, SecretsResolver)


@pytest.mark.parametrize(
    "factory",
    [_make_real, _make_fake],
    ids=["real:SecretsLoader", "fake:FakeSecretsLoader"],
)
def test_get_returns_none_on_miss(factory) -> None:
    """Both implementations return None (never raise) when no source resolves."""
    loader = factory()
    assert loader.get("connector", "m365", None, "tenant-id") is None


@pytest.mark.parametrize(
    "factory",
    [_make_real, _make_fake],
    ids=["real:SecretsLoader", "fake:FakeSecretsLoader"],
)
def test_require_raises_secret_not_found_on_miss(factory) -> None:
    """Both implementations raise :class:`SecretNotFoundError` (subclass
    of LookupError) when the requested identity doesn't resolve.
    """
    loader = factory()
    with pytest.raises(SecretNotFoundError):
        loader.require("connector", "m365", None, "tenant-id")


@pytest.mark.parametrize(
    "factory",
    [_make_real_with_value, _make_fake_with_value],
    ids=["real:SecretsLoader+value", "fake:FakeSecretsLoader+value"],
)
def test_get_returns_value_on_hit(factory) -> None:
    """Both implementations return the bound string when the identity
    resolves; no Optional[Any], no bytes, just ``str``.
    """
    loader = factory()
    value = loader.get("connector", "m365", None, "tenant-id")
    assert value == "tenant-x"
    assert isinstance(value, str)


@pytest.mark.parametrize(
    "factory",
    [_make_real_with_value, _make_fake_with_value],
    ids=["real:SecretsLoader+value", "fake:FakeSecretsLoader+value"],
)
def test_require_returns_value_on_hit(factory) -> None:
    """Both implementations return ``str`` (never None) on hit."""
    loader = factory()
    value = loader.require("connector", "m365", None, "tenant-id")
    assert value == "tenant-x"
    assert isinstance(value, str)
