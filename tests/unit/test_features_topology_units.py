"""Unit-layer coverage lift for the Wave D ``kairix features status
--topology`` surface (kairix/core/features/cli.py +
kairix/core/features/topology_status.py).

F1-clean / F2-clean / F5-clean: no @patch, no env-var manipulation,
no internal-name imports. The read_topology DI seam on ``main()``
keeps the SQLite read out of the unit layer.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import pytest

from kairix.core.connectors.cc_pair import create_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.features.cli import (
    format_json_envelope_with_topology,
    format_table_with_topology,
    main,
)
from kairix.core.features.resolver import FlagStatus
from kairix.core.features.topology_status import (
    ActorScopeSnapshot,
    CCPairSnapshot,
    TopologyDiagnostics,
    build_topology_diagnostics,
    render_topology_human,
    render_topology_json,
)

pytestmark = pytest.mark.unit


def _build_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    with closing(sqlite3.connect(str(db_path))) as db:
        create_schema(db, dims=4)
    return db_path


def _seed_full_topology(db_path: Path) -> None:
    """Seed a connector + cc_pair + collection + scope profile in the DB."""
    with closing(sqlite3.connect(str(db_path))) as db:
        now = "2026-05-23T00:00:00Z"
        cur = db.execute(
            "INSERT INTO topology_connectors "
            "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
            "VALUES ('obsidian', 'c1', '{}', 'internal', ?, ?)",
            (now, now),
        )
        connector_id = cur.lastrowid
        assert connector_id is not None
        pair = create_cc_pair(db, connector_id=int(connector_id), credential_id=None, name="cc1")
        cur = db.execute(
            "INSERT INTO topology_collections "
            "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
            "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?)",
            ("vault", now, now),
        )
        collection_id = cur.lastrowid
        assert collection_id is not None
        db.execute(
            "INSERT INTO topology_collection_sources "
            "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
            "VALUES (?, ?, ?, NULL)",
            (int(collection_id), pair.id, "*"),
        )
        cur = db.execute(
            "INSERT INTO topology_scope_profiles "
            "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
            "VALUES ('agent-alpha', 'agent', '[]', ?, ?)",
            (now, now),
        )
        profile_id = cur.lastrowid
        assert profile_id is not None
        db.execute(
            "INSERT INTO topology_scope_entries "
            "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
            "VALUES (?, 'vault', 1, 0, 'internal')",
            (int(profile_id),),
        )
        db.commit()


def _flag_status(name: str) -> FlagStatus:
    return FlagStatus(
        name=name,
        default=False,
        effective=False,
        source="default",
        stage="introduce",
        introduced_in="v1",
        target_retire_in="v2",
        owner="x",
        related_spec=None,
    )


def test_render_topology_human_empty_state() -> None:
    """Empty diagnostics render the (none declared) friendly lines."""
    out = render_topology_human(TopologyDiagnostics(cc_pairs=(), actor_scopes=()))
    assert "(none declared)" in out
    assert "cc_pairs:" in out
    assert "actor_scopes:" in out


def test_render_topology_human_populated() -> None:
    """Populated diagnostics render rows for each cc_pair + actor."""
    diag = TopologyDiagnostics(
        cc_pairs=(CCPairSnapshot(id=1, name="cc1", status="ACTIVE", access_type="PRIVATE"),),
        actor_scopes=(
            ActorScopeSnapshot(
                actor_id="agent-alpha",
                actor_kind="agent",
                readable_collections=("vault",),
                excluded_collections=(),
            ),
        ),
    )
    out = render_topology_human(diag)
    assert "cc1" in out
    assert "agent-alpha" in out
    assert "vault" in out


def test_render_topology_human_excluded_branch() -> None:
    """An actor with no readable collections renders (none)."""
    diag = TopologyDiagnostics(
        cc_pairs=(),
        actor_scopes=(
            ActorScopeSnapshot(
                actor_id="agent-alpha",
                actor_kind="agent",
                readable_collections=(),
                excluded_collections=("ghost",),
            ),
        ),
    )
    out = render_topology_human(diag)
    assert "ghost" in out
    assert "(none)" in out  # readable falls back


def test_render_topology_json_round_trips() -> None:
    """The JSON renderer returns a serialisable dict."""
    diag = TopologyDiagnostics(
        cc_pairs=(CCPairSnapshot(id=1, name="cc1", status="ACTIVE", access_type="PRIVATE"),),
        actor_scopes=(
            ActorScopeSnapshot(
                actor_id="agent-alpha",
                actor_kind="agent",
                readable_collections=("vault",),
                excluded_collections=(),
            ),
        ),
    )
    payload = render_topology_json(diag)
    serialised = json.dumps(payload)
    parsed = json.loads(serialised)
    assert parsed["cc_pairs"][0]["name"] == "cc1"
    assert parsed["actor_scopes"][0]["readable_collections"] == ["vault"]


def test_build_topology_diagnostics_returns_populated_snapshot(tmp_path: Path) -> None:
    """End-to-end snapshot: seed → build → assert."""
    db_path = _build_db(tmp_path)
    _seed_full_topology(db_path)
    with closing(sqlite3.connect(str(db_path))) as db:
        diag = build_topology_diagnostics(db)
    assert len(diag.cc_pairs) == 1
    assert diag.cc_pairs[0].name == "cc1"
    assert any(scope.actor_id == "agent-alpha" for scope in diag.actor_scopes)


def test_format_table_with_topology_omits_diag_when_none() -> None:
    """Backward-compat: when diag is None, output is identical to format_table."""
    entries = (_flag_status("flag1"),)
    out = format_table_with_topology(entries, None)
    assert "flag1" in out
    assert "Topology" not in out


def test_format_table_with_topology_appends_when_present() -> None:
    """When diag is supplied, the topology block is appended after the flag table."""
    entries = (_flag_status("flag1"),)
    diag = TopologyDiagnostics(cc_pairs=(), actor_scopes=())
    out = format_table_with_topology(entries, diag)
    assert "flag1" in out
    assert "Topology diagnostics" in out


def test_format_json_envelope_with_topology_diag_none_drops_key() -> None:
    """When diag is None, the JSON envelope contains only the flags key."""
    entries = (_flag_status("flag1"),)
    out = format_json_envelope_with_topology(entries, None)
    parsed = json.loads(out)
    assert "topology" not in parsed
    assert parsed["flags"][0]["name"] == "flag1"


def test_format_json_envelope_with_topology_diag_set_includes_key() -> None:
    """When diag is set, the JSON envelope carries both flags + topology keys."""
    entries = (_flag_status("flag1"),)
    diag = TopologyDiagnostics(cc_pairs=(), actor_scopes=())
    out = format_json_envelope_with_topology(entries, diag)
    parsed = json.loads(out)
    assert "topology" in parsed
    assert parsed["topology"]["cc_pairs"] == []


def test_main_with_topology_calls_diagnostics_resolver(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() with --topology calls the injected resolver + renders the block."""
    diag = TopologyDiagnostics(
        cc_pairs=(CCPairSnapshot(id=1, name="cc1", status="ACTIVE", access_type="PRIVATE"),),
        actor_scopes=(),
    )
    code = main(
        ["status", "--topology"],
        status_provider=lambda: (_flag_status("flag1"),),
        read_topology=lambda _db_path: diag,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Topology" in captured.out
    assert "cc1" in captured.out


def test_main_without_topology_flag_skips_resolver(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() without --topology does not call the resolver."""
    called = {"count": 0}

    def _resolver(_db_path: str | None) -> TopologyDiagnostics:
        called["count"] += 1
        return TopologyDiagnostics(cc_pairs=(), actor_scopes=())

    code = main(
        ["status"],
        status_provider=lambda: (_flag_status("flag1"),),
        read_topology=_resolver,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Topology" not in captured.out
    assert called["count"] == 0


def test_main_json_with_topology_renders_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() with --json + --topology emits the merged envelope."""
    diag = TopologyDiagnostics(cc_pairs=(), actor_scopes=())
    code = main(
        ["status", "--json", "--topology"],
        status_provider=lambda: (_flag_status("flag1"),),
        read_topology=lambda _db_path: diag,
    )
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert code == 0
    assert "topology" in parsed


def test_main_topology_default_resolver_handles_missing_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The production resolver path: pointing at a tmp_path without schema
    returns None and renders no topology block (graceful degrade).
    """
    bad_path = tmp_path / "no_schema.sqlite"
    sqlite3.connect(str(bad_path)).close()
    code = main(
        ["status", "--topology", "--db-path", str(bad_path)],
        status_provider=lambda: (_flag_status("flag1"),),
    )
    captured = capsys.readouterr()
    assert code == 0
    # The diag is None — the human-mode topology block is absent.
    assert "Topology" not in captured.out


def test_main_topology_default_resolver_renders_zero_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Production resolver against a schema-applied tmp DB returns the zero-snapshot."""
    db_path = _build_db(tmp_path)
    code = main(
        ["status", "--topology", "--db-path", str(db_path)],
        status_provider=lambda: (_flag_status("flag1"),),
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Topology diagnostics" in captured.out
    assert "(none declared)" in captured.out


def test_asdict_flag_status_shape() -> None:
    """FlagStatus is a frozen dataclass — sanity check for asdict round-trip."""
    payload = asdict(_flag_status("flag1"))
    assert payload["name"] == "flag1"
