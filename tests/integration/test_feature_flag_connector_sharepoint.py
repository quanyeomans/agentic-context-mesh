"""Integration tests for the ``connector_sharepoint`` flag.

Exercises both branches of
:func:`kairix.worker.dispatch_sharepoint_sync` through the production
composition surface:

  * **Flag OFF** — the connector slot is a no-op; the off-branch
    helper returns zero counters and emits the OFF-branch INFO log.
    The ON branch is wrapped with a never-call stub; a misroute
    increments its counter and the assertion fails loudly.
  * **Flag ON** — the ON branch fires; the OFF branch is wrapped
    with a never-call stub. The ON branch's marker INFO log appears.

F47 — both branches are reached via the production
``dispatch_sharepoint_sync`` entry point; no direct
``*Pipeline(...)`` construction in this file.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through the production ``dispatch_sharepoint_sync(read_flag=...)``
DI seam — no @patch / module-attribute substitution on kairix.

Sabotage proof (executed by the agent, restored on completion):
inverting the if/else in :func:`dispatch_sharepoint_sync` so OFF runs
the ON branch and ON runs the OFF branch — confirmed that BOTH
:func:`test_sharepoint_flag_off_branch_runs` AND
:func:`test_sharepoint_flag_on_branch_runs` fail. Restoring the
original branch direction returns both tests to green.
"""

from __future__ import annotations

import logging

import pytest

from kairix.worker import (
    ConnectorSyncResult,
    dispatch_sharepoint_sync,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_ON_MARKER = "sharepoint connector running (flag ON)"
_OFF_MARKER = "sharepoint connector gated off (flag OFF)"


def test_sharepoint_flag_off_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """OFF branch — sharepoint connector slot is a no-op; ON does not fire.

    The fake resolver pins ``connector_sharepoint`` to False, the
    production :func:`dispatch_sharepoint_sync` routes through the OFF
    branch, the OFF marker INFO log appears, and the ON branch's
    never-call stub records zero invocations.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", False)

    on_calls = {"n": 0}

    def _never_on() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=99, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_sharepoint_sync(
            read_flag=resolver.get,
            on_branch=_never_on,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_OFF_MARKER in m for m in messages), f"flag OFF must route through the OFF branch; logs={messages!r}"
    assert not any(_ON_MARKER in m for m in messages), (
        f"flag OFF must NOT route through the ON branch; logs={messages!r}"
    )
    assert on_calls["n"] == 0, "ON branch must not run when flag is OFF"
    assert result.synced == 0, f"OFF branch must return zero counters; got {result}"


def test_sharepoint_flag_on_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """ON branch — sharepoint connector ON branch helper fires; OFF does not.

    The fake resolver pins ``connector_sharepoint`` to True, the
    production :func:`dispatch_sharepoint_sync` routes through the ON
    branch helper, the ON marker INFO log appears, and the OFF
    branch's never-call stub records zero invocations.

    The ON branch is wrapped with a marker-emitting stub here so the
    integration test doesn't drive a real connector-pipeline run
    (that's the E2E test's job). The branch selection — gated by the
    flag — is the property under test.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_sharepoint", True)

    off_calls = {"n": 0}

    def _never_off() -> ConnectorSyncResult:
        off_calls["n"] += 1
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_on() -> ConnectorSyncResult:
        logging.getLogger("kairix.worker").info(_ON_MARKER)
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_sharepoint_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_never_off,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_ON_MARKER in m for m in messages), f"flag ON must route through the ON branch; logs={messages!r}"
    assert not any(_OFF_MARKER in m for m in messages), (
        f"flag ON must NOT route through the OFF branch; logs={messages!r}"
    )
    assert off_calls["n"] == 0, "OFF branch must not run when flag is ON"
    assert result.synced == 1, f"ON branch must have run and returned its result; got {result}"


# ---------------------------------------------------------------------------
# Path filtering — both filter-state branches against the same flag-ON setup
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
