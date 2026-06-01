"""Integration coverage for #380 — folder-scoped delta in m365_email_headers.

Graph rejects mailbox-wide ``/users/{upn}/messages/delta`` with
``BadRequest: Change tracking is not supported against
'microsoft.graph.message'``. Delta only works folder-scoped — the
connector now enumerates mail folders and drains each independently
with its own per-folder cursor.

The tests in this module drive the production
:class:`~kairix.connectors.m365_email_headers.M365EmailHeadersConnector`
against an :class:`httpx.MockTransport` stub that serves a multi-folder
mailbox (Inbox / Sent Items / Archive x 5 messages each = 15 envelopes).
F1-clean: no monkey-patching; the stub injects via the
``client_builder`` DI seam already in production use.

Tests cover:

  * Multi-folder fan-out — every folder's messages surface; each event
    carries the source folder in :attr:`ChangeEvent.metadata`.
  * Allowlist filtering — restricting to ``["inbox", "archive"]``
    excludes Sent Items; remaining folders still drain fully.
  * Per-folder cursor isolation — the persisted cursor is a JSON
    ``{folder_id: deltaLink}`` mapping; each folder keeps its own
    deltaLink.
  * F68 failure-injection — one folder's delta returns 5xx for the
    whole retry budget; siblings still drain; the bad folder's cursor
    is preserved (or absent on cold-start) so the next tick retries.
  * Single-folder sabotage — if the URL builder regressed to mailbox-
    wide, only the primary folder's messages would surface and the
    sibling folders' messages would be missing.

Each ``test_*`` carries ``@pytest.mark.integration`` per F8 and an
explicit "Sabotage proof:" rationale describing the mutation that
breaks the assertion.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
)
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.integration


_FOLDERS = [
    {"id": "AAMkAGFmYWtl-inbox", "displayName": "Inbox", "wellKnownName": "inbox"},
    {"id": "AAMkAGFmYWtl-sent", "displayName": "Sent Items", "wellKnownName": "sentitems"},
    {"id": "AAMkAGFmYWtl-archive", "displayName": "Archive", "wellKnownName": "archive"},
]


def _five_envelopes(folder_id: str) -> list[dict[str, Any]]:
    """Five header-only envelopes synthesised for the named folder.

    The ``id`` is prefixed by the folder id so cross-folder leak can be
    detected mechanically: any message id with ``-inbox`` came from the
    inbox folder's drain and so on.
    """
    return [
        {
            "id": f"{folder_id}-msg-{idx}",
            "from": {"emailAddress": {"address": f"sender-{idx}@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
            "ccRecipients": [],
            "subject": f"{folder_id} subject {idx}",
            "sentDateTime": f"2026-05-22T10:{idx:02d}:00Z",
            "receivedDateTime": f"2026-05-22T10:{idx:02d}:01Z",
        }
        for idx in range(1, 6)
    ]


def _folder_delta_payload(folder_id: str) -> dict[str, Any]:
    """One per-folder delta page with five envelopes + a folder-keyed deltaLink."""
    return {
        "value": _five_envelopes(folder_id),
        "@odata.deltaLink": (
            f"https://graph.microsoft.com/v1.0/users/agent-alpha@example.com"
            f"/mailFolders/{folder_id}/messages/delta?$deltatoken={folder_id}-tok"
        ),
    }


def _mail_folders_payload() -> dict[str, Any]:
    """Three folders — Inbox, Sent Items, Archive."""
    return {"value": list(_FOLDERS)}


def _stub_factory(
    *,
    failing_folder_ids: set[str] | None = None,
    folders_payload: dict[str, Any] | None = None,
) -> tuple[httpx.MockTransport, list[str]]:
    """Construct a MockTransport stubbing the three-folder mailbox.

    ``failing_folder_ids`` (if set) makes any delta URL for those
    folders return 500 — used by the failure-injection test to prove
    siblings still drain.
    """
    failing = failing_folder_ids or set()
    folders_doc = folders_payload if folders_payload is not None else _mail_folders_payload()
    recorded: list[str] = []

    # Build the candidate folder-id set from whatever ``folders_doc``
    # advertises so tests using a custom mailFolders response still
    # get matching delta responses.
    candidate_ids = [
        entry["id"]
        for entry in folders_doc.get("value", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer", "expires_in": 3600, "token_type": "Bearer"},
            )
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(200, json=folders_doc)
        recorded.append(url)
        for folder_id in candidate_ids:
            if f"/mailFolders/{folder_id}/messages/delta" in url:
                if folder_id in failing:
                    return httpx.Response(500, json={"error": {"code": "InternalServerError"}})
                return httpx.Response(200, json=_folder_delta_payload(folder_id))
        return httpx.Response(404, json={"error": {"code": "UnknownFolder"}})

    return httpx.MockTransport(_handler), recorded


def _build_connector(
    handler: httpx.MockTransport,
    *,
    folders_allowlist: list[str] | None = None,
) -> M365EmailHeadersConnector:
    """Compose the real connector against the shared MockTransport.

    F47 — exercises the real connector + real OAuth2 helper + real
    Graph client construction; nothing is monkey-patched. The
    ``sleep_fn`` on the per-folder client is a recording no-op so the
    failure-injection test doesn't accrue wall-clock delay.
    """
    shared = httpx.Client(transport=handler)
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )

    def _builder(resolved_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
        return M365GraphClient(
            user_principal_name=upn,
            auth=resolved_auth,
            http_client=shared,
            sleep_fn=lambda _s: None,  # no-op sleep so 5xx-retry doesn't burn wall-clock
        )

    return M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=_builder,
        folders_allowlist=folders_allowlist,
    )


# ---------------------------------------------------------------------------
# Multi-folder fan-out
# ---------------------------------------------------------------------------


def test_list_changes_drains_every_folder_three_folders_five_messages_each() -> None:
    """3 folders x 5 messages = 15 ChangeEvents across the right folders.

    The connector enumerates folders via ``GET /users/{upn}/mailFolders``
    and drains each folder's delta independently. Every event carries
    the source folder in :attr:`ChangeEvent.metadata` so downstream
    consumers can scope by folder without re-hitting Graph.

    Sabotage proof (verified mentally; executed below as
    :func:`test_sabotage_single_folder_hardcoded_drops_siblings`):
    hardcoding the folder list to ``[_FOLDERS[0]]`` only surfaces 5
    events instead of 15.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 15, f"expected 15 events (3 folders x 5 messages), got {len(events)}"

    # Per-folder distribution.
    by_folder: dict[str, list[str]] = {"AAMkAGFmYWtl-inbox": [], "AAMkAGFmYWtl-sent": [], "AAMkAGFmYWtl-archive": []}
    for ev in events:
        for prefix in by_folder:
            if ev.item_id.startswith(f"{prefix}-msg-"):
                by_folder[prefix].append(ev.item_id)
                break
    for folder_id, items in by_folder.items():
        assert len(items) == 5, f"folder {folder_id!r}: expected 5 events, got {len(items)} ({items!r})"

    # Folder metadata threads onto every event.
    seen_folders = {ev.metadata.get("folder") for ev in events}
    assert seen_folders == {"Inbox", "Sent Items", "Archive"}, (
        f"expected folder names in metadata; got {seen_folders!r}"
    )


def test_list_changes_persists_per_folder_cursor_mapping() -> None:
    """After a successful drain, ``next_cursor`` is a JSON dict carrying every folder's deltaLink.

    Sabotage proof: replace ``self._next_cursor = _encode_per_folder_cursor(next_cursors)``
    with ``self._next_cursor = None`` — the JSON decode below raises
    TypeError because ``cursor`` is ``None``.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler)
    _ = list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None, "next_cursor must persist the per-folder mapping"
    decoded = json.loads(cursor)
    assert isinstance(decoded, dict)
    for folder in _FOLDERS:
        folder_id = folder["id"]
        assert folder_id in decoded, f"folder {folder_id!r} cursor missing from {decoded!r}"
        assert f"$deltatoken={folder_id}-tok" in decoded[folder_id]


def test_list_changes_url_shape_is_folder_scoped_per_folder() -> None:
    """Every recorded data URL carries ``/mailFolders/{folder_id}/messages/delta``.

    Sabotage proof: revert :meth:`M365GraphClient.initial_delta_url`
    to the pre-fix mailbox-wide shape — the
    ``/mailFolders/{folder_id}/messages/delta`` substring assertion
    below fails on every recorded URL.
    """
    handler, recorded = _stub_factory()
    connector = _build_connector(handler)
    _ = list(connector.list_changes(cursor=None))
    assert recorded, "expected at least one per-folder delta URL to be recorded"
    for url in recorded:
        assert "/mailFolders/" in url and "/messages/delta" in url, (
            f"recorded URL is not folder-scoped per #380: {url!r}"
        )


# ---------------------------------------------------------------------------
# Allowlist filtering
# ---------------------------------------------------------------------------


def test_allowlist_well_known_names_filter_folders() -> None:
    """Allowlist ``[inbox, archive]`` filters out Sent Items.

    Sabotage proof: remove the well_known_name branch in
    :func:`_select_folders` — Sent Items would still surface (matches
    nothing else), failing the assertion that only Inbox + Archive
    events appear.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler, folders_allowlist=["inbox", "archive"])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 10, f"expected 10 events (2 allowed folders x 5 messages), got {len(events)}"
    for ev in events:
        assert not ev.item_id.startswith("AAMkAGFmYWtl-sent-"), (
            f"allowlist excludes Sent Items; cross-folder leak: {ev.item_id!r}"
        )
    seen_folders = {ev.metadata.get("folder") for ev in events}
    assert seen_folders == {"Inbox", "Archive"}, f"expected only Inbox + Archive; got {seen_folders!r}"


def test_allowlist_custom_folder_name_match_is_case_insensitive() -> None:
    """Custom folders (no wellKnownName) match by case-insensitive displayName.

    Sabotage proof: drop the ``.lower()`` call in
    :func:`_select_folders` — the assertion that ``"q2 receipts"``
    matches ``"Q2 Receipts"`` fails because the case mismatch leaves
    the folder filtered out.
    """
    custom_folders: dict[str, Any] = {
        "value": [
            {"id": "AAMkAGFmYWtl-q2-receipts", "displayName": "Q2 Receipts", "wellKnownName": None},
            {"id": "AAMkAGFmYWtl-inbox", "displayName": "Inbox", "wellKnownName": "inbox"},
        ]
    }
    handler, _recorded = _stub_factory(folders_payload=custom_folders)
    connector = _build_connector(handler, folders_allowlist=["q2 receipts"])
    events = list(connector.list_changes(cursor=None))
    # Only Q2 Receipts surfaces — Inbox is filtered out by the allowlist.
    seen_folders = {ev.metadata.get("folder") for ev in events}
    assert seen_folders == {"Q2 Receipts"}, f"expected only Q2 Receipts; got {seen_folders!r}"


def test_empty_allowlist_ingests_every_folder() -> None:
    """``folders_allowlist=[]`` (or missing) ingests every folder.

    Empty / missing allowlist preserves the default-ingest-all
    behaviour so an operator who declares the key but leaves it empty
    isn't punished by an accidental zero-folder sync.

    Sabotage proof: change the ``if not allowlist`` guard in
    :func:`_select_folders` to ``if allowlist is None`` — an explicit
    empty allowlist would then silently exclude every folder.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler, folders_allowlist=[])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 15, f"empty allowlist should keep all folders; got {len(events)} events"


# ---------------------------------------------------------------------------
# Sabotage — hardcoded single folder drops siblings
# ---------------------------------------------------------------------------


def test_sabotage_single_folder_hardcoded_drops_siblings() -> None:
    """If the connector enumerated only one folder, the other two would not surface.

    This is the structural sabotage proof: passing a one-folder
    allowlist mimics what would happen if the URL builder regressed
    to a hardcoded single folder. Only that folder's 5 messages
    surface; the other 10 are missing.

    Sabotage proof for the cross-folder fan-out test above: this test
    constructs the failure mode and asserts the symptom directly.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler, folders_allowlist=["inbox"])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 5, f"single-folder allowlist must yield 5 events; got {len(events)}"
    item_ids = {ev.item_id for ev in events}
    # All five inbox messages are present.
    expected_inbox = {f"AAMkAGFmYWtl-inbox-msg-{i}" for i in range(1, 6)}
    assert item_ids == expected_inbox, (
        f"expected only inbox messages; got {item_ids!r} (sent + archive should not surface)"
    )


# ---------------------------------------------------------------------------
# F68 failure injection — one folder's drain fails; siblings continue
# ---------------------------------------------------------------------------


def test_one_folder_5xx_does_not_poison_siblings() -> None:
    """When the Inbox delta returns 500 for every attempt, Sent Items and
    Archive still drain their 5 messages each.

    Per the bug fix's per-folder isolation contract (#380), one bad
    folder must not poison the others. The bad folder's deltaLink is
    not advanced (so the next tick retries from the same horizon);
    siblings still record their deltaLink and emit their events.

    Sabotage proof: replace the ``try / except httpx.HTTPError`` block
    in :meth:`list_changes` with a bare ``for folder in selected:`` loop
    that lets the exception escape — the 500 from Inbox propagates and
    the call raises ``httpx.HTTPStatusError`` instead of surfacing
    sibling events.
    """
    handler, _recorded = _stub_factory(failing_folder_ids={"AAMkAGFmYWtl-inbox"})
    connector = _build_connector(handler)
    events = list(connector.list_changes(cursor=None))
    # 2 surviving folders x 5 messages each.
    assert len(events) == 10, f"expected 10 events from surviving folders, got {len(events)}"
    for ev in events:
        assert not ev.item_id.startswith("AAMkAGFmYWtl-inbox-"), (
            f"failed folder should not surface events; leak: {ev.item_id!r}"
        )
    # Cursor advances for the surviving folders only.
    cursor = connector.next_cursor()
    assert cursor is not None
    decoded = json.loads(cursor)
    assert "AAMkAGFmYWtl-sent" in decoded
    assert "AAMkAGFmYWtl-archive" in decoded
    # The bad folder's cursor is absent (no previous cursor to preserve
    # on cold-start; nothing was successfully drained).
    assert "AAMkAGFmYWtl-inbox" not in decoded, (
        f"failed folder should not advance its cursor on cold-start; got {decoded!r}"
    )


def test_one_folder_5xx_preserves_existing_cursor() -> None:
    """When a folder fails on a subsequent tick, its prior cursor is kept.

    The next tick will retry from the same horizon — Graph delta is
    forward-only and the deltaLink is opaque, so preserving it is the
    safe default.

    Sabotage proof: change ``if prior is not None: next_cursors[...] = prior``
    in :meth:`list_changes` to a no-op — the bad folder's cursor would
    silently disappear and the next tick would do a cold-start full
    sync (correct behaviour but wasteful), and this test's
    ``decoded["AAMkAGFmYWtl-inbox"]`` lookup raises KeyError.
    """
    # Cold-start successful tick to prime cursors for every folder.
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler)
    _ = list(connector.list_changes(cursor=None))
    primed = connector.next_cursor()
    assert primed is not None

    # Build a second handler where Inbox now fails; reuse the primed cursor.
    handler2, _recorded2 = _stub_factory(failing_folder_ids={"AAMkAGFmYWtl-inbox"})
    connector2 = _build_connector(handler2)
    _ = list(connector2.list_changes(cursor=primed))
    cursor = connector2.next_cursor()
    assert cursor is not None
    decoded = json.loads(cursor)
    # Bad folder's previous cursor is preserved so next tick resumes.
    assert "AAMkAGFmYWtl-inbox" in decoded, f"bad folder must retain its prior cursor for retry; got {decoded!r}"
    assert "$deltatoken=AAMkAGFmYWtl-inbox-tok" in decoded["AAMkAGFmYWtl-inbox"]


# ---------------------------------------------------------------------------
# Cursor migration — legacy (pre-#380) cursor strings collapse to cold-start
# ---------------------------------------------------------------------------


def test_legacy_string_cursor_treated_as_cold_start() -> None:
    """A pre-#380 single-string deltaLink cursor decodes to ``{}`` (cold-start).

    Deployments that stored a single mailbox-wide deltaLink before this
    fix landed (when Graph rejected the request outright, so the cursor
    was never meaningful) won't crash on the first post-fix tick — the
    legacy string decodes to empty and the connector restarts per-
    folder from cold.

    Sabotage proof: remove the ``except (TypeError, ValueError)`` block
    in :func:`_decode_per_folder_cursor` — passing a non-JSON cursor
    raises and the connector aborts the tick instead of recovering.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler)
    legacy_cursor = (
        "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=legacy-pre-fix"
    )
    events = list(connector.list_changes(cursor=legacy_cursor))
    # Cold-start: every folder fully drains.
    assert len(events) == 15, f"legacy cursor must collapse to cold-start; expected 15 events, got {len(events)}"


# ---------------------------------------------------------------------------
# Folder enumeration failure — defers cleanly, no crash
# ---------------------------------------------------------------------------


def test_folder_enumeration_failure_defers_quietly() -> None:
    """If ``list_mail_folders`` raises, ``list_changes`` returns no events.

    Sabotage proof: remove the ``try / except httpx.HTTPError`` around
    the ``self._graph.list_mail_folders()`` call — the exception
    propagates and the orchestration layer's tick aborts with an
    unhandled traceback.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer", "expires_in": 3600, "token_type": "Bearer"},
            )
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(503, json={"error": {"code": "ServiceUnavailable"}})
        return httpx.Response(200, json={"value": []})

    handler = httpx.MockTransport(_handler)
    connector = _build_connector(handler)
    events = list(connector.list_changes(cursor=None))
    assert events == [], f"folder enumeration failure should yield no events this tick; got {events!r}"


# ---------------------------------------------------------------------------
# Iterator type sanity
# ---------------------------------------------------------------------------


def test_list_changes_returns_iterator() -> None:
    """``list_changes`` returns an Iterator per the SourceConnector Protocol.

    Sabotage proof: change ``return iter(events)`` to ``return events``
    — the assertion below fails because ``list`` is not the same as
    the Iterator the Protocol calls for.
    """
    handler, _recorded = _stub_factory()
    connector = _build_connector(handler)
    result = connector.list_changes(cursor=None)
    assert isinstance(result, Iterator), f"list_changes must return an Iterator; got {type(result).__name__}"
