"""Outcome test for ``kairix search --collection <name>``.

The IM-6 cutover surfaced that the CLI raised
``TypeError: _default_search() got an unexpected keyword argument 'collections'``
when ``--collection`` was passed. The bug: ``SearchDeps._search_with_collection``
threaded ``collections=[...]`` to ``_default_search`` which didn't accept the
kwarg, so the use-case path crashed before reaching ``pipeline.search``.

Per F30 (operator-outcome test) + CLAUDE.md "How to test" — outcome test
invokes via ``subprocess.run([sys.executable, "-m", "kairix.cli", "search", ...])``
and asserts on .stdout/.stderr content, not on internal call-counts.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m kairix.cli search ...``."""
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", "search", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_collection_flag_does_not_raise_type_error() -> None:
    """The ``--collection`` flag must not raise TypeError from the
    use-case path's ``_default_search`` signature.

    Pin the contract: even with an empty / non-existent collection, the
    CLI returns gracefully — not a Python traceback complaining about
    an unexpected keyword argument.
    """
    result = _run_cli("test query", "--collection", "obsidian", "--json")
    combined = result.stdout + result.stderr
    assert "unexpected keyword argument" not in combined, (
        f"--collection regressed to IM-6 TypeError shape. Output:\n{combined}"
    )


def test_collection_flag_returns_well_formed_json() -> None:
    """With ``--json``, the CLI returns a parseable result envelope even
    when no chunks match — the envelope shape itself is the contract."""
    result = _run_cli("test query", "--collection", "obsidian", "--json")
    stdout = result.stdout
    try:
        idx = stdout.index("{")
    except ValueError:
        raise AssertionError(f"no JSON object found in stdout:\n{stdout}\nstderr:\n{result.stderr}") from None
    envelope = json.loads(stdout[idx:])
    assert "query" in envelope
    assert "results" in envelope
    assert isinstance(envelope["results"], list)
    # The previous bug surfaced as `"error": "TypeError: _default_search()..."`
    # in the envelope. Verify absence.
    err = envelope.get("error", "")
    assert "unexpected keyword argument" not in err, f"envelope.error contains the IM-6 regression marker: {err}"
