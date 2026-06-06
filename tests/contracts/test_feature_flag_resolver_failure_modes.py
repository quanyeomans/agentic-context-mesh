"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`FeatureFlagResolver`.

Two methods + canonical failure shapes:

  * ``get(name)`` — raises ``KeyError`` on unknown flag (matches the
    production resolver's behaviour so typo-flags surface immediately
    instead of silently returning False).
  * ``iter_all()`` — returns an iterator that yields nothing for an
    empty registry (the ``returns_empty`` shape).

Both shapes are pinned through the canonical :class:`FakeFeatureFlagResolver`.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import FeatureFlagResolver
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.contract


def test_get_raises_keyerror_when_flag_unknown() -> None:
    """An unknown flag name raises KeyError — Protocol contract pins
    "unknown flags MUST surface", not "silently return False" (which
    would mask typos forever).

    Sabotage proof: change ``FakeFeatureFlagResolver.get`` to
    ``return False`` on unknown flags. Re-ran: ``pytest.raises`` sees
    nothing and the test fails. Restored.
    """
    resolver: FeatureFlagResolver = FakeFeatureFlagResolver()
    with pytest.raises(KeyError, match="unknown feature flag"):
        resolver.get("never-registered-flag")


def test_iter_all_returns_empty_when_no_flags_declared() -> None:
    """An empty resolver yields nothing — callers iterate without a
    null check.

    Sabotage proof: change ``FakeFeatureFlagResolver.iter_all`` to
    yield a sentinel FlagStatus when ``self._flags`` is empty.
    Re-ran: ``list(...) == []`` fails. Restored.
    """
    resolver: FeatureFlagResolver = FakeFeatureFlagResolver()
    assert list(resolver.iter_all()) == []
