"""Step definitions for feature_flag_topology_v2_config.feature (Wave D F54).

F1-clean: no @patch on kairix internals.
F2-clean: no env-var manipulation — flag value is pinned via
:class:`FakeFeatureFlagResolver`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_config"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    observed_effective: bool | None = None


@pytest.fixture
def topology_v2_config_ctx() -> _Ctx:
    return _Ctx()


@given(parsers.parse("the operator has the topology-v2-config flag set to {value}"))
def _operator_sets_flag(topology_v2_config_ctx: _Ctx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    topology_v2_config_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)


@when("the operator queries the topology-v2-config flag effective value")
def _operator_queries(topology_v2_config_ctx: _Ctx) -> None:
    assert topology_v2_config_ctx.resolver is not None, "Given step must run before When"
    topology_v2_config_ctx.observed_effective = bool(topology_v2_config_ctx.resolver.get(_FLAG_NAME))


@then(parsers.parse("the topology-v2-config flag is reported as effective {expected}"))
def _flag_effective(topology_v2_config_ctx: _Ctx, expected: str) -> None:
    expected_bool = expected.strip().lower() == "true"
    assert topology_v2_config_ctx.observed_effective is expected_bool, (
        f"expected {expected_bool}; observed {topology_v2_config_ctx.observed_effective}"
    )
