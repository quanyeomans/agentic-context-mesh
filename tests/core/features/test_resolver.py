"""Unit tests for the feature-flag resolver.

Covers the §3.4 resolution order (env var > config overlay > default),
the per-process cache, the unknown-flag KeyError affordance, and the
:func:`status` snapshot shape (frozen-dc per F42).

Public-API only: every test drives behaviour through ``flag()`` /
``status()`` / ``iter_status()`` with the documented ``env_reader`` /
``overlay_reader`` / ``registry_reader`` DI seams. No private imports
(F5-clean). No monkey-patching of kairix modules (F1/F2-clean).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest

from kairix.core.features.registry import FeatureFlag
from kairix.core.features.resolver import (
    FlagStatus,
    default_env_reader,
    default_overlay_reader,
    default_registry_reader,
    flag,
    iter_status,
    reset_cache,
    status,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def _fresh_registry() -> Iterator[dict[str, FeatureFlag]]:
    """Per-test mutable registry, plus a cache reset.

    Yields a dict that tests populate; the dict is fed to the resolver
    via the ``registry_reader`` DI seam — there's no global mutation,
    so concurrent tests can't see each other's flags.
    """
    reset_cache()
    fresh: dict[str, FeatureFlag] = {}
    yield fresh
    reset_cache()


def _declare(reg: dict[str, FeatureFlag], name: str, default: bool) -> None:
    """Add a flag to the synthetic registry for the duration of the test."""
    reg[name] = FeatureFlag(
        name=name,
        default=default,
        description=f"test flag {name}",
        stage="introduce",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
    )


def test_unknown_flag_raises_keyerror_with_fix_affordance(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """Unknown flag → KeyError with a fix: action marker (F21).

    Sabotage: drop the "fix:" prefix and this assertion fails — the
    operator's diagnostic becomes a bare "unknown flag" with no
    correction action.
    """
    with pytest.raises(KeyError) as excinfo:
        flag("does_not_exist", registry_reader=lambda: _fresh_registry)

    assert "fix:" in str(excinfo.value)
    assert "REGISTRY" in str(excinfo.value)


def test_unknown_flag_error_lists_known_flags_via_keyerror(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """The KeyError affordance lists existing flags so the operator can
    find their typo. Empty registry → ``(empty)`` placeholder.
    """
    with pytest.raises(KeyError) as excinfo:
        flag("missing", registry_reader=lambda: _fresh_registry)
    assert "(empty)" in str(excinfo.value)

    _declare(_fresh_registry, "alpha", default=False)
    with pytest.raises(KeyError) as excinfo:
        flag("missing", registry_reader=lambda: _fresh_registry)
    assert "alpha" in str(excinfo.value)


def test_flag_resolves_default_when_no_overrides(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """No env var, no config overlay → resolver returns ``FeatureFlag.default``."""
    _declare(_fresh_registry, "canary", default=False)
    assert (
        flag(
            "canary",
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
        is False
    )

    reset_cache()
    _declare(_fresh_registry, "canary_on", default=True)
    assert (
        flag(
            "canary_on",
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
        is True
    )


def test_flag_caches_per_process(_fresh_registry: dict[str, FeatureFlag]) -> None:
    """First call resolves through the layered chain; subsequent calls
    hit the cache. Mutating the entry's ``default`` after the first call
    must NOT change the cached value — the cache is the authority.
    """
    _declare(_fresh_registry, "cached", default=False)
    assert (
        flag(
            "cached",
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
        is False
    )

    # Mutate the entry's default by replacing it — the cache should win.
    _fresh_registry["cached"] = FeatureFlag(
        name="cached",
        default=True,
        description="canary",
        stage="introduce",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
    )
    assert (
        flag(
            "cached",
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
        is False
    )


def test_status_returns_frozen_dataclass_per_entry(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """``status()`` projects every registry entry into a FlagStatus tuple.

    Frozen-dataclass per F42: mutating any field raises. Order: sorted
    by flag name so operators reading the output see a deterministic
    layout.
    """
    _declare(_fresh_registry, "beta", default=False)
    _declare(_fresh_registry, "alpha", default=True)

    snapshots = status(
        env_reader=lambda _name: None,
        overlay_reader=dict,
        registry_reader=lambda: _fresh_registry,
    )
    assert isinstance(snapshots, tuple)
    assert len(snapshots) == 2
    assert [s.name for s in snapshots] == ["alpha", "beta"]
    assert snapshots[0].effective is True
    assert snapshots[1].effective is False
    assert snapshots[0].source == "default"

    with pytest.raises(FrozenInstanceError):
        snapshots[0].effective = False  # type: ignore[misc] — F42 frozen check


def test_iter_status_yields_same_order_as_status(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """``iter_status()`` is the iterator variant — must yield the same
    sequence as ``status()``.
    """
    _declare(_fresh_registry, "beta", default=False)
    _declare(_fresh_registry, "alpha", default=True)

    seen = tuple(
        iter_status(
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
    )
    snapped = status(
        env_reader=lambda _name: None,
        overlay_reader=dict,
        registry_reader=lambda: _fresh_registry,
    )
    assert seen == snapped


def test_status_returns_empty_tuple_when_registry_is_empty(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """The PR-2 landing shape — empty registry → empty tuple."""
    assert (
        status(
            env_reader=lambda _name: None,
            overlay_reader=dict,
            registry_reader=lambda: _fresh_registry,
        )
        == ()
    )


def test_flag_uses_injected_env_reader_first(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """When the env reader returns a bool, the resolver uses it
    (highest priority per §3.4). Locks the env > config > default
    ordering at the public API boundary.
    """
    _declare(_fresh_registry, "from_env", default=False)
    result = flag(
        "from_env",
        env_reader=lambda _name: True,
        overlay_reader=lambda: {"from_env": False},  # would win without env
        registry_reader=lambda: _fresh_registry,
    )
    assert result is True


def test_status_uses_injected_overlay_reader_when_env_unset(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """Env reader returns None → config overlay wins (middle of §3.4)."""
    _declare(_fresh_registry, "from_overlay", default=False)
    snapshots = status(
        env_reader=lambda _name: None,
        overlay_reader=lambda: {"from_overlay": True},
        registry_reader=lambda: _fresh_registry,
    )
    assert snapshots[0].effective is True
    assert snapshots[0].source == "config"


def test_status_records_env_source_when_env_reader_wins(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """The FlagStatus.source field reflects which §3.4 layer won."""
    _declare(_fresh_registry, "x", default=False)
    snapshots = status(
        env_reader=lambda _name: True,
        overlay_reader=dict,
        registry_reader=lambda: _fresh_registry,
    )
    assert snapshots[0].effective is True
    assert snapshots[0].source == "env"


def test_flag_status_carries_related_spec_when_present(
    _fresh_registry: dict[str, FeatureFlag],
) -> None:
    """``related_spec`` round-trips into the FlagStatus snapshot."""
    _fresh_registry["spec_linked"] = FeatureFlag(
        name="spec_linked",
        default=False,
        description="canary",
        stage="cutover",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
        related_spec="docs/architecture/example.md",
    )
    snapshots = status(
        env_reader=lambda _name: None,
        overlay_reader=dict,
        registry_reader=lambda: _fresh_registry,
    )
    assert snapshots[0].related_spec == "docs/architecture/example.md"
    assert snapshots[0].stage == "cutover"


def test_flag_status_is_frozen_dataclass() -> None:
    """FlagStatus must be immutable per F42."""
    s = FlagStatus(
        name="x",
        default=False,
        effective=False,
        source="default",
        stage="introduce",
        introduced_in="v0.0.0",
        target_retire_in="v9999.0.0",
        owner="t",
    )
    with pytest.raises(FrozenInstanceError):
        s.effective = True  # type: ignore[misc] — F42 frozen check


def test_default_env_reader_returns_none_for_unset_var() -> None:
    """The production env reader delegates to :mod:`kairix.paths`.
    Unset env var → ``None`` so the resolver falls through layers.
    """
    result = default_env_reader("a_made_up_flag_name_for_this_test_only")
    assert result is None


def test_default_overlay_reader_returns_dict() -> None:
    """The production overlay reader returns a dict (possibly empty)."""
    overlay = default_overlay_reader()
    assert isinstance(overlay, dict)


def test_default_registry_reader_returns_module_registry() -> None:
    """The production registry reader returns the module-global REGISTRY."""
    from kairix.core.features import registry as registry_module

    assert default_registry_reader() is registry_module.REGISTRY
