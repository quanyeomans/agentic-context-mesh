"""Step definitions for feature_flag_connector_m365_email_headers.feature.

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

_FLAG = "connector_m365_email_headers"
_KIND = "m365_email_headers"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    sibling_vault: Path | None = None
    captured_logs: list[str] = field(default_factory=list)
    result: ConnectorSyncResult | None = None


@pytest.fixture
def m365_email_headers_flag_ctx() -> _Ctx:
    return _Ctx()


def _two_connector_topology(vault: Path) -> dict[str, Any]:
    return {
        "topology_v2": {
            "connectors": [
                {"id": f"{_KIND}-conn", "kind": _KIND, "name": f"Corp {_KIND}", "connector_specific_config": {}},
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


@given(parsers.parse("the operator has the m365 email-headers connector flag set to {value}"))
def _operator_sets_flag(m365_email_headers_flag_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    m365_email_headers_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG, parsed)


@given("a flagless sibling connector with two notes is configured alongside m365 email-headers")
def _sibling_configured(m365_email_headers_flag_ctx: _Ctx, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nFirst note body.\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n\nSecond note body.\n", encoding="utf-8")
    m365_email_headers_flag_ctx.sibling_vault = vault


@when("the worker connector sync tick runs for m365 email-headers")
def _worker_tick_runs(
    m365_email_headers_flag_ctx: _Ctx,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invoke the production :func:`run_connector_sync_pipeline`."""
    resolver = m365_email_headers_flag_ctx.resolver
    vault = m365_email_headers_flag_ctx.sibling_vault
    assert resolver is not None and vault is not None, "Given steps must run before When"

    db_path = tmp_path / "index.sqlite"
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(vault),
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        m365_email_headers_flag_ctx.result = run_connector_sync_pipeline(deps)

    m365_email_headers_flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


def _gated_logs(ctx: _Ctx) -> list[str]:
    return [m for m in ctx.captured_logs if _KIND in m.lower() and "gated" in m.lower()]


@then("the m365 email-headers connector is gated off in the loop")
def _gated_off(m365_email_headers_flag_ctx: _Ctx) -> None:
    assert _gated_logs(m365_email_headers_flag_ctx), (
        f"expected a '{_KIND} gated off' INFO log; got {m365_email_headers_flag_ctx.captured_logs!r}"
    )


@then("the m365 email-headers connector is not gated off in the loop")
def _not_gated_off(m365_email_headers_flag_ctx: _Ctx) -> None:
    assert not _gated_logs(m365_email_headers_flag_ctx), (
        f"flag ON must NOT gate off {_KIND}; gated logs fired anyway: {_gated_logs(m365_email_headers_flag_ctx)!r}"
    )


@then("the flagless sibling connector still syncs its notes for m365 email-headers")
def _sibling_syncs(m365_email_headers_flag_ctx: _Ctx) -> None:
    result = m365_email_headers_flag_ctx.result
    assert result is not None, "When step must run before Then"
    assert result.synced >= 2, f"flagless obsidian sibling must still sync its notes; got {result}"
