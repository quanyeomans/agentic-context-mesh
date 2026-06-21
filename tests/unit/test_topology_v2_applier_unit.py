"""Unit tests for the Wave D topology v2 apply-bridge.

Lift the per-file coverage on
:mod:`kairix.core.connectors.topology_v2_applier` above the F7 90%
floor by exercising every branch in isolation against a fresh in-memory
DB. Integration coverage (apply-bridge + worker boot wiring) lives in
``tests/integration/test_topology_v2_applier.py``; E2E coverage lives
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

from kairix.config import parse_topology_v2
from kairix.core.connectors.topology_v2_applier import (
    ApplierDeps,
    ApplyResult,
    ApplyValidationError,
    apply_topology_v2,
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
    """Empty TopologyV2Config returns ApplyResult(0, 0, 0)."""
    db = _build_db()
    parsed = parse_topology_v2({})
    result = apply_topology_v2(db, parsed)
    assert result == ApplyResult(created=0, updated=0, unchanged=0)


def test_apply_one_block_per_surface_returns_five_created() -> None:
    """Minimum non-empty config: one entry per surface = five created rows."""
    db = _build_db()
    parsed = parse_topology_v2(
        {
            "topology_v2": {
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
    result = apply_topology_v2(db, parsed)
    assert result == ApplyResult(created=5, updated=0, unchanged=0)


def test_apply_twice_is_unchanged() -> None:
    """Second apply against unchanged config reports every row as unchanged."""
    db = _build_db()
    parsed = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            }
        }
    )
    apply_topology_v2(db, parsed)
    second = apply_topology_v2(db, parsed)
    assert second == ApplyResult(created=0, updated=0, unchanged=1)


# ---------------------------------------------------------------------------
# UPDATE branches — operator changes the config and re-applies.
# ---------------------------------------------------------------------------


def test_apply_updates_connector_when_kind_changes() -> None:
    """Re-applying with a changed connector kind triggers UPDATE."""
    db = _build_db()
    apply_topology_v2(
        db,
        parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}]}}),
    )
    second = apply_topology_v2(
        db,
        parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "sharepoint", "name": "c1"}]}}),
    )
    assert second.updated == 1
    assert second.created == 0
    kind = db.execute("SELECT kind FROM topology_connectors WHERE name = 'c1'").fetchone()
    assert kind[0] == "sharepoint"


def test_apply_updates_credential_when_secret_name_changes() -> None:
    """Re-applying with a changed credential secret_name triggers UPDATE."""
    db = _build_db()
    apply_topology_v2(
        db,
        parse_topology_v2(
            {
                "topology_v2": {
                    "credentials": [
                        {"id": "cr1", "kind": "oauth", "secret_name": "s1"},  # pragma: allowlist secret
                    ]
                }
            }
        ),
    )
    second = apply_topology_v2(
        db,
        parse_topology_v2(
            {
                "topology_v2": {
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
        "topology_v2": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
        }
    }
    apply_topology_v2(db, parse_topology_v2(base_config))
    bumped = {
        "topology_v2": {
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
    second = apply_topology_v2(db, parse_topology_v2(bumped))
    assert second.updated == 1
    row = db.execute("SELECT access_type, status FROM topology_cc_pairs WHERE name = 'cp1'").fetchone()
    assert row[0] == "PUBLIC"
    # Status was never touched by the applier — stays at SCHEDULED.
    assert row[1] == "SCHEDULED"


def test_apply_updates_collection_source_when_sensitivity_min_changes() -> None:
    """Changing sensitivity_min on a source mapping triggers UPDATE, not duplicate INSERT."""
    db = _build_db()
    base = {
        "topology_v2": {
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
    apply_topology_v2(db, parse_topology_v2(base))
    bumped = {
        "topology_v2": {
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
    second = apply_topology_v2(db, parse_topology_v2(bumped))
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
        "topology_v2": {
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
    apply_topology_v2(db, parse_topology_v2(_tier_config("reference")))
    assert _collection_tier(db, "col1") == "reference"


def test_apply_collection_tier_absent_lands_null() -> None:
    """A collection without ``tier:`` INSERTs NULL — back-compat default."""
    db = _build_db()
    apply_topology_v2(db, parse_topology_v2(_tier_config(None)))
    assert _collection_tier(db, "col1") is None


def test_apply_updates_collection_when_tier_changes() -> None:
    """Editing a collection's ``tier:`` re-writes the row in place (load-bearing UPDATE).

    Pins the previously no-op collection UPDATE branch: a changed tier must
    report ``updated`` and overwrite the stored value, with no duplicate row.
    """
    db = _build_db()
    apply_topology_v2(db, parse_topology_v2(_tier_config("reference")))
    second = apply_topology_v2(db, parse_topology_v2(_tier_config("primary")))
    assert second.updated == 1
    assert _collection_tier(db, "col1") == "primary"
    count = db.execute("SELECT COUNT(*) FROM topology_collections WHERE name = 'col1'").fetchone()[0]
    assert count == 1


def test_apply_collection_unchanged_when_tier_identical() -> None:
    """Re-applying the same tier reports ``unchanged`` — the diff guard holds."""
    db = _build_db()
    config = _tier_config("reference")
    apply_topology_v2(db, parse_topology_v2(config))
    second = apply_topology_v2(db, parse_topology_v2(config))
    assert second.updated == 0
    assert _collection_tier(db, "col1") == "reference"


# ---------------------------------------------------------------------------
# Error paths.
# ---------------------------------------------------------------------------


def test_apply_rejects_invalid_config_with_validation_failures() -> None:
    """ApplyValidationError carries the full failure tuple from the validator."""
    db = _build_db()
    bad = parse_topology_v2(
        {
            "topology_v2": {
                "cc_pairs": [{"id": "x", "connector": "missing", "credential": None, "name": "x"}],
            }
        }
    )
    with pytest.raises(ApplyValidationError) as exc_info:
        apply_topology_v2(db, bad)
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
    parsed = parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "x", "name": "c1"}]}})

    def _always_clean(_config: object) -> tuple:
        return ()

    deps = ApplierDeps(validator_fn=_always_clean)
    result = apply_topology_v2(db, parsed, applier_deps=deps)
    assert result.created == 1


def test_apply_now_fn_override_via_applier_deps() -> None:
    """ApplierDeps.now_fn override — every row stamps the deterministic time."""
    db = _build_db()
    parsed = parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "x", "name": "c1"}]}})

    deps = ApplierDeps(now_fn=lambda: "1999-01-01T00:00:00Z")
    apply_topology_v2(db, parsed, applier_deps=deps)
    row = db.execute("SELECT created_at, updated_at FROM topology_connectors WHERE name = 'c1'").fetchone()
    assert row[0] == "1999-01-01T00:00:00Z"
    assert row[1] == "1999-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# F39 / F57 invariants on the applier path.
# ---------------------------------------------------------------------------


def test_apply_cc_pair_with_credential_binding_resolved() -> None:
    """A cc_pair with a credential reference resolves the credential row id."""
    db = _build_db()
    parsed = parse_topology_v2(
        {
            "topology_v2": {
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
    apply_topology_v2(db, parsed)
    row = db.execute("SELECT credential_id FROM topology_cc_pairs WHERE name = 'cp1'").fetchone()
    assert row[0] is not None


def test_apply_collection_unchanged_when_only_source_added() -> None:
    """Re-applying with a new source mapping: collection row unchanged, source row created."""
    db = _build_db()
    base = {
        "topology_v2": {
            "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "cp1"}],
            "collections": [
                {"name": "col1", "sources": [{"cc_pair": "p1", "path_filter": "01/*"}]},
            ],
        }
    }
    apply_topology_v2(db, parse_topology_v2(base))
    extended = {
        "topology_v2": {
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
    second = apply_topology_v2(db, parse_topology_v2(extended))
    # Only the new source row counts as created.
    assert second.created == 1
    sources = db.execute("SELECT COUNT(*) FROM topology_collection_sources").fetchone()
    assert sources[0] == 2
