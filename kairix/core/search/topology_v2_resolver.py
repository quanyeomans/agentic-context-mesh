"""Topology v2 ``CollectionResolver`` — superset-of-scope-profile default.

GH #372 — when an agent calls kairix without specifying ``collections=``,
return the superset of every collection the agent's scope_profile grants
read access to.

Today the legacy :class:`kairix.core.search.resolver.DefaultCollectionResolver`
reads ``collections.shared[].in_default`` from ``kairix.config.yaml`` —
entirely blind to ``topology_scope_profiles`` + ``topology_scope_entries``.
The right primitive already exists:
:class:`kairix.core.connectors.scope_profile_resolver.ScopeProfileResolver`.
The wiring gap is what this Adapter closes.

Behaviour summary (mirrors the existing :class:`CollectionResolver` Protocol
at :mod:`kairix.core.protocols`):

* ``agent=X, scope=Scope.SHARED_AGENT`` (default) — call
  ``ScopeProfileResolver.resolve(actors=(X,))``; return the names of every
  collection the actor's scope_profile lets them read (can_read=True).
* ``agent=X, scope=Scope.AGENT`` — restrict to write-eligible
  (``can_write=True``) entries; this is the agent's own memory bucket.
* ``agent=X, scope=Scope.SHARED`` — return read-eligible entries (the
  same superset as SHARED_AGENT today, since v2 doesn't yet split
  "shared" vs "own" — Wave G of the topology v2 migration will).
* ``agent=None, scope=Scope.ALL_AGENTS`` or ``Scope.EVERYTHING`` — return
  every collection_name from ``topology_collections`` whose parent
  cc_pair has ``access_type='PUBLIC'``. This is the wildcard path for
  cross-agent synthesis.

The constructor accepts an injectable :class:`ScopeProfileResolver` so
tests can inject a :class:`tests.fakes.FakeScopeProfileResolver` without
seeding SQLite (F1-clean). Production callers pass only ``db=``; the
default factory wires a real :class:`ScopeProfileResolver` against the
connection.

This Adapter is wired by :func:`kairix.core.factory.build_search_pipeline`
behind the ``topology_v2_collection_resolver`` feature flag (default
False). The cutover is a separate deliberate action per the default-safe
principle (docs/architecture/feature-flag-architecture.md §2.1).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from kairix.core.connectors.scope_profile_resolver import (
    ScopeProfileResolver,
)
from kairix.core.search.scope import Scope

if TYPE_CHECKING:
    from kairix.core.protocols import F39Tier

logger = logging.getLogger(__name__)


# F39 tier ordering — least permissive at index 0; most at index 3.
# Matches kairix/core/connectors/scope_profile_resolver._F39_TIER_ORDER but
# we keep a private copy so this module doesn't reach into the underscored
# attribute of a sibling module (F5).
_F39_TIER_ORDER: tuple[F39Tier, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)


class TopologyV2CollectionResolver:
    """Production ``CollectionResolver`` backed by topology v2 scope profiles.

    Composes :class:`ScopeProfileResolver` with the search pipeline's
    Protocol contract: ``resolve(agent, scope) -> list[str] | None``.

    Returning ``None`` (or empty) means "no collection filter" — the
    search backends interpret that as "search everything". Returning a
    non-empty list scopes BM25 + vector to those collection names.

    Failure-injection contract (F68): if the underlying
    ``ScopeProfileResolver`` raises, this Adapter propagates the error
    rather than silently falling back to legacy. The search pipeline's
    ``_resolve_collections`` catches ``NotImplementedError`` only — every
    other exception surfaces with a fix:/run: affordance so operators
    see the misconfiguration immediately.
    """

    def __init__(
        self,
        *,
        db: sqlite3.Connection,
        scope_profile_resolver: ScopeProfileResolver | None = None,
        max_sensitivity_cap: F39Tier | None = None,
    ) -> None:
        """Construct the Adapter against ``db``.

        ``scope_profile_resolver`` is the DI seam — tests inject a fake
        Protocol-compliant resolver; production leaves it ``None`` and
        the constructor builds a real :class:`ScopeProfileResolver`.

        ``max_sensitivity_cap`` lets the caller cap the maximum
        sensitivity of any returned collection. When ``None`` (default),
        every scope-eligible entry is returned regardless of tier; when
        set, entries whose ``max_sensitivity`` exceeds the cap are
        dropped. Used by Wave F's per-skill cap enforcement.
        """
        self._db = db
        self._resolver = scope_profile_resolver if scope_profile_resolver is not None else ScopeProfileResolver(db)
        self._cap = max_sensitivity_cap

    def resolve(self, agent: str | None, scope: object) -> list[str] | None:
        """Return the concrete collection list for ``(agent, scope)``.

        See class docstring for the per-scope dispatch table.
        """
        scope_enum = scope if isinstance(scope, Scope) else Scope.parse(str(scope))

        # agent=None branch — public-access fan-out only makes sense for
        # ALL_AGENTS / EVERYTHING; SHARED / SHARED_AGENT / AGENT without an
        # agent name is operator misconfiguration the legacy resolver
        # treated as empty, so we mirror that for backwards compat.
        if agent is None:
            if scope_enum in (Scope.ALL_AGENTS, Scope.EVERYTHING):
                return self._public_collections() or None
            return None

        # agent supplied — resolve via the scope profile.
        resolved = self._resolver.resolve(actors=(agent,))
        names = self._filter_by_scope(resolved.collections, scope_enum, agent)
        return names or None

    def validate_explicit(
        self, agent: str, collections: list[str], scope: object
    ) -> tuple[list[str] | None, str | None]:
        """Validate ``collections`` are all within ``agent``'s scope.

        Returns ``(filtered, error_message)``:

          * ``(collections, None)`` — every entry is in scope; pass through.
          * ``(None, "<F21 message>")`` — one or more entries are NOT in
            the actor's scope; the message names the offending entries
            and lists the allowed set with ``fix:``/``next:``/``run:``
            action markers.

        The search pipeline calls this when the operator passes
        explicit ``collections=`` and the operator-supplied list must
        be sanity-checked against the actor's read scope.
        """
        scope_enum = scope if isinstance(scope, Scope) else Scope.parse(str(scope))
        resolved = self._resolver.resolve(actors=(agent,))
        allowed = set(self._filter_by_scope(resolved.collections, scope_enum, agent))
        requested = list(collections)
        unknown = [name for name in requested if name not in allowed]
        if unknown:
            allowed_sorted = ", ".join(sorted(allowed)) or "<empty>"
            unknown_sorted = ", ".join(sorted(unknown))
            return (
                None,
                (
                    f"collection(s) not in agent {agent!r} scope: {unknown_sorted}. "
                    f"fix: drop the out-of-scope entries from the explicit "
                    f"`collections=` list OR grant {agent!r} read access via "
                    f"topology_scope_entries. "
                    f"next: see docs/architecture/connector-scope-topology/ADR.md §6. "
                    f"run: kairix scope-profile show --actor {agent}. "
                    f"allowed collections: {allowed_sorted}."
                ),
            )
        return (requested, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _filter_by_scope(
        self,
        collections: tuple,
        scope_enum: Scope,
        agent: str,
    ) -> list[str]:
        """Apply scope mode + sensitivity cap to the resolved collections.

        Returns an ordered list of names; SHARED_AGENT / SHARED / AGENT
        each select a slice of the actor's profile:

          * AGENT — write-eligible entries only (the actor's own
            memory bucket; today's resolver maps this to ``<agent>-memory``).
          * SHARED — read-eligible entries (entire read scope; v2 does
            not yet distinguish "shared" vs "private" at the entry level).
          * SHARED_AGENT — read-eligible entries (matches default search).
          * ALL_AGENTS / EVERYTHING — handled before this helper (public
            collections, not scope-profile filtered).
        """
        # Look up per-entry can_write from the underlying SQL — the
        # ResolvedCollection shape doesn't carry write-rights forward,
        # so the AGENT branch needs a separate query to filter.
        if scope_enum == Scope.AGENT:
            return self._filter_writable(agent, [c.name for c in collections])

        # SHARED / SHARED_AGENT — every read-eligible entry passes.
        # ResolvedCollection already excluded the can_read=False rows in
        # _compose_intersection, so anything returned is read-eligible.
        names = []
        for c in collections:
            if self._cap is not None and self._exceeds_cap(c.max_sensitivity, self._cap):
                continue
            names.append(c.name)
        return names

    def _filter_writable(self, agent: str, candidate_names: list[str]) -> list[str]:
        """Return the subset of ``candidate_names`` where the actor can_write.

        Looks up the raw scope_entries rows for ``agent`` and intersects
        with the candidate list — preserves order of ``candidate_names``.
        """
        if not candidate_names:
            return []
        rows = self._db.execute(
            "SELECT se.collection_name "
            "FROM topology_scope_profiles sp "
            "JOIN topology_scope_entries se ON se.scope_profile_id = sp.id "
            "WHERE sp.actor_id = ? AND se.can_write = 1",  # F63-bounded: scope_entries is per-actor (≤O(collections))
            (agent,),
        ).fetchall()
        writable = {row[0] for row in rows}
        return [name for name in candidate_names if name in writable]

    def _public_collections(self) -> list[str]:
        """Collections whose parent cc_pair has ``access_type='PUBLIC'``.

        The wildcard / cross-agent path used by ``ALL_AGENTS`` and
        ``EVERYTHING`` scope. Joins ``topology_collections`` →
        ``topology_collection_sources`` → ``topology_cc_pairs`` and
        deduplicates collection names (a collection with multiple
        public sources counts once).
        """
        rows = self._db.execute(
            "SELECT DISTINCT tc.name "
            "FROM topology_collections tc "
            "JOIN topology_collection_sources tcs "
            "  ON tcs.collection_id = tc.id "
            "JOIN topology_cc_pairs ccp ON ccp.id = tcs.cc_pair_id "
            "WHERE ccp.access_type = 'PUBLIC' "
            "ORDER BY tc.name"  # F63-bounded: topology_collections is operator-config sized (≤O(100))
        ).fetchall()
        return [row[0] for row in rows]

    def _exceeds_cap(self, tier: F39Tier, cap: F39Tier) -> bool:
        """Return True when ``tier`` is more permissive than ``cap``.

        Tier ordering: public < internal < confidential < restricted.
        An entry's tier ``exceeds`` the cap when its index in
        :data:`_F39_TIER_ORDER` is greater than the cap's index.
        """
        try:
            tier_idx = _F39_TIER_ORDER.index(tier)
            cap_idx = _F39_TIER_ORDER.index(cap)
        except ValueError:
            # Unknown tier — fail closed (treat as exceeding cap so the
            # entry drops out rather than silently passing).
            return True
        return tier_idx > cap_idx
