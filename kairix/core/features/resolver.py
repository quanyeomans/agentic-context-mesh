"""Feature flag resolver — env var → config overlay → registry default.

See ``docs/architecture/feature-flag-architecture.md`` §3.4 for the
resolution order. The resolver is cached per-process so repeated
``flag("name")`` calls only consult the layered sources once.

Public surface (re-exported by :mod:`kairix.core.features.__init__`):

* :func:`flag` — boolean lookup for a flag name.
* :func:`status` — tuple of :class:`FlagStatus` describing every flag.
* :class:`FlagStatus` — frozen-dc snapshot per flag (F42 shape for the
  Protocol return type).

The resolver delegates env-var and config-yaml reads to
:mod:`kairix.paths` so F4 stays satisfied — no module outside paths.py
reads ``KAIRIX_*`` env vars.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from kairix.core.features.observability import (
    emit_activation_counter,
    log_first_activation,
)
from kairix.core.features.registry import REGISTRY, FeatureFlag, FlagStage

# Source of the effective value, recorded on every FlagStatus snapshot
# so operators / log readers can see which layer of §3.4 won.
FlagSource = Literal["env", "config", "default"]

# DI seams for the env-var and config-overlay reads. Production callers
# leave them at the defaults below; tests pass synthetic providers to
# drive the §3.4 chain without monkey-patching kairix.paths (F1-clean
# / F2-clean by construction).
EnvOverrideReader = Callable[[str], bool | None]
ConfigOverlayReader = Callable[[], dict[str, bool]]
RegistryReader = Callable[[], dict[str, FeatureFlag]]


def default_registry_reader() -> dict[str, FeatureFlag]:
    """Production registry reader — returns the module-global REGISTRY.

    A function (not a direct REGISTRY reference) so tests can supply
    a synthetic registry through ``registry_reader=lambda: {...}``
    without rebinding the module attribute (F1-clean).
    """
    return REGISTRY


@dataclass(frozen=True)
class FlagStatus:
    """Per-flag status snapshot — the public return shape.

    F42 frozen-dataclass discipline at the boundary; the resolver
    Protocol (``FeatureFlagResolver.iter_all``) returns these.
    """

    name: str
    default: bool
    effective: bool
    source: FlagSource
    stage: FlagStage
    introduced_in: str
    target_retire_in: str
    owner: str
    related_spec: str | None = None


# Per-process resolution cache. Cleared via :func:`reset_cache` in tests
# that need to re-observe layered reads.
_cache: dict[str, bool] = {}


def _unknown_flag_error(name: str, registry: dict[str, FeatureFlag] | None = None) -> KeyError:
    """Build a helpful KeyError when a flag isn't in the registry.

    The ``registry`` kwarg defaults to the module-global REGISTRY;
    tests pass a synthetic registry to drive the "(empty)" placeholder
    branch without rebinding REGISTRY (F1-clean).
    """
    reg = registry if registry is not None else REGISTRY
    known = ", ".join(sorted(reg)) or "(empty)"
    return KeyError(
        f"unknown feature flag {name!r}. "
        f"fix: declare it in kairix/core/features/registry.py:REGISTRY. "
        f"Known flags: {known}"
    )


def default_env_reader(name: str) -> bool | None:
    """Production env-override reader — delegates to :mod:`kairix.paths`.

    Kept as a thin wrapper so tests can inject a synthetic reader via
    the ``env_reader`` kwarg on :func:`flag` / :func:`status` without
    touching kairix.paths.
    """
    from kairix.paths import feature_flag_override

    return feature_flag_override(name)


def default_overlay_reader() -> dict[str, bool]:
    """Production config-overlay reader — delegates to :mod:`kairix.paths`."""
    from kairix.paths import feature_flag_config_overlay

    return feature_flag_config_overlay()


def _resolve_layered(
    name: str,
    *,
    env_reader: EnvOverrideReader = default_env_reader,
    overlay_reader: ConfigOverlayReader = default_overlay_reader,
    registry: dict[str, FeatureFlag] | None = None,
) -> tuple[bool, FlagSource]:
    """Apply the §3.4 resolution order and return (effective, source).

    Env-var lookup and config-yaml lookup are injected through the
    ``env_reader`` and ``overlay_reader`` seams so the resolver itself
    reads neither ``os.environ`` nor the filesystem (F4-clean — the
    paths module owns those reads). Tests pass synthetic readers to
    drive the chain deterministically.
    """
    env_value = env_reader(name)
    if env_value is not None:
        return env_value, "env"

    overlay = overlay_reader()
    if name in overlay:
        return bool(overlay[name]), "config"

    reg = registry if registry is not None else REGISTRY
    entry = reg[name]
    return entry.default, "default"


def flag(
    name: str,
    *,
    env_reader: EnvOverrideReader = default_env_reader,
    overlay_reader: ConfigOverlayReader = default_overlay_reader,
    registry_reader: RegistryReader = default_registry_reader,
) -> bool:
    """Resolve a feature flag to its effective boolean value.

    Cached per process — the first call per ``name`` consults the env
    var → config overlay → registry default chain (§3.4) and emits the
    one-shot INFO activation log; subsequent calls hit the in-memory
    cache. Raises ``KeyError`` (with a fix:/known-flags affordance)
    when ``name`` isn't declared in the registry.

    ``env_reader`` / ``overlay_reader`` / ``registry_reader`` are DI
    seams — production callers leave them at the defaults; tests pass
    fakes to drive the §3.4 chain without monkey-patching
    :mod:`kairix.paths` or the module-global REGISTRY (F1/F2-clean).
    """
    registry = registry_reader()
    if name not in registry:
        raise _unknown_flag_error(name, registry=registry)

    cached = _cache.get(name)
    if cached is not None:
        return cached

    effective, source = _resolve_layered(
        name,
        env_reader=env_reader,
        overlay_reader=overlay_reader,
        registry=registry,
    )
    _cache[name] = effective
    log_first_activation(name, effective=effective, source=source)
    emit_activation_counter(name, effective)
    return effective


def _build_status(
    entry: FeatureFlag,
    *,
    env_reader: EnvOverrideReader,
    overlay_reader: ConfigOverlayReader,
    registry: dict[str, FeatureFlag],
) -> FlagStatus:
    """Build a :class:`FlagStatus` for one registry entry.

    Bypasses the resolver cache by calling :func:`_resolve_layered`
    directly so the snapshot reflects what the env var / overlay say
    right now, not what was cached at an earlier call. ``status`` is an
    operator-facing query — it should reflect the live overlay.
    """
    effective, source = _resolve_layered(
        entry.name,
        env_reader=env_reader,
        overlay_reader=overlay_reader,
        registry=registry,
    )
    return FlagStatus(
        name=entry.name,
        default=entry.default,
        effective=effective,
        source=source,
        stage=entry.stage,
        introduced_in=entry.introduced_in,
        target_retire_in=entry.target_retire_in,
        owner=entry.owner,
        related_spec=entry.related_spec,
    )


def status(
    *,
    env_reader: EnvOverrideReader = default_env_reader,
    overlay_reader: ConfigOverlayReader = default_overlay_reader,
    registry_reader: RegistryReader = default_registry_reader,
) -> tuple[FlagStatus, ...]:
    """Return one :class:`FlagStatus` per registry entry, sorted by name.

    DI seams match :func:`flag`.
    """
    registry = registry_reader()
    return tuple(
        _build_status(
            registry[name],
            env_reader=env_reader,
            overlay_reader=overlay_reader,
            registry=registry,
        )
        for name in sorted(registry)
    )


def iter_status(
    *,
    env_reader: EnvOverrideReader = default_env_reader,
    overlay_reader: ConfigOverlayReader = default_overlay_reader,
    registry_reader: RegistryReader = default_registry_reader,
) -> Iterator[FlagStatus]:
    """Iterator variant of :func:`status` — sorted-by-name."""
    registry = registry_reader()
    for name in sorted(registry):
        yield _build_status(
            registry[name],
            env_reader=env_reader,
            overlay_reader=overlay_reader,
            registry=registry,
        )


def reset_cache() -> None:
    """Clear the resolver's per-process cache. Test-only helper."""
    _cache.clear()
