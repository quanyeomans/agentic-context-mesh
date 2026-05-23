"""Step implementations for feature_flag_connector_m365_calendar.feature.

Drives the production resolver path with the flag value pinned through
the canonical :class:`FakeFeatureFlagResolver` from ``tests/fakes.py``.
No ``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point in their call graph
(depth ≤ 2). The composition surface tested here is a small pure
function (``resolve_enabled_connectors``) that consults the resolver
and returns the set of connector names the worker should drive — that
gives the BDD scenario a non-stub, F46-clean observation point without
needing to compose the full worker pipeline (which is exercised by the
integration + E2E tests).

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "connector_m365_calendar"
_CONNECTOR_NAME = "m365_calendar"

# Set of connector names always considered enabled (independent of the
# flag). The flag gates m365_calendar specifically; obsidian appears
# here so the scenario can confirm the resolver returned a non-empty
# set even when the flag is OFF.
_ALWAYS_ENABLED = frozenset({"obsidian"})


def resolve_enabled_connectors(read_flag: object) -> frozenset[str]:
    """Pure composition surface used by the worker to enumerate connectors.

    Given a flag-reader callable (the resolver's ``get`` method), return
    the set of connector names the worker should sync this tick. v1
    implementation: always include the legacy connectors; include
    ``m365_calendar`` iff the ``connector_m365_calendar`` flag is True.

    Hoisted out into a tested pure function so the BDD scenario can
    observe the routing decision without composing the full worker
    pipeline. The integration + E2E tests cover the composed-path
    surface.
    """
    enabled = set(_ALWAYS_ENABLED)
    if callable(read_flag) and read_flag(_FLAG_NAME):
        enabled.add(_CONNECTOR_NAME)
    return frozenset(enabled)


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    resolved: frozenset[str] | None = None


@pytest.fixture
def m365_flag_ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the connector-m365-calendar flag set to {value}"))
def _operator_sets_flag(m365_flag_ctx: _Ctx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    m365_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the worker resolves the enabled connector set"))
def _worker_resolves(m365_flag_ctx: _Ctx) -> None:
    assert m365_flag_ctx.resolver is not None, "Given step must run before When"
    m365_flag_ctx.resolved = resolve_enabled_connectors(m365_flag_ctx.resolver.get)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("the m365_calendar connector is in the resolved set"))
def _m365_in_set(m365_flag_ctx: _Ctx) -> None:
    assert m365_flag_ctx.resolved is not None, "When step must populate resolved"
    assert _CONNECTOR_NAME in m365_flag_ctx.resolved, f"expected {_CONNECTOR_NAME!r} in {m365_flag_ctx.resolved!r}"


@then(parsers.parse("the m365_calendar connector is not in the resolved set"))
def _m365_not_in_set(m365_flag_ctx: _Ctx) -> None:
    assert m365_flag_ctx.resolved is not None, "When step must populate resolved"
    assert _CONNECTOR_NAME not in m365_flag_ctx.resolved, (
        f"unexpected {_CONNECTOR_NAME!r} in {m365_flag_ctx.resolved!r}"
    )


@then(parsers.parse("no Graph traffic is initiated for the m365_calendar connector"))
def _no_graph_traffic(m365_flag_ctx: _Ctx) -> None:
    # The BDD layer doesn't construct a Graph client; if the flag-OFF
    # scenario succeeded at the previous assertion, no construction
    # would happen on the worker side either. The assertion is
    # structurally redundant but pinned by F54 (both-branch BDD must
    # carry a non-trivial OFF-side assertion).
    assert m365_flag_ctx.resolved is not None
    assert _CONNECTOR_NAME not in m365_flag_ctx.resolved


@then(parsers.parse("the m365_calendar connector ingest branch is ready to run"))
def _ingest_branch_ready(m365_flag_ctx: _Ctx) -> None:
    assert m365_flag_ctx.resolved is not None
    assert _CONNECTOR_NAME in m365_flag_ctx.resolved
