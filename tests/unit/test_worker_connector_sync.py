"""Unit coverage for the canonical connector-sync enablement seam.

Task 2+3 of the connector canonical-collapse refactor (Phase 1) adds two
seams to :class:`kairix.worker.ConnectorSyncDeps` — ``config_mapping_fn``
(overlay-aware merged read) and ``flag_reader`` (the enablement predicate
source) — plus the module-level :func:`kairix.worker.connector_enabled`
predicate that keys on connector KIND (``connector_<kind>`` == REGISTRY
suffix), NOT the cc_pair name.

Task 4 wires this predicate into ``run_connector_sync_pipeline``'s
per-entry loop (the loop-wiring test lives in
``tests/test_worker_connector_sync.py``). These tests pin the predicate
behaviour and the default-factory wiring in isolation.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.worker import (
    ConnectorSyncDeps,
    connector_enabled,
)

pytestmark = pytest.mark.unit


def _flag_off(_name: str) -> bool:
    """``read_flag`` substitute that reports every flag OFF. F6-clean:
    a real callable, not None."""
    return False


def _flag_on(_name: str) -> bool:
    """``read_flag`` substitute that reports every flag ON."""
    return True


def test_connector_enabled_registered_and_off_is_false() -> None:
    """A registered connector kind (``sharepoint`` → ``connector_sharepoint``
    is in REGISTRY) with the flag reading False is disabled.

    Sabotage: flip ``name in registry`` to ``name not in registry`` in
    connector_enabled → this test fails because the predicate skips the
    flag read and returns True for the registered kind.
    """
    assert connector_enabled("sharepoint", read_flag=_flag_off) is False


def test_connector_enabled_flagless_kind_is_always_true() -> None:
    """A flagless connector kind (``obsidian`` — no ``connector_obsidian``
    in REGISTRY) always runs, regardless of what ``read_flag`` would say.

    Sabotage: flip ``name in registry`` to ``name not in registry`` → this
    test fails because the predicate consults the OFF read_flag and returns
    False for the flagless kind.
    """
    assert connector_enabled("obsidian", read_flag=_flag_off) is True


def test_connector_enabled_registered_and_on_is_true() -> None:
    """A registered connector kind with the flag reading True is enabled.

    Sabotage: change ``return read_flag(name)`` to ``return False`` → this
    test fails because the ON read_flag no longer propagates.
    """
    assert connector_enabled("sharepoint", read_flag=_flag_on) is True


def test_connector_enabled_resolves_flag_name_from_kind() -> None:
    """The predicate keys on ``connector_<kind>`` — it must pass the
    KIND-derived flag name to ``read_flag``, NOT the bare kind.

    Sabotage: drop the ``connector_`` prefix in connector_enabled (use
    the bare kind as the flag name) → this test fails because the recorded
    flag name no longer matches ``connector_sharepoint``.
    """
    seen: list[str] = []

    def _recording_read_flag(name: str) -> bool:
        seen.append(name)
        return True

    connector_enabled("sharepoint", read_flag=_recording_read_flag)

    assert seen == ["connector_sharepoint"]


def test_connector_enabled_accepts_injected_registry() -> None:
    """The ``registry`` arg is the membership source — an injected registry
    that omits the kind makes that kind flagless (always-on); one that
    contains it consults ``read_flag``.

    Sabotage: ignore the injected ``registry`` and default-import REGISTRY
    unconditionally → this test fails because ``custom`` would not resolve
    as registered under the real REGISTRY.
    """
    from kairix.core.features.registry import FeatureFlag

    custom: dict[str, FeatureFlag] = {
        "connector_custom": FeatureFlag(
            name="connector_custom",
            default=False,
            description="injected test-only flag declaration for the membership proof",
            stage="introduce",
            introduced_in="v2026.7.0",
            target_retire_in="v2027.1.0",
            owner="connector-framework",
            related_spec=None,
        )
    }

    assert connector_enabled("custom", read_flag=_flag_off, registry=custom) is False
    # A kind absent from the injected registry is flagless (always runs).
    assert connector_enabled("sharepoint", read_flag=_flag_off, registry=custom) is True


def test_connector_sync_deps_default_config_mapping_fn_reads_merged_mapping() -> None:
    """``ConnectorSyncDeps()``'s ``config_mapping_fn`` default is the
    overlay-aware merged read — calling it returns the same mapping the
    public :func:`kairix.config_layers.load_merged_mapping` boundary
    produces (the layered read the wizard writes through, #492).

    Driven through the public surface (F5): the default factory's observable
    output is compared to the public reader, not the private helper's name.

    Sabotage: drop the ``config_mapping_fn`` field default_factory → this
    test fails (AttributeError); rewire the default to a stub returning a
    sentinel → the dict no longer matches the public merged read.
    """
    from kairix.config_layers import load_merged_mapping

    deps = ConnectorSyncDeps()
    produced = deps.config_mapping_fn()

    assert isinstance(produced, dict)
    assert produced == load_merged_mapping()


def test_connector_sync_deps_default_flag_reader_delegates_to_features_flag() -> None:
    """``ConnectorSyncDeps()``'s ``flag_reader`` default resolves a flag name
    via the production :func:`kairix.core.features.flag` boundary, so the
    Task 8 dispatch-trio deletion does not orphan the helper.

    Driven through the public surface (F5): the default factory's observable
    output for a registered flag is compared to the public ``flag`` reader.

    Sabotage: drop the ``flag_reader`` field default_factory → this test
    fails (AttributeError); rewire the default to ``lambda _n: True`` →
    the value diverges from the public ``flag`` read for an OFF-by-default
    registered flag.
    """
    from kairix.core.features import flag

    deps = ConnectorSyncDeps()
    value = deps.flag_reader("connector_sharepoint")

    assert isinstance(value, bool)
    assert value == flag("connector_sharepoint")


def test_connector_sync_deps_accepts_injected_flag_reader() -> None:
    """The ``flag_reader`` seam is injectable so Task 4's loop wiring can be
    driven by a fake reader without touching the global REGISTRY.

    Sabotage: make ``flag_reader`` a non-init field → this test fails
    because the constructor rejects the kwarg.
    """
    fake_reader: Callable[[str], bool] = _flag_on
    deps = ConnectorSyncDeps(flag_reader=fake_reader)

    assert deps.flag_reader is fake_reader
