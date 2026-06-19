"""Step definitions for connector_linear.feature.

Drives the real :class:`kairix.connectors.linear.LinearConnector`
against a scripted :class:`tests.fakes.FakeLinearApiClient`. No real
network call — the fake returns one issue node so the behaviour
assertions can pin the typed ChangeEvent shape, the type-prefixed
item_id, the high-water-mark cursor, and the Markdown rendering.

Per F46, this step file reaches the connector through the real
constructor + the ``client_builder`` DI seam (depth <= 2). Direct
construction is permitted in BDD step files when the target is a
Protocol-compliant leaf such as ``LinearConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.linear import (
    LinearConnector,
    LinearCredentials,
)
from kairix.core.protocols import ChangeEvent, RawArtefact
from tests.fakes import FakeLinearApiClient

pytestmark = pytest.mark.bdd

_ISSUE_IDENTIFIER = "ENG-101"
_ISSUE_TITLE = "Ship the roadmap recall path"
_ISSUE_URL = "https://linear.app/your-team/issue/ENG-101"
_UPDATED_AT = "2026-05-22T10:00:00.000Z"


def _one_issue_node() -> dict[str, Any]:
    """One Linear issue node as the GraphQL surface returns it."""
    return {
        "id": "uuid-issue-0001",
        "identifier": _ISSUE_IDENTIFIER,
        "title": _ISSUE_TITLE,
        "description": "We need recall over the roadmap.",
        "url": _ISSUE_URL,
        "createdAt": "2026-05-20T09:00:00.000Z",
        "updatedAt": _UPDATED_AT,
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"displayName": "agent-alpha", "email": "agent-alpha@example.com"},
        "creator": {"displayName": "agent-beta", "email": "agent-beta@example.com"},
        "team": {"key": "ENG", "name": "Engineering"},
        "project": {"id": "uuid-project-0001", "name": "Roadmap recall"},
        "labels": {"nodes": [{"name": "roadmap"}]},
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    connector: LinearConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    artefact: RawArtefact | None = None


@pytest.fixture
def linear_ctx() -> _Ctx:
    return _Ctx()


def _build_connector() -> LinearConnector:
    """Construct the real connector wired to a scripted Linear API."""
    api = FakeLinearApiClient(pages={"issues": [[_one_issue_node()]]})
    return LinearConnector(
        credentials=LinearCredentials(api_key="lin_api_fixture_key"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _creds: api,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a stubbed Linear workspace that returns one changed issue"))
def _given_one_issue(linear_ctx: _Ctx) -> None:
    linear_ctx.connector = _build_connector()


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the linear connector list_changes with no cursor"))
def _when_list_changes(linear_ctx: _Ctx) -> None:
    assert linear_ctx.connector is not None, "Given step must run before When"
    linear_ctx.events = list(linear_ctx.connector.list_changes(cursor=None))


@when("the operator fetches the changed linear issue")
def _when_fetch(linear_ctx: _Ctx) -> None:
    assert linear_ctx.connector is not None
    assert linear_ctx.events, "list_changes must run (and emit) before fetch"
    linear_ctx.artefact = linear_ctx.connector.fetch(linear_ctx.events[0].item_id)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one linear modified change event is emitted")
def _one_modified_event(linear_ctx: _Ctx) -> None:
    events = linear_ctx.events
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events!r}"
    assert events[0].op == "modified", f"expected modified op, got {events[0]!r}"


@then("the linear change event item id is prefixed with the issue type")
def _event_prefixed(linear_ctx: _Ctx) -> None:
    item_id = linear_ctx.events[0].item_id
    assert item_id == f"issue:{_ISSUE_IDENTIFIER}", f"unexpected item_id: {item_id!r}"


@then("the linear change event carries an ISO-8601 modified_at timestamp")
def _event_has_iso(linear_ctx: _Ctx) -> None:
    event = linear_ctx.events[0]
    assert event.modified_at == _UPDATED_AT, f"event {event!r} modified_at mismatch"
    assert event.modified_at.endswith("Z"), f"modified_at not ISO-8601: {event.modified_at!r}"


@then("the linear change event's sensitivity tier is internal")
def _event_internal_tier(linear_ctx: _Ctx) -> None:
    tier = linear_ctx.events[0].metadata.get("sensitivity")
    assert tier == "internal", f"event sensitivity is not internal: {tier!r}"


@then("the linear connector exposes a non-empty next cursor")
def _connector_has_cursor(linear_ctx: _Ctx) -> None:
    assert linear_ctx.connector is not None
    cursor = linear_ctx.connector.next_cursor()
    assert cursor, f"expected non-empty next cursor, got {cursor!r}"


@then("the linear next cursor matches the highest updatedAt seen")
def _cursor_matches_high_water(linear_ctx: _Ctx) -> None:
    import json

    assert linear_ctx.connector is not None
    cursor = linear_ctx.connector.next_cursor()
    assert cursor is not None, "expected a next cursor after a clean drain"
    # The opaque cursor JSON-encodes a per-entity-type watermark map; the
    # issue type's watermark must equal the only issue's updatedAt.
    decoded = json.loads(cursor)
    assert decoded.get("issue") == _UPDATED_AT, f"issue watermark must equal the issue's updatedAt; got {decoded!r}"


@then("the fetched linear artefact is Markdown")
def _artefact_is_markdown(linear_ctx: _Ctx) -> None:
    assert linear_ctx.artefact is not None, "fetch step must run first"
    assert linear_ctx.artefact.mime == "text/markdown", f"unexpected mime: {linear_ctx.artefact.mime!r}"


@then("the fetched linear artefact contains the issue identifier")
def _artefact_contains_identifier(linear_ctx: _Ctx) -> None:
    assert linear_ctx.artefact is not None
    body = linear_ctx.artefact.raw.decode("utf-8")
    assert _ISSUE_IDENTIFIER in body, f"rendered Markdown missing identifier: {body!r}"
