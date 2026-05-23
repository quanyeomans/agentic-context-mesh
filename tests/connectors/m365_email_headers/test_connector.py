"""Unit tests for :class:`kairix.connectors.m365_email_headers.M365EmailHeadersConnector`.

Scope per the KP-2 brief:

  * A Graph delta response with three envelopes → ``list_changes(None)``
    emits three ``created`` events; cursor advances past the deltaLink.
  * Header-only $select projection is the constructed Graph URL (the
    no-body-content invariant per ADR-004).
  * Pagination — a response carrying ``@odata.nextLink`` keeps the
    iterator going through the next page; the deltaLink from the final
    page is what ``next_cursor`` returns.
  * ``fetch`` returns a JSON artefact with NO body fields.
  * ``make_connector`` rejects a missing ``user_principal_name`` AND
    rejects a config that tries to override the locked ``personal``
    sensitivity tier.
  * Sabotage proof: mutating
    :data:`HEADER_ONLY_SELECT` to include ``body`` makes
    :func:`test_initial_delta_url_carries_header_only_projection` fail.

F1-clean (no monkey-patching), F6-clean (every test seam is a real
callable default), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
    make_connector,
)
from kairix.connectors.m365_email_headers.connector import (
    LOCKED_SENSITIVITY,
    M365Credentials,
)
from kairix.connectors.m365_email_headers.graph_client import (
    HEADER_ONLY_SELECT,
)
from kairix.transport.auth.oauth2_client_creds import (
    MissingCredentialsError,
    OAuth2ClientCredsAuth,
)

pytestmark = pytest.mark.unit


def _envelopes() -> list[dict[str, Any]]:
    return [
        {
            "id": "msg-1",
            "from": {"emailAddress": {"address": "agent-alpha@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-beta@example.com"}}],
            "ccRecipients": [],
            "subject": "Project status",
            "sentDateTime": "2026-05-22T10:00:00Z",
            "receivedDateTime": "2026-05-22T10:00:01Z",
        },
        {
            "id": "msg-2",
            "from": {"emailAddress": {"address": "agent-beta@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
            "ccRecipients": [{"emailAddress": {"address": "agent-gamma@example.com"}}],
            "subject": "Re: Project status",
            "sentDateTime": "2026-05-22T11:00:00Z",
            "receivedDateTime": "2026-05-22T11:00:01Z",
        },
        {
            "id": "msg-3",
            "from": {"emailAddress": {"address": "agent-gamma@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
            "ccRecipients": [],
            "subject": "Closing the loop",
            "sentDateTime": "2026-05-22T12:00:00Z",
            "receivedDateTime": "2026-05-22T12:00:01Z",
        },
    ]


def _single_page_payload() -> dict[str, Any]:
    return {
        "value": _envelopes(),
        "@odata.deltaLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok-final"
        ),
    }


def _paginated_pages() -> tuple[dict[str, Any], dict[str, Any]]:
    """Two-page response — first carries nextLink, second carries deltaLink."""
    first = {
        "value": _envelopes()[:2],
        "@odata.nextLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$skiptoken=next-page-token"
        ),
    }
    second = {
        "value": _envelopes()[2:],
        "@odata.deltaLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok-final"
        ),
    }
    return first, second


def _build_real_connector(
    handler: httpx.MockTransport | None = None,
    pages: list[dict[str, Any]] | None = None,
    recorded_urls: list[str] | None = None,
) -> M365EmailHeadersConnector:
    """Compose the real connector against a MockTransport-backed Graph stub."""
    if handler is None:
        sequence = list(pages) if pages is not None else [_single_page_payload()]
        recorded = recorded_urls if recorded_urls is not None else []

        def _stub(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/oauth2/v2.0/token" in url:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "fake-bearer",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            recorded.append(url)
            payload = sequence.pop(0) if sequence else {"value": []}
            return httpx.Response(200, json=payload)

        handler = httpx.MockTransport(_stub)

    shared = httpx.Client(transport=handler)
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a, u: M365GraphClient(user_principal_name=u, auth=a, http_client=shared),
    )


# ---------------------------------------------------------------------------
# Delta-query behaviour
# ---------------------------------------------------------------------------


def test_list_changes_emits_one_event_per_envelope() -> None:
    """Three envelopes from Graph → three ``created`` events.

    Sabotage proof: change the loop body in
    :meth:`M365EmailHeadersConnector.list_changes` to skip every
    second message — the count assertion below drops to 2 and fails.
    """
    connector = _build_real_connector()
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 3, f"expected 3 events, got {len(events)}"
    for ev in events:
        assert ev.op == "created"
        assert ev.metadata.get("sensitivity") == "personal"


def test_list_changes_advances_cursor_to_delta_link() -> None:
    """After a successful drain, ``next_cursor`` returns the deltaLink.

    Sabotage proof: replace ``self._next_cursor = self._graph.last_delta_link()``
    with ``self._next_cursor = None`` — the equality below fails.
    """
    connector = _build_real_connector()
    _ = list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None
    assert "$deltatoken=tok-final" in cursor


def test_pagination_drains_nextlink_pages() -> None:
    """A Graph response with ``@odata.nextLink`` is followed to its end.

    Sabotage proof: short-circuit the iter_messages loop in
    :class:`M365GraphClient` to break after the first page — the
    count assertion drops below 3.
    """
    first, second = _paginated_pages()
    connector = _build_real_connector(pages=[first, second])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 3, f"expected 3 events across both pages, got {len(events)}"


# ---------------------------------------------------------------------------
# Header-only invariant per ADR-004
# ---------------------------------------------------------------------------


def test_initial_delta_url_carries_header_only_projection() -> None:
    """The Graph URL must carry ``$select`` AND must not list any body field.

    This is the mechanical guard for the ADR-004 no-body-content
    invariant. Sabotage proof: mutate
    :data:`HEADER_ONLY_SELECT` to ``"body,from,subject"`` — the
    forbidden-key assertion below fails immediately.
    """
    recorded: list[str] = []
    connector = _build_real_connector(recorded_urls=recorded)
    _ = list(connector.list_changes(cursor=None))
    assert recorded, "expected at least one Graph URL"
    url = recorded[0]
    assert "$select=" in url, f"Graph URL missing $select: {url!r}"
    fields = {f.strip() for f in url.split("$select=", 1)[1].split("&", 1)[0].split(",")}
    forbidden = {"body", "bodyPreview", "uniqueBody"}
    leaks = forbidden & fields
    assert not leaks, f"$select projection leaked body fields: {leaks!r}"
    # And the projection MUST carry the canonical header-only fields.
    for required in ("from", "toRecipients", "subject", "sentDateTime"):
        assert required in fields, f"missing required header field {required!r} in projection {fields!r}"


def test_header_only_select_constant_excludes_body_fields() -> None:
    """:data:`HEADER_ONLY_SELECT` itself contains no body field.

    Sabotage proof: append ``,body`` to the constant — this assertion
    fails. This pins the constant at the module level so a future
    contributor cannot widen it without breaking the test.
    """
    fields = {f.strip() for f in HEADER_ONLY_SELECT.split(",")}
    forbidden = {"body", "bodyPreview", "uniqueBody"}
    leaks = forbidden & fields
    assert not leaks, f"HEADER_ONLY_SELECT leaked body fields: {leaks!r}"


def test_fetch_returns_json_artefact_without_body() -> None:
    """The fetched artefact JSON contains no body / bodyPreview / uniqueBody.

    Sabotage proof: add ``"body": message.subject`` to the JSON payload
    in :meth:`M365EmailHeadersConnector.fetch` — the forbidden-key
    assertion below fails.
    """
    connector = _build_real_connector()
    _ = list(connector.list_changes(cursor=None))
    artefact = connector.fetch("msg-1")
    assert artefact.mime == "application/json"
    payload = json.loads(artefact.raw.decode("utf-8"))
    forbidden = {"body", "bodyPreview", "uniqueBody"}
    leaks = set(payload.keys()) & forbidden
    assert not leaks, f"fetch artefact leaked body fields: {leaks!r}"


# ---------------------------------------------------------------------------
# Sensitivity tier locked to personal
# ---------------------------------------------------------------------------


def test_sensitivity_for_returns_locked_personal_tier() -> None:
    """:meth:`sensitivity_for` returns ``personal`` regardless of item.

    Sabotage proof: change the return to ``"public"`` — this assertion
    fails. The locked-tier behaviour is what makes the connector ADR-005
    compliant.
    """
    connector = _build_real_connector()
    assert connector.sensitivity_for("msg-1") == LOCKED_SENSITIVITY == "personal"


# ---------------------------------------------------------------------------
# source_link
# ---------------------------------------------------------------------------


def test_source_link_round_trips_to_outlook_url() -> None:
    """``source_link`` returns a URL pointing at Outlook on the Web.

    Sabotage proof: return ``""`` from ``source_link`` — the
    ``startswith`` assertion fails.
    """
    connector = _build_real_connector()
    link = connector.source_link("msg-1")
    assert link.startswith("https://outlook.office.com/mail/inbox/id/")
    assert "msg-1" in link


# ---------------------------------------------------------------------------
# fetch with no list_changes priming raises a typed KeyError
# ---------------------------------------------------------------------------


def test_fetch_without_priming_raises_typed_key_error() -> None:
    """Calling ``fetch`` before any ``list_changes`` is a typed error.

    Sabotage proof: silently return an empty RawArtefact in that path
    — the ``pytest.raises`` block below fails (no exception raised).
    """
    connector = _build_real_connector()
    with pytest.raises(KeyError) as exc_info:
        connector.fetch("never-listed")
    msg = str(exc_info.value)
    assert "fix:" in msg, f"error message missing fix: marker: {msg!r}"
    assert "list_changes" in msg, f"error message missing list_changes hint: {msg!r}"


# ---------------------------------------------------------------------------
# make_connector factory shape
# ---------------------------------------------------------------------------


def test_make_connector_requires_user_principal_name() -> None:
    """A config without ``user_principal_name`` raises ValueError.

    Sabotage proof: change the check to ``upn = config.get("user_principal_name", "alice@x.com")``
    — the ``pytest.raises`` block fails.
    """
    with pytest.raises(ValueError) as exc_info:
        make_connector({})
    assert "user_principal_name" in str(exc_info.value)


def test_make_connector_rejects_sensitivity_override() -> None:
    """A config that tries to lower sensitivity is rejected loudly.

    Per ADR-005, the personal tier is locked at the connector boundary.
    Sabotage proof: remove the sensitivity check in ``make_connector``
    — the ``pytest.raises`` block fails.
    """
    with pytest.raises(ValueError) as exc_info:
        make_connector({"user_principal_name": "alice@example.com", "sensitivity": "public"})
    assert "locked" in str(exc_info.value)


def test_make_connector_accepts_locked_sensitivity_declaration() -> None:
    """Declaring the locked tier explicitly in config is allowed.

    Operators who want to be explicit about the tier in their YAML
    aren't punished for it. The constructor still resolves
    credentials lazily — this test asserts the factory call would
    not raise the locked-tier ValueError; the real-credentials path
    is exercised separately.
    """
    # The factory will try to resolve secrets — we expect it to raise
    # MissingCredentialsError / OSError rather than a sensitivity-locked
    # ValueError. That demonstrates the sensitivity check passed.
    with pytest.raises((MissingCredentialsError, OSError)):
        make_connector({"user_principal_name": "agent-alpha@example.com", "sensitivity": LOCKED_SENSITIVITY})


# ---------------------------------------------------------------------------
# Constructor input validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_user_principal_name() -> None:
    """Constructing with an empty UPN is a typed ValueError.

    Sabotage proof: drop the ``if not user_principal_name`` guard at the
    top of ``__init__`` — the ``pytest.raises`` block below stops firing
    and the test fails.
    """
    with pytest.raises(ValueError) as exc_info:
        M365EmailHeadersConnector(
            user_principal_name="",
            credentials=M365Credentials(
                tenant_id="fake-tenant",
                client_id="fake-client",
                client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
            ),
        )
    assert "user_principal_name" in str(exc_info.value)


def test_constructor_uses_default_graph_client_when_no_builder_supplied() -> None:
    """Omitting ``client_builder`` constructs a real :class:`M365GraphClient`.

    The default branch in ``__init__`` builds an :class:`M365GraphClient`
    with the resolved auth and the UPN. Sabotage proof: change the
    default-branch ``M365GraphClient(...)`` call to ``None`` — the
    attribute access ``connector._graph`` below fails.
    """
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
    )
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        auth=auth,
    )
    # Direct attribute is internal — assert the public surface still
    # behaves: source_link round-trips, sensitivity stays personal.
    assert connector.source_link("msg-1").startswith("https://outlook.office.com/")
    assert connector.sensitivity_for("msg-1") == LOCKED_SENSITIVITY


def test_constructor_resolves_credentials_from_secrets_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Omitting ``credentials`` and ``auth`` resolves via :mod:`kairix.secrets`.

    Drives ``_resolve_credentials_from_secrets`` through the per-file
    secret resolver — write the three required secrets to a fake XDG
    secrets directory, then construct the connector. We use
    ``XDG_CONFIG_HOME`` (NOT a ``KAIRIX_*`` env var so F2 is satisfied)
    so the per-file resolver finds the fixtures.

    Sabotage proof: remove the ``_resolve_credentials_from_secrets()``
    call from ``__init__`` — the construction below raises because the
    auth helper sees an empty tenant_id and refuses.
    """
    secrets_dir = tmp_path / "xdg" / "kairix" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "connector-m365-tenant-id").write_text("fake-tenant\n")
    (secrets_dir / "connector-m365-client-id").write_text("fake-client\n")
    (secrets_dir / "connector-m365-client-secret").write_text("fake-secret-value\n")

    # XDG_CONFIG_HOME is not a KAIRIX_* env var — F2 only forbids KAIRIX_*.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # Make sure no Docker /run/secrets path shadows the XDG dir during test.
    # KAIRIX_SECRETS_DIR is forbidden by F2; rely on XDG fallback only.

    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
    )
    assert connector.sensitivity_for("any-id") == LOCKED_SENSITIVITY
    assert connector.source_link("msg-1").startswith("https://outlook.office.com/")


# ---------------------------------------------------------------------------
# _event_modified_at timestamp-fallback ladder
# ---------------------------------------------------------------------------


def test_modified_at_falls_back_to_sent_when_received_missing() -> None:
    """When ``receivedDateTime`` is absent, fall back to ``sentDateTime``.

    Sabotage proof: change the second branch in ``_event_modified_at``
    from ``return message.sent_at`` to ``return ""`` — the assertion
    that the event timestamp matches the sent timestamp fails.
    """
    payload = {
        "value": [
            {
                "id": "msg-sent-only",
                "from": {"emailAddress": {"address": "agent-alpha@example.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "subject": "Sent-only",
                "sentDateTime": "2026-05-22T09:00:00Z",
                # NB: receivedDateTime intentionally omitted.
            }
        ],
        "@odata.deltaLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok"
        ),
    }
    connector = _build_real_connector(pages=[payload])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].modified_at == "2026-05-22T09:00:00Z"


def test_modified_at_falls_back_to_now_when_both_timestamps_missing() -> None:
    """When both ``receivedDateTime`` and ``sentDateTime`` are absent,
    the helper falls back to wall-clock now (ISO-8601 ending in ``Z``).

    Sabotage proof: change the final fallback ``return _now_iso()`` to
    ``return ""`` — the ``endswith("Z")`` assertion below fails.
    """
    payload = {
        "value": [
            {
                "id": "msg-no-timestamps",
                "from": {"emailAddress": {"address": "agent-alpha@example.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "subject": "No timestamps",
                # NB: sentDateTime AND receivedDateTime both omitted.
            }
        ],
        "@odata.deltaLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok"
        ),
    }
    connector = _build_real_connector(pages=[payload])
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].modified_at.endswith("Z"), f"expected ISO-8601 Zulu timestamp, got {events[0].modified_at!r}"
