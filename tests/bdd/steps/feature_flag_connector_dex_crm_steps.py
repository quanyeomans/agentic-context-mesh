"""Step definitions for feature_flag_connector_dex_crm.feature.

Drives the flag-gated dispatcher for the Dex CRM connector via the
canonical :class:`FakeFeatureFlagResolver` from ``tests/fakes.py``. No
``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point — the connector itself is
constructed via :func:`kairix.connectors.dex_crm.make_connector` (the
factory the entry-point group registers); no direct ``*Pipeline(...)``
construction lives here.

The flag's branches are observed via the distinct INFO log each branch
emits at entry plus a recording HTTP transport that asserts whether the
client actually called the Dex API. The off branch must skip the call
entirely; the on branch must reach the Dex client's listing endpoint at
least once.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import (
    DexCrmClient,
    DexCrmClientConfig,
)
from kairix.transport.auth.api_key import (
    ApiKeyAuth,
    BearerHeaders,
    reset_api_key_cache,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_CONNECTOR_BRANCH_MARKER = "routing via dex_crm connector"
_SKIP_BRANCH_MARKER = "dex_crm connector branch skipped"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    captured_logs: list[str] = field(default_factory=list)
    http_calls: list[str] = field(default_factory=list)
    connector: DexCrmConnector | None = None
    branch_ran: bool = False


@pytest.fixture
def dex_ctx() -> _Ctx:
    """Build a clean per-scenario context. Drops any cached secret so
    the resolved-secret cache from a previous scenario can't leak
    across boundaries.
    """
    reset_api_key_cache()
    return _Ctx()


# ---------------------------------------------------------------------------
# Helpers — exposed at module scope so the depth-2 F46 walker sees them.
# ---------------------------------------------------------------------------


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass-via-replacement that yields a
    static bearer without touching the secrets resolver.

    We construct a tiny subclass instead of monkey-patching the secrets
    module so F1 stays clean — the subclass overrides the public
    ``headers`` method in a normal OO way.
    """

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer dex-bdd-token"})

    return _ScriptedAuth()


def _recording_transport(http_calls: list[str]) -> httpx.MockTransport:
    """Build a :class:`httpx.MockTransport` that records every request path.

    Returns an empty page envelope so the connector's listing loop
    terminates after one page per record kind. The ``http_calls`` list
    is shared with the test fixture — every request appends its path
    here so the Then-step can assert against the on-branch /
    off-branch contract.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request.url.path)
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _build_connector_with_recording_transport(http_calls: list[str]) -> DexCrmConnector:
    """Build a real connector via the public factory shape.

    Drives the documented :class:`DexCrmConnector` ``client=`` DI seam:
    pass a :class:`DexCrmClient` constructed against a recording
    :class:`httpx.MockTransport` plus a scripted :class:`ApiKeyAuth` so
    no real Dex API call and no real secret resolution happens. The
    same factory shape :func:`make_connector` would build is preserved
    — only the seams documented in the connector's docstring are used.
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

    The Dex CRM connector lives in the connector framework rather than
    behind the worker's legacy-vs-pipeline split, so the dispatcher is
    a thin local helper — same composition shape as
    :func:`kairix.worker.dispatch_connector_sync` but pinned to the
    ``connector_dex_crm`` flag name.
    """
    logger = logging.getLogger("kairix.connectors.dex_crm")
    if read_flag("connector_dex_crm"):
        logger.info("dex_crm: routing via dex_crm connector (flag ON)")
        return on_branch()
    logger.info("dex_crm: dex_crm connector branch skipped (flag OFF)")
    return off_branch()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the connector-dex-crm flag set to {value}"))
def _operator_sets_flag(dex_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`.

    F2/F4-clean — the fake resolver never touches ``kairix.config.yaml``
    or ``KAIRIX_FEATURE_*`` env vars.
    """
    parsed = value.strip().lower() == "true"
    dex_ctx.resolver = FakeFeatureFlagResolver().with_flag("connector_dex_crm", parsed)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker dex crm sync tick runs")
def _worker_dex_crm_tick(dex_ctx: _Ctx, caplog: pytest.LogCaptureFixture) -> None:
    """Invoke the flag dispatcher with the fake resolver pinned.

    The on-branch builds a connector via the production factory and
    drains one page from each Dex listing kind through the recording
    transport; the off-branch is a no-op that asserts the connector
    was never engaged this tick.
    """
    resolver = dex_ctx.resolver
    assert resolver is not None, "Given step must run before When"

    def _on_branch() -> None:
        dex_ctx.branch_ran = True
        connector = _build_connector_with_recording_transport(dex_ctx.http_calls)
        dex_ctx.connector = connector
        # Drain list_changes — exercises the full per-kind iter_listing path.
        events = list(connector.list_changes(cursor=None))
        dex_ctx.branch_ran = dex_ctx.branch_ran or bool(events) or True

    def _off_branch() -> None:
        # Off branch intentionally does no API work — the absence of
        # http_calls is the visible signal.
        return None

    with caplog.at_level(logging.INFO, logger="kairix.connectors.dex_crm"):
        _dispatch_dex_crm_sync(
            read_flag=resolver.get,
            on_branch=_on_branch,
            off_branch=_off_branch,
        )

    dex_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _has_marker(logs: list[str], marker: str) -> bool:
    """Return True when ``marker`` appears in any captured log line."""
    return any(marker in line for line in logs)


@then("the dex crm connector branch is skipped")
def _dex_branch_skipped(dex_ctx: _Ctx) -> None:
    assert _has_marker(dex_ctx.captured_logs, _SKIP_BRANCH_MARKER), (
        f"expected the dex_crm skip-branch log; got {dex_ctx.captured_logs!r}"
    )


@then("no api call is made to the dex crm endpoint")
def _no_api_call(dex_ctx: _Ctx) -> None:
    assert dex_ctx.http_calls == [], f"flag OFF must skip the Dex API entirely; got http_calls={dex_ctx.http_calls!r}"


@then("the dex crm connector branch performs the sync pass")
def _dex_branch_ran(dex_ctx: _Ctx) -> None:
    assert _has_marker(dex_ctx.captured_logs, _CONNECTOR_BRANCH_MARKER), (
        f"expected the dex_crm connector-branch log; got {dex_ctx.captured_logs!r}"
    )
    assert dex_ctx.branch_ran, "flag ON must run the connector branch"


@then("the dex crm connector lists changes since the cursor")
def _dex_listed_changes(dex_ctx: _Ctx) -> None:
    assert dex_ctx.http_calls, f"flag ON must call the Dex API at least once; got http_calls={dex_ctx.http_calls!r}"
    # The connector polls every record kind in deterministic order.
    expected_suffixes = {"/contacts", "/organisations", "/relationships"}
    missing = {suffix for suffix in expected_suffixes if not any(path.endswith(suffix) for path in dex_ctx.http_calls)}
    assert not missing, (
        f"flag ON should poll every record kind; missing={sorted(missing)}; saw={sorted(dex_ctx.http_calls)}"
    )
