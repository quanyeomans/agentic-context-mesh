"""Thin Google Drive v3 REST client for the kairix Google Drive connector.

A focused wrapper around ``httpx.Client`` for the Google Drive v3 REST
surface the connector exercises — start-page-token resolution, changes
list (delta), per-file metadata fetch, and per-file binary download.
Three commitments:

  1. **Page-token pagination.** The Drive v3 changes endpoint pages on
     ``pageToken`` / ``nextPageToken`` and surfaces a
     ``newStartPageToken`` on the final page. The client surfaces the
     pagination via the typed :class:`ChangesPage` value object so the
     connector can advance cursors without parsing the wire shape itself.

  2. **OAuth2 bearer auth.** Every request adds an
     ``Authorization: Bearer <token>`` header via the injected access
     token. A 401 raises :class:`CredentialExpiredError` — the
     credential is expired or revoked and out-of-band rotation is
     required. The framework's cc_pair lifecycle catches this and
     transitions the cc_pair to a credential-renewal state.

  3. **Honour throttling.** Drive returns 429 on per-user / per-project
     rate-limit budget exhaustion; 403 with a ``userRateLimitExceeded``
     / ``rateLimitExceeded`` reason in the JSON body is the older shape.
     Both retry with backoff, honouring ``Retry-After`` when present.

Per F37, ``googleapiclient`` / ``google.auth`` imports are allowed only
under ``kairix/connectors/google_drive/``. We deliberately avoid the
official ``google-api-python-client`` SDK (it pulls a heavy transitive
set for what is essentially a small REST surface) and use raw
``httpx``. The client stays under F37's allowed surface (this module
lives at ``kairix/connectors/google_drive/client.py``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Final

import httpx
from tenacity import (
    RetryCallState,
    RetryError,
    Retrying,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

from kairix.core.protocols import CredentialExpiredError

logger = logging.getLogger(__name__)

# Default Drive v3 REST base URL. Pinned to v3 — the API has been stable
# since 2015. The version pin is part of the path so a future v4 cutover
# is an explicit operator action, not silent drift.
_DEFAULT_DRIVE_BASE: Final[str] = "https://www.googleapis.com/drive/v3"

# Per-request timeout. Drive list endpoints typically reply in <1s; 60s
# covers a cold connection on a paginated reply with a large
# corpus.
_DRIVE_REQUEST_TIMEOUT_S: Final[float] = 60.0

# Content fetch timeout — binaries are typically <5 MB but large
# presentations and PDFs can run higher; 120s covers the long tail
# without leaving a stuck connection open indefinitely.
_DRIVE_CONTENT_TIMEOUT_S: Final[float] = 120.0

# Retry tuning. ``_DEFAULT_MAX_ATTEMPTS`` is the total attempt count
# (initial call + retries); ``_DEFAULT_BACKOFF_MIN_S`` /
# ``_DEFAULT_BACKOFF_MAX_S`` clamp the exponential fallback when the
# server omits ``Retry-After``.
_DEFAULT_MAX_ATTEMPTS: Final[int] = 5
_DEFAULT_BACKOFF_MIN_S: Final[float] = 2.0
_DEFAULT_BACKOFF_MAX_S: Final[float] = 60.0
_RETRY_AFTER_HEADER: Final[str] = "Retry-After"

# 429 + transient 5xx are retried with backoff. 403 needs a body-payload
# inspection: only the rate-limit reasons are retryable — a plain 403
# (e.g. permission denied) is permanent and propagates immediately.
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_THROTTLED_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 503})

# F17 — field-name constants shared across the changes-page parser so
# renames have a single edit site. These are Drive REST response keys,
# NOT credentials — S105 (hardcoded-password lint) is a false positive
# because of the "Token" suffix; the noqa pins the rationale at the
# call site.
_FIELD_NEXT_PAGE_TOKEN: Final[str] = "nextPageToken"  # noqa: S105  # Drive REST response key, not a credential
_FIELD_NEW_START_PAGE_TOKEN: Final[str] = "newStartPageToken"  # noqa: S105  # Drive REST response key, not a credential

# Drive 403 rate-limit reasons. Drive surfaces transient quota-exhaust
# under HTTP 403 with one of these reasons in the JSON body (older
# shape); plain HTTP 403 without a rate-limit reason is permanent.
_DRIVE_403_RATE_LIMIT_REASONS: Final[frozenset[str]] = frozenset(
    {"userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded"}
)


@dataclass(frozen=True)
class DriveFileRef:
    """One Drive file envelope (changes list entry or metadata fetch result).

    Frozen per F42. ``mime_type`` distinguishes Google-native types
    (``application/vnd.google-apps.document`` etc., which require an
    export-as round trip) from binary uploads (``application/pdf`` etc.,
    which support direct ``alt=media`` download).
    """

    file_id: str
    name: str
    mime_type: str | None
    web_view_link: str | None
    modified_time: str | None
    created_time: str | None
    last_modifying_user_email: str | None
    last_modifying_user_name: str | None
    owner_emails: tuple[str, ...]
    removed: bool
    parents: tuple[str, ...]
    size: int | None


@dataclass(frozen=True)
class ChangesPage:
    """One page of a Drive v3 ``/changes`` response.

    Frozen per F42. ``next_page_token`` is non-``None`` while more pages
    remain in the current sync window; the caller follows it before
    advancing the cursor. ``new_start_page_token`` is non-``None`` on
    the *final* page — that's the opaque token the connector persists
    as the cursor for the next worker tick.
    """

    files: tuple[DriveFileRef, ...]
    next_page_token: str | None
    new_start_page_token: str | None


class GoogleDriveClient:
    """Thin Google Drive v3 REST wrapper for the connector.

    Args:
        access_token: OAuth2 bearer for the configured workspace user
            (or service-account impersonation). Required permissions on
            the credential: ``drive.readonly`` (or ``drive`` for a
            broader scope). Tests pass a literal string token so no
            real OAuth exchange runs.
        drive_base: Optional override for testing / sovereign endpoints.
            Defaults to the public Drive v3 base.
        http_client: Optional ``httpx.Client`` for the request path.
            Tests pass an :class:`httpx.MockTransport`-backed client so
            no real Drive call leaks from the test suite.
        sleep_fn: Optional sleep shim used by the throttling-retry loop.
            Defaults to :func:`time.sleep`; tests pass a recording
            no-op so the suite stays fast without monkey-patching
            stdlib.
        max_attempts: Total attempt count (initial call + retries) for
            any single Drive request. Defaults to
            :data:`_DEFAULT_MAX_ATTEMPTS` (5).
    """

    def __init__(
        self,
        *,
        access_token: str,
        drive_base: str | None = None,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._access_token = access_token
        self._drive_base = (drive_base or _DEFAULT_DRIVE_BASE).rstrip("/")
        self._http_client = http_client
        self._sleep_fn = sleep_fn
        self._max_attempts = max_attempts
        # Cache of the most recent newStartPageToken so the orchestrator
        # can read it after a sync tick without re-walking the changes
        # endpoint.
        self._last_new_start_page_token: str | None = None

    # ------------------------------------------------------------------
    # Public Drive surface
    # ------------------------------------------------------------------

    def get_start_page_token(self) -> str:
        """Fetch a fresh ``startPageToken`` for cold-start syncs.

        Calls ``GET /changes/startPageToken``; the response carries a
        single ``startPageToken`` field that the connector uses as the
        cursor seed for its first ``list_changes`` drain.
        """
        url = f"{self._drive_base}/changes/startPageToken"
        body = self._authorised_get(url).json()
        token = body.get("startPageToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError(
                "google_drive: startPageToken missing or malformed in response. "
                "fix: verify the Drive API is enabled for the project + the credential carries drive.readonly. "
                "next: see kairix/connectors/google_drive/README.md for the auth setup."
            )
        return token

    def fetch_changes_page(self, page_token: str) -> ChangesPage:
        """Fetch one page from the ``/changes`` endpoint.

        Args:
            page_token: The full token — either the seed from
                :meth:`get_start_page_token`, a previous response's
                ``nextPageToken`` (more pages this run), or a stored
                ``newStartPageToken`` cursor (next sync tick).

        Returns:
            A :class:`ChangesPage` carrying parsed file rows and the
            next-page / new-start-page pointers for the caller's
            pagination loop.

        Raises:
            CredentialExpiredError: On 401 — credential rotation needed.
            httpx.HTTPStatusError: On other non-2xx response.
        """
        url = (
            f"{self._drive_base}/changes"
            f"?pageToken={page_token}"
            "&fields=newStartPageToken,nextPageToken,changes("
            "fileId,removed,file(id,name,mimeType,webViewLink,modifiedTime,createdTime,"
            "lastModifyingUser(emailAddress,displayName),owners(emailAddress),parents,size))"
        )
        response = self._authorised_get(url)
        body = response.json()
        return _parse_changes_page(body)

    def iter_changes(self, start_token: str) -> Iterator[DriveFileRef]:
        """Iterate change rows across all pages, starting from ``start_token``.

        Args:
            start_token: Either a seed from :meth:`get_start_page_token`
                (cold start) or a stored ``newStartPageToken`` cursor
                (next worker tick).

        Yields:
            One :class:`DriveFileRef` per change row. The final page's
            ``newStartPageToken`` is cached on the client and accessible
            via :meth:`last_new_start_page_token` after iteration
            completes.
        """
        token: str | None = start_token
        last_new_start: str | None = None
        while token is not None:
            page = self.fetch_changes_page(token)
            yield from page.files
            if page.new_start_page_token is not None:
                last_new_start = page.new_start_page_token
            token = page.next_page_token
        if last_new_start is not None:
            self._last_new_start_page_token = last_new_start

    def fetch_file_content(self, file_id: str) -> tuple[bytes, str]:
        """Download the binary content of one Drive file.

        Calls ``GET /files/{file-id}?alt=media`` — Drive returns the raw
        bytes (or a redirect to a time-limited download URL; ``httpx``
        follows redirects by default so the caller gets the bytes
        either way).

        Returns ``(raw_bytes, content_type)`` so the connector can route
        the mime hint through to the extractor registry. Google-native
        types (``application/vnd.google-apps.*``) require ``/export``
        instead of ``alt=media``; this slice surfaces the native mime
        and lets the connector decide whether to skip or escalate to
        export (deferred to a follow-up — first slice handles binary
        uploads only).
        """
        url = f"{self._drive_base}/files/{file_id}?alt=media"
        response = self._authorised_get(url, timeout=_DRIVE_CONTENT_TIMEOUT_S)
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        return response.content, content_type

    def fetch_file_metadata(self, file_id: str) -> DriveFileRef:
        """Fetch metadata for one Drive file via ``GET /files/{file-id}``.

        Used by :meth:`GoogleDriveConnector.metadata_for` for files
        that were not seen via a previous changes-list drain (e.g. a
        replay of a failed item via :meth:`reindex`). Cached envelopes
        from changes-list are preferred — this is the fallback path.
        """
        url = (
            f"{self._drive_base}/files/{file_id}"
            "?fields=id,name,mimeType,webViewLink,modifiedTime,createdTime,"
            "lastModifyingUser(emailAddress,displayName),owners(emailAddress),parents,size"
        )
        body = self._authorised_get(url).json()
        return _drive_file_ref_from(body, removed=False)

    def last_new_start_page_token(self) -> str | None:
        """Return the ``newStartPageToken`` cached after the most recent
        :meth:`iter_changes` run, or ``None`` if no full drain has
        completed yet.
        """
        return self._last_new_start_page_token

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _authorised_get(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        """Issue a GET with the configured bearer + retry on throttle.

        Two layered behaviours:

          1. **401 raises immediately as CredentialExpiredError.** Drive
             treats 401 as token-revoked; the framework's cc_pair
             lifecycle catches this and transitions the cc_pair to a
             credential-renewal state. No silent refresh attempt — the
             operator must rotate the OAuth grant out of band.
          2. **429 / 5xx / quota-403 with backoff.** Throttled responses
             honour the server's ``Retry-After`` header when present;
             otherwise fall back to exponential backoff.
             ``_DEFAULT_MAX_ATTEMPTS`` total attempts. After exhaustion
             the final response's ``raise_for_status`` lifts an
             :class:`httpx.HTTPStatusError`.

        Other 4xx responses (e.g. 403 without a rate-limit reason, 404)
        raise immediately — they're permanent for this URL + credential
        pair.
        """
        retrying = Retrying(
            retry=retry_if_result(_is_retryable_response),
            wait=self._wait_strategy,
            stop=stop_after_attempt(self._max_attempts),
            sleep=self._sleep_fn,
            reraise=True,
        )
        try:
            response = retrying(self._authorised_get_once, url, timeout)
        except RetryError as exc:
            final: httpx.Response = exc.last_attempt.result()
            final.raise_for_status()
            return final  # pragma: no cover — raise_for_status above always raises here
        if response.status_code == 401:
            raise CredentialExpiredError(
                "google_drive: 401 from Drive API; OAuth credential expired or revoked. "
                "fix: rotate the OAuth grant for the configured workspace user; "
                "the worker will pick up the refreshed token on the next tick. "
                "next: see kairix/connectors/google_drive/README.md for the credential rotation flow."
            )
        response.raise_for_status()
        return response

    def _authorised_get_once(self, url: str, timeout: float | None) -> httpx.Response:
        """One bearer-authorised GET. The retry loop in
        :meth:`_authorised_get` inspects the status code and either
        retries or hands the response back to the caller.
        """
        return self._do_get(url, self._access_token, timeout=timeout)

    def _wait_strategy(self, retry_state: RetryCallState) -> float:
        """Compute the wait between retries.

        For 429 / 503 responses honour the server's ``Retry-After``
        header (seconds). For other retryable statuses (or when
        ``Retry-After`` is missing / unparseable) fall back to bounded
        exponential backoff.
        """
        outcome = retry_state.outcome
        if outcome is None or outcome.failed:  # pragma: no cover — exception path bypasses retry_if_result
            return _DEFAULT_BACKOFF_MIN_S
        response = outcome.result()
        retry_after = _parse_retry_after(response) if response.status_code in _THROTTLED_STATUS_CODES else None
        if retry_after is not None:
            logger.warning(
                "google_drive: %s on attempt %d; honouring Retry-After=%.1fs",
                response.status_code,
                retry_state.attempt_number,
                retry_after,
            )
            return retry_after
        backoff = wait_exponential(multiplier=1, min=_DEFAULT_BACKOFF_MIN_S, max=_DEFAULT_BACKOFF_MAX_S)(retry_state)
        logger.warning(
            "google_drive: %s on attempt %d; backing off %.1fs",
            response.status_code,
            retry_state.attempt_number,
            backoff,
        )
        return backoff

    def _do_get(self, url: str, token: str, *, timeout: float | None = None) -> httpx.Response:
        """Single HTTP GET. The bearer is composed into the
        Authorization header here ONLY; never logged, never returned.

        ``follow_redirects=True`` is critical for the ``alt=media``
        endpoint: Drive may return a 302 redirect to a time-limited
        Google CDN URL rather than the bytes inline. Caller-injected
        clients must also enable redirects.
        """
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        effective_timeout = timeout if timeout is not None else _DRIVE_REQUEST_TIMEOUT_S
        client = self._http_client
        if client is not None:
            return client.get(url, headers=headers, timeout=effective_timeout, follow_redirects=True)
        with httpx.Client(timeout=effective_timeout, follow_redirects=True) as owned:
            return owned.get(url, headers=headers)


# ---------------------------------------------------------------------------
# Free-function parsers — kept module-level so tests can pin them without
# constructing a client.
# ---------------------------------------------------------------------------


def _is_retryable_response(response: httpx.Response) -> bool:
    """``True`` when the response status (or a 403 with a rate-limit
    reason in the body) is retryable per Drive's documented quota
    failure modes.
    """
    if response.status_code in _RETRYABLE_STATUS_CODES:
        return True
    if response.status_code == 403:
        return _is_drive_quota_403(response)
    return False


def _is_drive_quota_403(response: httpx.Response) -> bool:
    """Inspect a 403 body for Drive's older rate-limit reason shape.

    Drive emits 403 with::

        {"error": {"errors": [{"reason": "userRateLimitExceeded", ...}]}}

    for legacy quota exhaustion. Plain 403 (permission denied, file not
    shared) doesn't carry these reasons and stays a hard permanent
    failure.
    """
    try:
        body = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    errors = error.get("errors")
    if not isinstance(errors, list):
        return False
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        if isinstance(reason, str) and reason in _DRIVE_403_RATE_LIMIT_REASONS:
            return True
    return False


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Return the ``Retry-After`` header (seconds) as a float, or ``None``."""
    raw = response.headers.get(_RETRY_AFTER_HEADER)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_changes_page(body: dict[str, Any]) -> ChangesPage:
    """Parse one Drive v3 ``/changes`` response.

    Tolerates the documented shape — ``changes`` is the array of
    change rows; ``nextPageToken`` advances within the sync window;
    ``newStartPageToken`` is the next-tick cursor. Missing fields
    default to ``None`` / empty tuple so a sparse fixture parses
    cleanly.
    """
    changes_raw = body.get("changes")
    files = tuple(_parse_change_entries(changes_raw)) if isinstance(changes_raw, list) else ()
    next_token = _string_or_none(body.get(_FIELD_NEXT_PAGE_TOKEN))
    new_start = _string_or_none(body.get(_FIELD_NEW_START_PAGE_TOKEN))
    return ChangesPage(files=files, next_page_token=next_token, new_start_page_token=new_start)


def _parse_change_entries(changes_raw: list[Any]) -> Iterator[DriveFileRef]:
    """Yield one :class:`DriveFileRef` per parseable entry in the changes array.

    Splits the per-entry branching out of :func:`_parse_changes_page` so the
    parent function stays under the F16 cognitive-complexity ceiling. Each
    entry is one of three shapes: malformed (drop), tombstone with only
    ``fileId``, or a full file block.
    """
    for entry in changes_raw:
        if not isinstance(entry, dict):
            continue
        ref = _parse_one_change_entry(entry)
        if ref is not None:
            yield ref


def _parse_one_change_entry(entry: dict[str, Any]) -> DriveFileRef | None:
    """Parse one change entry into a :class:`DriveFileRef` (or None to drop)."""
    removed = bool(entry.get("removed", False))
    file_block = entry.get("file") if isinstance(entry.get("file"), dict) else None
    if file_block is None and not removed:
        return None
    if file_block is None:
        # Drive emits a thin tombstone with only ``fileId`` set.
        file_id = _string_or_empty(entry.get("fileId"))
        if not file_id:
            return None
        return _tombstone_ref(file_id)
    return _drive_file_ref_from(file_block, removed=removed)


def _tombstone_ref(file_id: str) -> DriveFileRef:
    """Build a thin tombstone :class:`DriveFileRef` for a removed entry."""
    return DriveFileRef(
        file_id=file_id,
        name="",
        mime_type=None,
        web_view_link=None,
        modified_time=None,
        created_time=None,
        last_modifying_user_email=None,
        last_modifying_user_name=None,
        owner_emails=(),
        removed=True,
        parents=(),
        size=None,
    )


def _drive_file_ref_from(entry: dict[str, Any], *, removed: bool) -> DriveFileRef:
    """Lift one Drive file envelope into the typed dataclass."""
    lmu = entry.get("lastModifyingUser") if isinstance(entry.get("lastModifyingUser"), dict) else {}
    if not isinstance(lmu, dict):
        lmu = {}
    owner_emails = _parse_owner_emails(entry.get("owners"))
    parents = _parse_string_list(entry.get("parents"))
    size = _parse_size(entry.get("size"))
    return DriveFileRef(
        file_id=_string_or_empty(entry.get("id") or entry.get("fileId")),
        name=_string_or_empty(entry.get("name")),
        mime_type=_string_or_none(entry.get("mimeType")),
        web_view_link=_string_or_none(entry.get("webViewLink")),
        modified_time=_string_or_none(entry.get("modifiedTime")),
        created_time=_string_or_none(entry.get("createdTime")),
        last_modifying_user_email=_string_or_none(lmu.get("emailAddress")),
        last_modifying_user_name=_string_or_none(lmu.get("displayName")),
        owner_emails=owner_emails,
        removed=removed,
        parents=parents,
        size=size,
    )


def _parse_owner_emails(owners_raw: Any) -> tuple[str, ...]:
    """Extract owner email addresses from a Drive ``owners`` block."""
    if not isinstance(owners_raw, list):
        return ()
    emails: list[str] = []
    for owner in owners_raw:
        if not isinstance(owner, dict):
            continue
        email = owner.get("emailAddress")
        if isinstance(email, str) and email:
            emails.append(email)
    return tuple(emails)


def _parse_string_list(raw: Any) -> tuple[str, ...]:
    """Extract a tuple of non-empty strings from an arbitrary value."""
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item)


def _parse_size(size_raw: Any) -> int | None:
    """Parse a Drive file ``size`` value (int or numeric string)."""
    if isinstance(size_raw, int):
        return size_raw
    if isinstance(size_raw, str):
        try:
            return int(size_raw)
        except ValueError:
            return None
    return None


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
