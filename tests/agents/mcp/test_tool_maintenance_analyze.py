"""Unit tests for ``tool_maintenance_analyze`` (#376).

The MCP equivalent of ``kairix maintenance analyze``. Direct-call tests:
no FastMCP, no server boot — just the handler against a tmp SQLite file.

Test discipline:
  * F1 / F2 — handler accepts an explicit ``db_path``; tests pass a
    real tmp DB via the kwarg seam. No monkey-patching, no setenv.
  * F8 — module-level ``pytestmark = pytest.mark.unit``.
  * F30 — assertion is on the envelope contract (every documented key
    present + correct values), not on returncode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.agents.mcp.server import tool_maintenance_analyze
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


_NOW = "2026-06-04T00:00:00Z"


def _seed_db(db_path: Path, *, n_docs: int = 5) -> None:
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


def test_tool_maintenance_analyze_runs_and_returns_envelope(tmp_path: Path) -> None:
    """Handler runs ANALYZE against a seeded DB and returns the contract envelope.

    Sabotage proof (executed): replaced the body of the tool with a
    static envelope that always returned ``{"analyze_ran": True, ...}``
    without calling ``run_periodic_analyze`` — the assertion on
    ``rows_analyzed == 5`` failed because the static envelope hardcoded
    0. Restored the real handler.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_db(db_path, n_docs=5)

    envelope = tool_maintenance_analyze(db_path=db_path)

    assert envelope["error"] == ""
    assert envelope["analyze_ran"] is True
    assert envelope["rows_analyzed"] == 5
    assert envelope["elapsed_ms"] >= 0.0
    assert envelope["reason"]  # non-empty string
    assert "plan_before" in envelope
    assert "plan_after" in envelope
    assert "sample_query" in envelope


def test_tool_maintenance_analyze_envelope_has_documented_keys(tmp_path: Path) -> None:
    """Every key in the documented contract is present in the success envelope.

    Sabotage proof (executed): dropped the ``sample_query`` key from the
    handler's return dict and this assertion failed.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_db(db_path, n_docs=2)

    envelope = tool_maintenance_analyze(db_path=db_path)

    expected_keys = {
        "analyze_ran",
        "reason",
        "rows_analyzed",
        "previous_doc_count",
        "elapsed_ms",
        "plan_before",
        "plan_after",
        "sample_query",
        "error",
    }
    missing = expected_keys - set(envelope.keys())
    assert not missing, f"envelope missing keys: {missing}; got: {sorted(envelope.keys())}"


def test_tool_maintenance_analyze_error_envelope_on_bogus_path(tmp_path: Path) -> None:
    """A non-existent DB path produces an error envelope, never raises.

    Agents must always receive a structured response; raising would
    break the MCP contract.
    """
    # SQLite happily opens a path that doesn't exist (creates it), so to
    # provoke an error we point at a path that's actually a directory.
    bogus = tmp_path / "is-a-directory"
    bogus.mkdir()

    envelope = tool_maintenance_analyze(db_path=bogus)

    # The error envelope shape: error key non-empty, analyze_ran=False.
    assert envelope["analyze_ran"] is False
    assert envelope["error"] != ""
    assert "detail" in envelope
