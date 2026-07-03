"""Topology diagnostics surface — read-only operator-facing snapshot.

Backs the ``kairix features status --topology`` CLI flag AND the
``tool_features_status(topology=True)`` MCP variant. Returns a
frozen :class:`TopologyDiagnostics` snapshot showing:

* The declared cc_pairs (id + name + status).
* Per-actor scope-profile resolution — which collections each
  registered actor can read through their scope profile (using Wave
  C's :class:`ScopeProfileResolver`).

Default-safe: when the DB has no topology rows, returns a zero
snapshot — the operator sees "no cc_pairs declared / no scope profiles
declared" so the surface degrades cleanly on a fresh deployment.

Thin module — heavy lifting lives in
:mod:`kairix.core.connectors.cc_pair` (lifecycle reads) and
:mod:`kairix.core.connectors.scope_profile_resolver` (scope
composition).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from kairix.core.connectors.cc_pair import list_cc_pairs
from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

# F17 — column literal duplicated between actor enumeration and the
# snapshot builder; pull to a single constant.
_ACTOR_ID_COL = "actor_id"


@dataclass(frozen=True)
class CCPairSnapshot:
    """One row in the topology diagnostics — minimal cc_pair view."""

    id: int
    name: str
    status: str
    access_type: str


@dataclass(frozen=True)
class ActorScopeSnapshot:
    """One row per actor — collections the actor's profile grants read on."""

    actor_id: str
    actor_kind: str
    readable_collections: tuple[str, ...]
    excluded_collections: tuple[str, ...]


@dataclass(frozen=True)
class TopologyDiagnostics:
    """Aggregator snapshot — frozen, JSON-serialisable.

    Empty values are valid: a fresh DB returns an instance with both
    tuples empty so the operator sees the "nothing declared" state
    rather than an exception.
    """

    cc_pairs: tuple[CCPairSnapshot, ...]
    actor_scopes: tuple[ActorScopeSnapshot, ...]


def _read_cc_pair_snapshots(db: sqlite3.Connection) -> tuple[CCPairSnapshot, ...]:
    """Render the live ``topology_cc_pairs`` rows into snapshots."""
    return tuple(
        CCPairSnapshot(id=p.id, name=p.name, status=p.status, access_type=p.access_type) for p in list_cc_pairs(db)
    )


def _read_actor_ids(db: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """Read every declared (actor_id, actor_kind) tuple from scope_profiles."""
    # F63-bounded: scope_profiles is operator-config-sized (≤O(actors) ≤O(20)).
    rows = db.execute(
        f"SELECT {_ACTOR_ID_COL}, actor_kind FROM topology_scope_profiles ORDER BY {_ACTOR_ID_COL}"
    ).fetchall()
    return tuple((str(row[0]), str(row[1])) for row in rows)


def _resolve_one_actor(
    resolver: ScopeProfileResolver,
    actor_id: str,
    actor_kind: str,
) -> ActorScopeSnapshot:
    """Compose one actor's :class:`ActorScopeSnapshot` via the Wave C resolver."""
    resolved = resolver.resolve(actors=(actor_id,))
    return ActorScopeSnapshot(
        actor_id=actor_id,
        actor_kind=actor_kind,
        readable_collections=tuple(c.name for c in resolved.collections),
        excluded_collections=tuple(c.name for c in resolved.excluded_collections),
    )


def build_topology_diagnostics(db: sqlite3.Connection) -> TopologyDiagnostics:
    """Build a :class:`TopologyDiagnostics` from a live SQLite connection.

    Pure read; the caller still owns the connection lifecycle. Wraps
    the cc_pair lifecycle reader + the Wave C ScopeProfileResolver so
    the CLI / MCP surfaces consume one frozen snapshot instead of
    composing both surfaces themselves.
    """
    cc_pairs = _read_cc_pair_snapshots(db)
    actor_pairs = _read_actor_ids(db)
    resolver = ScopeProfileResolver(db)
    actor_scopes = tuple(_resolve_one_actor(resolver, actor_id, actor_kind) for actor_id, actor_kind in actor_pairs)
    return TopologyDiagnostics(cc_pairs=cc_pairs, actor_scopes=actor_scopes)


def render_topology_human(diag: TopologyDiagnostics) -> str:
    """Render the diagnostics as a text block for the CLI human mode."""
    lines: list[str] = ["Topology diagnostics:"]
    if not diag.cc_pairs:
        lines.append("  cc_pairs:        (none declared)")
    else:
        lines.append("  cc_pairs:")
        for cc in diag.cc_pairs:
            lines.append(f"    [{cc.id}] {cc.name:<40} status={cc.status:<18} access={cc.access_type}")
    if not diag.actor_scopes:
        lines.append("  actor_scopes:    (none declared)")
    else:
        lines.append("  actor_scopes:")
        for scope in diag.actor_scopes:
            readable = ", ".join(scope.readable_collections) or "(none)"
            excluded = ", ".join(scope.excluded_collections) or "(none)"
            lines.append(f"    {scope.actor_id} [{scope.actor_kind}]")
            lines.append(f"      readable:  {readable}")
            lines.append(f"      excluded:  {excluded}")
    return "\n".join(lines)


def render_topology_json(diag: TopologyDiagnostics) -> dict[str, Any]:
    """Render the diagnostics as a JSON-friendly dict (sorted-keys safe)."""
    return {
        "cc_pairs": [
            {"id": cc.id, "name": cc.name, "status": cc.status, "access_type": cc.access_type} for cc in diag.cc_pairs
        ],
        "actor_scopes": [
            {
                "actor_id": scope.actor_id,
                "actor_kind": scope.actor_kind,
                "readable_collections": list(scope.readable_collections),
                "excluded_collections": list(scope.excluded_collections),
            }
            for scope in diag.actor_scopes
        ],
    }
