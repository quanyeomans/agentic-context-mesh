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


def test_next_cursor_encodes_per_entity_type_watermarks() -> None:
    """next_cursor() encodes each type's own last-emitted updatedAt (spec §4).

    The cursor is a JSON-encoded per-type watermark map — issues advance
    to the latest issue updatedAt, projects to the latest project
    updatedAt, independently. Pins that each type tracks its OWN mark
    (a mutant collapsing to one shared watermark would lose a key).
    """
    import json

    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "projects": [[_project_node("uuid-p1", "2026-05-15T00:00:00.000Z")]],
        }
    )
    list(connector.list_changes(cursor=None))
    decoded = json.loads(connector.next_cursor() or "")
    assert decoded == {
        "issue": "2026-05-10T00:00:00.000Z",
        "project": "2026-05-15T00:00:00.000Z",
    }


def test_cursor_round_trips_to_resume_per_type() -> None:
    """next_cursor() → list_changes(cursor) resumes each type past its mark.

    A second tick fed the first tick's cursor must NOT re-emit items at or
    below each type's watermark, but MUST emit a newer item of any type.
    """
    connector = _build(
        {
            "issues": [
                [
                    _issue_node("ENG-1", "2026-05-10T00:00:00.000Z"),
                    _issue_node("ENG-2", "2026-05-20T00:00:00.000Z"),
                ]
            ],
        }
    )
    list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    # Second tick with the same fixture: every issue updatedAt is <= the
    # watermark, so nothing is re-emitted.
    again = list(connector.list_changes(cursor=cursor))
    assert again == [], "items at or below the per-type watermark must not re-emit"


def test_legacy_single_string_cursor_degrades_to_full_enum() -> None:
    """A pre-upgrade single-ISO-string cursor re-syncs rather than skipping.

    Pins the robust-decode contract (spec §4): a malformed/legacy token is
    treated as "no watermark for any type" → full enumeration, so existing
    operator state degrades safely instead of silently skipping data.
    """
    connector = _build({"issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]]})
    # Legacy cursor: a bare ISO string newer than the only event. Under the
    # OLD single-watermark code this would have filtered the event out; the
    # new decode treats it as "no watermark" and re-enumerates everything.
    events = list(connector.list_changes(cursor="2026-06-01T00:00:00.000Z"))
    assert [e.item_id for e in events] == ["issue:ENG-1"]


def test_per_tick_budget_advances_drained_type_watermark() -> None:
    """A budget-stopped tick advances each drained type's OWN watermark (spec §4).

    Forward progress: when per_tick_max_items caps the drain mid-page, the
    type's watermark moves to the max updatedAt it EMITTED — never None,
    never past an un-emitted item. The next tick resumes from there.
    """
    import json

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
    decoded = json.loads(connector.next_cursor() or "")
    # Watermark advanced to the SECOND emitted item — not past ENG-3.
    assert decoded == {"issue": "2026-05-11T00:00:00.000Z"}, (
        "budget-stopped tick must advance the issue watermark to its last emitted item, not skip ENG-3"
    )


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


def _document_node(did: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": did,
        "title": f"Doc {did}",
        "content": "doc body",
        "url": f"https://linear.app/team/document/{did}",
        "createdAt": "2026-05-01T00:00:00.000Z",
        "updatedAt": updated_at,
        "creator": {"displayName": "agent-gamma", "email": "agent-gamma@example.com"},
    }


def test_multi_type_budget_no_starvation_or_skip_across_ticks() -> None:
    """Under per-tick-budget pressure, every entity type is eventually
    emitted across ticks — no starvation, no skip, no livelock (Finding 1).

    With a SINGLE shared watermark + ``any()`` short-circuit drain (the old
    code), the first spec (issues) fills ``per_tick_max_items`` every tick,
    the other four types are never queried, and the held single cursor lets
    the next tick re-drain issues from scratch forever — projects/documents
    are emitted NEVER. The per-entity-type watermark cursor fixes this: each
    type advances its OWN watermark to its last-emitted updatedAt, so later
    ticks resume past the drained issues and reach the other types.

    Asserts: (a) all three issues + the project + the document are emitted
    across the ticks (no type skipped), (b) every tick before completion
    makes forward progress (no livelock), (c) no in-range item is lost.
    """
    issues = [
        _issue_node("ENG-1", "2026-05-10T00:00:00.000Z"),
        _issue_node("ENG-2", "2026-05-11T00:00:00.000Z"),
        _issue_node("ENG-3", "2026-05-12T00:00:00.000Z"),
    ]
    project = _project_node("uuid-p1", "2026-05-13T00:00:00.000Z")
    document = _document_node("uuid-d1", "2026-05-14T00:00:00.000Z")
    expected = {
        "issue:ENG-1",
        "issue:ENG-2",
        "issue:ENG-3",
        "project:uuid-p1",
        "document:uuid-d1",
    }
    connector = _build(
        {
            "issues": [issues],
            "projects": [[project]],
            "documents": [[document]],
        },
        per_tick_max_items=2,
    )

    seen: set[str] = set()
    cursor = None
    # Bound the loop well above the minimum needed (5 items / budget 2 = 3
    # ticks) so a livelock surfaces as "never converges" rather than hanging.
    max_ticks = 12
    for _ in range(max_ticks):
        before = len(seen)
        batch = list(connector.list_changes(cursor=cursor))
        seen.update(e.item_id for e in batch)
        cursor = connector.next_cursor()
        if seen == expected:
            break
        # Forward progress: a tick that emits nothing new while items remain
        # is a livelock — the per-type watermark must keep advancing.
        assert len(seen) > before, "livelock: a tick made no forward progress while items remain undrained"

    assert seen == expected, (
        f"every entity type must be emitted across ticks under budget pressure — missing: {expected - seen}"
    )


class _ExplodingNode(dict):  # type: ignore[type-arg]  # F3: test-only node that raises on identity access.
    """Yields a valid ``updatedAt`` (so the fake's since-filter passes it
    to the connector) but raises on any other key — forcing the
    connector's per-item conversion to throw, not the fake."""

    def get(self, key: Any, default: Any = None) -> Any:
        if key == "updatedAt":
            return "2026-05-09T00:00:00.000Z"
        raise ValueError("boom: malformed linear node")


def test_drain_skips_malformed_node_and_keeps_siblings(caplog: pytest.LogCaptureFixture) -> None:
    """A node that raises on conversion is skipped; siblings still emit (Finding 2).

    Spec §9 per-item isolation: one malformed item fails just that item,
    logged at WARNING with the traceback (``exc_info``) — never the whole
    tick. We force the conversion to raise by handing the drain a node that
    explodes on ``.get``, and assert the valid sibling issue is still
    emitted, the tick succeeds, AND the skip is logged WITH the exception
    traceback (pins ``exc_info=True`` — a mutant flipping it to ``False``
    drops the traceback the operator needs to diagnose the bad node).
    """
    import logging

    good = _issue_node("ENG-OK", "2026-05-10T00:00:00.000Z")
    connector = _build({"issues": [[_ExplodingNode(), good]]})
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.linear.connector"):
        events = list(connector.list_changes(cursor=None))
    ids = [e.item_id for e in events]
    assert ids == ["issue:ENG-OK"], "the malformed node is skipped, the valid sibling still emits"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "malformed" in r.getMessage()]
    assert warnings, "the skipped malformed node must be logged at WARNING"
    # exc_info=True populates record.exc_info with the (type, value, tb)
    # tuple; exc_info=False leaves it as the literal False. Assert the tuple
    # so a True->False mutant (dropping the traceback) is killed.
    exc = warnings[0].exc_info
    assert isinstance(exc, tuple) and exc[0] is ValueError, (
        f"the WARNING must carry the exception traceback (exc_info=True), got {exc!r}"
    )


def test_watermark_is_monotonic_even_with_out_of_order_nodes() -> None:
    """A type's watermark never moves backward when nodes arrive out of order.

    Pins the ``max(prior, modified_at)`` forward-progress advance (spec §4
    monotonicity): even if a later-yielded node of the same type has an
    OLDER updatedAt, the watermark stays at the newest one already emitted.
    A mutant that drops the ``max`` (taking the last value unconditionally)
    would regress the watermark to the older timestamp.
    """
    import json

    connector = _build(
        {
            "issues": [
                [
                    _issue_node("ENG-NEW", "2026-05-20T00:00:00.000Z"),  # newest first
                    _issue_node("ENG-OLD", "2026-05-10T00:00:00.000Z"),  # older, yielded after
                ]
            ]
        }
    )
    events = list(connector.list_changes(cursor=None))
    assert {e.item_id for e in events} == {"issue:ENG-NEW", "issue:ENG-OLD"}
    decoded = json.loads(connector.next_cursor() or "")
    assert decoded == {"issue": "2026-05-20T00:00:00.000Z"}, (
        "watermark must hold the newest emitted updatedAt, never regress to the older out-of-order node"
    )


def test_cursor_decode_drops_non_string_watermark_value() -> None:
    """A JSON cursor whose watermark value isn't a string is dropped to full enum.

    Pins the ``isinstance(value, str) and value`` filter in _decode_cursor
    (line 609): a corrupt per-type watermark (e.g. a number) must be
    discarded so that type re-enumerates from epoch rather than being
    applied as a bogus filter. A mutant swapping ``and`` for ``or`` would
    keep the non-string value and feed it to the API as ``since``.
    """
    import json

    # issue watermark is a NUMBER (corrupt); project watermark is a valid
    # future ISO string that filters the project out. The issue must still
    # emit (its corrupt watermark is dropped → epoch → full enum).
    corrupt = json.dumps({"issue": 12345, "project": "2026-12-01T00:00:00.000Z"})
    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "projects": [[_project_node("uuid-p1", "2026-05-11T00:00:00.000Z")]],
        }
    )
    events = list(connector.list_changes(cursor=corrupt))
    ids = {e.item_id for e in events}
    assert "issue:ENG-1" in ids, "a corrupt (non-string) issue watermark must drop to full enum, not skip the issue"
    assert "project:uuid-p1" not in ids, "the valid project watermark (future) still filters the project out"


def test_encode_cursor_keys_are_sorted_deterministically() -> None:
    """The encoded cursor token sorts its keys for a deterministic round-trip.

    Pins ``json.dumps(..., sort_keys=True)`` (line 618): the persisted token
    must be byte-stable regardless of drain order, so the orchestrator's
    cursor row doesn't churn. The connector drains in spec order
    (issue, then document), so the INSERTION order is ``issue, document``;
    SORTED order is ``document, issue`` (alphabetical). We assert the token
    is in sorted order — a mutant flipping ``sort_keys`` to ``False`` would
    emit the insertion order (``issue`` before ``document``) and fail.
    """
    connector = _build(
        {
            "issues": [[_issue_node("ENG-1", "2026-05-10T00:00:00.000Z")]],
            "documents": [[_document_node("uuid-d1", "2026-05-15T00:00:00.000Z")]],
        }
    )
    list(connector.list_changes(cursor=None))
    token = connector.next_cursor()
    assert token is not None
    assert token.index('"document"') < token.index('"issue"'), (
        f"encoded cursor keys must be sorted (document before issue), not drain order; got {token!r}"
    )
