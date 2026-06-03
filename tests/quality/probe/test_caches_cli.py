"""Unit + outcome tests for ``kairix probe caches`` (#396 W-B).

Drives :func:`kairix.quality.probe.caches_cli.main` and asserts on the
stdout / JSON shape, then runs an F30 subprocess outcome test that
boots the whole ``python -m kairix.cli probe caches`` binary.

F1-clean (no @patch), F2-clean (no env var), F5-clean (no private
imports). Each test documents its sabotage proof in the docstring.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from kairix.quality.probe.caches_cli import main

pytestmark = pytest.mark.unit


def _run_capture(argv: list[str]) -> tuple[int, str]:
    """Invoke main and capture (rc, stdout)."""
    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        rc = main(argv)
    return rc, out_buf.getvalue()


def test_text_mode_lists_every_cache() -> None:
    """Default text-mode output includes the W-B cache names.

    Sabotage proof: dropping ``brief_output_cache`` from the
    ``_collect_all_rows`` collectors list breaks this test — the
    expected cache name is missing from stdout.
    """
    rc, out = _run_capture([])
    assert rc == 0
    assert "kairix probe caches" in out
    for name in (
        "query_result_cache",
        "prep_summary_cache",
        "brief_output_cache",
        "brief_source_cache",
        "health_probe_cache",
    ):
        assert name in out, f"expected cache name {name!r} in default text output; missing. Output:\n{out}"


def test_json_mode_emits_envelope() -> None:
    """``--json`` switches to a structured JSON envelope keyed by ``caches``."""
    rc, out = _run_capture(["--json"])
    assert rc == 0
    envelope = json.loads(out)
    assert "caches" in envelope
    names = {row["name"] for row in envelope["caches"]}
    assert "query_result_cache" in names
    assert "prep_summary_cache" in names
    assert "brief_output_cache" in names
    assert "brief_source_cache" in names
    assert "health_probe_cache" in names


def test_json_row_shape() -> None:
    """Every row in the JSON envelope carries the documented keys."""
    rc, out = _run_capture(["--json"])
    assert rc == 0
    envelope = json.loads(out)
    for row in envelope["caches"]:
        assert set(row.keys()) >= {
            "name",
            "size",
            "hits",
            "misses",
            "evictions",
            "hit_rate_pct",
        }, f"row missing keys: {row}"


def test_since_flag_accepted_but_ignored() -> None:
    """``--since`` is accepted (mcp-calls parity) but doesn't affect caches output."""
    rc_a, out_a = _run_capture(["--json"])
    rc_b, out_b = _run_capture(["--since", "1h", "--json"])
    assert rc_a == rc_b == 0
    # The envelope keys are identical (caches are point-in-time; --since
    # is a no-op surface so operators can pipe both reports through one loop).
    env_a = json.loads(out_a)
    env_b = json.loads(out_b)
    assert {row["name"] for row in env_a["caches"]} == {row["name"] for row in env_b["caches"]}


@pytest.mark.slow
def test_subprocess_outcome_path() -> None:
    """F30 subprocess outcome test — boots the real CLI binary end-to-end.

    Sabotage proof: removing the ``caches`` dispatch branch in
    ``kairix.quality.probe.cli._run_caches`` makes this test fail —
    the subcommand isn't recognised and the parser surfaces an error.
    """
    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "probe", "caches", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # The CLI emits a deprecation banner to stderr but still exits 0.
    assert result.returncode == 0, (
        f"probe caches subprocess exited non-zero: rc={result.returncode} stderr={result.stderr!r}"
    )
    envelope = json.loads(result.stdout)
    assert "caches" in envelope
