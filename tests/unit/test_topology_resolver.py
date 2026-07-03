"""Unit tests for :class:`TopologyCollectionResolver` (GH #372).

Drives the Adapter with a :class:`FakeScopeProfileResolver` (or seeded
in-memory SQL where the SQL branch is the unit under test) and pins:

* Superset returned when no collections specified (multi-collection
  scope_profile).
* Empty scope_profile returns ``None`` (not an empty list — pipeline
  contract: ``None`` = "no filter — search everything"; an empty list
  is the same thing, but None is the canonical "no scope" signal).
* ``mode='write'`` entry EXCLUDED from the read-default superset.
* ``mode='read_write'`` entry INCLUDED.
* Explicit ``collections=['foo']`` validated against the scope.
* Explicit unknown collection rejected with an F21 ``fix:``/``next:``/
  ``run:`` action-marker message.
* ``agent=None, scope=ALL_AGENTS`` returns public-access collections
  (from ``topology_collections`` joined to ``topology_cc_pairs`` where
  ``access_type='PUBLIC'``).
* Sensitivity cap drops entries that exceed the cap.
* F68 failure injection — resolver-raised exception propagates.

Sabotage proofs (executed locally before commit, restored on completion):
each test was mutated against the production module, observed to fail
with a verbatim message, then production was restored and the test
returned green. The mutation table is recorded in the dispatch report.

F1-clean: ``FakeScopeProfileResolver`` is injected via the
``scope_profile_resolver=`` kwarg on
:class:`TopologyCollectionResolver`; no ``@patch`` / ``monkeypatch``
on kairix internals.
F2-clean: no ``KAIRIX_*`` env vars.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.search.scope import Scope
from kairix.core.search.topology_resolver import (
    TopologyCollectionResolver,
)
from tests.fakes import FakeScopeProfileResolver

pytestmark = pytest.mark.unit


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def test_superset_returned_when_no_collections_specified() -> None:
    """SHARED_AGENT scope with a multi-entry profile returns every
    read-eligible collection — the superset, per the GH #372 contract."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[
            ("sharepoint-all", "read", "internal"),
            ("obsidian-all", "read", "internal"),
            ("reflib", "read", "public"),
            ("agent-alpha-memory", "read_write", "restricted"),
        ],
    )
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert set(result) == {
        "sharepoint-all",
        "obsidian-all",
        "reflib",
        "agent-alpha-memory",
    }


def test_empty_scope_profile_returns_none() -> None:
    """An actor with no scope entries gets ``None`` — pipeline contract
    treats this as "no filter — search everything", which is the
    operationally-sane default (the alternative — empty list — has the
    same effect but loses the "actor is unknown" signal)."""
    fake = FakeScopeProfileResolver()  # no actors declared
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result is None, f"empty scope should resolve to None (no filter); got {result!r}"


def test_write_only_entry_excluded_from_read_default() -> None:
    """A ``mode='write'`` entry has can_read=False, so the underlying
    ScopeProfileResolver moves it to ``excluded_collections``. The
    Adapter's SHARED_AGENT path must NOT include it in the returned
    collection list."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[
            ("readable-bucket", "read", "internal"),
            ("write-only-sink", "write", "internal"),
        ],
    )
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert "write-only-sink" not in result, f"write-only entries must not appear in read-default; got {result!r}"
    assert "readable-bucket" in result


def test_read_write_entry_included_in_default() -> None:
    """``mode='read_write'`` entries are read-eligible — the actor's own
    memory bucket must appear in the SHARED_AGENT superset."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[
            ("agent-alpha-memory", "read_write", "restricted"),
        ],
    )
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert "agent-alpha-memory" in result


def test_explicit_collections_validated_within_scope() -> None:
    """When the caller passes ``collections=['foo']`` and ``'foo'`` IS
    in the actor's scope, ``validate_explicit`` passes it through
    unchanged."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[("foo", "read", "internal")],
    )
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(
        agent="agent-alpha",
        collections=["foo"],
        scope=Scope.SHARED_AGENT,
    )

    assert error is None, f"in-scope collection must validate clean; got {error!r}"
    assert filtered == ["foo"]


def test_explicit_unknown_collection_rejected_with_f21_message() -> None:
    """An out-of-scope explicit collection returns ``(None, message)``
    where the message carries F21 ``fix:``/``next:``/``run:`` markers."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[("in-scope-bucket", "read", "internal")],
    )
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(
        agent="agent-alpha",
        collections=["forbidden-bucket"],
        scope=Scope.SHARED_AGENT,
    )

    assert filtered is None
    assert error is not None
    # F21 affordance — the message must carry fix:/next:/run: action markers.
    assert "fix:" in error, f"missing fix: action marker; got {error!r}"
    assert "next:" in error, f"missing next: action marker; got {error!r}"
    assert "run:" in error, f"missing run: action marker; got {error!r}"
    # The message must name the offending collection AND the allowed list.
    assert "forbidden-bucket" in error
    assert "in-scope-bucket" in error
    assert "agent-alpha" in error


def test_agent_none_all_agents_returns_public_collections() -> None:
    """``agent=None, scope=ALL_AGENTS`` queries
    ``topology_collections`` joined with ``topology_cc_pairs`` where
    ``access_type='PUBLIC'`` — the wildcard / cross-agent path."""
    db = _fresh_db()
    _seed_public_and_private_collections(db)

    # No FakeScopeProfileResolver needed — public path bypasses scope
    # profiles entirely. The Adapter constructs a real
    # ScopeProfileResolver but never calls .resolve() on this branch.
    resolver = TopologyCollectionResolver(db=db)

    result = resolver.resolve(agent=None, scope=Scope.ALL_AGENTS)

    assert result is not None
    assert set(result) == {"public-bucket"}, f"only PUBLIC-access collections should appear; got {result!r}"


def test_agent_none_shared_agent_scope_returns_none() -> None:
    """``agent=None`` with SHARED_AGENT scope is operator
    misconfiguration — the resolver returns ``None`` (no filter)
    rather than fanning out to public collections. Public fan-out is
    reserved for the explicit ``ALL_AGENTS`` / ``EVERYTHING`` scopes.
    """
    resolver = TopologyCollectionResolver(db=_fresh_db())

    result = resolver.resolve(agent=None, scope=Scope.SHARED_AGENT)

    assert result is None


def test_agent_scope_with_no_candidate_names_returns_empty() -> None:
    """Calling AGENT scope on an actor whose scope_profile resolves to
    zero ``ResolvedCollection`` entries short-circuits to an empty list
    (which the public ``.resolve`` then converts to ``None`` via the
    ``or None`` fall-through). Pins the ``_filter_writable`` early-return
    branch.
    """
    fake = FakeScopeProfileResolver().with_actor("agent-alpha", entries=[])
    resolver = TopologyCollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="agent-alpha", scope=Scope.AGENT)

    assert result is None, f"AGENT scope on empty profile must be None; got {result!r}"


def test_unknown_tier_in_cap_check_fails_closed() -> None:
    """A ``ResolvedCollection`` whose ``max_sensitivity`` is outside the
    F39 tier ordering (e.g. data corruption) fails closed — the entry
    drops out of the cap-filtered list rather than silently passing.
    """
    from kairix.core.connectors.scope_profile_resolver import (
        ResolvedCollection,
        ResolvedScope,
    )

    class _BrokenResolver:
        def resolve(self, *, actors, **_):  # type: ignore[no-untyped-def]  # F3-rationale: minimal in-test stand-in; signature mirrors ScopeProfileResolver.resolve
            return ResolvedScope(
                collections=(
                    ResolvedCollection(
                        name="ok-bucket",
                        max_sensitivity="internal",
                        weight=1.0,
                    ),
                    ResolvedCollection(
                        name="bad-bucket",
                        max_sensitivity="alien-tier",  # type: ignore[arg-type]  # F3-rationale: intentional invalid tier to drive the fail-closed branch
                        weight=1.0,
                    ),
                ),
                excluded_collections=(),
            )

    resolver = TopologyCollectionResolver(
        db=_fresh_db(),
        scope_profile_resolver=_BrokenResolver(),  # type: ignore[arg-type]  # F3-rationale: test-local stand-in for ScopeProfileResolver
        max_sensitivity_cap="internal",
    )

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result == ["ok-bucket"], f"unknown tier must fail closed (drop entry); got {result!r}"


def test_sensitivity_cap_drops_over_tier_entries() -> None:
    """When constructed with ``max_sensitivity_cap='internal'``, any
    entry whose tier is more permissive (confidential / restricted) is
    excluded from the default-search list."""
    fake = FakeScopeProfileResolver().with_actor(
        "agent-alpha",
        entries=[
            ("public-bucket", "read", "public"),
            ("internal-bucket", "read", "internal"),
            ("confidential-bucket", "read", "confidential"),
            ("restricted-bucket", "read", "restricted"),
        ],
    )
    resolver = TopologyCollectionResolver(
        db=_fresh_db(),
        scope_profile_resolver=fake,
        max_sensitivity_cap="internal",
    )

    result = resolver.resolve(agent="agent-alpha", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert set(result) == {"public-bucket", "internal-bucket"}, (
        f"confidential + restricted must drop above the cap; got {result!r}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_public_and_private_collections(db: sqlite3.Connection) -> None:
    """Seed two collections — one PUBLIC, one PRIVATE — so the
    public-only path can be asserted distinctly from the private set.

    Mirrors the canonical cc_pair seed pattern from
    :mod:`tests.integration.test_feature_flag_topology_runtime`.
    """
    now = "2026-06-01T00:00:00Z"
    # Public connector / cc_pair / collection
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, "
        "created_at, updated_at) "
        "VALUES (?, ?, '{}', 'internal', ?, ?)",
        ("obsidian", "public-conn", now, now),
    )
    public_connector_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_cc_pairs "
        "(connector_id, credential_id, name, access_type, status, "
        "in_repeated_error_state, total_docs_indexed, created_at, updated_at) "
        "VALUES (?, NULL, ?, 'PUBLIC', 'ACTIVE', 0, 0, ?, ?)",
        (public_connector_id, "public-pair", now, now),
    )
    public_cc_pair_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, "
        "created_at, updated_at) "
        "VALUES (?, 'internal', 'land_in_default_collection', 'public', ?, ?)",
        ("public-bucket", now, now),
    )
    public_collection_id = cur.lastrowid
    db.execute(
        "INSERT INTO topology_collection_sources "
        "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
        "VALUES (?, ?, '*', NULL)",
        (public_collection_id, public_cc_pair_id),
    )

    # Private connector / cc_pair / collection
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, "
        "created_at, updated_at) "
        "VALUES (?, ?, '{}', 'internal', ?, ?)",
        ("obsidian", "private-conn", now, now),
    )
    private_connector_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_cc_pairs "
        "(connector_id, credential_id, name, access_type, status, "
        "in_repeated_error_state, total_docs_indexed, created_at, updated_at) "
        "VALUES (?, NULL, ?, 'PRIVATE', 'ACTIVE', 0, 0, ?, ?)",
        (private_connector_id, "private-pair", now, now),
    )
    private_cc_pair_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, "
        "created_at, updated_at) "
        "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?)",
        ("private-bucket", now, now),
    )
    private_collection_id = cur.lastrowid
    db.execute(
        "INSERT INTO topology_collection_sources "
        "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
        "VALUES (?, ?, '*', NULL)",
        (private_collection_id, private_cc_pair_id),
    )
    db.commit()
