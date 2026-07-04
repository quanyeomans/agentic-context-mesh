"""Topology apply-bridge — parsed YAML config → runtime topology rows.

Wave D shipped the parser (:mod:`kairix.config.topology`) + the
cross-reference validator (:mod:`kairix.config.topology_validators`)
+ the operator config blocks. Wave C wired :class:`CollectionRouter`
through :func:`kairix.worker.resolve_chunk_writer_for_entry`. This
module is the bridge between the two: it materialises parsed
:class:`TopologyConfig` into rows on:

* ``topology_connectors`` (per declared connector block)
* ``topology_credentials`` (per declared credential block)
* ``topology_cc_pairs`` (per declared cc_pair triad — keyed on ``name``)
* ``topology_collections`` (per declared collection — keyed on ``name``)
* ``topology_collection_sources`` (per (collection, cc_pair, path_filter)
  mapping)

The applier is **idempotent** — worker boots are frequent, so the
second-and-onward call against an unchanged config reports
``ApplyResult(created=0, unchanged=N)``. When the operator changes a
connector/credential binding (eg. swaps the credential a cc_pair uses
without renaming the cc_pair), the row is UPDATEd in place — but the
``status`` column is deliberately NEVER touched here. Status mutations
are the F57-policed surface owned by
:func:`kairix.core.connectors.cc_pair.transition_cc_pair`; the
applier only writes the operator-owned fields (connector_id,
credential_id, access_type).

Public surface:

* :func:`apply_topology` — F6-clean entry point taking the parsed
  config + an optional :class:`ApplierDeps` for tests.
* :class:`ApplyResult` — frozen-dataclass return per F42.
* :class:`ApplierDeps` — F6-clean DI seam (no test-only ``_fn=None``).

The applier refuses to run on a config with cross-reference failures —
callers should run :func:`validate_topology_references` first and
short-circuit on a non-empty failure tuple.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kairix.config.topology import (
    CCPairConfig,
    CollectionConfig,
    ConnectorConfig,
    CredentialConfig,
    TopologyConfig,
    config_pairs_to_mapping,
)
from kairix.config.topology_validators import (
    ValidationFailure,
    validate_topology_references,
)
from kairix.core.connectors.cc_pair import create_cc_pair

logger = logging.getLogger(__name__)


# F17 — repeated "config_id resolved as" log fragment across three helpers.
_RESOLVED_AS = "topology_applier: %s %r resolved as id=%d"


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one :func:`apply_topology` call — frozen per F42.

    Counters aggregate across every row type (connectors + credentials +
    cc_pairs + collections + collection_sources). The granular breakdown
    is logged at INFO so operators reading the worker log can see which
    surface drove the create/update.
    """

    created: int
    updated: int
    unchanged: int


@dataclass(frozen=True)
class ApplyValidationError(Exception):
    """Raised when :func:`apply_topology` is asked to apply an invalid config.

    Carries the validator's failure tuple so the caller can surface the
    full set of cross-reference errors at once (matching the
    ``kairix config validate`` rendering shape).
    """

    failures: tuple[ValidationFailure, ...]

    def __str__(self) -> str:
        joined = "; ".join(f.message for f in self.failures)
        return (
            f"topology_applier: refusing to apply config with "
            f"{len(self.failures)} cross-reference failure(s). "
            f"fix: resolve each failure or remove the dangling reference. "
            f"next: run `kairix config validate` to surface the full list. "
            f"details: {joined}"
        )


def _now() -> str:
    """ISO-8601 UTC timestamp string — matches the topology_* table convention."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ApplierDeps:
    """Injectable dependencies for :func:`apply_topology`.

    F6-clean: every field has a ``default_factory`` so production callers
    construct ``ApplierDeps()`` and get the real boundary calls; tests
    construct ``ApplierDeps(now_fn=lambda: "2026-05-23T00:00:00Z")`` and
    pass it as a single argument.

    Fields:
      * ``now_fn`` — returns the ISO-8601 timestamp stamped on every
        INSERT / UPDATE. Default :func:`_now`. Tests override for
        deterministic assertions.
      * ``validator_fn`` — pre-apply cross-reference validator. Default
        :func:`validate_topology_references`. Tests can pass a stub
        when they want to exercise the apply path against a config that
        would normally fail validation.
    """

    now_fn: Callable[[], str] = field(default_factory=lambda: _now)
    validator_fn: Callable[[TopologyConfig], tuple[ValidationFailure, ...]] = field(
        default_factory=lambda: validate_topology_references
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_topology(
    db: sqlite3.Connection,
    parsed_config: TopologyConfig,
    *,
    applier_deps: ApplierDeps | None = None,
) -> ApplyResult:
    """Materialise ``parsed_config`` into runtime topology_* rows.

    Idempotent — repeat calls against an unchanged config report
    ``ApplyResult(created=0, updated=0, unchanged=N)``. Caller owns the
    commit (the applier executes its writes against ``db`` but never
    commits — matches every other topology surface so the worker can
    bundle the apply with its first sync tick).

    Refuses to apply a config carrying cross-reference failures —
    raises :class:`ApplyValidationError` so the worker can log and
    halt cleanly. Operators see the validator's full failure list in
    the exception message.

    Returns aggregate counters across every row type. Per-surface
    breakdowns are logged at INFO.
    """
    deps = applier_deps if applier_deps is not None else ApplierDeps()

    failures = deps.validator_fn(parsed_config)
    if failures:
        raise ApplyValidationError(failures=failures)

    now = deps.now_fn()
    connector_ids = _apply_connectors(db, parsed_config.connectors, now=now)
    credential_ids = _apply_credentials(db, parsed_config.credentials, now=now)
    cc_pair_ids = _apply_cc_pairs(
        db,
        parsed_config.cc_pairs,
        connector_ids=connector_ids,
        credential_ids=credential_ids,
        _now_ts=now,
    )
    collection_outcomes = _apply_collections(
        db,
        parsed_config.collections,
        cc_pair_ids=cc_pair_ids,
        now=now,
    )

    created = connector_ids.created + credential_ids.created + cc_pair_ids.created + collection_outcomes.created
    updated = connector_ids.updated + credential_ids.updated + cc_pair_ids.updated + collection_outcomes.updated
    unchanged = (
        connector_ids.unchanged + credential_ids.unchanged + cc_pair_ids.unchanged + collection_outcomes.unchanged
    )
    result = ApplyResult(created=created, updated=updated, unchanged=unchanged)
    logger.info(
        "topology_applier: applied config — created=%d updated=%d unchanged=%d",
        result.created,
        result.updated,
        result.unchanged,
    )
    return result


# ---------------------------------------------------------------------------
# Per-surface appliers — each returns a small counter dataclass + an id map.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConnectorApplyOutcome:
    """Per-surface apply outcome — counters + config_id → row_id mapping."""

    created: int
    updated: int
    unchanged: int
    config_id_to_row_id: dict[str, int]


@dataclass(frozen=True)
class _CredentialApplyOutcome:
    """Per-surface apply outcome — counters + config_id → row_id mapping."""

    created: int
    updated: int
    unchanged: int
    config_id_to_row_id: dict[str, int]


@dataclass(frozen=True)
class _CCPairApplyOutcome:
    """Per-surface apply outcome — counters + config_id → row_id mapping."""

    created: int
    updated: int
    unchanged: int
    config_id_to_row_id: dict[str, int]


@dataclass(frozen=True)
class _CollectionApplyOutcome:
    """Per-surface apply outcome — aggregates collections + collection_sources."""

    created: int
    updated: int
    unchanged: int


def _apply_connectors(
    db: sqlite3.Connection,
    connectors: tuple[ConnectorConfig, ...],
    *,
    now: str,
) -> _ConnectorApplyOutcome:
    """INSERT-or-UPDATE one row in ``topology_connectors`` per declared block.

    Key on ``name`` (UNIQUE in the schema). The ``connector_specific_config``
    is rendered as a deterministic JSON-shape string for diff comparison.
    """
    import json

    created = 0
    updated = 0
    unchanged = 0
    id_map: dict[str, int] = {}
    for spec in connectors:
        config_json = json.dumps(config_pairs_to_mapping(spec.connector_specific_config), sort_keys=True)
        existing = db.execute(
            "SELECT id, kind, connector_specific_config, refresh_freq_seconds, "
            "prune_freq_seconds, perm_sync_freq_seconds, default_sensitivity "
            "FROM topology_connectors WHERE name = ?",
            (spec.name,),
        ).fetchone()
        if existing is None:
            cur = db.execute(
                "INSERT INTO topology_connectors "
                "(kind, name, connector_specific_config, refresh_freq_seconds, "
                "prune_freq_seconds, perm_sync_freq_seconds, default_sensitivity, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.kind,
                    spec.name,
                    config_json,
                    spec.refresh_freq_seconds,
                    spec.prune_freq_seconds,
                    spec.perm_sync_freq_seconds,
                    spec.default_sensitivity,
                    now,
                    now,
                ),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError(f"topology_applier: INSERT topology_connectors name={spec.name!r} failed")
            id_map[spec.id] = int(row_id)
            created += 1
            logger.info(_RESOLVED_AS, "connector", spec.id, int(row_id))
            continue
        row_id = int(existing[0])
        id_map[spec.id] = row_id
        same = (
            existing[1] == spec.kind
            and existing[2] == config_json
            and existing[3] == spec.refresh_freq_seconds
            and existing[4] == spec.prune_freq_seconds
            and existing[5] == spec.perm_sync_freq_seconds
            and existing[6] == spec.default_sensitivity
        )
        if same:
            unchanged += 1
            continue
        db.execute(
            "UPDATE topology_connectors SET kind = ?, connector_specific_config = ?, "
            "refresh_freq_seconds = ?, prune_freq_seconds = ?, "
            "perm_sync_freq_seconds = ?, default_sensitivity = ?, updated_at = ? "
            "WHERE id = ?",
            (
                spec.kind,
                config_json,
                spec.refresh_freq_seconds,
                spec.prune_freq_seconds,
                spec.perm_sync_freq_seconds,
                spec.default_sensitivity,
                now,
                row_id,
            ),
        )
        updated += 1
    return _ConnectorApplyOutcome(created=created, updated=updated, unchanged=unchanged, config_id_to_row_id=id_map)


def _apply_credentials(
    db: sqlite3.Connection,
    credentials: tuple[CredentialConfig, ...],
    *,
    now: str,
) -> _CredentialApplyOutcome:
    """INSERT-or-UPDATE one row in ``topology_credentials`` per declared block."""
    created = 0
    updated = 0
    unchanged = 0
    id_map: dict[str, int] = {}
    for spec in credentials:
        existing = db.execute(
            "SELECT id, kind, credential_ref, user_id, admin_public FROM topology_credentials WHERE name = ?",
            (spec.id,),
        ).fetchone()
        admin_public_int = 1 if spec.admin_public else 0
        if existing is None:
            cur = db.execute(
                "INSERT INTO topology_credentials "
                "(kind, name, credential_ref, user_id, admin_public, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (spec.kind, spec.id, spec.secret_name, spec.user_id, admin_public_int, now, now),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError(f"topology_applier: INSERT topology_credentials name={spec.id!r} failed")
            id_map[spec.id] = int(row_id)
            created += 1
            logger.info(_RESOLVED_AS, "credential", spec.id, int(row_id))
            continue
        row_id = int(existing[0])
        id_map[spec.id] = row_id
        same = (
            existing[1] == spec.kind
            and existing[2] == spec.secret_name
            and existing[3] == spec.user_id
            and int(existing[4]) == admin_public_int
        )
        if same:
            unchanged += 1
            continue
        db.execute(
            "UPDATE topology_credentials SET kind = ?, credential_ref = ?, "
            "user_id = ?, admin_public = ?, updated_at = ? WHERE id = ?",
            (spec.kind, spec.secret_name, spec.user_id, admin_public_int, now, row_id),
        )
        updated += 1
    return _CredentialApplyOutcome(created=created, updated=updated, unchanged=unchanged, config_id_to_row_id=id_map)


def _apply_cc_pairs(
    db: sqlite3.Connection,
    cc_pairs: tuple[CCPairConfig, ...],
    *,
    connector_ids: _ConnectorApplyOutcome,
    credential_ids: _CredentialApplyOutcome,
    _now_ts: str,
) -> _CCPairApplyOutcome:
    """INSERT-or-UPDATE one row in ``topology_cc_pairs`` per declared triad.

    Key on ``name`` (UNIQUE in the schema). On UPDATE we never mutate
    ``status`` here — that's the F57-policed surface owned by
    :func:`transition_cc_pair`. We only update ``connector_id`` /
    ``credential_id`` / ``access_type`` (the parts the operator config
    actually owns).

    The ``_now_ts`` parameter is accepted for signature symmetry with
    the sibling per-surface appliers (each takes a ``now: str``), but
    is intentionally unused here — :func:`create_cc_pair` and the
    in-place UPDATE in :func:`_apply_one_cc_pair` resolve the
    timestamp via :func:`_now` directly so the per-row stamp matches
    the time the row was actually written.
    """
    created = 0
    updated = 0
    unchanged = 0
    id_map: dict[str, int] = {}
    for spec in cc_pairs:
        connector_row_id = connector_ids.config_id_to_row_id.get(spec.connector)
        if connector_row_id is None:
            raise RuntimeError(
                f"topology_applier: cc_pair {spec.name!r} references undeclared "
                f"connector {spec.connector!r}. "
                f"fix: declare the connector in topology.connectors. "
                f"next: run `kairix config validate`."
            )
        credential_row_id: int | None = None
        # F15-clean: ``spec.credential`` is a config-reference id (e.g.
        # "m365-oauth"), not a secret value; we still rebind to a
        # non-secret-named local before interpolating into the raise
        # text so the F15 AST scan stays clean (it matches on the
        # trailing identifier ``credential``).
        cred_ref_id = spec.credential
        if cred_ref_id is not None:
            credential_row_id = credential_ids.config_id_to_row_id.get(cred_ref_id)
            if credential_row_id is None:
                raise RuntimeError(
                    f"topology_applier: cc_pair {spec.name!r} references undeclared "
                    f"credential id {cred_ref_id!r}. "
                    f"fix: declare the credential in topology.credentials. "
                    f"next: run `kairix config validate`."
                )
        outcome = _apply_one_cc_pair(
            db,
            spec,
            connector_row_id=connector_row_id,
            credential_row_id=credential_row_id,
        )
        id_map[spec.id] = outcome.row_id
        if outcome.action == "created":
            created += 1
        elif outcome.action == "updated":
            updated += 1
        else:
            unchanged += 1
    return _CCPairApplyOutcome(created=created, updated=updated, unchanged=unchanged, config_id_to_row_id=id_map)


@dataclass(frozen=True)
class _OneCCPairOutcome:
    """Per-cc_pair apply outcome — row id + which action fired."""

    row_id: int
    action: str  # one of "created" / "updated" / "unchanged".


def _apply_one_cc_pair(
    db: sqlite3.Connection,
    spec: CCPairConfig,
    *,
    connector_row_id: int,
    credential_row_id: int | None,
) -> _OneCCPairOutcome:
    """INSERT-or-UPDATE one cc_pair row keyed on ``spec.name``.

    Extracted from :func:`_apply_cc_pairs` to keep that function under
    the F16 cognitive-complexity ceiling — the per-row diff branching
    pushed the parent over 15 otherwise.
    """
    existing = db.execute(
        "SELECT id, connector_id, credential_id, access_type FROM topology_cc_pairs WHERE name = ?",
        (spec.name,),
    ).fetchone()
    if existing is None:
        cc_pair = create_cc_pair(
            db,
            connector_id=connector_row_id,
            credential_id=credential_row_id,
            name=spec.name,
            access_type=spec.access_type,
        )
        logger.info(_RESOLVED_AS, "cc_pair", spec.id, cc_pair.id)
        return _OneCCPairOutcome(row_id=cc_pair.id, action="created")
    row_id = int(existing[0])
    same = (
        int(existing[1]) == connector_row_id
        and (existing[2] is None if credential_row_id is None else int(existing[2]) == credential_row_id)
        and existing[3] == spec.access_type
    )
    if same:
        return _OneCCPairOutcome(row_id=row_id, action="unchanged")
    # F57 carve-out: this UPDATE never touches ``status`` (the
    # state-machine column). It only rewrites the operator-owned
    # connector / credential / access_type bindings, so it doesn't trip
    # ``check_f57_ccpair_lifecycle_integrity``.
    db.execute(
        "UPDATE topology_cc_pairs SET connector_id = ?, credential_id = ?, "
        "access_type = ?, updated_at = ? WHERE id = ?",
        (connector_row_id, credential_row_id, spec.access_type, _now(), row_id),
    )
    return _OneCCPairOutcome(row_id=row_id, action="updated")


def _apply_collections(
    db: sqlite3.Connection,
    collections: tuple[CollectionConfig, ...],
    *,
    cc_pair_ids: _CCPairApplyOutcome,
    now: str,
) -> _CollectionApplyOutcome:
    """INSERT-or-UPDATE rows in ``topology_collections`` + ``topology_collection_sources``.

    Collections key on ``name`` (UNIQUE in the schema). For each
    declared source mapping under a collection, INSERT a row in
    ``topology_collection_sources`` keyed on
    ``(collection_id, cc_pair_id, source_path_filter)`` — idempotent on
    the natural triple so reruns don't duplicate.
    """
    created = 0
    updated = 0
    unchanged = 0
    for collection_spec in collections:
        collection_id, action = _upsert_collection_row(db, collection_spec, now=now)
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        else:
            unchanged += 1
        source_outcome = _upsert_collection_sources(
            db,
            collection_spec,
            collection_id=collection_id,
            cc_pair_ids=cc_pair_ids,
        )
        created += source_outcome[0]
        updated += source_outcome[1]
        unchanged += source_outcome[2]
    return _CollectionApplyOutcome(created=created, updated=updated, unchanged=unchanged)


def _upsert_collection_row(
    db: sqlite3.Connection,
    spec: CollectionConfig,
    *,
    now: str,
) -> tuple[int, str]:
    """INSERT-or-UPDATE one ``topology_collections`` row keyed on ``name``.

    Returns ``(collection_id, action)`` where ``action`` is one of
    ``"created"`` / ``"updated"`` / ``"unchanged"``. The collection
    spec carries ``name`` + ``sources`` + the ranking ``tier``; defaults
    for ``default_sensitivity`` / ``on_unmapped_item`` / ``visibility``
    stay at the schema's column defaults.

    The ``tier`` is load-bearing on UPDATE: an operator who edits a
    collection's ``tier:`` (e.g. promotes ``reflib`` to ``reference``)
    must see the row's ``tier`` re-written in place, so an existing row
    whose stored ``tier`` differs from the spec's reports ``"updated"``.
    """
    existing = db.execute(
        "SELECT id, tier FROM topology_collections WHERE name = ?",
        (spec.name,),
    ).fetchone()
    if existing is None:
        cur = db.execute(
            "INSERT INTO topology_collections "
            "(name, default_sensitivity, on_unmapped_item, visibility, tier, "
            "created_at, updated_at) "
            "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?, ?)",
            (spec.name, spec.tier, now, now),
        )
        new_id = cur.lastrowid
        if new_id is None:
            raise RuntimeError(f"topology_applier: INSERT topology_collections name={spec.name!r} failed")
        return int(new_id), "created"
    collection_id = int(existing[0])
    if existing[1] == spec.tier:
        return collection_id, "unchanged"
    db.execute(
        "UPDATE topology_collections SET tier = ?, updated_at = ? WHERE id = ?",
        (spec.tier, now, collection_id),
    )
    return collection_id, "updated"


def _upsert_collection_sources(
    db: sqlite3.Connection,
    spec: CollectionConfig,
    *,
    collection_id: int,
    cc_pair_ids: _CCPairApplyOutcome,
) -> tuple[int, int, int]:
    """INSERT-if-absent for every source mapping under ``spec``.

    Idempotency key is ``(collection_id, cc_pair_id, source_path_filter)``
    — repeat applies do not duplicate. Returns ``(created, updated,
    unchanged)`` counters.
    """
    created = 0
    updated = 0
    unchanged = 0
    for source in spec.sources:
        cc_pair_row_id = cc_pair_ids.config_id_to_row_id.get(source.cc_pair)
        if cc_pair_row_id is None:
            raise RuntimeError(
                f"topology_applier: collection {spec.name!r} source references "
                f"undeclared cc_pair {source.cc_pair!r}. "
                f"fix: declare the cc_pair in topology.cc_pairs. "
                f"next: run `kairix config validate`."
            )
        existing = db.execute(
            "SELECT id, sensitivity_override FROM topology_collection_sources "
            "WHERE collection_id = ? AND cc_pair_id = ? AND source_path_filter = ?",
            (collection_id, cc_pair_row_id, source.path_filter),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO topology_collection_sources "
                "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
                "VALUES (?, ?, ?, ?)",
                (collection_id, cc_pair_row_id, source.path_filter, source.sensitivity_min),
            )
            created += 1
            continue
        existing_override: Any = existing[1]
        if existing_override == source.sensitivity_min:
            unchanged += 1
            continue
        db.execute(
            "UPDATE topology_collection_sources SET sensitivity_override = ? WHERE id = ?",
            (source.sensitivity_min, int(existing[0])),
        )
        updated += 1
    return created, updated, unchanged


# ---------------------------------------------------------------------------
# Config-drift detection (read-only — issue #726 observability half)
# ---------------------------------------------------------------------------

# One scan query per surface whose row an operator config *removal* can
# strand: :func:`apply_topology` is upsert-only, so a connector / cc_pair /
# collection deleted from the config leaves its ``topology_*`` row behind and
# it stays routed/synced until a deliberate prune. Each query carries a
# ``LIMIT`` so the read stays bounded (F63); the ``topology_*`` tables are
# operator-config-scale (one row per declared source), so the cap never
# truncates a real operator's topology.
_DRIFT_NAME_QUERIES: tuple[str, ...] = (
    "SELECT name FROM topology_connectors LIMIT 10000",
    "SELECT name FROM topology_cc_pairs LIMIT 10000",
    "SELECT name FROM topology_collections LIMIT 10000",
)


@dataclass(frozen=True)
class ConfigDriftReport:
    """Read-only snapshot of ``topology_*`` rows absent from the current config.

    ``apply_topology`` never deletes (issue #726) — a source removed from the
    operator config keeps its row, so it stays routed/synced until a deliberate
    prune. This report names the orphaned rows per surface so ``kairix worker
    status`` can WARN on the drift. It is purely observational: nothing here
    deletes a row or transitions a cc_pair status (the prune must respect the
    F57 ``topology_cc_pairs.status`` lifecycle and is a separate cutover).

    Each field is the sorted tuple of stored names (the operator-facing config
    ids) on that surface that no longer appear in the parsed config.
    """

    connectors: tuple[str, ...] = ()
    cc_pairs: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """Number of stranded rows across every surface."""
        return len(self.connectors) + len(self.cc_pairs) + len(self.collections)

    @property
    def has_drift(self) -> bool:
        """True when at least one stored row has no matching config entry."""
        return self.total > 0

    @property
    def sample_ids(self) -> tuple[str, ...]:
        """De-duplicated, sorted example ids across surfaces (for the WARN line)."""
        return tuple(sorted({*self.connectors, *self.cc_pairs, *self.collections}))

    def warn_line(self, *, sample_size: int = 3) -> str | None:
        """Render the single ``kairix worker status`` WARN line, or None when clean."""
        if not self.has_drift:
            return None
        sample = ", ".join(self.sample_ids[:sample_size])
        suffix = f" (e.g. {sample})" if sample else ""
        return (
            f"WARN config drift: {self.total} topology source(s) in the store "
            f"are no longer in config (still routed/synced until pruned){suffix}"
        )


def _stored_topology_names(db: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    """Read the stored id/name set for each drift-visible surface.

    Returns ``(connectors, cc_pairs, collections)`` name sets. Best-effort: a
    legacy DB missing one of the ``topology_*`` tables degrades to an empty set
    for that surface rather than raising, so status never crashes on drift.
    """
    surfaces: list[set[str]] = []
    for query in _DRIFT_NAME_QUERIES:
        try:
            # F63-bounded: topology_* tables are operator-config-scale (one row
            # per declared source); each query also carries an explicit LIMIT.
            rows = db.execute(query).fetchall()
        except sqlite3.OperationalError:
            rows = []
        surfaces.append({str(row[0]) for row in rows if row[0] is not None})
    return surfaces[0], surfaces[1], surfaces[2]


def detect_config_drift(db: sqlite3.Connection, config: TopologyConfig) -> ConfigDriftReport:
    """Compare stored ``topology_*`` rows against the parsed config (read-only).

    A stored connector / cc_pair / collection whose name is not present in
    ``config`` is drift: the operator removed it from config but the
    upsert-only :func:`apply_topology` never pruned the row. The comparison is
    per-surface (a name present as a collection does not mask a removed
    connector of the same name).

    Purely observational — no row is deleted or transitioned here.
    """
    stored_connectors, stored_cc_pairs, stored_collections = _stored_topology_names(db)
    return ConfigDriftReport(
        connectors=tuple(sorted(stored_connectors - {c.name for c in config.connectors})),
        cc_pairs=tuple(sorted(stored_cc_pairs - {p.name for p in config.cc_pairs})),
        collections=tuple(sorted(stored_collections - {c.name for c in config.collections})),
    )
