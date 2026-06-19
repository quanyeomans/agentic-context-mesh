"""Unit tests for :class:`kairix.connectors.linear.LinearConnector`.

Drives the real connector against a scripted
:class:`tests.fakes.FakeLinearApiClient` (no network). Covers the cursor
high-water-mark, type-prefixed dispatch, fetch rendering, metadata
surfacing, sensitivity, source_link, the per-tick budget, the credential
loader, and the slim enumeration.

F1/F2-clean: the api client is injected via the ``client_builder`` seam;
no @patch, no env vars. F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from typing import Any

import pytest

# Import via the module path (``...linear.connector``) — not just the
# package — so the mutation-parity impacted-test selector (import-graph
# heuristic) maps this file's strong per-line assertions onto
# ``kairix/connectors/linear/connector.py`` mutants and kills them.
from kairix.connectors.linear.connector import (
    LinearConnector,
    LinearCredentials,
    make_connector,
)
from kairix.core.protocols import Container, RawArtefact, SourceMetadata
from tests.fakes import FakeLinearApiClient

pytestmark = pytest.mark.unit


def _issue_node(identifier: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Issue {identifier}",
        "description": "body",
        "url": f"https://linear.app/team/issue/{identifier}",
        "createdAt": "2026-05-01T00:00:00.000Z",
        "updatedAt": updated_at,
        "state": {"name": "Todo"},
        "creator": {"displayName": "agent-alpha", "email": "agent-alpha@example.com"},
        "team": {"name": "Engineering"},
        "project": {"id": "uuid-proj", "name": "Reliability"},
        "labels": {"nodes": [{"name": "bug"}]},
    }


def _project_node(pid: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": pid,
        "name": f"Project {pid}",
        "description": "overview",
        "url": f"https://linear.app/team/project/{pid}",
        "createdAt": "2026-05-01T00:00:00.000Z",
        "updatedAt": updated_at,
        "lead": {"displayName": "agent-beta", "email": "agent-beta@example.com"},
    }


def _build(pages: dict[str, list[list[dict[str, Any]]]], **kwargs: Any) -> LinearConnector:
    api = FakeLinearApiClient(pages=pages)
    return LinearConnector(
        credentials=LinearCredentials(api_key="lin_fixture"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: api,
        **kwargs,
    )


def test_list_changes_emits_type_prefixed_events() -> None:
    """Each entity type yields a ``modified`` event with a type-prefixed id."""
    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "projects": [[_project_node("uuid-p1", "2026-05-11T00:00:00.000Z")]],
        }
    )
    events = list(connector.list_changes(cursor=None))
    ids = {e.item_id for e in events}
    assert "issue:ENG-1" in ids
    assert "project:uuid-p1" in ids
    assert all(e.op == "modified" for e in events)


def test_list_changes_event_modified_at_is_the_node_updated_at() -> None:
    """The emitted event's modified_at equals the node's ``updatedAt``.

    Pins the ``_opt_str(node.get("updatedAt")) or _now_iso()`` branch — a
    mutant that swaps ``or`` for ``and`` would substitute the wall-clock
    now() even when updatedAt is present.
    """
    connector = _build({"issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]]})
    events = list(connector.list_changes(cursor=None))
    assert events[0].modified_at == "2026-05-10T00:00:00.000Z"


def test_next_cursor_is_high_water_mark_across_types() -> None:
    """next_cursor() returns the max updatedAt across all drained entities."""
    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "projects": [[_project_node("uuid-p1", "2026-05-15T00:00:00.000Z")]],
        }
    )
    list(connector.list_changes(cursor=None))
    assert connector.next_cursor() == "2026-05-15T00:00:00.000Z"


def test_next_cursor_keeps_prior_cursor_when_no_events() -> None:
    """A clean drain that produced no events preserves the prior cursor.

    Pins the ``if not events: return cursor`` branch in _high_water — the
    high-water-mark must not be clobbered to None when nothing changed.
    """
    connector = _build({"issues": [[]]})
    list(connector.list_changes(cursor="2026-05-01T00:00:00.000Z"))
    assert connector.next_cursor() == "2026-05-01T00:00:00.000Z"


def test_next_cursor_keeps_higher_prior_cursor_over_older_events() -> None:
    """When the prior cursor is newer than every event, it is preserved.

    Pins the ``cursor > latest`` guard in _high_water — the mark must never
    move backwards even if a re-fetched older item shows up in the drain.
    """
    connector = _build({"issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]]})
    list(connector.list_changes(cursor="2026-06-01T00:00:00.000Z"))
    assert connector.next_cursor() == "2026-06-01T00:00:00.000Z"


def test_per_tick_budget_stops_early_and_holds_cursor() -> None:
    """When per_tick_max_items is hit, the drain stops and the cursor stays None.

    Spec §4: a budget-stopped tick must NOT advance the cursor past the
    point it reached, so the next tick re-drains from the last good cursor.
    """
    connector = _build(
        {
            "issues": [
                [
                    _issue_node("ENG-1", "2026-05-10T00:00:00.000Z"),
                    _issue_node("ENG-2", "2026-05-11T00:00:00.000Z"),
                    _issue_node("ENG-3", "2026-05-12T00:00:00.000Z"),
                ]
            ]
        },
        per_tick_max_items=2,
    )
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 2, "budget should cap the tick at 2 items"
    assert connector.next_cursor() is None, "budget-stopped tick must not advance the cursor"


def test_fetch_dispatches_to_renderer_by_prefix() -> None:
    """fetch() renders the cached node as Markdown via the prefix dispatch."""
    connector = _build({"issues": [[_issue_node("ENG-7", "2026-05-10T00:00:00.000Z")]]})
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("issue:ENG-7")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "text/markdown"
    assert b"ENG-7" in artefact.raw


def test_fetch_uncached_item_raises_keyerror() -> None:
    """fetch() on an un-drained item raises a fix-pointer KeyError."""
    connector = _build({"issues": [[_issue_node("ENG-7", "2026-05-10T00:00:00.000Z")]]})
    with pytest.raises(KeyError, match="node cache"):
        connector.fetch("issue:NEVER-SEEN")


def test_metadata_for_surfaces_author_dates_and_labels() -> None:
    """metadata_for() surfaces creator + dates + label tags (F65)."""
    connector = _build({"issues": [[_issue_node("ENG-9", "2026-05-10T00:00:00.000Z")]]})
    list(connector.list_changes(cursor=None))
    meta = connector.metadata_for("issue:ENG-9")
    assert isinstance(meta, SourceMetadata)
    assert meta.author == "agent-alpha"
    assert meta.author_email == "agent-alpha@example.com"
    assert meta.modified_at == "2026-05-10T00:00:00.000Z"
    assert meta.created_at == "2026-05-01T00:00:00.000Z"
    assert "bug" in meta.tags
    assert meta.properties.get("team") == "Engineering"


def test_metadata_for_uncached_returns_empty() -> None:
    """metadata_for() on an un-drained item collapses to empty SourceMetadata."""
    connector = _build({"issues": [[]]})
    meta = connector.metadata_for("issue:UNSEEN")
    assert meta == SourceMetadata()


def test_metadata_for_coerces_non_string_dates_to_none() -> None:
    """A non-string ``createdAt`` is coerced to None, not crashed or kept.

    Pins the ``isinstance(value, str) and value.strip()`` guard in
    _opt_str — a mutant swapping ``and`` for ``or`` would call ``.strip()``
    on the int and raise (or keep the wrong value). The connector must
    tolerate a malformed envelope field by dropping it to None.
    """
    node = _issue_node("ENG-13", "2026-05-10T00:00:00.000Z")
    node["createdAt"] = 12345  # malformed: an int, not an ISO string
    connector = _build({"issues": [[node]]})
    list(connector.list_changes(cursor=None))
    meta = connector.metadata_for("issue:ENG-13")
    assert meta.created_at is None
    # modified_at is still the valid string updatedAt — drop affected only created_at.
    assert meta.modified_at == "2026-05-10T00:00:00.000Z"


def test_source_link_returns_linear_url() -> None:
    """source_link() returns the node's linear.app URL after a drain."""
    connector = _build({"issues": [[_issue_node("ENG-5", "2026-05-10T00:00:00.000Z")]]})
    list(connector.list_changes(cursor=None))
    assert connector.source_link("issue:ENG-5") == "https://linear.app/team/issue/ENG-5"


def test_source_link_fallback_when_uncached() -> None:
    """source_link() falls back to a ``linear://`` shape for an unknown id."""
    connector = _build({"issues": [[]]})
    assert connector.source_link("issue:GONE") == "linear://issue:GONE"


def test_source_link_falls_back_when_cached_node_has_empty_url() -> None:
    """A cached node with an empty url string falls back to ``linear://``.

    Pins the ``isinstance(url, str) and url`` guard — a mutant swapping
    ``and`` for ``or`` would return the empty string as the link.
    """
    node = _issue_node("ENG-3", "2026-05-10T00:00:00.000Z")
    node["url"] = ""  # present but empty
    connector = _build({"issues": [[node]]})
    list(connector.list_changes(cursor=None))
    assert connector.source_link("issue:ENG-3") == "linear://issue:ENG-3"


def test_metadata_for_surfaces_author_with_email_only_creator() -> None:
    """A creator with an email but no displayName still surfaces author_email.

    Pins the ``if name or email`` selection in _author_of — a mutant
    swapping ``or`` for ``and`` would skip a person who has only an email.
    """
    node = _issue_node("ENG-11", "2026-05-10T00:00:00.000Z")
    node["creator"] = {"email": "email-only@example.com"}  # no displayName
    connector = _build({"issues": [[node]]})
    list(connector.list_changes(cursor=None))
    meta = connector.metadata_for("issue:ENG-11")
    assert meta.author_email == "email-only@example.com"
    assert meta.author is None


def test_metadata_for_skips_person_with_no_name_or_email() -> None:
    """A creator block with neither name nor email is skipped (author None).

    Together with the email-only case, this pins both sides of the
    ``name or email`` selection so neither operand can be dropped.
    """
    node = _issue_node("ENG-12", "2026-05-10T00:00:00.000Z")
    node["creator"] = {}  # empty person — neither name nor email
    node.pop("assignee", None)
    connector = _build({"issues": [[node]]})
    list(connector.list_changes(cursor=None))
    meta = connector.metadata_for("issue:ENG-12")
    assert meta.author is None
    assert meta.author_email is None


def test_sensitivity_for_returns_configured_default() -> None:
    """sensitivity_for() returns the connector's configured tier."""
    connector = _build({"issues": [[]]}, default_sensitivity="client-confidential")
    assert connector.sensitivity_for("issue:any") == "client-confidential"


def test_list_changes_for_container_delegates_to_cursor() -> None:
    """PollConnector path uses the container's cursor token as the high-water-mark."""
    connector = _build({"issues": [[_issue_node("ENG-2", "2026-05-10T00:00:00.000Z")]]})
    container = Container(
        cc_pair_id=1,
        container_id="workspace",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert [e.item_id for e in events] == ["issue:ENG-2"]


def test_retrieve_all_slim_docs_enumerates_ids() -> None:
    """SlimConnector yields the type-prefixed id for every current node."""
    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "projects": [[_project_node("uuid-p1", "2026-05-11T00:00:00.000Z")]],
        }
    )
    container = Container(
        cc_pair_id=1,
        container_id="workspace",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    ids = set(connector.retrieve_all_slim_docs(container))
    assert ids == {"issue:ENG-1", "project:uuid-p1"}


def test_load_credentials_normalises_api_key() -> None:
    """CredentialsConnector normalises ``api_key`` / ``token`` -> {api_key}."""
    connector = _build({"issues": [[]]})
    from_api_key = connector.load_credentials({"api_key": " key1 "})  # pragma: allowlist secret — test fixture
    from_token = connector.load_credentials({"token": "key2"})  # pragma: allowlist secret — test fixture
    assert from_api_key == {"api_key": "key1"}  # pragma: allowlist secret — test fixture
    assert from_token == {"api_key": "key2"}  # pragma: allowlist secret — test fixture


def test_load_credentials_rejects_empty() -> None:
    """CredentialsConnector returns None when no usable key is present."""
    connector = _build({"issues": [[]]})
    assert connector.load_credentials({}) is None
    assert connector.load_credentials({"api_key": "   "}) is None


def test_make_connector_rejects_bad_sensitivity() -> None:
    """make_connector validates the F39 sensitivity tier."""
    with pytest.raises(ValueError, match="not a valid F39 tier"):
        make_connector({"default_sensitivity": "secret"})


def test_make_connector_rejects_bad_budget() -> None:
    """make_connector validates per_tick_max_items is a positive int."""
    with pytest.raises(ValueError, match="positive integer"):
        make_connector({"per_tick_max_items": 0})


def test_make_connector_accepts_budget_of_one() -> None:
    """A per_tick_max_items of exactly 1 is the lowest VALID budget.

    Pins the ``raw_budget < 1`` boundary — a mutant tightening it to
    ``<= 1`` would reject the legal minimum with the "positive integer"
    ValueError. ``make_connector`` validates the budget BEFORE it
    resolves credentials, so a budget of 1 must clear validation and
    proceed to the credential-resolution step (which raises
    SecretNotFoundError here because no secret backend is provisioned).
    We assert that the ONLY way this raises is the secret lookup — never
    the budget ValueError — which the ``<= 1`` mutant would trigger.
    """
    from kairix.secrets.loader import SecretNotFoundError

    with pytest.raises(SecretNotFoundError):
        make_connector({"per_tick_max_items": 1, "default_sensitivity": "internal"})
