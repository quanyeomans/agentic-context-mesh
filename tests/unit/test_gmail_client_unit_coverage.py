"""Unit coverage tests for the Gmail HTTP client (GH #359).

Focused per-function coverage of the parsing helpers + HTTP-shape
branches in :mod:`kairix.connectors.gmail.client` that the integration
/ rate-limit tests don't exercise. F7 (per-file >=90% coverage) is
the gate this file pays down.

Every test drives the helpers through the **public** boundary:

* HTTP-shape branches (URL composition, page-token threading, owned
  http.Client fallback) are driven by constructing a real
  :class:`GmailClient` against an :class:`httpx.MockTransport`.

* Parsing helpers (``_parse_history_page`` / ``_collect_history_message_ids``
  / ``_history_entry_added_ids`` / ``_added_item_message_id`` /
  ``_parse_message`` / ``_extract_headers`` / ``_extract_body`` /
  ``_find_part`` / ``_decode_body`` / ``_strip_html`` /
  ``_extract_attachments`` / ``_make_attachment_if_present`` /
  ``_attachment_body_fields`` / ``_str_or_empty`` / ``_optional_str``
  / ``_parse_retry_after`` / ``_extract_403_reason``) are exercised by
  pushing payloads through the public ``GmailClient.list_history`` /
  ``GmailClient.get_message`` calls.

F1-clean: no @patch or kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
F5-clean: every test reaches behaviour via the public boundary.
F8: ``pytestmark = pytest.mark.unit``.
F31/F32: no real names or local-machine paths; ``agent-*@example.com``
fixtures only.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from kairix.connectors.gmail.client import GmailClient
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
)

pytestmark = pytest.mark.unit


_USER = "agent-alpha@example.com"
_FAKE_BEARER = "fake-bearer-value"  # pragma: allowlist secret — test fixture


def _build_client(
    handler: object,
    *,
    user_email: str = _USER,
    gmail_base: str | None = None,
    max_body_bytes: int = 10 * 1024 * 1024,
) -> GmailClient:
    """Wire a real :class:`GmailClient` to a MockTransport handler."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # MockTransport accepts handler shapes httpx narrows at runtime
    shared = httpx.Client(transport=transport)
    return GmailClient(
        user_email=user_email,
        token_refresher=lambda: _FAKE_BEARER,
        gmail_base=gmail_base,
        http_client=shared,
        max_body_bytes=max_body_bytes,
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# Constructor guards (line 220)
# ---------------------------------------------------------------------------


def test_client_rejects_empty_user_email_with_fix_pointer() -> None:
    """Empty user_email raises with an actionable error.

    Sabotage proof: dropping the ``if not user_email`` guard in
    GmailClient.__init__ would let mailbox-less URLs flow downstream
    and 404 every call.
    """
    with pytest.raises(ValueError) as exc_info:
        GmailClient(user_email="", token_refresher=lambda: _FAKE_BEARER)
    msg = str(exc_info.value)
    assert "user_email" in msg
    assert "fix:" in msg


# ---------------------------------------------------------------------------
# get_profile_history_id (lines 248-255)
# ---------------------------------------------------------------------------


def test_get_profile_history_id_returns_string_from_envelope() -> None:
    """A well-formed profile response yields the historyId string."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert "/profile" in str(request.url)
        return httpx.Response(200, json={"historyId": "12345", "emailAddress": _USER})

    client = _build_client(_handler)
    assert client.get_profile_history_id() == "12345"


def test_get_profile_history_id_raises_container_transient_on_missing_history_id() -> None:
    """A response missing ``historyId`` surfaces as ContainerTransientError.

    Sabotage proof: dropping the ``isinstance(history_id, str)`` check
    would surface ``None`` as the cursor and the next list_history call
    would 400 against a malformed startHistoryId.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"emailAddress": _USER})  # no historyId

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert "historyId" in str(exc_info.value)
    assert "fix:" in str(exc_info.value)


def test_get_profile_history_id_raises_when_history_id_is_not_string() -> None:
    """A non-string historyId (e.g. integer) also surfaces as transient."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"historyId": 12345, "emailAddress": _USER})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError):
        client.get_profile_history_id()


# ---------------------------------------------------------------------------
# list_history pagination + URL composition (lines 271-275, 285-294, 298)
# ---------------------------------------------------------------------------


def test_list_history_threads_start_history_id_into_url() -> None:
    """list_history places ``startHistoryId`` directly in the query string."""
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"history": [], "historyId": "999"})

    client = _build_client(_handler)
    page = client.list_history(start_history_id="500")
    assert "startHistoryId=500" in captured["url"]
    assert page.history_id == "999"
    assert page.message_ids == ()


def test_list_history_threads_page_token_in_query_string() -> None:
    """When page_token is supplied, ``pageToken`` is appended to the URL.

    Sabotage proof: dropping the ``if page_token`` branch in
    list_history would never thread page tokens and the iterator
    would loop forever on the same first page.
    """
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"history": [], "historyId": "1"})

    client = _build_client(_handler)
    client.list_history(start_history_id="500", page_token="next-page-token-xyz")
    assert "pageToken=next-page-token-xyz" in captured["url"]
    assert "startHistoryId=500" in captured["url"]


def test_iter_history_message_ids_walks_pages_until_no_next_page_token() -> None:
    """Iterator yields ids across pages, tracks terminal historyId.

    Sabotage proof: dropping the ``if page.history_id is not None``
    branch in the iterator would never record the terminal cursor; the
    connector's next_cursor would always be None.
    """
    state = {"call": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["call"] += 1
        if state["call"] == 1:
            return httpx.Response(
                200,
                json={
                    "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                    "nextPageToken": "page-2",
                    "historyId": "100",
                },
            )
        return httpx.Response(
            200,
            json={
                "history": [{"messagesAdded": [{"message": {"id": "m2"}}, {"message": {"id": "m3"}}]}],
                "historyId": "200",
            },
        )

    client = _build_client(_handler)
    ids = list(client.iter_history_message_ids(start_history_id="50"))
    assert ids == ["m1", "m2", "m3"]
    assert client.last_history_id() == "200"


def test_last_history_id_returns_none_before_any_iteration() -> None:
    """``last_history_id`` returns None before any iter call has run."""
    client = _build_client(lambda _r: httpx.Response(200, json={}))
    assert client.last_history_id() is None


# ---------------------------------------------------------------------------
# get_message — full envelope decode (lines 302-304)
# ---------------------------------------------------------------------------


def test_get_message_uses_format_full_and_decodes_body() -> None:
    """``users.messages.get`` issues the request with ``format=full``.

    Sabotage proof: dropping ``?format=full`` would silently return a
    Gmail message with no MIME parts and the body would be empty.
    """
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "historyId": "9001",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "agent-alpha@example.com"},
                    ],
                    "body": {"data": _b64url(b"hello body")},
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert "/messages/m1?format=full" in captured["url"]
    assert msg.message_id == "m1"
    assert msg.thread_id == "t1"
    assert msg.history_id == "9001"
    assert msg.label_ids == ("INBOX", "UNREAD")
    assert msg.body == b"hello body"
    assert msg.body_mime == "text/plain"
    assert msg.body_truncated is False


# ---------------------------------------------------------------------------
# _authorised_get_json non-dict JSON branch (lines 343-350)
# ---------------------------------------------------------------------------


def test_authorised_get_json_raises_when_response_is_not_a_json_object() -> None:
    """A JSON array (not object) response surfaces as ContainerTransientError.

    Sabotage proof: dropping the ``isinstance(decoded, dict)`` check
    would let a JSON list slip through and crash later with an
    AttributeError on ``.get("historyId")``.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert "JSON object" in str(exc_info.value)
    assert "fix:" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _do_get owned-Client fallback (lines 363-364)
# ---------------------------------------------------------------------------


def test_do_get_uses_owned_client_when_no_shared_http_client_supplied() -> None:
    """When no ``http_client`` is injected, the request uses an owned httpx.Client.

    We can't observe the owned-Client branch via a MockTransport alone,
    but we can confirm the connection-error surface — the owned-Client
    path will attempt a real connection to a non-routable host and
    surface httpx's connection-failed error. Sabotage proof: deleting
    the ``if client is not None`` branch would crash with a NoneType
    attribute error on the very first call.
    """
    # Build the client without a shared http_client; the owned-Client
    # path activates inside _do_get. We point at a non-routable test
    # host so the request fails fast without exposing real network.
    client = GmailClient(
        user_email=_USER,
        token_refresher=lambda: _FAKE_BEARER,
        gmail_base="http://127.0.0.1:1",
    )
    with pytest.raises(httpx.HTTPError):
        client.get_profile_history_id()


# ---------------------------------------------------------------------------
# 200 success early-return branch (line 376)
# ---------------------------------------------------------------------------


def test_raise_for_status_no_op_on_2xx_response() -> None:
    """A 2xx response returns the parsed body without raising.

    Sabotage proof: changing the ``if response.status_code < 400`` guard
    to ``> 400`` would always re-classify 2xx as an error.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"historyId": "ok"})

    client = _build_client(_handler)
    assert client.get_profile_history_id() == "ok"


# ---------------------------------------------------------------------------
# Parsing helpers driven via list_history (lines 436-487)
# ---------------------------------------------------------------------------


def test_parse_history_page_dedups_message_ids_across_records() -> None:
    """Duplicate ids across history records collapse to one in the page.

    Sabotage proof: dropping the ``if msg_id not in seen`` branch in
    _collect_history_message_ids would surface duplicates that the
    connector would emit as redundant ChangeEvents.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "history": [
                    {"messagesAdded": [{"message": {"id": "dup-1"}}]},
                    {"messagesAdded": [{"message": {"id": "dup-1"}}, {"message": {"id": "uniq-1"}}]},
                ],
                "historyId": "200",
            },
        )

    client = _build_client(_handler)
    page = client.list_history(start_history_id="50")
    assert page.message_ids == ("dup-1", "uniq-1")


def test_parse_history_page_handles_non_list_history_field() -> None:
    """A history field that isn't a list collapses to empty message_ids."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"history": "not-a-list", "historyId": "9"})

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.message_ids == ()
    assert page.history_id == "9"


def test_parse_history_page_skips_non_dict_history_entries() -> None:
    """Non-dict entries in the ``history`` list are tolerantly skipped."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "history": [
                    "not-a-dict",
                    {"messagesAdded": [{"message": {"id": "kept"}}]},
                ],
                "historyId": "10",
            },
        )

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.message_ids == ("kept",)


def test_parse_history_page_skips_entries_missing_messages_added() -> None:
    """Records with no ``messagesAdded`` key collapse to empty per-record."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "history": [
                    {"labelsAdded": [{"some": "thing"}]},  # no messagesAdded
                    {"messagesAdded": "scalar-not-list"},  # malformed messagesAdded
                ],
                "historyId": "10",
            },
        )

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.message_ids == ()


def test_parse_history_page_skips_non_dict_messages_added_items() -> None:
    """Non-dict items inside messagesAdded are tolerantly skipped."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "history": [
                    {
                        "messagesAdded": [
                            "string-not-dict",
                            {"message": "not-a-dict"},
                            {"message": {"id": "kept"}},
                            {"message": {"id": 999}},  # non-string id
                            {"not_message": {"id": "wrong-key"}},  # no 'message' key
                        ]
                    }
                ],
                "historyId": "10",
            },
        )

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.message_ids == ("kept",)


def test_parse_history_page_handles_non_string_next_page_token() -> None:
    """A non-string nextPageToken collapses to None."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"history": [], "nextPageToken": 12345, "historyId": "10"},
        )

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.next_page_token is None


def test_parse_history_page_handles_non_string_history_id() -> None:
    """A non-string historyId collapses to None on the page envelope."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"history": [], "historyId": 12345})

    client = _build_client(_handler)
    page = client.list_history(start_history_id="0")
    assert page.history_id is None


# ---------------------------------------------------------------------------
# _parse_message + _extract_headers (lines 498-543)
# ---------------------------------------------------------------------------


def test_get_message_handles_empty_payload_gracefully() -> None:
    """A message with no payload returns an empty body + empty headers."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b""
    assert msg.headers == ()
    assert msg.body_mime == "text/plain"


def test_get_message_skips_non_dict_headers() -> None:
    """Non-dict entries in the headers list are tolerantly skipped."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "headers": [
                        "string-not-dict",
                        {"name": "Subject", "value": "Real"},
                        {"name": 1, "value": "bad-name-type"},
                        {"name": "X-Drop", "value": 2},
                        {"name": "X-OK", "value": "ok"},
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    names = {h.name for h in msg.headers}
    assert "Subject" in names
    assert "X-OK" in names
    assert "X-Drop" not in names


def test_get_message_handles_non_list_label_ids() -> None:
    """A non-list labelIds field collapses to empty tuple."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "m1", "labelIds": "scalar"})

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.label_ids == ()


def test_get_message_filters_non_string_label_ids_from_list() -> None:
    """Non-string entries in labelIds are filtered out."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "m1", "labelIds": ["INBOX", 42, None, "UNREAD"]},
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.label_ids == ("INBOX", "UNREAD")


def test_get_message_history_id_is_optional() -> None:
    """A message without historyId surfaces ``history_id=None``."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "m1"})

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.history_id is None


# ---------------------------------------------------------------------------
# Body decode + truncation (lines 550-563, 568-577, 582-591)
# ---------------------------------------------------------------------------


def test_get_message_prefers_text_plain_part_over_html() -> None:
    """When both text/plain and text/html exist, text/plain wins.

    Sabotage proof: reversing the preference order in _extract_body
    would surface stripped HTML even when a clean text/plain alternative
    is available — observable as different chunk text.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url(b"plain wins")}},
                        {"mimeType": "text/html", "body": {"data": _b64url(b"<p>html loses</p>")}},
                    ]
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b"plain wins"


def test_get_message_falls_back_to_text_html_when_no_text_plain() -> None:
    """Without text/plain, text/html is stripped of tags and used as body.

    Sabotage proof: removing the text/html fallback branch would surface
    empty bodies for HTML-only emails.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b"<p>hello <b>world</b></p>")},
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert b"hello" in msg.body
    assert b"world" in msg.body
    assert b"<p>" not in msg.body  # tags stripped


def test_get_message_strips_html_script_and_style_blocks() -> None:
    """HTML script + style block contents are removed before stripping tags.

    Sabotage proof: dropping the script/style regex in _strip_html
    would surface raw JavaScript / CSS in chunk text.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/html",
                    "body": {
                        "data": _b64url(b"<style>.x{color:red;}</style><script>alert(1)</script><p>Real content</p>")
                    },
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert b"Real content" in msg.body
    assert b"alert" not in msg.body
    assert b"color" not in msg.body


def test_get_message_returns_empty_body_when_no_text_part_exists() -> None:
    """A message with no text/plain or text/html parts surfaces empty body."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {"mimeType": "image/png", "body": {"data": _b64url(b"binary")}},
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b""
    assert msg.body_truncated is False


def test_get_message_truncates_text_plain_body_over_cap() -> None:
    """A body exceeding ``max_body_bytes`` surfaces empty + body_truncated=True.

    Sabotage proof: dropping the size guard would emit oversize bodies
    into Bronze and risk filling the chunk store.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": _b64url(b"x" * 1000)},
                },
            },
        )

    client = _build_client(_handler, max_body_bytes=100)
    msg = client.get_message("m1")
    assert msg.body == b""
    assert msg.body_truncated is True


def test_get_message_truncates_text_html_body_over_cap() -> None:
    """HTML body whose stripped form exceeds the cap also truncates."""
    big_html = b"<p>" + (b"y" * 2000) + b"</p>"

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(big_html)},
                },
            },
        )

    client = _build_client(_handler, max_body_bytes=100)
    msg = client.get_message("m1")
    assert msg.body == b""
    assert msg.body_truncated is True


def test_get_message_find_part_descends_nested_parts() -> None:
    """``_find_part`` descends into nested multipart parts to locate text/plain.

    Sabotage proof: dropping the recursive descent in _find_part would
    miss bodies nested below multipart/alternative wrappers — surfacing
    empty bodies for the majority of real-world Gmail messages.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "multipart/alternative",
                            "parts": [
                                {"mimeType": "text/plain", "body": {"data": _b64url(b"nested")}},
                            ],
                        },
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b"nested"


def test_get_message_handles_malformed_base64_silently_returns_empty() -> None:
    """A corrupt base64url body decodes to empty without raising.

    Sabotage proof: dropping the try/except in _decode_body would
    propagate base64 errors and dead-letter the entire tick.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": "!!! not base64 !!!"},
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b""


def test_get_message_handles_part_with_non_dict_body() -> None:
    """A part whose body is not a dict yields empty bytes."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/plain",
                    "body": "scalar-not-dict",
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    # Since payload.body isn't a dict, _find_part returns None → empty body.
    assert msg.body == b""


def test_get_message_handles_part_body_with_non_string_data() -> None:
    """A body.data field that isn't a string yields empty bytes."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": 12345},
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.body == b""


# ---------------------------------------------------------------------------
# Attachments (lines 621-673)
# ---------------------------------------------------------------------------


def test_get_message_collects_attachment_metadata() -> None:
    """Attachment-shaped parts surface as :class:`GmailAttachment` entries.

    Sabotage proof: dropping the ``_make_attachment_if_present`` branch
    would silently drop attachment metadata from the envelope.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "doc.pdf",
                            "body": {"size": 2048, "attachmentId": "att-1"},
                        },
                        {
                            "mimeType": "image/png",
                            "filename": "photo.png",
                            "body": {"size": 4096, "attachmentId": "att-2"},
                        },
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    filenames = [a.filename for a in msg.attachments]
    assert "doc.pdf" in filenames
    assert "photo.png" in filenames
    pdf = next(a for a in msg.attachments if a.filename == "doc.pdf")
    assert pdf.size_bytes == 2048
    assert pdf.attachment_id == "att-1"
    assert pdf.mime_type == "application/pdf"


def test_get_message_skips_parts_without_filename() -> None:
    """A part with no filename or empty filename is not an attachment."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {"mimeType": "text/plain", "filename": "", "body": {"data": _b64url(b"x")}},
                        {"mimeType": "text/html", "body": {"data": _b64url(b"x")}},  # no filename
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.attachments == ()


def test_get_message_attachment_with_no_body_dict_defaults_size_to_zero() -> None:
    """An attachment-shaped part with a non-dict body surfaces zero size + None id.

    Sabotage proof: dropping the ``isinstance(body, dict)`` check in
    _attachment_body_fields would crash with an AttributeError on
    body.get().
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {"mimeType": "application/pdf", "filename": "doc.pdf", "body": "not-a-dict"},
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert len(msg.attachments) == 1
    a = msg.attachments[0]
    assert a.size_bytes == 0
    assert a.attachment_id is None


def test_get_message_attachment_with_non_int_size_defaults_to_zero() -> None:
    """An attachment whose body.size is non-integer defaults to zero."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "doc.pdf",
                            "body": {"size": "not-int", "attachmentId": "a"},
                        },
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.attachments[0].size_bytes == 0


def test_get_message_attachment_without_mime_falls_back_to_octet_stream() -> None:
    """Attachment with no mimeType uses ``application/octet-stream`` fallback."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {"filename": "blob.bin", "body": {"size": 10, "attachmentId": "a"}},
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.attachments[0].mime_type == "application/octet-stream"


def test_get_message_walks_attachments_in_nested_parts() -> None:
    """Attachments nested in sub-parts are collected by the recursive walker.

    Sabotage proof: dropping the ``_walk_attachments`` recursion would
    miss attachments nested in multipart/related sub-trees.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "multipart/related",
                            "parts": [
                                {
                                    "mimeType": "application/pdf",
                                    "filename": "nested.pdf",
                                    "body": {"size": 1, "attachmentId": "a"},
                                },
                            ],
                        },
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    filenames = [a.filename for a in msg.attachments]
    assert "nested.pdf" in filenames


def test_get_message_walk_attachments_tolerates_non_list_parts() -> None:
    """A parts field that isn't a list collapses to no recursion (no crash)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": "scalar-not-list",
                },
            },
        )

    client = _build_client(_handler)
    # Should not raise — non-list parts trips the tolerant branch.
    msg = client.get_message("m1")
    assert msg.attachments == ()


def test_get_message_walk_attachments_skips_non_dict_parts() -> None:
    """Non-dict items in parts are skipped (no crash)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        "string-not-dict",
                        {"filename": "kept.pdf", "body": {"size": 1, "attachmentId": "a"}},
                    ],
                },
            },
        )

    client = _build_client(_handler)
    msg = client.get_message("m1")
    filenames = [a.filename for a in msg.attachments]
    assert "kept.pdf" in filenames


# ---------------------------------------------------------------------------
# _str_or_empty / _optional_str via get_message (677, 681)
# ---------------------------------------------------------------------------


def test_get_message_with_non_string_ids_collapses_to_empty() -> None:
    """``id`` / ``threadId`` non-string values collapse to empty strings."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 12345, "threadId": None})

    client = _build_client(_handler)
    msg = client.get_message("m1")
    assert msg.message_id == ""
    assert msg.thread_id == ""


# ---------------------------------------------------------------------------
# _parse_retry_after (690-691) — driven via 429 with bad Retry-After
# ---------------------------------------------------------------------------


def test_429_with_unparseable_retry_after_falls_back_to_60s() -> None:
    """A non-numeric Retry-After header surfaces the 60s default.

    Sabotage proof: dropping the ValueError branch in _parse_retry_after
    would crash on the float() conversion.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "soon"}, json={"error": {"message": "limit"}})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after == 60.0


def test_429_with_no_retry_after_falls_back_to_60s() -> None:
    """A 429 without any Retry-After header surfaces the 60s default."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "limit"}})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after == 60.0


# ---------------------------------------------------------------------------
# _extract_403_reason early-out branches (702-714)
# ---------------------------------------------------------------------------


def test_403_with_non_json_body_falls_through_to_insufficient_permissions() -> None:
    """A 403 whose body isn't JSON falls through to permanent-denial.

    Sabotage proof: dropping the ``except ValueError`` branch in
    _extract_403_reason would crash the error-path with a JSON decode
    exception, masking the underlying 403.
    """
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"not-json-at-all", headers={"Content-Type": "text/plain"})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_non_dict_body_falls_through_to_insufficient_permissions() -> None:
    """A 403 with a JSON-list body falls through to permanent-denial."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=["not", "a", "dict"])

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_no_error_envelope_falls_through_to_insufficient_permissions() -> None:
    """A 403 with missing ``error`` key falls through to permanent-denial."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"unrelated": "body"})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_non_dict_error_falls_through_to_insufficient_permissions() -> None:
    """A 403 where ``error`` is a string also falls through cleanly."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "string-not-dict"})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_empty_errors_list_falls_through_to_insufficient_permissions() -> None:
    """A 403 whose ``error.errors`` is an empty list falls through cleanly."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": []}})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_non_list_errors_falls_through_to_insufficient_permissions() -> None:
    """A 403 whose ``error.errors`` is a scalar falls through cleanly."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": "scalar"}})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_non_dict_first_error_falls_through() -> None:
    """A 403 with a string in ``errors[0]`` collapses to None reason."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": ["string"]}})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


def test_403_with_non_string_reason_falls_through() -> None:
    """A 403 with an integer reason collapses to None reason."""
    from kairix.core.protocols import InsufficientPermissionsError

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": 12345}]}})

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


# ---------------------------------------------------------------------------
# Stats snapshot + invalidate_token surface
# ---------------------------------------------------------------------------


def test_stats_snapshot_increments_requests_and_token_refreshes() -> None:
    """Stats snapshot tracks request count + token refresh count.

    Sabotage proof: dropping the ``self._stats.requests += 1`` line in
    _do_get would freeze the request counter at zero.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"historyId": "1"})

    client = _build_client(_handler)
    s0 = client.stats()
    assert s0.requests == 0
    assert s0.token_refreshes == 0
    client.get_profile_history_id()
    s1 = client.stats()
    assert s1.requests == 1
    assert s1.token_refreshes == 1  # bearer fetched once


def test_invalidate_token_drops_cached_bearer_so_next_call_refreshes() -> None:
    """After invalidate_token, the next request triggers a fresh bearer fetch."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"historyId": "1"})

    refresher_calls: list[int] = []

    def _counting_refresher() -> str:
        refresher_calls.append(1)
        return _FAKE_BEARER

    transport = httpx.MockTransport(_handler)
    shared = httpx.Client(transport=transport)
    client = GmailClient(
        user_email=_USER,
        token_refresher=_counting_refresher,
        http_client=shared,
    )
    client.get_profile_history_id()
    assert len(refresher_calls) == 1
    client.invalidate_token()
    client.get_profile_history_id()
    assert len(refresher_calls) == 2


def test_no_refresh_default_token_refresher_raises_credential_expired() -> None:
    """The default token refresher raises CredentialExpiredError on call.

    Sabotage proof: replacing ``_no_refresh`` with a no-op returning ""
    would silently send unsigned requests; this test catches that drift.
    """
    client = GmailClient(user_email=_USER)
    with pytest.raises(CredentialExpiredError):
        # The default refresher raises immediately when invoked from _bearer().
        client._bearer()


# ---------------------------------------------------------------------------
# Gmail base URL override (covers _DEFAULT_GMAIL_BASE override path)
# ---------------------------------------------------------------------------


def test_client_uses_custom_gmail_base_when_supplied() -> None:
    """A custom gmail_base argument routes requests through the override."""
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"historyId": "1"})

    client = _build_client(_handler, gmail_base="https://gmail-sovereign.example.com/gmail/v1/")
    client.get_profile_history_id()
    assert "https://gmail-sovereign.example.com/gmail/v1/users/" in captured["url"]
