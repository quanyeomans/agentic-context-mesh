"""Integration tests for the ``connector_m365_calendar`` flag.

Exercises both branches of the connector-enablement gate through the
REAL production loop — :func:`kairix.worker.run_connector_sync_pipeline`
with ``flag_reader`` pinned via :class:`FakeFeatureFlagResolver`. The
gate is :func:`kairix.worker.connector_enabled` consulted per entry in
that loop; the assertion target is the observable
:class:`ConnectorSyncResult` counter outcome + the gated-off INFO log,
NOT a dead dispatcher's branch-marker log.

  * **Flag OFF** — ``connector_m365_calendar`` reads False. The M365 calendar entry is gated
    off (skipped before plugin resolution, so it never needs a live
    credential); a flagless ``obsidian`` sibling in the same tick still
    indexes its two notes. The aggregate ``synced`` reflects only the
    sibling.
  * **Flag ON** — ``connector_m365_calendar`` reads True. The gate lets the M365 calendar
    entry through to ``_run_one_connector_batch`` (where, lacking a live
    credential, it is logged + counted as a per-entry failure and the
    loop continues). The "gated off" log does NOT fire for m365_calendar; the
    flagless sibling still indexes its notes.

F47 — both branches are reached through the production
``run_connector_sync_pipeline`` entry point; no direct
``*Pipeline(...)`` construction here.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through ``ConnectorSyncDeps(flag_reader=...)`` — no @patch /
module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
deleting the ``if not connector_enabled(...)`` skip in
``run_connector_sync_pipeline``'s loop makes the OFF test fail — the
"gated off" log no longer fires and the m365_calendar entry runs. Restoring the
gate returns the test to green.

Spec: docs/architecture/connector-ingestion-architecture.md
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.worker import (
    ConnectorSyncDeps,
    ConnectorSyncResult,
    run_connector_sync_pipeline,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_KIND = "m365_calendar"


def _two_connector_topology(vault: Path) -> dict[str, Any]:
    """Merged mapping: the gated connector kind + a flagless obsidian sibling."""
    return {
        "topology_v2": {
            "connectors": [
                {
                    "id": f"{_KIND}-conn",
                    "kind": _KIND,
                    "name": f"Corp {_KIND}",
                    "connector_specific_config": {},
                },
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                },
            ],
            "cc_pairs": [
                {"id": f"{_KIND}-pair", "connector": f"{_KIND}-conn", "credential": None, "name": f"{_KIND}-cc"},
                {"id": "ob-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"},
            ],
        }
    }


def _seed_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nFirst note body.\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n\nSecond note body.\n", encoding="utf-8")
    return vault


def _drive_loop(
    tmp_path: Path,
    resolver: FakeFeatureFlagResolver,
    caplog: pytest.LogCaptureFixture,
) -> ConnectorSyncResult:
    vault = _seed_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(vault),
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )
    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        return run_connector_sync_pipeline(deps)


def _gated_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        rec.getMessage()
        for rec in caplog.records
        if _KIND in rec.getMessage().lower() and "gated" in rec.getMessage().lower()
    ]


def test_flag_off_gates_connector_sibling_still_runs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OFF — the m365_calendar entry is gated off in the loop; the flagless obsidian
    sibling still syncs both notes.

    With the flag OFF the predicate skips the m365_calendar entry BEFORE plugin
    resolution (no live credential needed); the "gated off" INFO log fires
    and the aggregate ``synced`` reflects only the obsidian sibling.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_calendar", False)
    result = _drive_loop(tmp_path, resolver, caplog)

    assert result.synced == 2, f"flagless obsidian sibling must still sync both notes; got {result}"
    assert result.failed == 0, f"OFF gate must not surface a failure for the gated connector; got {result}"
    assert _gated_logs(caplog), (
        f"flag OFF must skip the {_KIND} entry with a 'gated off' INFO log; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )


def test_flag_on_lets_connector_through_sibling_still_runs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ON — the gate lets the m365_calendar entry through to the batch runner; the
    flagless obsidian sibling still syncs both notes.

    With the flag ON the predicate does NOT skip the m365_calendar entry: it
    reaches ``_run_one_connector_batch`` where, lacking a live credential,
    it fails and is logged + counted as a per-entry failure (the loop
    continues). The "gated off" log must NOT fire for m365_calendar — proving the
    ON branch took a different path than OFF.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_calendar", True)
    result = _drive_loop(tmp_path, resolver, caplog)

    assert result.synced >= 2, f"flagless obsidian sibling must still sync its notes; got {result}"
    assert not _gated_logs(caplog), (
        f"flag ON must NOT gate off the {_KIND} entry; the 'gated off' log fired anyway: {_gated_logs(caplog)!r}"
    )

    failure_logs = [
        rec.getMessage() for rec in caplog.records if f"{_KIND}-cc" in rec.getMessage() and "failed" in rec.getMessage()
    ]
    assert failure_logs, (
        f"flag ON must let the {_KIND} entry reach the batch runner (where it fails without a "
        f"live credential); got {[r.getMessage() for r in caplog.records]}"
    )
