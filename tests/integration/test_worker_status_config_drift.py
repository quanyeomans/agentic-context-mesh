"""Integration coverage — ``kairix worker status`` config-drift WARN (#726).

``apply_topology`` is upsert-only: a connector / cc_pair / collection removed
from the operator config leaves its ``topology_*`` row behind, so the source
stays routed/synced until a deliberate prune. This is the OBSERVABILITY half of
issue #726 — surface the drift as a WARN in ``kairix worker status`` so an
operator can see the store diverged from config. The prune itself (which must
transition removed cc_pairs through the F57 status lifecycle) is deferred to a
separate deliberate cutover and is explicitly out of scope here; this scan is
read-only w.r.t. the DB.

Boundary chain exercised (multi-component, real sqlite):

  parse_topology(config)
    → apply_topology(db, ...)                 # seed the store the production way
    → worker_cli.status(db_path=..., config_mapping=...)
      → detect_config_drift(db, parsed_config)  # read-only store↔config diff
      → format_status(...) + WARN line          # operator-visible surface

Sabotage proof (executed during authoring):

  * Mutation: in ``kairix/worker_cli.py:status`` delete the
    ``if warn: out.write(warn + "\n")`` branch.
    Observed failure: ``test_status_warns_on_removed_topology_source``
    failed — the ``WARN config drift`` assertion was absent from stdout.
    Restoration: re-add the WARN-write branch.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.config import parse_topology
from kairix.core.connectors.topology_applier import apply_topology
from kairix.core.db.schema import create_schema
from kairix.worker_cli import status
from kairix.worker_state import WorkerPhase, WorkerState, write_state

pytestmark = pytest.mark.integration


_TWO_SOURCE_CONFIG: dict[str, Any] = {
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

# obsidian retained; the SharePoint source is removed from config (still in store).
_ONE_SOURCE_CONFIG: dict[str, Any] = {
    "topology": {
        "connectors": [{"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"}],
        "cc_pairs": [{"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obsidian-personal"}],
        "collections": [{"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]}],
    }
}


def _seed_store(tmp_path: Path, config: dict[str, Any]) -> Path:
    """Seed a file-backed topology store via the production applier."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    apply_topology(db, parse_topology(config))
    db.commit()
    db.close()
    return db_path


def _state(tmp_path: Path) -> Path:
    state_path = tmp_path / "worker-state.json"
    write_state(WorkerState(current_phase=WorkerPhase.IDLE, embedded_total=1), state_path)
    return state_path


def test_status_warns_on_removed_topology_source(tmp_path: Path) -> None:
    """Store seeded with two sources, config now has one → drift WARN names the 3 orphans."""
    db_path = _seed_store(tmp_path, _TWO_SOURCE_CONFIG)
    state_path = _state(tmp_path)

    out = io.StringIO()
    rc = status(
        state_path=state_path,
        out=out,
        err=io.StringIO(),
        db_path=db_path,
        config_mapping=_ONE_SOURCE_CONFIG,
    )

    printed = out.getvalue()
    assert rc == 0
    assert "WARN config drift: 3 topology source(s) in the store are no longer in config" in printed, printed
    assert "still routed/synced until pruned" in printed, printed
    for stranded in ("sp-conn", "sharepoint-corp", "sharepoint-public"):
        assert stranded in printed, f"stranded id {stranded!r} missing from WARN: {printed!r}"


def test_status_emits_no_drift_warn_when_store_matches_config(tmp_path: Path) -> None:
    """Store and config agree → no drift WARN, status still renders cleanly."""
    db_path = _seed_store(tmp_path, _TWO_SOURCE_CONFIG)
    state_path = _state(tmp_path)

    out = io.StringIO()
    rc = status(
        state_path=state_path,
        out=out,
        err=io.StringIO(),
        db_path=db_path,
        config_mapping=_TWO_SOURCE_CONFIG,
    )

    printed = out.getvalue()
    assert rc == 0
    assert "config drift" not in printed, printed
    assert "Phase: IDLE" in printed


def test_status_drift_scan_leaves_store_rows_untouched(tmp_path: Path) -> None:
    """The scan is read-only: reporting drift must NOT delete/prune any row (#726 scope)."""
    db_path = _seed_store(tmp_path, _TWO_SOURCE_CONFIG)
    state_path = _state(tmp_path)

    status(
        state_path=state_path,
        out=io.StringIO(),
        err=io.StringIO(),
        db_path=db_path,
        config_mapping=_ONE_SOURCE_CONFIG,
    )

    db = sqlite3.connect(str(db_path))
    try:
        connectors = db.execute("SELECT COUNT(*) FROM topology_connectors").fetchone()[0]
        cc_pairs = db.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()[0]
        collections = db.execute("SELECT COUNT(*) FROM topology_collections").fetchone()[0]
    finally:
        db.close()
    # All rows survive the drift scan — the prune is a separate deliberate cutover.
    assert (connectors, cc_pairs, collections) == (2, 2, 2)
