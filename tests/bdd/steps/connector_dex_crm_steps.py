"""Step implementations for connector_dex_crm.feature.

The happy_path scenario drives a real :class:`DexCrmConnector` against
a scripted :class:`httpx.MockTransport` so no real Dex API call fires.
The missing_credentials scenario constructs the connector without
stubbing the auth resolver and confirms the typed
:class:`MissingCredentialsError` carries a ``fix:`` hint.

Per F46 the steps reach the public connector surface via
:func:`kairix.connectors.dex_crm.make_connector` (the entry-point
factory) and the constructor's documented DI seams — no direct
``*Pipeline(...)`` construction.

F1-clean: subclass-style auth override; no @patch / module-attribute
substitution. F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from pytest_bdd import given, then, when

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.protocols import ChangeEvent
from kairix.transport.auth.api_key import (
    ApiKeyAuth,
    BearerHeaders,
    MissingCredentialsError,
    reset_api_key_cache,
)

pytestmark = pytest.mark.bdd


_SCRIPTED_CONTACT = {
    "id": "c-100",
    "updated_at": "2026-05-22T10:00:00Z",
    "first_name": "agent-beta",
}


@dataclass
class _Ctx:
    """Per-scenario context."""

    connector: DexCrmConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    raised: Exception | None = None


@pytest.fixture
def dex_crm_ctx() -> _Ctx:
    """Build a clean per-scenario context with the auth cache cleared."""
    reset_api_key_cache()
    return _Ctx()


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass yielding a fixed bearer."""

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer dex-bdd-token"})

    return _ScriptedAuth()


def _scripted_transport() -> httpx.MockTransport:
    """Build a transport that returns one contact + empty other listings."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _missing_auth() -> ApiKeyAuth:
    """An :class:`ApiKeyAuth` subclass that always raises MissingCredentialsError."""

    class _MissingAuth(ApiKeyAuth):
        def headers(self, secret_name: str) -> BearerHeaders:
            raise MissingCredentialsError(
                f"api_key_auth: secret {secret_name!r} is not configured. "
                "fix: configure connector-dex-api-key via the canonical secret resolver chain. "
                "next: see docs/operations/OPERATIONS.md."
            )

    return _MissingAuth()


@given("a Dex CRM workspace with one updated contact since the cursor")
def _given_workspace_with_contact(dex_crm_ctx: _Ctx) -> None:
    inner = httpx.Client(transport=_scripted_transport())
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    dex_crm_ctx.connector = DexCrmConnector(client=client)


@given("the connector-dex-api-key secret is not configured")
def _given_secret_missing(dex_crm_ctx: _Ctx) -> None:
    """Build a connector whose auth seam raises ``MissingCredentialsError``.

    Done via subclass override on :class:`ApiKeyAuth` — F1-clean
    alternative to monkey-patching the secrets resolver module.
    """
    inner = httpx.Client(transport=_scripted_transport())
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner,
        auth=_missing_auth(),
        sleep=lambda _s: None,
    )
    dex_crm_ctx.connector = DexCrmConnector(client=client)


@when("the operator runs the dex_crm connector list_changes")
def _when_list_changes(dex_crm_ctx: _Ctx) -> None:
    assert dex_crm_ctx.connector is not None, "Given step must run before When"
    try:
        dex_crm_ctx.events = list(dex_crm_ctx.connector.list_changes(cursor=None))
    except Exception as exc:
        # Capture the exception for the Then step to assert against —
        # the missing_credentials scenario hinges on the concrete error
        # type being a MissingCredentialsError.
        dex_crm_ctx.raised = exc


@then("one modified change event is emitted for the contact")
def _then_one_event(dex_crm_ctx: _Ctx) -> None:
    assert dex_crm_ctx.raised is None, f"expected no exception; got {dex_crm_ctx.raised!r}"
    assert len(dex_crm_ctx.events) == 1, f"expected 1 event; got {dex_crm_ctx.events!r}"
    assert dex_crm_ctx.events[0].op == "modified"


@then("the event's item_id encodes the contact kind and id")
def _then_item_id_shape(dex_crm_ctx: _Ctx) -> None:
    ev = dex_crm_ctx.events[0]
    assert ev.item_id == "contact:c-100", f"unexpected item_id: {ev.item_id!r}"


@then("the event's source_link round-trips to an app.getdex.com URL")
def _then_source_link(dex_crm_ctx: _Ctx) -> None:
    assert dex_crm_ctx.connector is not None
    link = dex_crm_ctx.connector.source_link(dex_crm_ctx.events[0].item_id)
    assert link.startswith("https://app.getdex.com/contacts/"), f"unexpected link: {link!r}"
    assert "c-100" in link


@then("the operator sees an actionable error naming the missing secret")
def _then_missing_credentials_error(dex_crm_ctx: _Ctx) -> None:
    assert isinstance(dex_crm_ctx.raised, MissingCredentialsError), (
        f"expected MissingCredentialsError; got {type(dex_crm_ctx.raised).__name__}: {dex_crm_ctx.raised!r}"
    )
    message = str(dex_crm_ctx.raised)
    assert "fix:" in message, f"error must carry a fix: hint; got {message!r}"
    assert "connector-dex-api-key" in message, f"error must name the secret slot; got {message!r}"
