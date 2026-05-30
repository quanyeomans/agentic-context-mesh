"""Thin Gmail API client for full-message body + envelope retrieval.

A focused wrapper around ``httpx.Client`` for the Gmail REST API. Three
commitments:

  1. **Full-message retrieval** (Onyx Gmail design). Every message
     ``users.messages.get`` call uses ``format=full`` so the response
     carries the MIME parts the connector decodes into a body and the
     envelope headers (Subject / From / To / Cc / Bcc / Date) it lifts
     into :class:`~kairix.core.protocols.SourceMetadata`. Attachments
     are surfaced as metadata only (filename / size / mime); the
     attachment body is intentionally not fetched in v1 (the Drive
     connector is the right home for attachment bodies).

  2. **OAuth2 bearer auth.** Every request adds an
     ``Authorization: Bearer <token>`` header from the injected token
     callable. A 401 triggers a single token-refresh + retry; persistent
     401 propagates as :class:`CredentialExpiredError`.

  3. **History API pagination.** The connector hands an opaque
     ``historyId`` between ticks; the Gmail History API response is
     ``users.history.list?startHistoryId=<id>`` and surfaces both
     ``nextPageToken`` (more pages this run) and ``historyId`` (the
     next-tick cursor). The cold-start path calls ``users.getProfile``
     to seed the first ``historyId``.

Per F37, the ``googleapiclient`` SDK is intentionally NOT used — the
SDK pulls a heavy transitive set; the History + Messages query surface
is a straightforward REST call. The client uses raw ``httpx`` and stays
under F37's allowed surface (this module lives at
``kairix/connectors/gmail/client.py``).

Typed-error contract per spec §5: a 429 / 503 / 403-with-rate-limit-
reason surfaces as :class:`ContainerTransientError` carrying a retry
budget; a 401 surfaces as :class:`CredentialExpiredError`; a 403
without rate-limit signal surfaces as :class:`InsufficientPermissionsError`.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Final

import httpx

from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    InsufficientPermissionsError,
)

logger = logging.getLogger(__name__)

# Default base URL — overrideable for sovereign clouds and test stubs.
_DEFAULT_GMAIL_BASE: Final[str] = "https://gmail.googleapis.com/gmail/v1"

# Per-request timeout. Gmail responses typically arrive in <1s; 60s
# covers a cold connection on a large message with many parts.
_GMAIL_REQUEST_TIMEOUT_S: Final[float] = 60.0

# 10 MB body cap per spec — mirrors the Onyx Gmail default. Messages
# larger than this surface as a body-truncated artefact (the metadata
# still propagates).
_DEFAULT_MAX_BODY_BYTES: Final[int] = 10 * 1024 * 1024

# MIME tag the connector reports for every body — text/plain is the
# preferred decode target and the fallback after HTML stripping. F17:
# extracted because the literal appears in ≥3 sites (body decode,
# fallback, empty case).
_MIME_TEXT_PLAIN: Final[str] = "text/plain"

# Gmail-specific 403 sub-reasons that mean "throttled" rather than
# "permission denied". The connector surfaces these as transient errors
# so the runner defers the tick instead of pausing the cc_pair.
_RATE_LIMIT_REASONS: Final[frozenset[str]] = frozenset(
    {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"}
)


@dataclass(frozen=True)
class GmailHeader:
    """One header line as projected from a Gmail message.

    The raw Gmail response carries headers as a list of
    ``{"name": ..., "value": ...}`` objects under ``payload.headers``.
    """

    name: str
    value: str


@dataclass(frozen=True)
class GmailAttachment:
    """One attachment surfaced by a Gmail message.

    The attachment body is NOT fetched in v1 — only the metadata
    (filename / size / mime) is surfaced. The Drive connector is the
    right home for attachment bodies.
    """

    filename: str
    mime_type: str
    size_bytes: int
    attachment_id: str | None


@dataclass(frozen=True)
class GmailMessage:
    """One Gmail message envelope + body.

    The dataclass shape captures the per-message data the connector
    surfaces to the orchestrator: the message id, the thread id, the
    historyId at which the message landed, the label ids, the
    envelope headers, the decoded body (text/plain preferred, text/html
    stripped fallback), the body mime tag, and the attachment metadata.

    ``body_truncated`` is True when the decoded body exceeded the
    configured cap (default 10 MB); the body field is empty in that
    case and the chunk-write path can pick up the metadata-only path.
    """

    message_id: str
    thread_id: str
    history_id: str | None
    label_ids: tuple[str, ...]
    headers: tuple[GmailHeader, ...]
    body: bytes
    body_mime: str
    body_truncated: bool
    attachments: tuple[GmailAttachment, ...]


@dataclass(frozen=True)
class HistoryPage:
    """One page of the ``users.history.list`` response.

    ``next_page_token`` is non-``None`` when more pages remain for the
    current sync window; the caller follows it before advancing the
    cursor. ``history_id`` is the next-tick cursor.

    ``message_ids`` is the deduplicated set of message ids that
    appeared in this page's ``messagesAdded`` events; the connector
    surfaces one ``created`` ChangeEvent per id.
    """

    message_ids: tuple[str, ...]
    next_page_token: str | None
    history_id: str | None


@dataclass
class _GmailStats:
    """Internal mutable counter set; snapshot returned via :meth:`stats`."""

    requests: int = 0
    rate_limited_403_total: int = 0
    token_refreshes: int = 0


@dataclass(frozen=True)
class GmailStatsSnapshot:
    """Frozen wire-side counter snapshot per F42."""

    requests: int
    rate_limited_403_total: int
    token_refreshes: int


def _no_refresh() -> str:
    """Default token-refresher — raises so the production path must
    supply a real refresher.

    F6-clean callable default; tests pass a stub returning a fake bearer.
    """
    raise CredentialExpiredError(
        "gmail: no token refresher configured. "
        "fix: pass token_refresher=... when constructing GmailClient. "
        "next: see kairix/connectors/gmail/client.py docstring."
    )


class GmailClient:
    """Thin Gmail REST wrapper for History + Messages queries.

    Args:
        user_email: The mailbox to sync — typically the OAuth-authorised
            user's primary email (``alice@example.com``). The mailbox
            is encoded into every URL path as ``users/<user_email>``;
            Gmail accepts ``me`` as an alias for the authorised user
            but we pass the explicit email so multi-user deploys can
            route per-mailbox.
        token_refresher: Callable returning a fresh bearer string. The
            client caches the bearer between calls and re-calls the
            refresher on 401 (single retry). Tests pass a stub
            returning a fixed bearer.
        gmail_base: Optional override for the Gmail base URL. Defaults
            to the public Gmail v1 endpoint.
        http_client: Optional ``httpx.Client`` for the request path.
            Tests pass an :class:`httpx.MockTransport`-backed client
            so no real Gmail call leaks from the test suite.
        max_body_bytes: Upper bound on the decoded body size; bytes
            beyond this cap are dropped and the message surfaces with
            ``body_truncated=True``. Default 10 MB per spec.
    """

    def __init__(
        self,
        *,
        user_email: str,
        token_refresher: Callable[[], str] = _no_refresh,
        gmail_base: str | None = None,
        http_client: httpx.Client | None = None,
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        if not user_email:
            raise ValueError(
                "GmailClient: user_email is empty. "
                "fix: pass the OAuth-authorised mailbox email (e.g. agent-alpha@example.com). "
                "next: see docs/architecture/connector-ingestion-architecture.md §8 "
                "for the Gmail connector config shape."
            )
        self._user = user_email
        self._token_refresher = token_refresher
        self._gmail_base = (gmail_base or _DEFAULT_GMAIL_BASE).rstrip("/")
        self._http_client = http_client
        self._max_body_bytes = max_body_bytes
        self._cached_token: str | None = None
        self._stats = _GmailStats()

    # ------------------------------------------------------------------
    # High-level surface used by the connector
    # ------------------------------------------------------------------

    def get_profile_history_id(self) -> str:
        """Return the current ``historyId`` for the mailbox.

        Used at cold-start to seed the first cursor — Gmail's History
        API rejects ``startHistoryId`` values older than ~7 days, so
        the connector starts the cursor at the live tip when no prior
        cursor exists.
        """
        url = f"{self._gmail_base}/users/{self._user}/profile"
        body = self._authorised_get_json(url, action="users.getProfile")
        history_id = body.get("historyId")
        if not isinstance(history_id, str):
            raise ContainerTransientError(
                "gmail: users.getProfile response missing 'historyId'. "
                "fix: confirm the OAuth scope includes gmail.readonly. "
                "next: see kairix/connectors/gmail/README.md for the scope contract.",
            )
        return history_id

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        """Fetch one page of the History API since ``start_history_id``.

        Args:
            start_history_id: The cursor — Gmail returns events strictly
                after this point.
            page_token: Optional pagination token from a prior
                ``HistoryPage.next_page_token``.

        Returns:
            One :class:`HistoryPage` carrying the deduplicated message
            ids that appeared in ``messagesAdded`` plus the
            ``nextPageToken`` / ``historyId`` cursors.
        """
        url = f"{self._gmail_base}/users/{self._user}/history?startHistoryId={start_history_id}"
        if page_token:
            url = f"{url}&pageToken={page_token}"
        body = self._authorised_get_json(url, action="users.history.list")
        return _parse_history_page(body)

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        """Iterate every message id observed since ``start_history_id``
        across all History API pages.

        Yields one id per ``messagesAdded`` event; ids are deduplicated
        per page (Gmail can repeat ids across pages so the caller is
        responsible for cross-page dedup if desired).
        """
        token: str | None = None
        self._last_history_id: str | None = None
        while True:
            page = self.list_history(start_history_id=start_history_id, page_token=token)
            yield from page.message_ids
            if page.history_id is not None:
                self._last_history_id = page.history_id
            if not page.next_page_token:
                return
            token = page.next_page_token

    def last_history_id(self) -> str | None:
        """Return the most recently observed ``historyId`` after iteration."""
        return getattr(self, "_last_history_id", None)

    def get_message(self, message_id: str) -> GmailMessage:
        """Fetch a single message in ``format=full`` and decode body + headers."""
        url = f"{self._gmail_base}/users/{self._user}/messages/{message_id}?format=full"
        body = self._authorised_get_json(url, action="users.messages.get")
        return _parse_message(body, max_body_bytes=self._max_body_bytes)

    def stats(self) -> GmailStatsSnapshot:
        """Return a frozen snapshot of the wire-side counters per F42."""
        return GmailStatsSnapshot(
            requests=self._stats.requests,
            rate_limited_403_total=self._stats.rate_limited_403_total,
            token_refreshes=self._stats.token_refreshes,
        )

    def invalidate_token(self) -> None:
        """Drop the cached bearer so the next call re-fetches."""
        self._cached_token = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bearer(self) -> str:
        """Return the cached bearer, fetching one via the refresher if absent."""
        if self._cached_token is None:
            self._cached_token = self._token_refresher()
            self._stats.token_refreshes += 1
        return self._cached_token

    def _authorised_get_json(self, url: str, *, action: str) -> dict[str, Any]:
        """Issue a GET, decode JSON, raise typed errors on non-2xx.

        On 401: invalidate the cached token, fetch a fresh one, retry
        once. A second 401 propagates as :class:`CredentialExpiredError`.
        """
        token = self._bearer()
        response = self._do_get(url, token)
        if response.status_code == 401:
            logger.info("gmail: received 401 on %s; invalidating token and retrying once", action)
            self.invalidate_token()
            token = self._bearer()
            response = self._do_get(url, token)
        self._raise_for_status(response, action=action)
        decoded = response.json()
        if not isinstance(decoded, dict):
            raise ContainerTransientError(
                f"gmail: {action} response was not a JSON object; got type {type(decoded).__name__}. "
                "fix: confirm the OAuth scope grants gmail.readonly access to this mailbox. "
                "next: see kairix/connectors/gmail/README.md for scope + base URL config.",
            )
        return decoded

    def _do_get(self, url: str, token: str) -> httpx.Response:
        """Single HTTP GET — separated for the 401-retry path's symmetry.

        The bearer string is composed into the Authorization header
        here ONLY; never logged, never returned.
        """
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._stats.requests += 1
        client = self._http_client
        if client is not None:
            return client.get(url, headers=headers, timeout=_GMAIL_REQUEST_TIMEOUT_S)
        with httpx.Client(timeout=_GMAIL_REQUEST_TIMEOUT_S) as owned:
            return owned.get(url, headers=headers)

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        """Translate non-2xx into typed exceptions per F64 / spec §5.

        429 with optional Retry-After → :class:`ContainerTransientError`
        carrying the retry budget; 403 with a Gmail rate-limit reason
        also routes to transient; 401 → :class:`CredentialExpiredError`;
        bare 403 → :class:`InsufficientPermissionsError`; 5xx →
        transient.
        """
        if response.status_code < 400:
            return
        # F15-clean — log status + action only, never the response body
        # (could contain leaked tokens in error envelopes).
        logger.warning("gmail: %s returned %s", action, response.status_code)
        if response.status_code == 401:
            self.invalidate_token()
            raise CredentialExpiredError(
                f"gmail: {action} returned 401 unauthorised. "
                "fix: re-run the OAuth consent for the mailbox; the access token expired. "
                "next: see kairix/connectors/gmail/README.md for the OAuth refresh contract."
            )
        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            self._stats.rate_limited_403_total += 1
            raise ContainerTransientError(
                f"gmail: {action} returned 429; retry after {retry_after}s.",
                retry_after=retry_after,
            )
        if response.status_code == 403:
            reason = _extract_403_reason(response)
            if reason in _RATE_LIMIT_REASONS:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                self._stats.rate_limited_403_total += 1
                raise ContainerTransientError(
                    f"gmail: {action} hit rate-limit reason {reason!r}; retry after {retry_after}s.",
                    retry_after=retry_after,
                )
            raise InsufficientPermissionsError(
                f"gmail: {action} returned 403 forbidden (reason={reason!r}). "
                "fix: confirm the OAuth grant includes gmail.readonly on this mailbox. "
                "next: see kairix/connectors/gmail/README.md §scopes."
            )
        if response.status_code in (500, 502, 503, 504):
            raise ContainerTransientError(
                f"gmail: {action} returned {response.status_code}; treating as transient.",
                retry_after=30.0,
            )
        # 404 / 410 / other 4xx → raise as a transient too so the
        # framework dead-letters the specific item rather than the
        # cc_pair.
        raise ContainerTransientError(
            f"gmail: {action} returned {response.status_code}; dead-lettering item.",
            retry_after=None,
        )


# ---------------------------------------------------------------------------
# Parsing helpers (module-level so they're testable + cheap)
# ---------------------------------------------------------------------------


def _parse_history_page(body: dict[str, Any]) -> HistoryPage:
    """Parse one ``users.history.list`` response.

    Gmail's response surfaces ``history[]`` records, each carrying a
    ``messagesAdded[]`` list of ``{message: {id, threadId, labelIds}}``.
    We dedup ids per page so the same message that appears in multiple
    history records (label add + label removal) only emits one
    ChangeEvent.
    """
    message_ids = _collect_history_message_ids(body.get("history"))
    next_page = body.get("nextPageToken")
    history_id = body.get("historyId")
    return HistoryPage(
        message_ids=tuple(message_ids),
        next_page_token=next_page if isinstance(next_page, str) else None,
        history_id=history_id if isinstance(history_id, str) else None,
    )


def _collect_history_message_ids(history_entries: Any) -> list[str]:
    """Walk the ``history[]`` records, return a deduplicated list of message ids.

    Extracted from :func:`_parse_history_page` to keep the parent's
    cognitive complexity flat (Sonar S3776 / F16).
    """
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(history_entries, list):
        return out
    for entry in history_entries:
        for msg_id in _history_entry_added_ids(entry):
            if msg_id not in seen:
                seen.add(msg_id)
                out.append(msg_id)
    return out


def _history_entry_added_ids(entry: Any) -> list[str]:
    """Extract the ``messagesAdded[].message.id`` values from one history record."""
    if not isinstance(entry, dict):
        return []
    added = entry.get("messagesAdded")
    if not isinstance(added, list):
        return []
    ids: list[str] = []
    for item in added:
        msg_id = _added_item_message_id(item)
        if msg_id is not None:
            ids.append(msg_id)
    return ids


def _added_item_message_id(item: Any) -> str | None:
    """Pull ``message.id`` out of one ``messagesAdded[]`` item."""
    if not isinstance(item, dict):
        return None
    msg = item.get("message")
    if not isinstance(msg, dict):
        return None
    msg_id = msg.get("id")
    return msg_id if isinstance(msg_id, str) else None


def _parse_message(body: dict[str, Any], *, max_body_bytes: int) -> GmailMessage:
    """Parse one ``users.messages.get?format=full`` response.

    Picks the first ``text/plain`` body part; if absent, falls back to
    ``text/html`` and strips tags down to plain text via :func:`_strip_html`.
    Body decoding tolerates malformed base64url silently — a corrupt
    part becomes empty rather than raising.
    """
    message_id = _str_or_empty(body.get("id"))
    thread_id = _str_or_empty(body.get("threadId"))
    history_id = _optional_str(body.get("historyId"))
    label_ids_raw = body.get("labelIds")
    label_ids: tuple[str, ...] = ()
    if isinstance(label_ids_raw, list):
        label_ids = tuple(str(x) for x in label_ids_raw if isinstance(x, str))

    payload = body.get("payload")
    headers: tuple[GmailHeader, ...] = ()
    body_bytes = b""
    body_mime = _MIME_TEXT_PLAIN
    truncated = False
    attachments: list[GmailAttachment] = []
    if isinstance(payload, dict):
        headers = _extract_headers(payload)
        body_bytes, body_mime, truncated = _extract_body(payload, max_body_bytes=max_body_bytes)
        attachments = _extract_attachments(payload)

    return GmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        history_id=history_id,
        label_ids=label_ids,
        headers=headers,
        body=body_bytes,
        body_mime=body_mime,
        body_truncated=truncated,
        attachments=tuple(attachments),
    )


def _extract_headers(payload: dict[str, Any]) -> tuple[GmailHeader, ...]:
    """Pull the headers list off a Gmail payload."""
    raw = payload.get("headers")
    out: list[GmailHeader] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if isinstance(name, str) and isinstance(value, str):
                out.append(GmailHeader(name=name, value=value))
    return tuple(out)


def _extract_body(payload: dict[str, Any], *, max_body_bytes: int) -> tuple[bytes, str, bool]:
    """Walk the MIME parts and return the decoded body + chosen mime + truncation flag.

    Preference order: text/plain → text/html (stripped) → empty.
    """
    plain = _find_part(payload, mime_type=_MIME_TEXT_PLAIN)
    if plain is not None:
        decoded = _decode_body(plain)
        if len(decoded) > max_body_bytes:
            return b"", _MIME_TEXT_PLAIN, True
        return decoded, _MIME_TEXT_PLAIN, False
    html = _find_part(payload, mime_type="text/html")
    if html is not None:
        decoded = _decode_body(html)
        stripped = _strip_html(decoded.decode("utf-8", errors="replace")).encode("utf-8")
        if len(stripped) > max_body_bytes:
            return b"", _MIME_TEXT_PLAIN, True
        return stripped, _MIME_TEXT_PLAIN, False
    return b"", _MIME_TEXT_PLAIN, False


def _find_part(payload: dict[str, Any], *, mime_type: str) -> dict[str, Any] | None:
    """Depth-first scan for the first part matching ``mime_type``."""
    if payload.get("mimeType") == mime_type and isinstance(payload.get("body"), dict):
        return payload
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                found = _find_part(part, mime_type=mime_type)
                if found is not None:
                    return found
    return None


def _decode_body(part: dict[str, Any]) -> bytes:
    """Decode the base64url body of one MIME part — tolerant of malformed input."""
    body = part.get("body")
    if not isinstance(body, dict):
        return b""
    data = body.get("data")
    if not isinstance(data, str):
        return b""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return b""


def _strip_html(html: str) -> str:
    """Strip HTML tags down to plain text — minimal, no external deps.

    The function removes tags + collapses whitespace; for production-
    grade HTML→Markdown the downstream extractor registry handles
    that via the ``markitdown`` extractor. This stripping path is only
    used when a Gmail message has no text/plain alternative and we
    need something for the chunk text.
    """
    import re

    # Drop script + style blocks entirely (their content isn't readable text).
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    # Drop every remaining tag.
    no_tags = re.sub(r"<[^>]+>", " ", no_script)
    # Collapse whitespace runs.
    collapsed = re.sub(r"\s+", " ", no_tags).strip()
    return collapsed


def _extract_attachments(payload: dict[str, Any]) -> list[GmailAttachment]:
    """Walk the MIME tree, collecting attachment metadata.

    An attachment is any part with a non-empty ``filename``. The body
    itself is NOT fetched — only ``attachmentId`` plus the headers
    that round-trip to a Drive-style identifier later.
    """
    out: list[GmailAttachment] = []
    _walk_attachments(payload, out)
    return out


def _walk_attachments(payload: dict[str, Any], collector: list[GmailAttachment]) -> None:
    """Depth-first MIME walk that collects every attachment-shaped part.

    Delegates the per-part attachment-metadata extraction to
    :func:`_make_attachment_if_present`; the recursive walk handles
    the parts[] descent.
    """
    attachment = _make_attachment_if_present(payload)
    if attachment is not None:
        collector.append(attachment)
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return
    for part in parts:
        if isinstance(part, dict):
            _walk_attachments(part, collector)


def _make_attachment_if_present(payload: dict[str, Any]) -> GmailAttachment | None:
    """Return one :class:`GmailAttachment` if ``payload`` is attachment-shaped.

    A payload is attachment-shaped when it carries a non-empty
    ``filename`` field. Extracted from :func:`_walk_attachments` so
    the recursive walk's complexity stays under F16's ceiling.
    """
    filename = payload.get("filename")
    if not (isinstance(filename, str) and filename):
        return None
    body = payload.get("body")
    size, attachment_id = _attachment_body_fields(body)
    mime = payload.get("mimeType")
    return GmailAttachment(
        filename=filename,
        mime_type=str(mime) if isinstance(mime, str) else "application/octet-stream",
        size_bytes=size,
        attachment_id=attachment_id,
    )


def _attachment_body_fields(body: Any) -> tuple[int, str | None]:
    """Lift ``size`` + ``attachmentId`` from one attachment body block."""
    if not isinstance(body, dict):
        return 0, None
    size_raw = body.get("size")
    size = size_raw if isinstance(size_raw, int) else 0
    aid = body.get("attachmentId")
    attachment_id = aid if isinstance(aid, str) else None
    return size, attachment_id


def _str_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_retry_after(value: str | None) -> float:
    """Parse the Retry-After header; default to 60s when missing or unparseable."""
    if value is None:
        return 60.0
    try:
        return float(value)
    except ValueError:
        return 60.0


def _extract_403_reason(response: httpx.Response) -> str | None:
    """Lift the first error reason out of a Gmail 403 response body.

    Gmail's error envelope is
    ``{"error": {"errors": [{"reason": "userRateLimitExceeded", ...}], ...}}``.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    errors = err.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    reason = first.get("reason")
    return reason if isinstance(reason, str) else None


# Intentionally exported for tests that need the same parsing routines.
__all__ = [
    "GmailAttachment",
    "GmailClient",
    "GmailHeader",
    "GmailMessage",
    "GmailStatsSnapshot",
    "HistoryPage",
]
