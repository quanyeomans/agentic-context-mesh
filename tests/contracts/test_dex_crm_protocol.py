"""Contract test for the Dex CRM connector plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeDexCrmConnector`)
AND the real implementation
(:class:`kairix.connectors.dex_crm.DexCrmConnector`) through the same
:class:`~kairix.core.protocols.SourceConnector` Protocol assertions.

F43 requires this pairing — without it the fake can drift away from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Real-impl path is driven against an in-process
:class:`httpx.MockTransport` plus a subclassed :class:`ApiKeyAuth` that
returns a fixed bearer — no real Dex API call and no real secret
resolution happens.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeDexCrmConnector

_SCRIPTED_CONTACT = {
    "id": "c-200",
    "updated_at": "2026-05-22T11:00:00Z",
    "first_name": "agent-gamma",
}


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass yielding a fixed bearer."""

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer dex-contract-token"})

    return _ScriptedAuth()


def _scripted_transport() -> httpx.MockTransport:
    """Build a transport returning one contact + empty other listings."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — pre-seeded events + cached content."""
    return FakeDexCrmConnector(
        events=[
            ChangeEvent(op="modified", item_id="contact:c-200", modified_at="2026-05-22T11:00:00Z"),
        ],
        content={"contact:c-200": _SCRIPTED_CONTACT},
    )


def _real_factory() -> SourceConnector:
    """Real-impl factory — drives a recording transport."""
    reset_api_key_cache()
    inner = httpx.Client(transport=_scripted_transport())
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client)


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_dex_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: removing ``list_changes`` from
    :class:`DexCrmConnector` flips the real-impl isinstance check to
    False; deleting the corresponding attribute from
    :class:`FakeDexCrmConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "dex_crm"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_dex_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream :class:`ChangeEvent` instances.

    Sabotage-proof: the real impl mutated to return ``[None]`` from
    ``list_changes`` flunks the isinstance loop below; the fake
    mutated to yield ``{"op": "modified"}`` dicts flunks the same loop.
    """
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "deleted")


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_dex_connector_fetch_returns_raw_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape.

    Sabotage-proof: returning a tuple from ``fetch`` instead breaks the
    isinstance assertion for both impls.
    """
    connector = factory()
    # Need to pre-populate the real impl's cache via list_changes.
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("contact:c-200")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    assert b"c-200" in artefact.raw
    assert artefact.fetched_at.endswith("Z") or "+" in artefact.fetched_at


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_dex_connector_source_link_round_trips_to_getdex_scheme(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """``source_link`` returns an ``app.getdex.com`` URL on both impls."""
    connector = factory()
    link = connector.source_link("contact:c-200")
    assert link.startswith("https://app.getdex.com/contacts/"), f"{name!r} produced unexpected link: {link!r}"
    assert "c-200" in link, f"{name!r} link does not carry record id: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_dex_connector_sensitivity_for_returns_configured_tier(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """``sensitivity_for`` returns the connector's configured tier — defaults to ``internal``."""
    connector = factory()
    tier = connector.sensitivity_for("contact:c-200")
    assert tier == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"
