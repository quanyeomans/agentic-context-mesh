"""Contract test for the Linear connector plugin (F43 behavioural parity).

Exercises the canonical fake (:class:`tests.fakes.FakeLinearConnector`)
AND the real implementation
(:class:`kairix.connectors.linear.LinearConnector`) through the same
:class:`~kairix.core.protocols.SourceConnector` Protocol assertions in
ONE parametrized body per behaviour (F43). Without the pairing the fake
can drift from the real wire (or vice versa) and the production path
silently diverges from what BDD / unit tests measure.

Every ``test_*`` function here is parametrized over the
``(name, factory)`` pair so the SAME assertion runs against both the
real and fake implementation — the F43 LIMB-2 requirement. The
per-method failure-injection coverage lives in
:mod:`tests.contracts.test_linear_connector_contract` (F68-style
behaviour-under-failure; spec §11).

Real-impl path is driven against a scripted
:class:`tests.fakes.FakeLinearApiClient`; no real network call is made.

Sabotage proofs (spec §5 / §11):
  * Removing ``list_changes`` from :class:`LinearConnector` flips
    ``test_connector_satisfies_source_connector_protocol`` (real branch).
  * Replacing ``fetch``'s return with plain ``bytes`` breaks
    ``test_connector_fetch_returns_markdown_artefact``.
  * Mutating :data:`DEFAULT_SENSITIVITY` to ``"public"`` flips
    ``test_connector_default_sensitivity_is_internal``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.connectors.linear import (
    DEFAULT_SENSITIVITY,
    LinearConnector,
    LinearCredentials,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeLinearApiClient, FakeLinearConnector

pytestmark = pytest.mark.contract

_ISSUE_ID = "ENG-301"
_PROJECT_ID = "uuid-project-301"


def _issue_node() -> dict[str, Any]:
    return {
        "id": "uuid-issue-301",
        "identifier": _ISSUE_ID,
        "title": "Contract issue",
        "description": "Issue body for the contract test.",
        "url": "https://linear.app/your-team/issue/ENG-301",
        "createdAt": "2026-05-20T09:00:00.000Z",
        "updatedAt": "2026-05-22T10:00:00.000Z",
        "state": {"name": "Backlog"},
        "creator": {"displayName": "agent-alpha", "email": "agent-alpha@example.com"},
        "team": {"name": "Engineering"},
        "project": {"id": _PROJECT_ID, "name": "Contract project"},
        "labels": {"nodes": [{"name": "roadmap"}]},
    }


def _project_node() -> dict[str, Any]:
    return {
        "id": _PROJECT_ID,
        "name": "Contract project",
        "description": "Project body for the contract test.",
        "url": "https://linear.app/your-team/project/uuid-project-301",
        "createdAt": "2026-05-19T09:00:00.000Z",
        "updatedAt": "2026-05-21T10:00:00.000Z",
        "lead": {"displayName": "agent-beta", "email": "agent-beta@example.com"},
    }


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds one issue + one project node."""
    return FakeLinearConnector(
        nodes={
            "issue": [_issue_node()],
            "project": [_project_node()],
        }
    )


def _real_factory() -> SourceConnector:
    """Real-impl factory — scripted FakeLinearApiClient, cache primed."""
    api = FakeLinearApiClient(
        pages={
            "issues": [[_issue_node()]],
            "projects": [[_project_node()]],
        }
    )
    connector = LinearConnector(
        credentials=LinearCredentials(api_key="lin_contract_fixture"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: api,
    )
    # Prime the node cache so fetch()/metadata_for()/source_link() work.
    list(connector.list_changes(cursor=None))
    return connector


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol."""
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "linear"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream type-prefixed :class:`ChangeEvent` instances."""
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "archived", "deleted", "access_lost")
        assert ":" in ev.item_id, f"{name!r} item_id not type-prefixed: {ev.item_id!r}"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_markdown_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape."""
    connector = factory()
    artefact = connector.fetch(f"issue:{_ISSUE_ID}")
    assert isinstance(artefact, RawArtefact), f"{name!r} fetch did not return a RawArtefact: {artefact!r}"
    assert artefact.mime == "text/markdown", f"{name!r} fetch mime is wrong: {artefact.mime!r}"
    assert artefact.raw, f"{name!r} fetch raw bytes is empty"
    assert _ISSUE_ID.encode() in artefact.raw, f"{name!r} rendered body missing identifier"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_linear(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns a linear.app URL on both implementations."""
    connector = factory()
    link = connector.source_link(f"issue:{_ISSUE_ID}")
    assert link, f"{name!r} produced empty source_link"
    assert link.startswith(("https://linear.app/", "linear://")), f"{name!r} unexpected link scheme: {link!r}"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_default_sensitivity_is_internal(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``sensitivity_for`` returns the documented default ``internal`` tier."""
    connector = factory()
    tier = connector.sensitivity_for(f"issue:{_ISSUE_ID}")
    assert tier == DEFAULT_SENSITIVITY == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_metadata_for_surfaces_author(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations surface the issue author through metadata_for."""
    connector = factory()
    meta = connector.metadata_for(f"issue:{_ISSUE_ID}")
    assert meta.author == "agent-alpha", f"{name!r} author mismatch: {meta.author!r}"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_next_cursor_advances_on_clean_drain(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations expose a high-water-mark cursor after a clean drain."""
    connector = factory()
    list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor == "2026-05-22T10:00:00.000Z", f"{name!r} cursor not the max updatedAt: {cursor!r}"
