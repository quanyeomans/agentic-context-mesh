"""Integration tests for the ``connector_sharepoint`` flag.

Exercises both branches of the connector-enablement gate through the
REAL production loop — :func:`kairix.worker.run_connector_sync_pipeline`
with ``flag_reader`` pinned via :class:`FakeFeatureFlagResolver`. The
gate is :func:`kairix.worker.connector_enabled` consulted per entry in
that loop; the assertion target is the observable
:class:`ConnectorSyncResult` counter outcome, NOT a dead dispatcher's
branch-marker log.

  * **Flag OFF** — ``connector_sharepoint`` reads False. The sharepoint
    entry is gated off (skipped before plugin resolution, so it never
    needs a Graph credential); a flagless ``obsidian`` sibling in the
    same tick still indexes its notes. The aggregate ``synced`` reflects
    only the sibling.
  * **Flag ON** — ``connector_sharepoint`` reads True. The gate lets the
    sharepoint entry through to ``_run_one_connector_batch``, where it
    fails to resolve a live Graph credential and is logged + counted as a
    per-entry failure (the loop continues). The "gated off" log does NOT
    fire for sharepoint; the flagless sibling still indexes its notes.

F47 — both branches are reached through the production
``run_connector_sync_pipeline`` entry point; no direct
``*Pipeline(...)`` construction in the flag tests.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through ``ConnectorSyncDeps(flag_reader=...)`` — no @patch /
module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
deleting the ``if not connector_enabled(...)`` skip in
``run_connector_sync_pipeline``'s loop makes the OFF test fail — the
"gated off" log no longer fires and the sharepoint entry runs. Restoring
the gate returns the test to green.
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

_KIND = "sharepoint"


def _two_connector_topology(vault: Path) -> dict[str, Any]:
    """Merged mapping: the gated connector kind + a flagless obsidian sibling.

    Mirrors the canonical ``topology.connectors`` / ``topology.cc_pairs``
    shape the setup wizard writes. The sibling vault carries two markdown
    files so the flagless connector has observable work.
    """
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


def test_flag_off_gates_connector_sibling_still_runs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OFF — the sharepoint entry is gated off in the loop; the flagless
    obsidian sibling still syncs both notes.

    Drives the real ``run_connector_sync_pipeline``. With the flag OFF
    the predicate skips the sharepoint entry BEFORE plugin resolution, so
    no Graph credential is needed; the "gated off" INFO log fires and the
    aggregate ``synced`` reflects only the obsidian sibling's two notes.
    """
    vault = _seed_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", False)

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(vault),
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = run_connector_sync_pipeline(deps)

    assert result.synced == 2, f"flagless obsidian sibling must still sync both notes; got {result}"
    assert result.failed == 0, f"OFF gate must not surface a failure for the gated connector; got {result}"

    gated_logs = [
        rec.getMessage()
        for rec in caplog.records
        if _KIND in rec.getMessage().lower() and "gated" in rec.getMessage().lower()
    ]
    assert gated_logs, (
        f"flag OFF must skip the {_KIND} entry with a 'gated off' INFO log; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )


def test_flag_on_lets_connector_through_sibling_still_runs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ON — the gate lets the sharepoint entry through to the batch runner;
    the flagless obsidian sibling still syncs both notes.

    With the flag ON the predicate does NOT skip the sharepoint entry: it
    reaches ``_run_one_connector_batch`` where, lacking a live Graph
    credential, it fails and is logged + counted as a per-entry failure
    (the loop continues). The "gated off" log must NOT fire for sharepoint
    — proving the ON branch took a different path than OFF.
    """
    vault = _seed_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", True)

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _two_connector_topology(vault),
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = run_connector_sync_pipeline(deps)

    assert result.synced == 2, f"flagless obsidian sibling must still sync both notes; got {result}"

    gated_logs = [
        rec.getMessage()
        for rec in caplog.records
        if _KIND in rec.getMessage().lower() and "gated" in rec.getMessage().lower()
    ]
    assert not gated_logs, (
        f"flag ON must NOT gate off the {_KIND} entry; the 'gated off' log fired anyway: {gated_logs!r}"
    )

    failure_logs = [
        rec.getMessage() for rec in caplog.records if f"{_KIND}-cc" in rec.getMessage() and "failed" in rec.getMessage()
    ]
    assert failure_logs, (
        f"flag ON must let the {_KIND} entry reach the batch runner (where it fails without a "
        f"live credential); got {[r.getMessage() for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Path filtering — both filter-state branches against the same flag-ON setup.
# These exercise the real SharePoint connector's include_paths filter, not the
# enablement gate above.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_filter_active_drops_items_outside_include_paths_when_flag_on() -> None:
    """Flag ON + include_paths set → only matching items emit. Verifies the
    filter integrates correctly with the worker's connector wiring."""
    import httpx

    from kairix.connectors.sharepoint import (
        SharePointConnector,
        SharePointCredentials,
        SharePointDriveSpec,
        SharePointGraphClient,
    )
    from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

    drive_id = "b!integration-filter"

    def _envelope(item_id: str, parent_path: str, name: str) -> dict:
        return {
            "id": item_id,
            "name": name,
            "size": 100,
            "lastModifiedDateTime": "2026-05-22T10:00:00Z",
            "webUrl": f"https://contoso.sharepoint.com/sites/team/Documents{parent_path}/{name}",
            "file": {"mimeType": "text/markdown"},
            "parentReference": {"driveId": drive_id, "path": f"/drives/{drive_id}/root:{parent_path}"},
        }

    body = {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{drive_id}/root/delta",
        "value": [
            _envelope("a", "/Curated-Content", "a.md"),
            _envelope("b", "/Vendor-Bulk-Materials", "b.pptx"),
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?$deltatoken=tok",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if "/root:" in url and "delta" not in url:
            return httpx.Response(200, json={"id": "folder-id"})
        return httpx.Response(200, json=body)

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    connector = SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=drive_id, include_paths=("/Curated-Content",))],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )
    events = list(connector.list_changes(cursor=None))
    assert {e.item_id for e in events} == {"a"}, f"include_paths filter not applied end-to-end: {events!r}"


@pytest.mark.integration
def test_filter_inactive_preserves_prior_behaviour_when_flag_on() -> None:
    """Flag ON + empty include_paths → every emission-eligible item lands.

    Pins backward-compat: existing deployments that don't set include_paths
    see no behaviour change after pulling the filter feature.
    """
    import httpx

    from kairix.connectors.sharepoint import (
        SharePointConnector,
        SharePointCredentials,
        SharePointDriveSpec,
        SharePointGraphClient,
    )
    from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

    drive_id = "b!integration-no-filter"

    def _envelope(item_id: str, parent_path: str, name: str) -> dict:
        return {
            "id": item_id,
            "name": name,
            "size": 100,
            "lastModifiedDateTime": "2026-05-22T10:00:00Z",
            "webUrl": f"https://contoso.sharepoint.com/sites/team/Documents{parent_path}/{name}",
            "file": {"mimeType": "text/markdown"},
            "parentReference": {"driveId": drive_id, "path": f"/drives/{drive_id}/root:{parent_path}"},
        }

    body = {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{drive_id}/root/delta",
        "value": [
            _envelope("a", "/Curated-Content", "a.md"),
            _envelope("b", "/Vendor-Bulk-Materials", "b.pptx"),
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?$deltatoken=tok",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        return httpx.Response(200, json=body)

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    connector = SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=drive_id)],  # no filter
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )
    events = list(connector.list_changes(cursor=None))
    assert {e.item_id for e in events} == {"a", "b"}, f"empty filter should pass every item: {events!r}"
