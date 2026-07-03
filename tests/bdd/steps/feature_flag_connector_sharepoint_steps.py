"""Step definitions for feature_flag_connector_sharepoint.feature.

Drives the REAL connector-sync loop —
:func:`kairix.worker.run_connector_sync_pipeline` — with the flag value
pinned through the canonical :class:`FakeFeatureFlagResolver` from
``tests/fakes.py``. The enablement gate under test is
:func:`kairix.worker.connector_enabled`, consulted per entry in that
loop. No ``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point in their call graph —
``run_connector_sync_pipeline`` composes the production
:class:`~kairix.core.connectors.ConnectorPipeline` internally (no direct
``*Pipeline(...)`` construction here). The assertion target is the
observable :class:`ConnectorSyncResult` counter outcome + the gated-off
INFO log, NOT a dead dispatcher's branch marker.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.worker import (
    ConnectorSyncDeps,
    ConnectorSyncResult,
    run_connector_sync_pipeline,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG = "connector_sharepoint"
_KIND = "sharepoint"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    sibling_vault: Path | None = None
    captured_logs: list[str] = field(default_factory=list)
    result: ConnectorSyncResult | None = None


@pytest.fixture
def sharepoint_flag_ctx() -> _Ctx:
    return _Ctx()


def _two_connector_topology(kind: str, vault: Path) -> dict[str, Any]:
    return {
        "topology": {
            "connectors": [
                {"id": f"{kind}-conn", "kind": kind, "name": f"Corp {kind}", "connector_specific_config": {}},
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                },
            ],
            "cc_pairs": [
                {"id": f"{kind}-pair", "connector": f"{kind}-conn", "credential": None, "name": f"{kind}-cc"},
                {"id": "ob-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"},
            ],
        }
    }


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the sharepoint connector flag set to {value}"))
def _operator_sets_flag(sharepoint_flag_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    sharepoint_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG, parsed)


@given("a flagless sibling connector with two notes is configured alongside sharepoint")
def _sibling_configured(sharepoint_flag_ctx: _Ctx, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nFirst note body.\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n\nSecond note body.\n", encoding="utf-8")
    sharepoint_flag_ctx.sibling_vault = vault


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker connector sync tick runs")
def _worker_tick_runs(
    sharepoint_flag_ctx: _Ctx,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invoke the production :func:`run_connector_sync_pipeline`.

    The flag is pinned through ``deps.flag_reader``; the gate
    (``connector_enabled``) is consulted per entry inside the real loop.
    """
    resolver = sharepoint_flag_ctx.resolver
    vault = sharepoint_flag_ctx.sibling_vault
    assert resolver is not None and vault is not None, "Given steps must run before When"

    db_path = tmp_path / "index.sqlite"
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(_KIND, vault),
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        sharepoint_flag_ctx.result = run_connector_sync_pipeline(deps)

    sharepoint_flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _gated_logs(ctx: _Ctx) -> list[str]:
    return [m for m in ctx.captured_logs if _KIND in m.lower() and "gated" in m.lower()]


@then("the sharepoint connector is gated off in the loop")
def _gated_off(sharepoint_flag_ctx: _Ctx) -> None:
    assert _gated_logs(sharepoint_flag_ctx), (
        f"expected a 'sharepoint gated off' INFO log; got {sharepoint_flag_ctx.captured_logs!r}"
    )


@then("the sharepoint connector is not gated off in the loop")
def _not_gated_off(sharepoint_flag_ctx: _Ctx) -> None:
    assert not _gated_logs(sharepoint_flag_ctx), (
        f"flag ON must NOT gate off sharepoint; gated logs fired anyway: {_gated_logs(sharepoint_flag_ctx)!r}"
    )


@then("the flagless sibling connector still syncs its notes")
def _sibling_syncs(sharepoint_flag_ctx: _Ctx) -> None:
    result = sharepoint_flag_ctx.result
    assert result is not None, "When step must run before Then"
    assert result.synced == 2, f"flagless obsidian sibling must still sync both notes; got {result}"
