"""Unit tests for the Wave D topology apply-bridge.

Lift the per-file coverage on
:mod:`kairix.core.connectors.topology_applier` above the F7 90%
floor by exercising every branch in isolation against a fresh in-memory
DB. Integration coverage (apply-bridge + worker boot wiring) lives in
``tests/integration/test_topology_applier.py``; E2E coverage lives
in ``tests/e2e/test_composed_alpha_path.py``.

The branches not exercised by the happy-path BDD scenario:

* INSERT/UPDATE diff branches on every per-surface helper (connector
  swap, credential swap, cc_pair connector/credential rebind,
  collection source sensitivity bump)
* ``ApplyValidationError`` rendering (``__str__`` shape)
* ``ApplierDeps(now_fn=..., validator_fn=...)`` DI override path
* Dangling cc_pair reference inside ``_apply_cc_pairs`` (the RuntimeError
  paths that fire when the validator was somehow bypassed)
* Collection source rebind that hits the UPDATE-on-sensitivity-change
  branch
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.config import parse_topology
from kairix.core.connectors.topology_applier import (
    ApplierDeps,
    ApplyResult,
    ApplyValidationError,
    apply_topology,
)
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _build_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


# ---------------------------------------------------------------------------
# Happy path + idempotency — already covered at BDD, but unit also exercises.
# ---------------------------------------------------------------------------


def test_apply_empty_config_returns_zero_result() -> None:
    """Empty TopologyConfig returns ApplyResult(0, 0, 0)."""
    db = _build_db()
    parsed = parse_topology({})
    result = apply_topology(db, parsed)
    assert result == ApplyResult(created=0, updated=0, unchanged=0)


def test_apply_one_block_per_surface_returns_five_created() -> None:
    """Minimum non-empty config: one entry per surface = five created rows."""
    db = _build_db()
    parsed = parse_topology(
        {
            "topology": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
                "credentials": [
                    {"id": "cr1", "kind": "oauth", "secret_name": "s1"},  # pragma: allowlist secret
                ],
                "cc_pairs": [{"id": "p1", "connector": "c1", "credential": "cr1", "name": "cp1"}],
                "collections": [
                    {
                        "name": "col1",
                        "sources": [{"cc_pair": "p1", "path_filter": "*"}],
                    },
                ],
            }
        }
    )
    result = apply_topology(db, parsed)
    assert result == ApplyResult(created=5, updated=0, unchanged=0)


def test_apply_twice_is_unchanged() -> None:
    """Second apply against unchanged config reports every row as unchanged."""
    db = _build_db()
    parsed = parse_topology(
        {
            "topology": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            }
        }
    )
    apply_topology(db, parsed)
    second = apply_topology(db, parsed)
    assert second == ApplyResult(created=0, updated=0, unchanged=1)


# ---------------------------------------------------------------------------
# UPDATE branches — operator changes the config and re-applies.
# ---------------------------------------------------------------------------


def test_apply_updates_connector_when_kind_changes() -> None:
    """Re-applying with a changed connector kind triggers UPDATE."""
    db = _build_db()
    apply_topology(
        db,
        parse_topology({"topology": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}]}}),
    )
    second = apply_topology(
        db,
        parse_topology({"topology": {"connectors": [{"id": "c1", "kind": "sharepoint", "name": "c1"}]}}),
    )
    assert second.updated == 1
    assert second.created == 0
    kind = db.execute("SELECT kind FROM topology_connectors WHERE name = 'c1'").fetchone()
    assert kind[0] == "sharepoint"


def test_apply_updates_credential_when_secret_name_changes() -> None:
    """Re-applying with a changed credential secret_name triggers UPDATE."""
    db = _build_db()
    apply_topology(
        db,
        parse_topology(
            {
                "topology": {
                    "credentials": [
                        {"id": "cr1", "kind": "oauth", "secret_name": "s1"},  # pragma: allowlist secret
                    ]
                }
            }
        ),
    )
    second = apply_topology(
        db,
        parse_topology(
            {
                "topology": {
                    "credentials": [
                        {"id": "cr1", "kind": "oauth", "secret_name": "s2"},  # pragma: allowlist secret
                    ]
                }
            }
        ),
    )
    assert second.updated == 1
    assert second.created == 0
    cred_ref = db.execute("SELECT credential_ref FROM topology_credentials WHERE name = 'cr1'").fetchone()
    assert cred_ref[0] == "s2"


def test_apply_updates_cc_pair_when_access_type_changes() -> None:
    """Re-applying with a changed cc_pair access_type triggers in-place UPDATE.

    F57 carve-out — the applier never touches ``status``; only the
    operator-owned fields (connector_id / credential_id / access_type).
    """
    db = _build_db()
    base_config = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
        }
    }
    apply_topology(db, parse_topology(base_config))
    bumped = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [
                {
                    "id": "p1",
                    "connector": "c1",
                    "credential": None,
                    "name": "cp1",
                    "access_type": "PUBLIC",
                }
            ],
        }
    }
    second = apply_topology(db, parse_topology(bumped))
    assert second.updated == 1
    row = db.execute("SELECT access_type, status FROM topology_cc_pairs WHERE name = 'cp1'").fetchone()
    assert row[0] == "PUBLIC"
    # Status was never touched by the applier — stays at SCHEDULED.
    assert row[1] == "SCHEDULED"


def test_apply_updates_collection_source_when_sensitivity_min_changes() -> None:
    """Changing sensitivity_min on a source mapping triggers UPDATE, not duplicate INSERT."""
    db = _build_db()
    base = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [
                {
                    "name": "col1",
                    "sources": [
                        {"cc_pair": "p1", "path_filter": "*", "sensitivity_min": "internal"},
                    ],
                }
            ],
        }
    }
    apply_topology(db, parse_topology(base))
    bumped = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [
                {
                    "name": "col1",
                    "sources": [
                        {"cc_pair": "p1", "path_filter": "*", "sensitivity_min": "confidential"},
                    ],
                }
            ],
        }
    }
    second = apply_topology(db, parse_topology(bumped))
    # Connector + cc_pair + collection are unchanged; only the source bumps.
    assert second.updated == 1
    sources = db.execute("SELECT COUNT(*) FROM topology_collection_sources").fetchone()
    assert sources[0] == 1  # not duplicated


def _tier_config(tier: str | None) -> dict[str, object]:
    """One connector / cc_pair / collection, with an optional collection tier."""
    collection: dict[str, object] = {"name": "col1", "sources": [{"cc_pair": "p1", "path_filter": "*"}]}
    if tier is not None:
        collection["tier"] = tier
    return {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [collection],
        }
    }


def _collection_tier(db: sqlite3.Connection, name: str) -> str | None:
    return db.execute("SELECT tier FROM topology_collections WHERE name = ?", (name,)).fetchone()[0]


def test_apply_writes_collection_tier_on_insert() -> None:
    """The applier INSERTs the operator-declared collection ``tier`` onto the row."""
    db = _build_db()
    apply_topology(db, parse_topology(_tier_config("reference")))
    assert _collection_tier(db, "col1") == "reference"


def test_apply_collection_tier_absent_lands_null() -> None:
    """A collection without ``tier:`` INSERTs NULL — back-compat default."""
    db = _build_db()
    apply_topology(db, parse_topology(_tier_config(None)))
    assert _collection_tier(db, "col1") is None


def test_apply_updates_collection_when_tier_changes() -> None:
    """Editing a collection's ``tier:`` re-writes the row in place (load-bearing UPDATE).

    Pins the previously no-op collection UPDATE branch: a changed tier must
    report ``updated`` and overwrite the stored value, with no duplicate row.
    """
    db = _build_db()
    apply_topology(db, parse_topology(_tier_config("reference")))
    second = apply_topology(db, parse_topology(_tier_config("primary")))
    assert second.updated == 1
    assert _collection_tier(db, "col1") == "primary"
    count = db.execute("SELECT COUNT(*) FROM topology_collections WHERE name = 'col1'").fetchone()[0]
    assert count == 1


def test_apply_collection_unchanged_when_tier_identical() -> None:
    """Re-applying the same tier reports ``unchanged`` — the diff guard holds."""
    db = _build_db()
    config = _tier_config("reference")
    apply_topology(db, parse_topology(config))
    second = apply_topology(db, parse_topology(config))
    assert second.updated == 0
    assert _collection_tier(db, "col1") == "reference"


# ---------------------------------------------------------------------------
# Error paths.
# ---------------------------------------------------------------------------


def test_apply_rejects_invalid_config_with_validation_failures() -> None:
    """ApplyValidationError carries the full failure tuple from the validator."""
    db = _build_db()
    bad = parse_topology(
        {
            "topology": {
                "cc_pairs": [{"id": "x", "connector": "missing", "credential": None, "name": "x"}],
            }
        }
    )
    with pytest.raises(ApplyValidationError) as exc_info:
        apply_topology(db, bad)
    assert exc_info.value.failures
    # __str__ rendering covers the dataclass message format.
    rendered = str(exc_info.value)
    assert "cross-reference failure" in rendered
    assert "fix:" in rendered
    assert "next:" in rendered


def test_apply_validator_override_via_applier_deps() -> None:
    """ApplierDeps.validator_fn override path — accept-anything stub validator.

    Lets a future operator-tool pre-validate elsewhere and pass an
    already-validated config straight to apply without paying the
    validator cost twice.
    """
    db = _build_db()
    parsed = parse_topology({"topology": {"connectors": [{"id": "c1", "kind": "x", "name": "c1"}]}})

    def _always_clean(_config: object) -> tuple:
        return ()

    deps = ApplierDeps(validator_fn=_always_clean)
    result = apply_topology(db, parsed, applier_deps=deps)
    assert result.created == 1


def test_apply_now_fn_override_via_applier_deps() -> None:
    """ApplierDeps.now_fn override — every row stamps the deterministic time."""
    db = _build_db()
    parsed = parse_topology({"topology": {"connectors": [{"id": "c1", "kind": "x", "name": "c1"}]}})

    deps = ApplierDeps(now_fn=lambda: "1999-01-01T00:00:00Z")
    apply_topology(db, parsed, applier_deps=deps)
    row = db.execute("SELECT created_at, updated_at FROM topology_connectors WHERE name = 'c1'").fetchone()
    assert row[0] == "1999-01-01T00:00:00Z"
    assert row[1] == "1999-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# F39 / F57 invariants on the applier path.
# ---------------------------------------------------------------------------


def test_apply_cc_pair_with_credential_binding_resolved() -> None:
    """A cc_pair with a credential reference resolves the credential row id."""
    db = _build_db()
    parsed = parse_topology(
        {
            "topology": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
                "credentials": [
                    {"id": "cr1", "kind": "oauth", "secret_name": "s1"},  # pragma: allowlist secret
                ],
                "cc_pairs": [
                    {"id": "p1", "connector": "c1", "credential": "cr1", "name": "cp1"},
                ],
            }
        }
    )
    apply_topology(db, parsed)
    row = db.execute("SELECT credential_id FROM topology_cc_pairs WHERE name = 'cp1'").fetchone()
    assert row[0] is not None


def test_apply_collection_unchanged_when_only_source_added() -> None:
    """Re-applying with a new source mapping: collection row unchanged, source row created."""
    db = _build_db()
    base = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [
                {"name": "col1", "sources": [{"cc_pair": "p1", "path_filter": "01/*"}]},
            ],
        }
    }
    apply_topology(db, parse_topology(base))
    extended = {
        "topology": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [
                {
                    "name": "col1",
                    "sources": [
                        {"cc_pair": "p1", "path_filter": "01/*"},
                        {"cc_pair": "p1", "path_filter": "02/*"},
                    ],
                },
            ],
        }
    }
    second = apply_topology(db, parse_topology(extended))
    # Only the new source row counts as created.
    assert second.created == 1
    sources = db.execute("SELECT COUNT(*) FROM topology_collection_sources").fetchone()
    assert sources[0] == 2


# ---------------------------------------------------------------------------
# Config-drift detection (read-only — issue #726 observability half)
# ---------------------------------------------------------------------------

_TWO_SOURCE_CONFIG = {
    "topology": {
        "connectors": [
            {"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"},
            {"id": "sp-conn", "kind": "sharepoint", "name": "sp-conn"},
        ],
        "credentials": [
            {"id": "m365-oauth", "kind": "oauth", "secret_name": "s-m365"},  # pragma: allowlist secret
        ],
        "cc_pairs": [
            {"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obsidian-personal"},
            {"id": "sp-cp", "connector": "sp-conn", "credential": "m365-oauth", "name": "sharepoint-corp"},
        ],
        "collections": [
            {"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]},
            {"name": "sharepoint-public", "sources": [{"cc_pair": "sp-cp", "path_filter": "*"}]},
        ],
    }
}

_ONE_SOURCE_CONFIG = {
    "topology": {
        "connectors": [{"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"}],
        "cc_pairs": [{"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obsidian-personal"}],
        "collections": [{"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]}],
    }
}


def test_detect_config_drift_flags_rows_absent_from_config() -> None:
    """A store seeded with two sources, config with one → the dropped source drifts.

    Sabotage proof: replace the ``stored_* - {...}`` subtraction in
    ``detect_config_drift`` with ``tuple(sorted(stored_*))`` (drop the config
    exclusion) — the ``obs-conn`` / ``obsidian-personal`` / ``obsidian-all``
    still-present rows leak into the report and ``total == 3`` fails.
    """
    from kairix.core.connectors.topology_applier import detect_config_drift

    db = _build_db()
    apply_topology(db, parse_topology(_TWO_SOURCE_CONFIG))

    report = detect_config_drift(db, parse_topology(_ONE_SOURCE_CONFIG))

    assert report.has_drift is True
    assert report.total == 3
    assert report.connectors == ("sp-conn",)
    assert report.cc_pairs == ("sharepoint-corp",)
    assert report.collections == ("sharepoint-public",)


def test_detect_config_drift_clean_when_config_matches_store() -> None:
    """Store and config agree → no drift, no WARN line.

    Sabotage proof: hard-code ``has_drift`` to return True — this assertion of
    ``warn_line() is None`` fails.
    """
    from kairix.core.connectors.topology_applier import detect_config_drift

    db = _build_db()
    apply_topology(db, parse_topology(_TWO_SOURCE_CONFIG))

    report = detect_config_drift(db, parse_topology(_TWO_SOURCE_CONFIG))

    assert report.has_drift is False
    assert report.total == 0
    assert report.warn_line() is None


def test_config_drift_warn_line_names_count_and_sample() -> None:
    """The WARN line names the total and up to ``sample_size`` example ids."""
    from kairix.core.connectors.topology_applier import ConfigDriftReport

    report = ConfigDriftReport(
        connectors=("conn-2",),
        cc_pairs=("pair-2",),
        collections=("coll-remove", "coll-remove-2"),
    )

    line = report.warn_line(sample_size=2)

    assert line is not None
    assert "config drift: 4 topology source(s)" in line
    assert "still routed/synced until pruned" in line
    # Sample is de-duplicated + sorted, capped at sample_size (2 shown of 4).
    assert "coll-remove, coll-remove-2" in line


def test_detect_config_drift_per_surface_no_cross_surface_masking() -> None:
    """A name present on one surface must not mask a removed row of the same name.

    ``shared`` is declared as a collection in the config but the store also has
    a ``shared`` connector that the config dropped — the connector must still
    surface as drift.
    """
    from kairix.core.connectors.topology_applier import detect_config_drift

    seed = {
        "topology": {
            "connectors": [{"id": "shared", "kind": "obsidian", "name": "shared"}],
            "cc_pairs": [{"id": "cp", "connector": "shared", "credential": None, "name": "cp"}],
            "collections": [{"name": "shared", "sources": [{"cc_pair": "cp", "path_filter": "*"}]}],
        }
    }
    current = {
        "topology": {
            "connectors": [],
            "cc_pairs": [{"id": "cp", "connector": "shared", "credential": None, "name": "cp"}],
            "collections": [{"name": "shared", "sources": [{"cc_pair": "cp", "path_filter": "*"}]}],
        }
    }
    # The current config drops the connector but keeps a collection named
    # "shared"; the cc_pair still references a connector that no longer exists,
    # so we validate the drift detector, not the applier, by seeding first.
    db = _build_db()
    apply_topology(db, parse_topology(seed))

    report = detect_config_drift(db, parse_topology(current))

    assert report.connectors == ("shared",)
    assert report.collections == ()  # collection "shared" still declared


def test_detect_config_drift_legacy_db_missing_tables_is_clean() -> None:
    """A DB without the topology_* tables degrades to no drift, never raises.

    Sabotage proof: drop the ``except sqlite3.OperationalError`` guard in
    ``_stored_topology_names`` — this call raises OperationalError instead of
    returning a clean report.
    """
    from kairix.core.connectors.topology_applier import detect_config_drift

    bare = sqlite3.connect(":memory:")  # no create_schema → no topology_* tables

    report = detect_config_drift(bare, parse_topology(_ONE_SOURCE_CONFIG))

    assert report.has_drift is False
    assert report.total == 0
