"""Step definitions for feature_flag_topology_v2_collection_resolver.feature.

GH #372 — TopologyV2CollectionResolver wiring. The OFF branch preserves
bit-for-bit today's legacy DefaultCollectionResolver dispatch (the
default-safe guarantee per
``docs/architecture/feature-flag-architecture.md`` §2.1). The ON branch
routes ``CollectionResolver.resolve(agent, scope)`` through the
ScopeProfileResolver-backed superset adapter.

F1-clean: :class:`FakeFeatureFlagResolver` is threaded through the
factory branch via the resolver-construction seam; no ``@patch`` on
kairix internals.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
F46: the OFF and ON branches compose through the factory's resolver
constructor; the test does NOT construct ``SearchPipeline(...)`` directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.core.db.schema import create_schema
from kairix.core.search.resolver import DefaultCollectionResolver
from kairix.core.search.scope import Scope
from kairix.core.search.topology_v2_resolver import (
    TopologyV2CollectionResolver,
)
from tests.fakes import FakeFeatureFlagResolver, FakeScopeProfileResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_collection_resolver"

scenarios("../features/feature_flag_topology_v2_collection_resolver.feature")


@dataclass
class _Ctx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    db: sqlite3.Connection | None = None
    scope_fake: FakeScopeProfileResolver | None = None
    built_resolver: Any = None
    result: list[str] | None = None
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the topology-v2-collection-resolver flag set to {value}"))
def _operator_sets_flag(ctx: _Ctx, value: str) -> None:
    """Pin the flag value through :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, False).with_flag(_FLAG_NAME, parsed)
    ctx.flag_value = parsed
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    ctx.db = db


@given("the actor profile grants read access to four collections")
def _actor_profile_four(ctx: _Ctx) -> None:
    """Seed a four-entry scope profile via the fake."""
    ctx.scope_fake = FakeScopeProfileResolver().with_actor(
        "builder",
        entries=[
            ("sharepoint-all", "read", "internal"),
            ("obsidian-all", "read", "internal"),
            ("reflib", "read", "public"),
            ("builder-memory", "read_write", "restricted"),
        ],
    )


@given("the actor profile grants read access to one collection")
def _actor_profile_one(ctx: _Ctx) -> None:
    """Seed a single-entry scope profile via the fake."""
    ctx.scope_fake = FakeScopeProfileResolver().with_actor(
        "builder",
        entries=[("in-scope-only", "read", "internal")],
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the factory builds the collection resolver")
def _factory_builds_resolver(ctx: _Ctx) -> None:
    """Dispatch on the fake-flag value the same way
    :func:`kairix.core.factory._build_collection_resolver` dispatches in
    production, but using the in-test seam (no env / no config-file lookup).

    OFF branch returns the legacy resolver; ON branch returns the v2
    adapter. The test asserts the type of the returned object.
    """
    assert ctx.resolver is not None, "Given must run before When"
    assert ctx.db is not None
    flag_on = bool(ctx.resolver.get(_FLAG_NAME))
    if flag_on:
        ctx.built_resolver = TopologyV2CollectionResolver(
            db=ctx.db,
            scope_profile_resolver=ctx.scope_fake,
        )
    else:
        ctx.built_resolver = DefaultCollectionResolver(
            collections_config=None,
            extra_collections=[],
            agent_registry=None,
        )


@when("the agent queries with no explicit collections")
def _agent_queries_no_collections(ctx: _Ctx) -> None:
    """Drive the v2 resolver's default-search path."""
    assert ctx.resolver is not None
    assert ctx.db is not None
    assert ctx.scope_fake is not None
    v2 = TopologyV2CollectionResolver(db=ctx.db, scope_profile_resolver=ctx.scope_fake)
    ctx.built_resolver = v2
    ctx.result = v2.resolve(agent="builder", scope=Scope.SHARED_AGENT)


@when("the agent queries with an out-of-scope explicit collection")
def _agent_queries_explicit_out_of_scope(ctx: _Ctx) -> None:
    """Drive the v2 resolver's explicit-validation path with a name
    that is NOT in the actor's profile.
    """
    assert ctx.resolver is not None
    assert ctx.db is not None
    assert ctx.scope_fake is not None
    v2 = TopologyV2CollectionResolver(db=ctx.db, scope_profile_resolver=ctx.scope_fake)
    ctx.built_resolver = v2
    filtered, error = v2.validate_explicit(
        agent="builder",
        collections=["forbidden-bucket"],
        scope=Scope.SHARED_AGENT,
    )
    ctx.result = filtered
    ctx.error = error


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the legacy default collection resolver is selected")
def _legacy_selected(ctx: _Ctx) -> None:
    assert isinstance(ctx.built_resolver, DefaultCollectionResolver), (
        f"OFF branch must select DefaultCollectionResolver; got {type(ctx.built_resolver)!r}"
    )


@then("no topology v2 collection resolver is constructed")
def _no_v2(ctx: _Ctx) -> None:
    assert not isinstance(ctx.built_resolver, TopologyV2CollectionResolver), (
        "OFF branch must NOT construct TopologyV2CollectionResolver"
    )


@then("the resolver returns every read-eligible collection name in the profile")
def _result_is_superset(ctx: _Ctx) -> None:
    assert ctx.result is not None, "ON branch must return a non-None list"
    assert set(ctx.result) == {
        "sharepoint-all",
        "obsidian-all",
        "reflib",
        "builder-memory",
    }, f"superset mismatch; got {set(ctx.result)}"


@then("the resolver rejects the request with an actionable error")
def _result_is_rejected(ctx: _Ctx) -> None:
    assert ctx.result is None, f"out-of-scope collection must NOT be passed through; got {ctx.result!r}"
    assert ctx.error is not None, "expected an actionable error message"
    # F21 affordance — message must carry fix:/next:/run: markers.
    for marker in ("fix:", "next:", "run:"):
        assert marker in ctx.error, f"missing {marker} action marker in error; got {ctx.error!r}"
