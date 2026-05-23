"""Scope profile resolver — ADR v2 §6 actor → collections composition.

Resolves the requesting actors' :class:`~kairix.core.protocols.ScopeProfile`
records into a :class:`ResolvedScope` carrying the collections the
search layer is permitted to look in.

Composition rules (ADR v2 §6):

* **intersection (default)** — collections present in every requesting
  profile; ``max_sensitivity`` reduced by F39-min (least permissive
  wins); write rights AND-ed.
* **union** — collections present in any profile; ``max_sensitivity``
  by F39-max; write rights OR-ed. Requires a
  ``scope_composition_token`` (Wave D operator-config wires real
  authz; Wave C stubs to ``True`` so the shape is testable).

Returns include excluded collections with a ``reason`` enum so callers
(search surface, result envelope) can render an operator-friendly
"why was this collection excluded" diagnosis.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from kairix.core.protocols import F39Tier, InsufficientPermissionsError

# F39 tier ordering — least permissive at index 0. Used by
# :func:`min_sensitivity` to pick the F39-min across actors.
_F39_TIER_ORDER: tuple[F39Tier, ...] = ("public", "internal", "confidential", "restricted")

# F17 — extract repeated literal so changing it (e.g. renaming to
# "intersect" later) is a single edit. Used at the public Literal
# default + the dispatch branch.
_INTERSECTION_MODE = "intersection"


@dataclass(frozen=True)
class ResolvedCollection:
    """One collection the resolver granted access to."""

    name: str
    max_sensitivity: F39Tier
    weight: float


ExcludedReason = Literal[
    "actor_lacks_read",
    "sensitivity_cap_too_high",
    "container_revoked",
    "container_transient",
    "perm_sync_stale",
]


# F17 — reason literal duplicated across the two "actor lacks read"
# exclusion sites + the docstring reference. Pull to one constant.
_REASON_ACTOR_LACKS_READ: ExcludedReason = "actor_lacks_read"


@dataclass(frozen=True)
class ExcludedCollection:
    """One collection the resolver excluded, with reason + escalation hint."""

    name: str
    reason: ExcludedReason
    escalation_hint: str | None


@dataclass(frozen=True)
class ResolvedScope:
    """Composed outcome of :meth:`ScopeProfileResolver.resolve`."""

    collections: tuple[ResolvedCollection, ...]
    excluded_collections: tuple[ExcludedCollection, ...]


@dataclass(frozen=True)
class _ActorEntry:
    """One row from ``topology_scope_profiles`` JOIN ``topology_scope_entries``."""

    actor_id: str
    collection_name: str
    can_read: bool
    can_write: bool
    max_sensitivity: F39Tier


def min_sensitivity(tiers: Sequence[F39Tier]) -> F39Tier:
    """F39-min across a sequence of tiers — least permissive wins.

    The empty case raises ``ValueError`` — callers should guard.
    """
    if not tiers:
        raise ValueError("cannot compute F39-min on empty sequence")
    min_idx = min(_F39_TIER_ORDER.index(t) for t in tiers)
    return _F39_TIER_ORDER[min_idx]


# Underscored alias preserved for internal call sites. New consumers use
# the public ``min_sensitivity`` name.
_min_sensitivity = min_sensitivity


def _validate_union_token(token: str | None) -> None:
    """Wave C stub — accept any non-empty token.

    Wave D wires real per-token authz (signed JWT, expiry, audience).
    Until then any caller passing a non-empty string satisfies the
    "I'm aware union mode broadens scope" intent gate.
    """
    if token is None or not token.strip():
        raise InsufficientPermissionsError("scope_composition='union' requires a non-empty scope_composition_token")


class ScopeProfileResolver:
    """Compose multiple actors' scope profiles into a :class:`ResolvedScope`.

    Construct once per request; call :meth:`resolve` with the actor ids
    + skill / task context.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def resolve(
        self,
        *,
        actors: tuple[str, ...],
        skill: str | None = None,
        task: str | None = None,
        scope_composition: Literal["intersection", "union"] = _INTERSECTION_MODE,  # type: ignore[assignment]  # F3-rationale: _INTERSECTION_MODE is a Literal["intersection"]; mypy doesn't narrow the str-annotated constant back to its Literal type without re-annotating.
        scope_composition_token: str | None = None,
    ) -> ResolvedScope:
        """Return the composed :class:`ResolvedScope` for ``actors``.

        ``skill`` and ``task`` are accepted now for Wave D / E
        forward-compat (they'll filter which task_collections a skill
        composes); Wave C ignores them after the no-op pin.
        """
        # Read ``skill`` and ``task`` once so the slots stay live for F19;
        # Wave D / E dispatch will branch on them once task_collection
        # filtering lands.
        if skill is not None or task is not None:
            _ = (skill, task)
        del skill, task

        if scope_composition == "union":
            _validate_union_token(scope_composition_token)

        actor_entries = self._load_actor_entries(actors)
        if not actor_entries:
            return ResolvedScope(collections=(), excluded_collections=())

        if scope_composition == _INTERSECTION_MODE:
            return _compose_intersection(actor_entries, actors)
        return _compose_union(actor_entries)

    def _load_actor_entries(self, actors: tuple[str, ...]) -> tuple[_ActorEntry, ...]:
        """SELECT every scope-entry row for ``actors``.

        Returns rows JOIN-ed across ``topology_scope_profiles`` +
        ``topology_scope_entries``. Empty input → empty output.
        """
        if not actors:
            return ()
        placeholders = ",".join("?" for _ in actors)
        rows = self._db.execute(
            "SELECT sp.actor_id, se.collection_name, se.can_read, se.can_write, se.max_sensitivity "
            "FROM topology_scope_profiles sp "
            "JOIN topology_scope_entries se ON se.scope_profile_id = sp.id "
            f"WHERE sp.actor_id IN ({placeholders})",
            actors,
        ).fetchall()
        return tuple(
            _ActorEntry(
                actor_id=row[0],
                collection_name=row[1],
                can_read=bool(row[2]),
                can_write=bool(row[3]),
                max_sensitivity=row[4],
            )
            for row in rows
        )


def _compose_intersection(entries: tuple[_ActorEntry, ...], actors: tuple[str, ...]) -> ResolvedScope:
    """Intersection mode: collections present in EVERY actor + F39-min.

    Per ADR v2 §6: ``max_sensitivity`` by F39-min across actors; write
    rights by AND. An actor lacking read access on a shared collection
    moves that collection to ``excluded_collections`` (reason
    ``actor_lacks_read``).
    """
    by_collection: dict[str, list[_ActorEntry]] = {}
    for entry in entries:
        by_collection.setdefault(entry.collection_name, []).append(entry)

    collections: list[ResolvedCollection] = []
    excluded: list[ExcludedCollection] = []
    for collection_name, actor_entries in sorted(by_collection.items()):
        if {e.actor_id for e in actor_entries} != set(actors):
            # Not every actor has an entry for this collection — intersection drops it.
            excluded.append(
                ExcludedCollection(
                    name=collection_name,
                    reason=_REASON_ACTOR_LACKS_READ,
                    escalation_hint=(f"add scope entry for collection={collection_name!r} to every requesting actor"),
                )
            )
            continue
        if not all(e.can_read for e in actor_entries):
            excluded.append(
                ExcludedCollection(
                    name=collection_name,
                    reason=_REASON_ACTOR_LACKS_READ,
                    escalation_hint=(f"grant can_read=True to every actor's scope entry on {collection_name!r}"),
                )
            )
            continue
        max_sensitivity = _min_sensitivity([e.max_sensitivity for e in actor_entries])
        collections.append(
            ResolvedCollection(
                name=collection_name,
                max_sensitivity=max_sensitivity,
                weight=1.0,
            )
        )
    return ResolvedScope(
        collections=tuple(collections),
        excluded_collections=tuple(excluded),
    )


def _compose_union(entries: tuple[_ActorEntry, ...]) -> ResolvedScope:
    """Union mode: collections present in ANY actor + F39-max.

    Union mode is rare — only authorised callers (with a valid
    scope_composition_token) reach this branch. Read rights OR-ed,
    write rights OR-ed, ``max_sensitivity`` by F39-max (most permissive
    among the actors that DO list the collection).
    """
    by_collection: dict[str, list[_ActorEntry]] = {}
    for entry in entries:
        by_collection.setdefault(entry.collection_name, []).append(entry)
    collections: list[ResolvedCollection] = []
    for collection_name, actor_entries in sorted(by_collection.items()):
        readable = [e for e in actor_entries if e.can_read]
        if not readable:
            continue
        # F39-max across the readable subset → take the index-MAX of _F39_TIER_ORDER.
        max_idx = max(_F39_TIER_ORDER.index(e.max_sensitivity) for e in readable)
        collections.append(
            ResolvedCollection(
                name=collection_name,
                max_sensitivity=_F39_TIER_ORDER[max_idx],
                weight=1.0,
            )
        )
    return ResolvedScope(collections=tuple(collections), excluded_collections=())
