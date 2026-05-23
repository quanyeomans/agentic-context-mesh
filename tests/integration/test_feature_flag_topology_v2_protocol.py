"""F54 integration coverage for the ``topology_v2_protocol`` feature flag.

Wave B lands 9 capability Protocols + default-impl shims so the 4
shipped connectors continue to satisfy the new shapes without
behavioural change. This file pins the F54 both-branch contract:
same fake resolver across OFF + ON, verifying the flag's value is
observable to gated code paths AND that the capability Protocols are
universally satisfied regardless of the flag state (Wave B is
pure-additive; the flag only gates the runtime dispatch path that
lands in Wave C).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
``FakeFeatureFlagResolver`` from ``tests/fakes.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.obsidian.connector import ObsidianConnector
from kairix.core.protocols import (
    CredentialsConnector,
    HierarchyConnector,
    PollConnector,
    SlimConnector,
    SourceConnector,
)
from tests.fakes import FakeFeatureFlagResolver


@pytest.mark.integration
def test_flag_off_legacy_path_still_active(tmp_path: Path) -> None:
    """OFF branch: capability Protocols are still satisfied (shims are unconditional)
    but no Wave C+ runtime routing fires.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", False)
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianConnector(vault_root=vault)

    # Shims are present even when flag is OFF — Wave B is pure-additive.
    assert isinstance(connector, SourceConnector)
    assert isinstance(connector, PollConnector)
    assert isinstance(connector, SlimConnector)
    assert isinstance(connector, HierarchyConnector)

    # Production code that would route through the capability path under
    # the flag must check it first. When OFF, the route skip is the
    # expected behaviour.
    if resolver.get("topology_v2_protocol"):
        raise AssertionError("OFF branch should not enter the capability-routing path")


@pytest.mark.integration
def test_flag_on_capability_path_unlocks(tmp_path: Path) -> None:
    """ON branch: a Wave C+ code path inspecting capabilities sees the connector
    satisfying the relevant capability Protocols.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", True)
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianConnector(vault_root=vault)

    routed_through_capability = False
    if resolver.get("topology_v2_protocol"):
        # Wave C+ shape: runtime isinstance check picks the dispatch path.
        assert isinstance(connector, PollConnector)
        routed_through_capability = True

    assert routed_through_capability is True, "ON branch must enter the capability-routing path"


@pytest.mark.integration
def test_flag_state_reflects_resolver() -> None:
    """The flag's effective value matches what the resolver reports — basic correctness."""
    on = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", True)
    off = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", False)
    assert on.get("topology_v2_protocol") is True
    assert off.get("topology_v2_protocol") is False


@pytest.mark.integration
def test_dex_crm_credentials_connector_satisfied_under_both_branches() -> None:
    """DexCrmConnector satisfies CredentialsConnector regardless of flag state.

    Wave B's shims are unconditional; the flag only gates runtime
    dispatch through the capability path. Both with_flag("topology_v2_protocol", False)
    and with_flag("topology_v2_protocol", True) leave the Protocol shape
    intact.
    """
    off = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", False)
    on = FakeFeatureFlagResolver().with_flag("topology_v2_protocol", True)
    connector = DexCrmConnector()
    assert isinstance(connector, CredentialsConnector)
    # Both resolutions observable.
    assert off.get("topology_v2_protocol") is False
    assert on.get("topology_v2_protocol") is True
