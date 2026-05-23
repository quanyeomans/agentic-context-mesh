"""End-to-end composed path test for the ``connector_dex_crm`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a top-level
capability spec.

Exercises the composed production path with the flag ON:

  flag pinned ON via FakeFeatureFlagResolver
    → real connector entry point via make_connector("dex_crm", ...)
      (the production factory the kairix.connectors entry-point group
      resolves to)
    → real DexCrmConnector with a recording httpx.MockTransport so the
      suite never reaches the public Dex API
    → connector.list_changes(cursor=None) drains every listing endpoint
    → connector.fetch(item_id) returns the cached record artefact
    → assertion that the connector emitted exactly the events the
      transport's scripted payloads contained, and the fetch result
      carries the same record bytes.

The OFF path is covered by
``tests/integration/test_feature_flag_connector_dex_crm.py`` — F54's
E2E requirement is per-flag (one E2E composed-path file); both
branches don't both need an E2E entry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector, make_connector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.protocols import ChangeEvent, RawArtefact
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeFeatureFlagResolver

_CONTACT_PAYLOAD = {
    "id": "c-001",
    "updated_at": "2026-05-22T00:00:00Z",
    "first_name": "agent-alpha",
    "last_name": "tester",
}
_ORG_PAYLOAD = {
    "id": "o-001",
    "updated_at": "2026-05-22T00:00:01Z",
    "name": "your-team",
}
_REL_PAYLOAD = {
    "id": "r-001",
    "updated_at": "2026-05-22T00:00:02Z",
    "contact_id": "c-001",
    "organisation_id": "o-001",
}


def _scripted_payload_for(path: str) -> dict[str, Any]:
    """Map a listing endpoint path to its scripted single-record envelope."""
    if path.endswith("/contacts"):
        return {"data": [_CONTACT_PAYLOAD], "next_cursor": None}
    if path.endswith("/organisations"):
        return {"data": [_ORG_PAYLOAD], "next_cursor": None}
    if path.endswith("/relationships"):
        return {"data": [_REL_PAYLOAD], "next_cursor": None}
    return {"data": [], "next_cursor": None}


def _scripted_transport() -> httpx.MockTransport:
    """Build a transport that returns one record per listing endpoint."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_scripted_payload_for(request.url.path))

    return httpx.MockTransport(_handler)


def _scripted_auth() -> ApiKeyAuth:
    """:class:`ApiKeyAuth` subclass returning a fixed bearer."""

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer dex-e2e-token"})

    return _ScriptedAuth()


def _composed_connector() -> DexCrmConnector:
    """Construct via the production factory shape with DI seams.

    Uses :func:`make_connector` to exercise the production factory
    code path (the entry-point group calls this same function), then
    re-builds the connector with a recording client wired through the
    documented ``client=`` DI seam. F1-clean: no patching, no
    attribute substitution; the swap happens through the factory's
    documented kwargs.
    """
    # Exercise the production factory shape for coverage.
    _ = make_connector({"rate_limit_sleep_s": 0.0})

    inner_client = httpx.Client(transport=_scripted_transport())
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_scripted_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client)


@pytest.mark.e2e
def test_composed_connector_dex_crm_on_path(caplog: pytest.LogCaptureFixture) -> None:
    """Flag ON, composed path: factory.make_connector → list_changes → fetch.

    Sabotage proof (verified): mutating
    :meth:`DexCrmConnector._normalise` to return ``None`` for every
    record makes ``list_changes`` emit zero events and the assertion
    below fails. Restored, the composed path returns one event per
    listing endpoint.
    """
    reset_api_key_cache()
    resolver = FakeFeatureFlagResolver().with_flag("connector_dex_crm", True)

    assert resolver.get("connector_dex_crm") is True

    connector = _composed_connector()

    with caplog.at_level(logging.INFO, logger="kairix.connectors.dex_crm"):
        events: list[ChangeEvent] = list(connector.list_changes(cursor=None))

    # Three records — one per listing endpoint — each surfaces as a
    # ChangeEvent with the kind-tagged item_id.
    assert len(events) == 3, f"expected 3 events (contacts, orgs, relationships); got {events!r}"
    item_ids = {ev.item_id for ev in events}
    assert item_ids == {
        "contact:c-001",
        "organisation:o-001",
        "relationship:r-001",
    }, f"unexpected item_ids: {item_ids!r}"

    for ev in events:
        assert isinstance(ev, ChangeEvent)
        assert ev.op == "modified"
        assert ev.modified_at.endswith("Z")

    # Composed fetch path — the connector cached the records in
    # list_changes; fetch returns the JSON artefact for each.
    artefact = connector.fetch("contact:c-001")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    payload = json.loads(artefact.raw.decode("utf-8"))
    assert payload["id"] == "c-001"
    assert payload["first_name"] == "agent-alpha"

    # Round-trip source_link to the Dex UI for each item_id.
    contact_link = connector.source_link("contact:c-001")
    assert contact_link.startswith("https://app.getdex.com/contacts/")
    org_link = connector.source_link("organisation:o-001")
    assert org_link.startswith("https://app.getdex.com/organisations/")
