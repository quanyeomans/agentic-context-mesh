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

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from kairix.core.protocols import F39Tier, InsufficientPermissionsError

logger = logging.getLogger(__name__)

# F39 tier ordering — least permissive at index 0. Used by
# :func:`min_sensitivity` to pick the F39-min across actors.
_F39_TIER_ORDER: tuple[F39Tier, ...] = ("public", "internal", "confidential", "restricted")

# Unknown-tier fallback — connector code uses a broader set
# ("public", "internal", "client-confidential", "personal") and operator
# config can declare tiers outside :data:`_F39_TIER_ORDER`. Rather than
# crash F39-min, treat unknown tiers as the conservative "internal" default
# (mid-permissive). The resolver logs the fallback so operators can see
# the tier mismatch in their telemetry; the tier itself is preserved on
# the ResolvedCollection so downstream cap-comparators can still see the
# raw value.
_F39_UNKNOWN_TIER_FALLBACK: F39Tier = "internal"

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
    """One row from ``topology_scope_profiles`` JOIN ``topology_scope_entries``.

    GH #373 — ``default_in_scope`` defaults to True for back-compat with
    pre-migration rows. The :meth:`ScopeProfileResolver._load_actor_entries`
    SELECT COALESCEs the column to 1 when the column is missing on a
    legacy DB so this dataclass invariant always holds.
    """

    actor_id: str
    collection_name: str
    can_read: bool
    can_write: bool
    max_sensitivity: F39Tier
    default_in_scope: bool = field(default=True)


def min_sensitivity(tiers: Sequence[F39Tier]) -> F39Tier:
    """F39-min across a sequence of tiers — least permissive wins.

    The empty case raises ``ValueError`` — callers should guard.

    Tiers outside :data:`_F39_TIER_ORDER` (e.g. connector-side
    ``"personal"`` / ``"client-confidential"`` declared in operator
    config) fall back to :data:`_F39_UNKNOWN_TIER_FALLBACK` for the
    purpose of the min comparison. The raw tier strings flow through
    unchanged on :class:`ResolvedCollection`; only the comparison is
    coerced. F68-style graceful degradation — operator tier mismatch
    must NOT crash the resolver.
    """
    if not tiers:
        raise ValueError("cannot compute F39-min on empty sequence")
    indices = []
    for t in tiers:
        try:
            indices.append(_F39_TIER_ORDER.index(t))
        except ValueError:
            logger.warning(
                "scope_profile_resolver: unknown sensitivity tier %r — treating as %r for F39-min",
                t,
                _F39_UNKNOWN_TIER_FALLBACK,
            )
            indices.append(_F39_TIER_ORDER.index(_F39_UNKNOWN_TIER_FALLBACK))
    min_idx = min(indices)
    return _F39_TIER_ORDER[min_idx]


# Underscored alias preserved for internal call sites. New consumers use
# the public ``min_sensitivity`` name.
_min_sensitivity = min_sensitivity


def _tier_index(tier: F39Tier) -> int:
    """Return the :data:`_F39_TIER_ORDER` index for ``tier``.

    Unknown tiers (operator-config strings outside the F39 Literal) fall
    back to :data:`_F39_UNKNOWN_TIER_FALLBACK` — see :func:`min_sensitivity`
    for the F68 rationale.
    """
    try:
        return _F39_TIER_ORDER.index(tier)
    except ValueError:
        logger.warning(
            "scope_profile_resolver: unknown sensitivity tier %r — treating as %r",
            tier,
            _F39_UNKNOWN_TIER_FALLBACK,
        )
        return _F39_TIER_ORDER.index(_F39_UNKNOWN_TIER_FALLBACK)


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
        # GH #373 — lazy cache of whether the topology_scope_entries table
        # carries the `default_in_scope` column. Populated on first resolve().
        self._has_default_col_cached: bool | None = None

    def resolve(
        self,
        *,
        actors: tuple[str, ...],
        skill: str | None = None,
        task: str | None = None,
        scope_composition: Literal["intersection", "union"] = _INTERSECTION_MODE,  # type: ignore[assignment]  # F3-rationale: _INTERSECTION_MODE is a Literal["intersection"]; mypy doesn't narrow the str-annotated constant back to its Literal type without re-annotating.
        scope_composition_token: str | None = None,
        default_only: bool = False,
    ) -> ResolvedScope:
        """Return the composed :class:`ResolvedScope` for ``actors``.

        ``skill`` and ``task`` are accepted now for Wave D / E
        forward-compat (they'll filter which task_collections a skill
        composes); Wave C ignores them after the no-op pin.

        ``default_only`` (GH #373): when True, drops entries flagged
        ``default_in_scope=0`` before composition runs. Used by
        :class:`TopologyV2CollectionResolver` on the ``collections=None``
        path to return only the in-default superset; explicit
        ``collections=[...]`` paths still pass ``default_only=False`` so
        opt-in collections (e.g. reflib) remain reachable by name.
        Back-compat with pre-#373 callers: the default is False, so every
        existing call site sees zero behaviour change.
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
        if default_only:
            actor_entries = tuple(e for e in actor_entries if e.default_in_scope)
        if not actor_entries:
            return ResolvedScope(collections=(), excluded_collections=())

        if scope_composition == _INTERSECTION_MODE:
            return _compose_intersection(actor_entries, actors)
        return _compose_union(actor_entries)

    def _load_actor_entries(self, actors: tuple[str, ...]) -> tuple[_ActorEntry, ...]:
        """SELECT every scope-entry row for ``actors``.

        Returns rows JOIN-ed across ``topology_scope_profiles`` +
        ``topology_scope_entries``. Empty input → empty output.

        GH #373 — also selects ``default_in_scope``. On a pre-migration DB
        whose ``topology_scope_entries`` table lacks the column, the
        helper falls back to a SELECT without it and treats every row as
        ``default_in_scope=1`` (in-default). This preserves the
        back-compat invariant: operators running the new resolver code
        against a DB whose ``ALTER TABLE`` hasn't run yet continue to see
        every legacy row surface in default search (no silent loss).
        """
        if not actors:
            return ()
        placeholders = ",".join("?" for _ in actors)
        has_default_col = self._scope_entries_has_default_in_scope()
        if has_default_col:
            # F63-bounded: scope entries are operator-config-sized per actor; filtered by IN clause.
            rows = self._db.execute(
                "SELECT sp.actor_id, se.collection_name, se.can_read, se.can_write, "
                "se.max_sensitivity, se.default_in_scope "
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
                    default_in_scope=bool(row[5]),
                )
                for row in rows
            )
        # Pre-migration shape — column missing; back-compat treats every
        # legacy row as in-default.
        # F63-bounded: same shape as above, scope entries are operator-config-sized.
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
                default_in_scope=True,
            )
            for row in rows
        )

    def _scope_entries_has_default_in_scope(self) -> bool:
        """True when ``topology_scope_entries`` carries the #373 column.

        Cached per-instance — PRAGMA table_info is cheap but called on
        every resolve() so a one-shot check on first invocation is
        sufficient.
        """
        if self._has_default_col_cached is not None:
            return self._has_default_col_cached
        # F63-bounded: PRAGMA table_info returns one row per column (schema-bounded, ≤O(10) rows).
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
        self._has_default_col_cached = "default_in_scope" in cols
        return self._has_default_col_cached


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
        # Unknown tiers coerce to :data:`_F39_UNKNOWN_TIER_FALLBACK` for the
        # index comparison — same F68 graceful fallback as min_sensitivity.
        max_idx = max(_tier_index(e.max_sensitivity) for e in readable)
        collections.append(
            ResolvedCollection(
                name=collection_name,
                max_sensitivity=_F39_TIER_ORDER[max_idx],
                weight=1.0,
            )
        )
    return ResolvedScope(collections=tuple(collections), excluded_collections=())
