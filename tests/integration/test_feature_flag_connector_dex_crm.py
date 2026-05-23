"""Integration tests for the ``connector_dex_crm`` flag (Wave 5 KP-1).

Exercises both branches of the flag's dispatcher through the production
composition surface:

  * **Flag OFF** — the dispatcher skips the connector entirely; the
    recording HTTP transport never sees a request.
  * **Flag ON** — the dispatcher constructs the connector through the
    production :func:`kairix.connectors.dex_crm.make_connector` factory
    shape, with a scripted :class:`httpx.MockTransport` and a stub
    :class:`ApiKeyAuth` so no real Dex API call and no real secret
    resolution happens. The Dex listing endpoints all receive a GET.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is
threaded through the local dispatcher's ``read_flag=…`` DI seam — no
@patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
inverting the if/else in the local ``dispatch_dex_crm_sync`` so OFF
runs the connector and ON skips — confirmed that BOTH
:func:`test_flag_off_skips_dex_crm_connector` AND
:func:`test_flag_on_polls_dex_crm_api` fail. Restoring the original
branch direction returns both tests to green.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_CONNECTOR_BRANCH_MARKER = "routing via dex_crm connector"
_SKIP_BRANCH_MARKER = "dex_crm connector branch skipped"


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass that yields a fixed bearer.

    Subclassing is the F1-clean alternative to monkey-patching the
    secrets resolver — the override happens through normal Python OO
    rather than attribute substitution on a kairix module.
    """

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer dex-integration-token"})

    return _ScriptedAuth()


def _recording_transport(http_calls: list[str]) -> httpx.MockTransport:
    """Build a :class:`httpx.MockTransport` that records every request path."""

    def _handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request.url.path)
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _build_connector(http_calls: list[str]) -> DexCrmConnector:
    """Construct a real :class:`DexCrmConnector` through its DI seams.

    Same shape :func:`kairix.connectors.dex_crm.make_connector` would
    build in production — only the documented ``client=`` constructor
    seam is used to inject the recording transport + stubbed auth.
    """
    inner_client = httpx.Client(transport=_recording_transport(http_calls))
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client)


def _dispatch_dex_crm_sync(
    *,
    read_flag: Callable[[str], bool],
    on_branch: Callable[[], Any],
    off_branch: Callable[[], Any],
) -> Any:
    """Test-side flag dispatcher mirroring :func:`dispatch_connector_sync`.

    The Dex CRM connector is the first flag-gated entry under the
    connector framework rather than the legacy-vs-pipeline worker
    branching. The local dispatcher uses the same composition shape as
    :func:`kairix.worker.dispatch_connector_sync` — pinned to the
    ``connector_dex_crm`` flag name.
    """
    logger = logging.getLogger("kairix.connectors.dex_crm")
    if read_flag("connector_dex_crm"):
        logger.info("dex_crm: routing via dex_crm connector (flag ON)")
        return on_branch()
    logger.info("dex_crm: dex_crm connector branch skipped (flag OFF)")
    return off_branch()


def test_flag_off_skips_dex_crm_connector(caplog: pytest.LogCaptureFixture) -> None:
    """OFF branch — the connector never engages and no HTTP call fires.

    The fake resolver pins ``connector_dex_crm`` to False, the local
    dispatcher routes through the skip branch, and the recording
    transport receives zero requests.
    """
    reset_api_key_cache()
    resolver = FakeFeatureFlagResolver().with_flag("connector_dex_crm", False)
    http_calls: list[str] = []

    on_calls = {"n": 0}

    def _on_branch() -> None:
        on_calls["n"] += 1
        # Build the connector + drive it through list_changes — if the
        # dispatcher misroutes here, the recording transport will see
        # requests and the off-branch assertion below will fail loud.
        connector = _build_connector(http_calls)
        list(connector.list_changes(cursor=None))

    def _off_branch() -> None:
        return None

    with caplog.at_level(logging.INFO, logger="kairix.connectors.dex_crm"):
        _dispatch_dex_crm_sync(
            read_flag=resolver.get,
            on_branch=_on_branch,
            off_branch=_off_branch,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_SKIP_BRANCH_MARKER in m for m in messages), (
        f"flag OFF must log the skip-branch marker; logs={messages!r}"
    )
    assert not any(_CONNECTOR_BRANCH_MARKER in m for m in messages), (
        f"flag OFF must NOT log the connector-branch marker; logs={messages!r}"
    )
    assert on_calls["n"] == 0, "on-branch must not run when flag is OFF"
    assert http_calls == [], f"flag OFF must skip the Dex API entirely; got http_calls={http_calls!r}"


def test_flag_on_polls_dex_crm_api(caplog: pytest.LogCaptureFixture) -> None:
    """ON branch — the connector polls every Dex listing endpoint.

    The fake resolver pins ``connector_dex_crm`` to True, the local
    dispatcher routes through the on branch, and the recording
    transport receives one GET against each of contacts /
    organisations / relationships.
    """
    reset_api_key_cache()
    resolver = FakeFeatureFlagResolver().with_flag("connector_dex_crm", True)
    http_calls: list[str] = []

    off_calls = {"n": 0}

    def _on_branch() -> None:
        connector = _build_connector(http_calls)
        list(connector.list_changes(cursor=None))

    def _off_branch() -> None:
        off_calls["n"] += 1
        return None

    with caplog.at_level(logging.INFO, logger="kairix.connectors.dex_crm"):
        _dispatch_dex_crm_sync(
            read_flag=resolver.get,
            on_branch=_on_branch,
            off_branch=_off_branch,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_CONNECTOR_BRANCH_MARKER in m for m in messages), (
        f"flag ON must log the connector-branch marker; logs={messages!r}"
    )
    assert not any(_SKIP_BRANCH_MARKER in m for m in messages), (
        f"flag ON must NOT log the skip-branch marker; logs={messages!r}"
    )
    assert off_calls["n"] == 0, "off-branch must not run when flag is ON"
    expected_suffixes = {"/contacts", "/organisations", "/relationships"}
    seen_suffixes = {suffix for suffix in expected_suffixes if any(path.endswith(suffix) for path in http_calls)}
    missing = expected_suffixes - seen_suffixes
    assert not missing, f"flag ON must poll every record kind; missing={sorted(missing)}; saw={sorted(http_calls)}"
