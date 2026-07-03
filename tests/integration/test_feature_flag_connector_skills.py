"""Integration tests for the ``connector_skills`` flag.

Exercises both branches of the connector-enablement gate through the
REAL production loop — :func:`kairix.worker.run_connector_sync_pipeline`
with ``flag_reader`` pinned via :class:`FakeFeatureFlagResolver`. The
gate is :func:`kairix.worker.connector_enabled` consulted per entry in
that loop; the assertion target is the observable
:class:`ConnectorSyncResult` counter outcome + the gated-off INFO log,
NOT a dead dispatcher's branch-marker log.

The skills connector is unusual: it degrades gracefully (it walks a
``~/.claude`` tree and indexes whatever skills/agents are present, or
no-ops where absent) rather than raising when un-credentialled. The test
points it at an EMPTY ``claude_root`` so the ON branch reaches the batch
runner deterministically — zero local skills → the flagless sibling is
the only contributor to ``synced``. The distinguishing observable across
branches is the gated-off log.

  * **Flag OFF** — ``connector_skills`` reads False. The skills entry is
    gated off (skipped); a flagless ``obsidian`` sibling still indexes its
    two notes. The aggregate ``synced`` is exactly the sibling's count.
  * **Flag ON** — ``connector_skills`` reads True. The gate lets the
    skills entry through; the "gated off" log does NOT fire and the
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
"gated off" log no longer fires for skills. Restoring the gate returns
the test to green.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.worker import (
    ConnectorSyncDeps,
    run_connector_sync_pipeline,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_KIND = "skills"


def _two_connector_topology(vault: Path, claude_root: Path) -> dict[str, Any]:
    """Merged mapping: the skills connector kind + a flagless obsidian sibling.

    The skills connector is pointed at an EMPTY ``claude_root`` so the ON
    branch reaches the batch runner deterministically (zero local skills →
    the sibling is the only contributor to ``synced``).
    """
    return {
        "topology": {
            "connectors": [
                {
                    "id": "skills-conn",
                    "kind": _KIND,
                    "name": "Local skills",
                    "connector_specific_config": {"claude_root": str(claude_root)},
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
                {"id": "skills-pair", "connector": "skills-conn", "credential": None, "name": "skills-cc"},
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
) -> Any:
    vault = _seed_vault(tmp_path)
    claude_root = tmp_path / "claude_home"
    claude_root.mkdir()
    db_path = tmp_path / "index.sqlite"
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(vault, claude_root),
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
    """OFF — the skills entry is gated off in the loop; the flagless obsidian
    sibling still syncs both notes and is the only contributor to ``synced``.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_skills", False)
    result = _drive_loop(tmp_path, resolver, caplog)

    assert result.synced == 2, (
        f"flag OFF must gate skills; only the flagless obsidian sibling's two notes sync; got {result}"
    )
    assert _gated_logs(caplog), (
        f"flag OFF must skip the {_KIND} entry with a 'gated off' INFO log; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )


def test_flag_on_lets_connector_through_sibling_still_runs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ON — the gate lets the skills entry through to the batch runner; the
    "gated off" log does NOT fire and the flagless sibling still syncs.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_skills", True)
    result = _drive_loop(tmp_path, resolver, caplog)

    assert result.synced >= 2, f"flagless obsidian sibling must still sync its notes; got {result}"
    assert not _gated_logs(caplog), (
        f"flag ON must NOT gate off the {_KIND} entry; the 'gated off' log fired anyway: {_gated_logs(caplog)!r}"
    )
