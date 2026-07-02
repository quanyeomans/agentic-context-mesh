"""Unit tests for ``tool_capabilities`` — the programmatic capability catalogue.

Per affordance pattern 4 (docs/architecture/operational-tests-design.md), the
catalogue is what an AI-driven SRE agent introspects to discover the kairix
surface. These tests pin the shape, vocabulary, and the catalogue↔registry
contract so a drift between the hand-maintained list and the FastMCP
registration breaks at unit-test time, not at runtime against an LLM.

Each test carries a ``# Sabotage:`` comment naming a concrete production
change that falsifies it — used to evidence sabotage-proofing in the
review-gate checklist.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import kairix.agents.mcp.server as server_module
from kairix.agents.mcp.server import (
    CAP_CATEGORY_AGENT,
    CAP_CATEGORY_CONFIGURATION,
    CAP_CATEGORY_DIAGNOSTIC,
    CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
    CAP_CATEGORY_KNOWLEDGE_WRITE,
    CAP_CATEGORY_RETRIEVAL,
    CAP_CATEGORY_SYNTHESIS,
    CAPABILITIES_CATALOG,
    LOOP_GROUP_ORDER,
    MCP_PROBE_CONCURRENCY_CAP,
    MCP_PROBE_QUERIES_CAP,
    RECOMMEND_CAPABILITIES_TOOL_NAME,
    Capability,
    agent_facing,
    build_server,
    by_loop_group,
    tool_capabilities,
)

pytestmark = pytest.mark.unit

# The frozen pre-PLA-317 output of ``tool_capabilities()``. The refactor that
# promoted the ``_cap(...)`` rows out of the function body into the module-level
# ``CAPABILITIES_CATALOG`` tuple MUST keep the emitted envelope byte-identical;
# this snapshot is the guard. Regenerate it deliberately (and review the diff)
# only when a capability is intentionally added / changed.
_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "tool_capabilities_snapshot.json"


_WELL_KNOWN_CATEGORIES = {
    CAP_CATEGORY_RETRIEVAL,
    CAP_CATEGORY_SYNTHESIS,
    CAP_CATEGORY_DIAGNOSTIC,
    CAP_CATEGORY_DIAGNOSTIC_OPERATOR_ONLY,
    CAP_CATEGORY_KNOWLEDGE_WRITE,
    CAP_CATEGORY_AGENT,
    # PR 1.4 / #420 — onboard_scan + onboard_agent agent-config tools.
    CAP_CATEGORY_CONFIGURATION,
}


def test_returns_dict_with_capabilities_key() -> None:
    """Top-level envelope must expose `capabilities` (list) + `schema_version`."""
    # Sabotage: rename the "capabilities" key to "items" in tool_capabilities()
    # → this assertion fails.
    out = tool_capabilities()
    assert isinstance(out, dict)
    assert "capabilities" in out
    assert isinstance(out["capabilities"], list)
    assert out["capabilities"], "catalogue must not be empty"
    assert "schema_version" in out


def test_every_entry_has_required_keys() -> None:
    """Every entry must carry `name`, `mcp_tool`, `cli`, `category`.

    Entries whose `mcp_tool` is None (escalation-only / CLI-only) MUST point
    to an escalation target via `escalate_via`.
    """
    # Sabotage: drop the `cli` key from any catalogue entry → this fails.
    required = {"name", "mcp_tool", "cli", "category"}
    for entry in tool_capabilities()["capabilities"]:
        missing = required - entry.keys()
        assert not missing, f"entry {entry.get('name')!r} missing keys: {missing}"
        if entry["mcp_tool"] is None:
            assert entry.get("escalate_via"), f"entry {entry['name']!r} has mcp_tool=None but no escalate_via target"


def test_categories_are_well_known() -> None:
    """Every entry's category string must be in the well-known set."""
    # Sabotage: change one entry's category to "misc" → this assertion fails.
    for entry in tool_capabilities()["capabilities"]:
        assert entry["category"] in _WELL_KNOWN_CATEGORIES, (
            f"entry {entry['name']!r} has unknown category {entry['category']!r}; "
            f"allowed: {sorted(_WELL_KNOWN_CATEGORIES)}"
        )


def test_probe_search_entry_has_mcp_caps() -> None:
    """probe_search exposes the agent-safe caps verbatim (20 queries / 3 concurrency)."""
    # Sabotage: drop `mcp_caps` from the probe_search entry, or change the
    # MCP_PROBE_QUERIES_CAP/MCP_PROBE_CONCURRENCY_CAP module constants → fails.
    catalogue = {e["name"]: e for e in tool_capabilities()["capabilities"]}
    probe = catalogue["probe_search"]
    assert "mcp_caps" in probe, "probe_search entry must publish mcp_caps for agents"
    assert probe["mcp_caps"] == {"queries_max": 20, "concurrency_max": 3}
    # Pin the constants themselves — if either changes, the agent contract changes.
    assert MCP_PROBE_QUERIES_CAP == 20
    assert MCP_PROBE_CONCURRENCY_CAP == 3


def test_escalate_via_targets_match_existing_stubs() -> None:
    """Every `escalate_via` value must resolve to a real public surface.

    The target either appears as another catalogue entry's `mcp_tool` (i.e. a
    registered MCP wrapper), OR exists as a public ``tool_<name>`` symbol on
    ``kairix.agents.mcp.server`` (the canonical escalation stubs do).
    """
    # Sabotage: rename `tool_soak_run` to `tool_soak_runner` in server.py
    # without updating the escalate_via target → fails.
    entries = tool_capabilities()["capabilities"]
    registered_mcp_tools = {e["mcp_tool"] for e in entries if e["mcp_tool"]}
    for entry in entries:
        target = entry.get("escalate_via")
        if target is None:
            continue
        in_catalogue = target in registered_mcp_tools
        as_public_stub = hasattr(server_module, f"tool_{target}")
        assert in_catalogue or as_public_stub, (
            f"entry {entry['name']!r} escalates to {target!r}, "
            f"which is neither a registered MCP tool nor a public tool_<name> symbol"
        )


def test_catalogue_includes_every_registered_mcp_tool() -> None:
    """Every FastMCP-registered tool name must appear in the catalogue.

    Catches the failure mode: someone adds a new ``@server.tool()`` wrapper
    inside ``build_server`` but forgets to add the matching catalogue entry,
    so an introspecting agent can't see it. The catalogue exists exactly to
    prevent that drift.

    A registered tool may surface in the catalogue in either role: as another
    entry's `mcp_tool` (agent-callable), OR as another entry's `escalate_via`
    target (the escalation stubs that return an OperatorOnlyCapability
    envelope). Both forms are catalogued — the test just checks that nothing
    is silently registered without an entry.
    """
    # Sabotage: add a new @server.tool() wrapper inside build_server without
    # updating tool_capabilities() → this assertion fails.
    server = build_server(host="127.0.0.1", port=18190)
    registered = {t.name for t in asyncio.run(server.list_tools())}
    entries = tool_capabilities()["capabilities"]
    catalogued = {e["mcp_tool"] for e in entries if e["mcp_tool"]} | {
        e["escalate_via"] for e in entries if e.get("escalate_via")
    }
    missing = registered - catalogued
    assert not missing, (
        f"registered MCP tools missing from catalogue: {sorted(missing)}. "
        f"fix: add a catalogue entry in tool_capabilities() for each."
    )


def test_catalogue_is_stable_round_trip() -> None:
    """Two calls return equal dicts — no timestamps, no nondeterminism."""
    # Sabotage: add a `"generated_at": time.time()` field to the envelope →
    # this equality fails.
    assert tool_capabilities() == tool_capabilities()


def test_envelope_serialises_via_json_dumps() -> None:
    """The catalogue must JSON-serialise cleanly and round-trip back equal.

    MCP transports the envelope over JSON, so any tuple/set/datetime leakage
    here would silently corrupt the agent's view.
    """
    # Sabotage: change one `category` value from str to an enum instance →
    # json.dumps raises TypeError and this test fails.
    original = tool_capabilities()
    encoded = json.dumps(original)
    decoded = json.loads(encoded)
    assert decoded == original


# ---------------------------------------------------------------------------
# PLA-317 — catalogue promoted to importable typed data (CAPABILITIES_CATALOG),
# with agent_facing() / by_loop_group() accessors. tool_capabilities() must
# stay byte-identical.
# ---------------------------------------------------------------------------


def test_tool_capabilities_is_byte_identical_to_frozen_snapshot() -> None:
    """`tool_capabilities()` matches the frozen pre-refactor snapshot exactly.

    This is the byte-identical guard for the PLA-317 promotion: the rows moved
    out of the function body into the module-level `CAPABILITIES_CATALOG`, but
    the emitted envelope — same rows, same order, same field values, same key
    order — must not drift. Structural equality catches drops/renames; the
    `json.dumps` byte comparison additionally catches a reordered field (equal
    dicts, different serialisation).
    """
    # Sabotage: drop a `when_to_use=...` kwarg from any CAPABILITIES_CATALOG row,
    # reorder two rows, or reorder the keys in Capability.as_dict → this fails.
    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    out = tool_capabilities()
    assert out == expected, "tool_capabilities() drifted from the frozen snapshot (dropped/renamed field?)"
    assert json.dumps(out) == json.dumps(expected), "tool_capabilities() field/row order drifted from the snapshot"


def test_capabilities_catalog_is_typed_capability_rows() -> None:
    """`CAPABILITIES_CATALOG` is importable typed data — a tuple of Capability.

    Consumers (CLI dispatch, guide generator, E2E harness) read the catalogue
    without executing MCP semantics, so it must be a plain frozen-dataclass
    tuple, not the JSON envelope.
    """
    # Sabotage: make CAPABILITIES_CATALOG a list, or have _cap return a dict →
    # one of these assertions fails.
    assert isinstance(CAPABILITIES_CATALOG, tuple)
    assert CAPABILITIES_CATALOG, "catalogue must not be empty"
    assert all(isinstance(cap, Capability) for cap in CAPABILITIES_CATALOG)
    # Frozen: a row can't be mutated after construction.
    with pytest.raises((AttributeError, TypeError)):
        CAPABILITIES_CATALOG[0].name = "mutated"  # type: ignore[misc]  # F3-rationale: assigning to a frozen dataclass field is deliberate here to prove the FrozenInstanceError guard fires at runtime.


def test_tool_capabilities_projects_the_catalog() -> None:
    """The envelope's `capabilities` list is exactly the catalog's `as_dict` rows."""
    # Sabotage: have tool_capabilities() build its own rows instead of reading
    # CAPABILITIES_CATALOG → the lengths/values diverge and this fails.
    out = tool_capabilities()
    assert out["capabilities"] == [cap.as_dict() for cap in CAPABILITIES_CATALOG]


def test_agent_facing_excludes_escalation_stubs_and_flag_off_recommend() -> None:
    """`agent_facing()` returns only directly-callable rows.

    A row is agent-facing when it exposes an MCP tool, is not an operator-only
    escalation stub, and is not the flag-off `recommend_capabilities`
    recommender.
    """
    # Sabotage: drop the `escalate_via is None` clause (escalation stubs leak in)
    # or the recommender exclusion (flag-off recommend leaks in) → this fails.
    facing = agent_facing()
    assert facing, "there must be agent-facing capabilities"
    assert all(isinstance(cap, Capability) for cap in facing)
    assert all(cap.mcp_tool is not None for cap in facing), "agent-facing rows must expose an MCP tool"
    assert all(cap.escalate_via is None for cap in facing), "escalation stubs are not agent-facing"
    assert all(cap.mcp_tool != RECOMMEND_CAPABILITIES_TOOL_NAME for cap in facing), "flag-off recommend excluded"
    assert "recommend" not in {cap.name for cap in facing}
    # It is a strict subset of the full catalogue (the excluded rows exist).
    assert set(facing) < set(CAPABILITIES_CATALOG)


def test_by_loop_group_places_every_agent_facing_cap_in_exactly_one_group() -> None:
    """`by_loop_group()` buckets the catalogue into the loop-ordered IA.

    Groups are keyed in loop order; every agent-facing capability lands in
    exactly one group (and the six groups partition the whole catalogue).
    """
    # Sabotage: map a category to two groups, or append a cap to two buckets in
    # by_loop_group → the exactly-one-group assertion fails.
    grouped = by_loop_group()
    assert list(grouped.keys()) == list(LOOP_GROUP_ORDER)

    placements: dict[str, int] = {}
    for members in grouped.values():
        for cap in members:
            placements[cap.name] = placements.get(cap.name, 0) + 1

    # Every agent-facing capability is placed exactly once.
    for cap in agent_facing():
        assert placements.get(cap.name) == 1, f"{cap.name!r} is not in exactly one loop group"

    # The six groups partition the entire catalogue (no row lost or duplicated).
    total = sum(len(members) for members in grouped.values())
    assert total == len(CAPABILITIES_CATALOG)
    assert all(count == 1 for count in placements.values())


def test_by_loop_group_routes_escalation_stubs_to_escalate() -> None:
    """Operator-only escalation rows land in the `Escalate` group, whatever
    their category — that's the rule that splits `knowledge-write` between
    `Remember` (agent-callable writes) and `Escalate` (operator-only writes)."""
    # Sabotage: drop the `escalate_via`-first branch in _loop_group_for →
    # operator-only knowledge-write rows fall into Remember and this fails.
    escalate = by_loop_group()["Escalate"]
    assert escalate, "there must be escalation capabilities"
    assert all(cap.escalate_via is not None for cap in escalate)
    escalate_names = {cap.name for cap in escalate}
    assert {"embed", "store_crawl", "soak_run"} <= escalate_names
