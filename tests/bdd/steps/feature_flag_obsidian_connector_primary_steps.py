"""Step definitions for feature_flag_obsidian_connector_primary.feature.

Drives the production :func:`kairix.worker.dispatch_connector_sync`
composition surface with the flag value pinned through the canonical
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``. No
``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point in their call graph
(depth ≤ 2). ``dispatch_connector_sync`` delegates to either
:func:`run_connector_sync_pipeline` (factory-composed pipeline path)
or :func:`run_via_legacy_document_scanner` (legacy
``DocumentScanner``); no direct ``*Pipeline(...)`` construction in
this step file.

The branch selection is observed via the distinct INFO log each
helper emits at entry — that's the operator-visible signal a real
deploy uses to confirm which branch ran. Legacy-branch I/O is
sandboxed via :class:`LegacyScannerDeps` rooted at a tmp_path
``FakePaths`` document_root + a tmp_path SQLite DB so the BDD
scenario never touches the dev's real vault.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.worker import (
    ConnectorSyncResult,
    LegacyScannerDeps,
    dispatch_connector_sync,
    run_via_connector_pipeline,
    run_via_legacy_document_scanner,
)
from tests.fakes import FakeFeatureFlagResolver, FakePaths

pytestmark = pytest.mark.bdd

_CONNECTOR_BRANCH_MARKER = "routing via obsidian connector pipeline"
_LEGACY_BRANCH_MARKER = "routing via legacy document scanner"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    paths: Any = None
    resolver: FakeFeatureFlagResolver | None = None
    captured_logs: list[str] | None = None
    branch_result: ConnectorSyncResult | None = None


@pytest.fixture
def flag_ctx(tmp_path: Path) -> _Ctx:
    """Build a clean per-scenario context with FakePaths over tmp_path.

    Seeds an empty document_root + a real SQLite schema so the legacy
    branch's ``DocumentScanner`` returns zero counters cleanly rather
    than scanning the dev's home directory.
    """
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    paths.document_root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(paths.db_path), timeout=10.0)
    create_schema(db)
    db.close()
    return _Ctx(paths=paths)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the obsidian-connector-primary flag set to {value}"))
def _operator_sets_flag(flag_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`.

    The fake resolver is the F2/F4-clean seam for declaring a flag's
    effective value — it never touches ``kairix.config.yaml`` or
    ``KAIRIX_FEATURE_*`` env vars.
    """
    parsed = value.strip().lower() == "true"
    flag_ctx.resolver = FakeFeatureFlagResolver().with_flag("obsidian_connector_primary", parsed)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker connector sync tick runs")
def _worker_sync_tick_runs(flag_ctx: _Ctx, caplog: pytest.LogCaptureFixture) -> None:
    """Invoke the production :func:`dispatch_connector_sync` with the
    fake resolver pinned through the ``read_flag`` seam.

    Legacy-branch I/O is sandboxed via :class:`LegacyScannerDeps`
    rooted at the fixture's tmp_path so the scanner sees an empty
    (existent) document root and short-circuits to zero counters
    without reading any of the dev's real markdown files.

    The connector-pipeline branch reads its config via the production
    :func:`run_connector_sync_pipeline` default deps — which, with no
    ``kairix.config.yaml`` present in tmp_path, short-circuits to a
    zero-counter result too. Either way the assertion target is the
    branch-identifier log line, not the counters.
    """
    resolver = flag_ctx.resolver
    paths = flag_ctx.paths
    assert resolver is not None, "Given step must run before When"

    legacy_deps = LegacyScannerDeps(
        document_root_resolver=lambda: paths.document_root,
        db_factory=lambda: sqlite3.connect(str(paths.db_path)),
    )

    def _sandboxed_legacy() -> ConnectorSyncResult:
        return run_via_legacy_document_scanner(legacy_deps)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        flag_ctx.branch_result = dispatch_connector_sync(
            read_flag=resolver.get,
            on_branch=run_via_connector_pipeline,
            off_branch=_sandboxed_legacy,
        )

    flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _has_marker(logs: list[str] | None, marker: str) -> bool:
    """Return True when ``marker`` appears in any captured log line."""
    return any(marker in line for line in (logs or []))


@then("the legacy document scanner branch performs the indexing pass")
def _legacy_branch_runs(flag_ctx: _Ctx) -> None:
    assert _has_marker(flag_ctx.captured_logs, _LEGACY_BRANCH_MARKER), (
        f"expected the legacy DocumentScanner branch log; got {flag_ctx.captured_logs!r}"
    )


@then("the obsidian connector pipeline branch performs the indexing pass")
def _connector_branch_runs(flag_ctx: _Ctx) -> None:
    assert _has_marker(flag_ctx.captured_logs, _CONNECTOR_BRANCH_MARKER), (
        f"expected the connector pipeline branch log; got {flag_ctx.captured_logs!r}"
    )


@then("the obsidian connector pipeline branch does not run")
def _connector_branch_skipped(flag_ctx: _Ctx) -> None:
    assert not _has_marker(flag_ctx.captured_logs, _CONNECTOR_BRANCH_MARKER), (
        f"expected the connector pipeline branch to NOT run; got {flag_ctx.captured_logs!r}"
    )


@then("the legacy document scanner branch does not run")
def _legacy_branch_skipped(flag_ctx: _Ctx) -> None:
    assert not _has_marker(flag_ctx.captured_logs, _LEGACY_BRANCH_MARKER), (
        f"expected the legacy DocumentScanner branch to NOT run; got {flag_ctx.captured_logs!r}"
    )
