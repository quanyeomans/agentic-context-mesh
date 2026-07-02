"""Contract: CLI ↔ MCP parity for the ``maintenance analyze`` diagnostic.

The analyze diagnostic ships ONE use case,
:func:`kairix.core.maintenance.cli.build_analyze_envelope`, behind two thin
adapters: the CLI ``kairix maintenance analyze`` (``run_analyze_command``,
``--json``) and the MCP ``tool_maintenance_analyze``. This contract proves the
two adapters return the SAME analyze envelope for the same DB state — the
CLI↔MCP parity invariant — AND that both delegate to the single use case rather
than each re-deriving the analyze sequence + envelope shape (the DRY-consolidation
lock: before W5d the MCP adapter carried its own copy, including a hard-coded
``sample_query`` literal).

Both adapters run against two freshly-seeded copies of the same schema so the
comparison isolates the adapter wiring, not the SQLite backend (F1/F2/F5-clean —
no @patch, no env vars, public surface only; each adapter takes an explicit
``db_path``).
"""

from __future__ import annotations

import inspect
import io
import json
import sqlite3
from pathlib import Path

import pytest

from kairix.agents.mcp.server import tool_maintenance_analyze
from kairix.core.db.schema import create_schema
from kairix.core.maintenance import cli as maintenance_cli
from kairix.core.maintenance.cli import run_analyze_command

pytestmark = pytest.mark.contract

_NOW = "2026-06-04T00:00:00Z"

# Keys whose values are deterministic across two identical fresh DBs. The
# analyze wall-clock (``elapsed_ms``) legitimately varies run-to-run, so it is
# compared for presence + type, not equality.
_DETERMINISTIC_KEYS = (
    "analyze_ran",
    "reason",
    "rows_analyzed",
    "previous_doc_count",
    "plan_before",
    "plan_after",
    "sample_query",
)


def _seed(db_path: Path, *, n_docs: int = 4) -> None:
    """Create the kairix schema at ``db_path`` and seed ``n_docs`` documents."""
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    rows = [
        (
            "default",
            f"doc-{i:04d}.md",
            f"agent-alpha-{i:04d}",
            None,
            None,
            None,
            None,
            "public",
            _NOW,
            _NOW,
            1,
        )
        for i in range(n_docs)
    ]
    db.executemany(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    db.close()


def _cli_envelope(db_path: Path) -> dict:
    out, err = io.StringIO(), io.StringIO()
    code = run_analyze_command(db_path=db_path, out=out, err=err, as_json=True)
    assert code == 0, f"CLI exited {code}; stderr={err.getvalue()!r}"
    return json.loads(out.getvalue())


def test_cli_and_mcp_return_equivalent_analyze_envelopes(tmp_path: Path) -> None:
    """Same seeded DB → matching analyze envelopes through both adapters.

    Sabotage-proof (executed): reverted ``tool_maintenance_analyze`` to its own
    inline analyze sequence with the hard-coded
    ``sample_query="SELECT id FROM documents WHERE collection=? AND active=1"``
    but a mismatched literal — the ``sample_query`` equality below fired.
    Restored the ``build_analyze_envelope`` delegation.
    """
    cli_db = tmp_path / "cli.sqlite"
    mcp_db = tmp_path / "mcp.sqlite"
    _seed(cli_db, n_docs=4)
    _seed(mcp_db, n_docs=4)

    cli = _cli_envelope(cli_db)
    mcp = tool_maintenance_analyze(db_path=mcp_db)

    # The MCP contract adds the always-present ``error`` key; the CLI JSON does
    # not carry it — that is the only intended shape difference.
    assert mcp["error"] == ""
    assert "error" not in cli

    for key in _DETERMINISTIC_KEYS:
        assert cli[key] == mcp[key], f"{key} diverged: CLI={cli[key]!r} MCP={mcp[key]!r}"

    # The analyze actually ran against the seeded rows on both surfaces.
    assert cli["rows_analyzed"] == 4
    assert cli["sample_query"] == "SELECT id FROM documents WHERE collection=? AND active=1"

    # elapsed_ms is timing — present + numeric on both, not compared for equality.
    assert isinstance(cli["elapsed_ms"], float)
    assert isinstance(mcp["elapsed_ms"], float)


def test_both_adapters_delegate_to_build_analyze_envelope() -> None:
    """CLI + MCP adapters call the single ``build_analyze_envelope`` use case.

    This is the DRY-consolidation lock: neither adapter may re-derive the
    analyze sequence / envelope shape locally.

    Sabotage-proof (executed): inlined ``_explain_plan``/``run_periodic_analyze``
    back into ``tool_maintenance_analyze`` (dropping the
    ``build_analyze_envelope`` call) — the MCP source assertion failed. Restored.
    """
    cli_src = inspect.getsource(run_analyze_command)
    assert "build_analyze_envelope(" in cli_src

    mcp_src = inspect.getsource(tool_maintenance_analyze)
    assert "build_analyze_envelope(" in mcp_src
    assert "from kairix.core.maintenance.cli import build_analyze_envelope" in mcp_src

    # The use case is a first-class public export, not a reached-into private.
    assert "build_analyze_envelope" in maintenance_cli.__all__
