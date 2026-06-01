"""Unit tests for the topology v2 config-loader extensions (GH #373).

Pins the parser surface for the new ``default_in_scope`` field on scope
entries + the wildcard ``applies_to: ["*"]`` expansion + the
cross-reference validations (every referenced collection exists, every
agent has a covering profile).

Scaffolding pattern: every test xfails with strict=False until the
parser change lands; the impl agent removes the decorator inline as
each branch becomes real. F11-clean (reason= cites #373 + flag).

The tests import :func:`kairix.config.topology_v2.parse_topology_v2`
(the existing parser surface). Post-#373 the parser will accept the
``default_in_scope`` field on every scope_entry and the ``applies_to``
field on every scope_profile.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _config(scope_profile_entries: list[dict], *, agents: list[str] | None = None) -> dict:
    """Build a minimal topology_v2 YAML-dict fixture.

    The collections block is always populated with the references the
    scope entries point at so the cross-reference validator has a
    canonical target list.
    """
    referenced_collections = {e.get("collection_name") for e in scope_profile_entries if e.get("collection_name")}
    return {
        "topology_v2": {
            "collections": [
                {"name": name, "sources": [{"cc_pair": "cc-pair-1"}]}
                for name in sorted(filter(None, referenced_collections))
            ],
            "scope_profiles": [
                {
                    "name": "test-profile",
                    "actor_kind": "agent",
                    "entries": scope_profile_entries,
                }
            ],
        },
        "agents": agents or [],
    }


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_default_in_scope_missing_defaults_to_true() -> None:
    """A scope_entry YAML without ``default_in_scope`` parses to
    ``default_in_scope=True`` (back-compat — every pre-#373 entry is
    in-default).
    """
    from kairix.config.topology_v2 import parse_topology_v2

    data = _config(
        [
            {"actor_id": "shape", "collection_name": "sharepoint", "mode": "read"},
        ]
    )

    cfg = parse_topology_v2(data)

    profile = cfg.scope_profiles[0]
    entry = profile.entries[0]
    assert getattr(entry, "default_in_scope", None) is True, (
        f"scope entry without explicit default_in_scope must default to True; "
        f"got {getattr(entry, 'default_in_scope', None)!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_default_in_scope_explicit_false_persists() -> None:
    """A scope_entry YAML with ``default_in_scope: false`` parses to
    ``default_in_scope=False``.
    """
    from kairix.config.topology_v2 import parse_topology_v2

    data = _config(
        [
            {
                "actor_id": "shape",
                "collection_name": "reflib",
                "mode": "read",
                "default_in_scope": False,
            },
        ]
    )

    cfg = parse_topology_v2(data)

    entry = cfg.scope_profiles[0].entries[0]
    assert getattr(entry, "default_in_scope", None) is False, (
        f"scope entry with default_in_scope: false must parse to False; "
        f"got {getattr(entry, 'default_in_scope', None)!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_default_in_scope_non_bool_raises_f21() -> None:
    """``default_in_scope: "yes"`` (or any non-bool) raises a config
    error whose message carries fix:/next:/run: action markers (F21).
    """
    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2

    data = _config(
        [
            {
                "actor_id": "shape",
                "collection_name": "reflib",
                "mode": "read",
                "default_in_scope": "yes",  # non-bool
            },
        ]
    )

    with pytest.raises(TopologyV2ParseError) as exc_info:
        parse_topology_v2(data)

    msg = str(exc_info.value)
    assert "default_in_scope" in msg, f"error must name the offending field; got {msg!r}"
    assert "fix:" in msg, f"F21 affordance missing from parse error: {msg!r}"
    assert "next:" in msg, f"F21 affordance missing from parse error: {msg!r}"


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_wildcard_applies_to_expands_to_all_registered_agents() -> None:
    """``applies_to: ["*"]`` + 6 agents in the registry → 6 materialised
    profile rows.

    Pins the wildcard fan-out: one operator-authored profile produces N
    materialised profiles, one per registered agent, so the resolver
    sees concrete (not wildcard-aware) rows.
    """
    from kairix.config.topology_v2 import parse_topology_v2

    agents = ["shape", "builder", "consultant", "growth", "coach", "family"]
    data = {
        "topology_v2": {
            "collections": [{"name": "sharepoint", "sources": [{"cc_pair": "cc-1"}]}],
            "scope_profiles": [
                {
                    "name": "agent-default",
                    "actor_kind": "agent",
                    "applies_to": ["*"],
                    "entries": [
                        {
                            "actor_id": "__placeholder__",
                            "collection_name": "sharepoint",
                            "mode": "read",
                            "default_in_scope": True,
                        },
                    ],
                }
            ],
        },
        "agents": agents,
    }

    cfg = parse_topology_v2(data)

    materialised_actors = {p.entries[0].actor_id for p in cfg.scope_profiles if p.entries}
    assert materialised_actors == set(agents), (
        f"applies_to=['*'] must expand to every registered agent; expected {set(agents)!r}, got {materialised_actors!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_wildcard_applies_to_with_zero_agents_raises_f21() -> None:
    """``applies_to: ["*"]`` with an empty agents block raises an F21 error.

    Pins the loud-failure mode: wildcard fan-out across zero agents is
    a misconfiguration the operator wants to hear about loudly, not a
    silent empty result.
    """
    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2

    data = {
        "topology_v2": {
            "collections": [{"name": "sharepoint", "sources": [{"cc_pair": "cc-1"}]}],
            "scope_profiles": [
                {
                    "name": "agent-default",
                    "actor_kind": "agent",
                    "applies_to": ["*"],
                    "entries": [
                        {
                            "actor_id": "__placeholder__",
                            "collection_name": "sharepoint",
                            "mode": "read",
                        },
                    ],
                }
            ],
        },
        "agents": [],
    }

    with pytest.raises(TopologyV2ParseError) as exc_info:
        parse_topology_v2(data)

    msg = str(exc_info.value)
    assert "agents" in msg.lower(), f"wildcard + zero-agents error must name 'agents'; got {msg!r}"
    assert "fix:" in msg, f"F21 affordance missing: {msg!r}"


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_collection_name_referenced_but_not_defined_raises_f21() -> None:
    """Scope entry references a collection not in the collections list
    → loud error naming the missing collection.

    Pins the cross-reference validator: dangling collection references
    are the most common config typo, must surface with F21 affordance.
    """
    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2

    data = {
        "topology_v2": {
            "collections": [
                {"name": "sharepoint", "sources": [{"cc_pair": "cc-1"}]},
            ],
            "scope_profiles": [
                {
                    "name": "test-profile",
                    "actor_kind": "agent",
                    "entries": [
                        {
                            "actor_id": "shape",
                            "collection_name": "ghost-collection",  # not defined
                            "mode": "read",
                        },
                    ],
                }
            ],
        },
    }

    with pytest.raises(TopologyV2ParseError) as exc_info:
        parse_topology_v2(data)

    msg = str(exc_info.value)
    assert "ghost-collection" in msg, f"dangling reference must name the missing collection; got {msg!r}"
    assert "fix:" in msg, f"F21 affordance missing from cross-ref error: {msg!r}"


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_agent_unreachable_from_all_profiles_raises_f21() -> None:
    """An agent listed in the agents block but not covered by any profile
    raises an F21 error.

    Pins the "every agent has a scope" invariant: a forgotten agent
    means default search returns zero results for them — a silent
    failure mode the validator catches up front.
    """
    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2

    data = {
        "topology_v2": {
            "collections": [{"name": "sharepoint", "sources": [{"cc_pair": "cc-1"}]}],
            "scope_profiles": [
                {
                    "name": "shape-only-profile",
                    "actor_kind": "agent",
                    "applies_to": ["shape"],
                    "entries": [
                        {
                            "actor_id": "shape",
                            "collection_name": "sharepoint",
                            "mode": "read",
                        },
                    ],
                }
            ],
        },
        "agents": ["shape", "orphan-agent"],
    }

    with pytest.raises(TopologyV2ParseError) as exc_info:
        parse_topology_v2(data)

    msg = str(exc_info.value)
    assert "orphan-agent" in msg, f"orphan agent must be named in the unreachable-agent error; got {msg!r}"
    assert "fix:" in msg, f"F21 affordance missing: {msg!r}"


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_applies_to_list_supports_explicit_agent_names() -> None:
    """``applies_to: ["shape", "builder"]`` → 2 materialised rows.

    Pins that operators can mix wildcard + explicit-list patterns —
    explicit-name lists fan out exactly like the wildcard but to the
    named subset.
    """
    from kairix.config.topology_v2 import parse_topology_v2

    data = {
        "topology_v2": {
            "collections": [{"name": "sharepoint", "sources": [{"cc_pair": "cc-1"}]}],
            "scope_profiles": [
                {
                    "name": "subset-profile",
                    "actor_kind": "agent",
                    "applies_to": ["shape", "builder"],
                    "entries": [
                        {
                            "actor_id": "__placeholder__",
                            "collection_name": "sharepoint",
                            "mode": "read",
                            "default_in_scope": True,
                        },
                    ],
                }
            ],
        },
        "agents": ["shape", "builder", "consultant"],
    }

    cfg = parse_topology_v2(data)

    materialised_actors = {p.entries[0].actor_id for p in cfg.scope_profiles if p.entries}
    assert materialised_actors == {"shape", "builder"}, (
        f"applies_to=['shape','builder'] must materialise 2 rows, not 3 (consultant); got {materialised_actors!r}"
    )
