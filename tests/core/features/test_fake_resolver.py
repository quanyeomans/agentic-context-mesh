"""Unit tests for ``FakeFeatureFlagResolver``.

Covers the in-memory store, the builder pattern, Protocol shape via
``iter_all`` returning FlagStatus, and the unknown-flag KeyError
contract that mirrors the production resolver.
"""

from __future__ import annotations

import pytest

from kairix.core.features.resolver import FlagStatus
from kairix.core.protocols import FeatureFlagResolver
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.unit


def test_fake_resolver_satisfies_protocol() -> None:
    """``FakeFeatureFlagResolver`` is structurally a FeatureFlagResolver
    — the runtime_checkable Protocol must accept the fake.
    """
    fake = FakeFeatureFlagResolver({"alpha": True})
    assert isinstance(fake, FeatureFlagResolver)


def test_get_returns_declared_value() -> None:
    fake = FakeFeatureFlagResolver({"alpha": True, "beta": False})
    assert fake.get("alpha") is True
    assert fake.get("beta") is False


def test_get_raises_keyerror_on_unknown_flag() -> None:
    """Mirror the production resolver — unknown flags fail loudly."""
    fake = FakeFeatureFlagResolver()
    with pytest.raises(KeyError, match="fix:"):
        fake.get("never_declared")


def test_with_flag_returns_new_instance() -> None:
    """The builder is immutable — chaining doesn't mutate the original."""
    original = FakeFeatureFlagResolver()
    derived = original.with_flag("alpha", True)
    assert derived is not original
    # Original still unaware of 'alpha'
    with pytest.raises(KeyError):
        original.get("alpha")
    # Derived has it
    assert derived.get("alpha") is True


def test_with_flag_supports_chaining() -> None:
    fake = FakeFeatureFlagResolver().with_flag("alpha", True).with_flag("beta", False)
    assert fake.get("alpha") is True
    assert fake.get("beta") is False


def test_iter_all_yields_flagstatus_snapshots() -> None:
    """``iter_all`` returns FlagStatus instances (F42 frozen-dc shape)
    sorted by name.
    """
    fake = FakeFeatureFlagResolver().with_flag("beta", False).with_flag("alpha", True)
    snapshots = list(fake.iter_all())
    assert len(snapshots) == 2
    assert [s.name for s in snapshots] == ["alpha", "beta"]
    assert all(isinstance(s, FlagStatus) for s in snapshots)
    assert snapshots[0].effective is True
    assert snapshots[1].effective is False


def test_iter_all_yields_empty_when_no_flags() -> None:
    """The PR-2 default — empty fake → empty iterator."""
    fake = FakeFeatureFlagResolver()
    assert list(fake.iter_all()) == []
