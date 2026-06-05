"""Step definitions for collection_v2_default_in_scope.feature (GH #373).

Scaffolding ahead of implementation. Every @when step xfails its
scenario via ``pytest.xfail("impl pending — #373 / flag
topology_v2_default_in_scope")`` so the BDD scenarios are present and
authored, but they don't gate green until the production change lands.
The implementation agent removes the xfail call inline as each path
becomes real.

F46-compliant: every step composes via the factory
(:func:`kairix.core.factory.build_search_pipeline` /
:func:`kairix.core.factory.build_collection_resolver`) — no direct
``TopologyV2CollectionResolver(...)`` / ``SearchPipeline(...)``
construction inside step impls.

F1-clean: no monkeypatch of kairix internals.
F2-clean: no ``KAIRIX_*`` env vars.
F13-clean: scenario language uses operator vocabulary (collections,
scope, agents) — implementation symbols (TopologyV2CollectionResolver,
ScopeProfileResolver) stay out of the Gherkin.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_collection_resolver, reset_search_pipeline_cache

pytestmark = pytest.mark.bdd

_XFAIL_REASON = "impl pending — #373 / feature-flag topology_v2_default_in_scope"


@pytest.fixture
def v2_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario state container."""
    reset_search_pipeline_cache()
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()
    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "flag_on": False,
        "in_default_collections": [],
        "opt_in_collections": [],
        "agents": [],
        "scope_profiles": [],
        "search_result": None,
        "error_message": None,
        "resolver": None,
        "materialised_scope": {},
    }


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("the operator has migrated to the topology v2 collection model")
def _operator_migrated(v2_state: dict[str, Any]) -> None:
    """No-op Background step — schema is created via the fixture; the
    Background line is here to keep the Gherkin operator-friendly.
    """
    del v2_state  # explicit ack of the fixture-driven setup


# ---------------------------------------------------------------------------
# Given — flag state, scope config, agent set
# ---------------------------------------------------------------------------


@given("the operator has configured 7 in-default collections and 1 opt-in collection")
def _seven_in_default_one_opt_in(v2_state: dict[str, Any]) -> None:
    v2_state["in_default_collections"] = [
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
    ]
    v2_state["opt_in_collections"] = ["reflib"]


@given(parsers.parse('agent "{agent}" has a scope_profile covering all 8 collections'))
def _agent_scope_covers_all(v2_state: dict[str, Any], agent: str) -> None:
    v2_state["agents"].append(agent)
    v2_state["scope_profiles"].append(
        {
            "actor": agent,
            "entries": [
                *[(c, True) for c in v2_state["in_default_collections"]],
                *[(c, False) for c in v2_state["opt_in_collections"]],
            ],
        }
    )


@given(parsers.parse('agent "{agent}" has reflib in scope with default_in_scope=false'))
def _agent_has_reflib_opt_in(v2_state: dict[str, Any], agent: str) -> None:
    v2_state["agents"].append(agent)
    v2_state["scope_profiles"].append(
        {
            "actor": agent,
            "entries": [
                ("sharepoint", True),
                ("reflib", False),
            ],
        }
    )


@given(parsers.parse('agent "{agent}" does not have builder-memory in scope'))
def _agent_no_builder_memory(v2_state: dict[str, Any], agent: str) -> None:
    v2_state["agents"].append(agent)
    v2_state["scope_profiles"].append(
        {
            "actor": agent,
            "entries": [
                ("sharepoint", True),
                (f"{agent}-memory", True),
            ],
        }
    )


@given(parsers.parse("the legacy collections block declares {count:d} in-default collections"))
def _legacy_collections(v2_state: dict[str, Any], count: int) -> None:
    v2_state["legacy_in_default_count"] = count


@given(parsers.parse('a scope_profile with applies_to=["*"]'))
def _wildcard_profile(v2_state: dict[str, Any]) -> None:
    v2_state["wildcard_profile"] = {
        "applies_to": ["*"],
        "entries": [("sharepoint", True), ("obsidian", True)],
    }


@given(parsers.parse("{count:d} registered agents in the agents block"))
def _registered_agents(v2_state: dict[str, Any], count: int) -> None:
    v2_state["agents"] = [f"agent-{i:02d}" for i in range(count)]


# ---------------------------------------------------------------------------
# When — drive the production surface via the factory
# ---------------------------------------------------------------------------


@when(parsers.parse('agent "{agent}" issues a search with no collections specified'))
def _default_search(v2_state: dict[str, Any], agent: str) -> None:
    """Drive the search via build_search_pipeline (F46/F47).

    Scaffold: production wiring isn't there yet; xfail the scenario with
    a concrete reason that the impl agent removes when the path lands.
    """
    del agent
    pytest.xfail(_XFAIL_REASON)


@when(parsers.parse('agent "{agent}" issues a search with collections=["{collection}"]'))
def _explicit_search(v2_state: dict[str, Any], agent: str, collection: str) -> None:
    del agent, collection
    pytest.xfail(_XFAIL_REASON)


@when("the config loader materialises the scope_profiles")
def _materialise_profiles(v2_state: dict[str, Any]) -> None:
    """Drive the production config parser to expand the wildcard.

    F46-compliant: composes via the public
    :func:`kairix.config.topology_v2.parse_topology_v2` parser surface,
    not by direct construction of the internal
    ``_expand_wildcard_profiles`` helper. The materialised actor →
    collections map populated here is what the ``Then`` step asserts on.
    """
    from kairix.config.topology_v2 import parse_topology_v2

    wildcard = v2_state.get("wildcard_profile") or {}
    entries_payload = [
        {
            "actor_id": "__placeholder__",
            "collection_name": name,
            "mode": "read",
            "default_in_scope": default_in_scope,
        }
        for name, default_in_scope in wildcard.get("entries", [])
    ]
    collection_names = sorted({e["collection_name"] for e in entries_payload})
    yaml_doc = {
        "topology_v2": {
            "collections": [{"name": name, "sources": [{"cc_pair": "cc-bdd-1"}]} for name in collection_names],
            "scope_profiles": [
                {
                    "name": "agent-default",
                    "actor_kind": "agent",
                    "applies_to": list(wildcard.get("applies_to", ["*"])),
                    "entries": entries_payload,
                }
            ],
        },
        "agents": list(v2_state.get("agents", [])),
    }

    cfg = parse_topology_v2(yaml_doc)

    materialised: dict[str, set[str]] = {}
    for profile in cfg.scope_profiles:
        for entry in profile.entries:
            materialised.setdefault(entry.actor_id, set()).add(entry.collection_name)
    v2_state["materialised_scope"] = materialised


# ---------------------------------------------------------------------------
# Then — assertions (xfailed paths above mean these aren't reached until impl)
# ---------------------------------------------------------------------------


@then("the search returns hits from all 7 in-default collections")
def _hits_from_seven(v2_state: dict[str, Any]) -> None:
    result = v2_state.get("search_result")
    assert result is not None, "search_result not populated — production wiring incomplete"
    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    expected = set(v2_state["in_default_collections"])
    assert expected.issubset(collections_seen), f"in-default search missed: {expected - collections_seen}"


@then("the search does not return hits from the opt-in collection")
def _no_opt_in_hits(v2_state: dict[str, Any]) -> None:
    result = v2_state.get("search_result")
    assert result is not None
    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    for opt_in in v2_state["opt_in_collections"]:
        assert opt_in not in collections_seen, (
            f"opt-in collection {opt_in!r} leaked into default search: {collections_seen!r}"
        )


@then(parsers.parse("the search returns hits from reflib only"))
def _hits_from_reflib_only(v2_state: dict[str, Any]) -> None:
    result = v2_state.get("search_result")
    assert result is not None
    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    assert collections_seen == {"reflib"}, f"explicit reflib search must return reflib only; got {collections_seen!r}"


@then("the search returns no results")
def _no_results(v2_state: dict[str, Any]) -> None:
    result = v2_state.get("search_result")
    assert result is not None
    assert not result.results, f"expected no results; got {result.results!r}"


@then(parsers.parse('the operator-facing error message contains "{fragment}" and "{fragment2}" markers'))
def _error_carries_markers(v2_state: dict[str, Any], fragment: str, fragment2: str) -> None:
    error = v2_state.get("error_message") or ""
    assert fragment in error, f"missing {fragment!r} affordance in error: {error!r}"
    assert fragment2 in error, f"missing {fragment2!r} affordance in error: {error!r}"


@then("the search routes via the legacy default collection resolver")
def _routes_via_legacy(v2_state: dict[str, Any]) -> None:
    # Drive the factory under the flag-OFF reader and inspect the type.
    def _off_reader(_name: str) -> bool:
        return False

    resolver = build_collection_resolver(db_path=v2_state["db_path"], flag_reader=_off_reader)
    assert not hasattr(resolver, "validate_explicit"), (
        f"flag OFF must yield legacy resolver; got {type(resolver).__name__}"
    )


@then(parsers.parse("returns hits from the {count:d} in-default legacy collections"))
def _legacy_hits(v2_state: dict[str, Any], count: int) -> None:
    legacy_count = v2_state.get("legacy_in_default_count", 0)
    assert legacy_count == count, f"legacy in-default count mismatch: expected {count}, configured {legacy_count}"


@then("every agent has the wildcard profile's collections in their scope")
def _every_agent_has_wildcard(v2_state: dict[str, Any]) -> None:
    materialised = v2_state.get("materialised_scope") or {}
    wildcard_collections = {name for name, _default_in_scope in v2_state["wildcard_profile"]["entries"]}
    for agent in v2_state["agents"]:
        agent_scope = materialised.get(agent, set())
        missing = wildcard_collections - agent_scope
        assert not missing, f"agent {agent!r} missing wildcard collections {missing!r}"
