"""F30 outcome test — ``kairix features`` subprocess surface.

Per the feature-flag-architecture spec §3.5, the CLI subcommand must
emit an envelope that operators (and the F30 outcome test) can read
from ``proc.stdout``. PR-2 ships the empty-registry happy path; the
PR-6 dogfood-cutover work will exercise the populated-registry shapes.

Boundary chain exercised:

  subprocess([python, -m, kairix.cli, features, status])
    → kairix/cli.py dispatch
    → kairix/core/features/cli.py:main
    → kairix.core.features.status() (delegating to the resolver)
    → format_table / format_json_envelope
    → print(...) on stdout
    → exit 0

Sabotage-proof anchor: removing the "No feature flags registered"
line in :func:`format_table` (e.g. returning an empty string) makes
``test_features_status_subprocess_text_mode_reports_empty_registry``
fail on the stdout assertion. Removing the ``"flags"`` key from
:func:`format_json_envelope` makes ``test_features_status_subprocess_json_mode_emits_envelope``
fail on the JSON parse assertion. Verified locally.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration


def test_features_status_subprocess_text_mode_reports_empty_registry() -> None:
    """Empty registry → exit 0 + friendly "No feature flags" line on stdout."""
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "features", "status"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"features status exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "No feature flags registered" in proc.stdout, (
        f"expected friendly empty-registry line in stdout: {proc.stdout!r}"
    )
    # Operator surfaces should stay fast — failing the budget here means
    # the dispatcher is doing real work it shouldn't.
    assert elapsed_ms < 10000.0, f"features status subprocess took {elapsed_ms:.1f}ms (threshold 10000ms)"


def test_features_status_subprocess_json_mode_emits_envelope() -> None:
    """``--json`` → exit 0 + JSON envelope on stdout with the ``flags`` key.

    Asserts on the envelope content (a parseable JSON object with the
    expected ``flags`` shape) — not just on ``returncode == 0``. This is
    the F30 contract: operator-visible output reflects the documented
    envelope so agents and humans can both consume it.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "features", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"features status --json exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    parsed = json.loads(proc.stdout)
    assert "flags" in parsed, f"expected 'flags' key in envelope; got keys: {list(parsed)}"
    assert isinstance(parsed["flags"], list), f"expected 'flags' to be a list; got {type(parsed['flags']).__name__}"
    # Registry is empty at PR-2 landing — every future PR that adds a
    # flag will see this list grow. The shape itself is what F30 asserts;
    # the per-flag contents live in PR-6+ outcome tests.
    assert parsed["flags"] == [], f"expected empty flags list at PR-2 landing; got: {parsed['flags']!r}"
