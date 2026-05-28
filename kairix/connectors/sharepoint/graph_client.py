"""Thin Microsoft Graph client for SharePoint document libraries.

A focused wrapper around ``httpx.Client`` for the Microsoft Graph
SharePoint surface — site enumeration, drive listing, drive-level
delta sync, and per-item binary content download. Three commitments:

  1. **Delta-token pagination.** The connector hands an opaque cursor
     (the previous tick's ``@odata.deltaLink``) between worker ticks;
     the client surfaces both ``@odata.nextLink`` (more pages now) and
     ``@odata.deltaLink`` (resume here next tick) via the
     :class:`DriveDeltaPage` value object so the connector can advance
     cursors without parsing URLs itself.

  2. **OAuth2 client-credentials auth.** Every request adds an
     ``Authorization: Bearer <token>`` header via the injected
     :class:`OAuth2ClientCredsAuth` helper. A 401 triggers a single
     :meth:`OAuth2ClientCredsAuth.invalidate` + retry; persistent 401
     propagates as :class:`httpx.HTTPStatusError`.

  3. **Lazy binary fetch.** Listing pages return cheap envelope rows
     (id / name / mime / lastModifiedDateTime / size + the web URL).
     The connector lifts the content stream only when Silver asks for
     it via :meth:`fetch_item_content` — Bronze persists the raw bytes
     once, then re-extraction runs against Bronze.

Per F37, ``msgraph_core`` / ``msgraph`` import is allowed only under
``kairix/connectors/<name>/`` — like the M365 email-headers sibling,
we deliberately avoid the SDK (the delta query is a small REST call
and the SDK pulls a heavy transitive set). The client uses raw
``httpx`` and stays under F37's allowed surface (this module lives at
``kairix/connectors/sharepoint/graph_client.py``).
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

from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

logger = logging.getLogger(__name__)

# Default Graph base URL — overrideable for sovereign clouds (e.g. Graph
# for US Government). The trailing ``/v1.0`` matches the public Microsoft
# Graph v1 surface used by every shipped Graph integration.
_DEFAULT_GRAPH_BASE: Final[str] = "https://graph.microsoft.com/v1.0"

# Per-request timeout. Graph delta replies typically arrive in <1s;
# 60s covers a cold connection on a paginated reply with a large drive.
_GRAPH_REQUEST_TIMEOUT_S: Final[float] = 60.0

# Content fetch timeout — binaries are typically <5 MB but large
# presentations and PDFs can run higher; 120s covers the long tail
# without leaving a stuck connection open indefinitely.
_GRAPH_CONTENT_TIMEOUT_S: Final[float] = 120.0

# Graph collection-pagination key. Extracted once so renames have a
# single edit site and the F17 dup-literal gate stays green as the
# client grows (every list_* / iter_* method follows the same
# pagination convention).
_ODATA_NEXT_LINK_KEY: Final[str] = "@odata.nextLink"

# Retry tuning for Graph throttling. ``_DEFAULT_MAX_ATTEMPTS`` is the
# total attempt count (initial call + retries) for any single
# ``_authorised_get``; ``_DEFAULT_BACKOFF_MIN_S`` / ``_DEFAULT_BACKOFF_MAX_S``
# clamp the exponential fallback when the server omits ``Retry-After``.
# Graph documents 429 + 503 as the throttled responses; both carry
# ``Retry-After`` per https://learn.microsoft.com/graph/throttling.
_DEFAULT_MAX_ATTEMPTS: Final[int] = 5
_DEFAULT_BACKOFF_MIN_S: Final[float] = 2.0
_DEFAULT_BACKOFF_MAX_S: Final[float] = 60.0
_RETRY_AFTER_HEADER: Final[str] = "Retry-After"
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_THROTTLED_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 503})


@dataclass(frozen=True)
class SiteRef:
    """One SharePoint site as enumerated by the Graph ``/sites`` endpoint.

    Frozen per F42. ``site_id`` is the Graph composite id
    (``<hostname>,<site-guid>,<web-guid>``); the connector uses it to
    resolve the site's document libraries.
    """

    site_id: str
    display_name: str
    web_url: str


@dataclass(frozen=True)
class DriveRef:
    """One document library on a SharePoint site.

    Frozen per F42. ``drive_id`` is the Graph drive identifier; the
    connector uses it to drive the per-drive delta query and the
    per-item content download.
    """

    drive_id: str
    site_id: str
    name: str
    web_url: str


@dataclass(frozen=True)
class DriveItemRef:
    """One file inside a SharePoint document library.

    Frozen per F42. Envelope-only — ``size`` is the declared size in
    bytes (None when the source omits it). The binary stream is fetched
    lazily via :meth:`SharePointGraphClient.fetch_item_content` when
    the connector emits the matching ``ChangeEvent`` and the
    orchestrator calls :meth:`SharePointConnector.fetch`.

    ``removed`` is True for tombstone entries the delta surface emits
    when a file has been deleted; the connector translates these into
    ``deleted`` :class:`ChangeEvent` items.
    """

    item_id: str
    drive_id: str
    name: str
    mime: str | None
    web_url: str | None
    size: int | None
    last_modified_at: str | None
    removed: bool
    parent_path: str | None = None
    # ADR-021 (Wave E.5): envelope-derived display names — lifted from
    # ``createdBy.user.displayName`` and ``lastModifiedBy.user.displayName``.
    # ``None`` when the Graph response omits the block (rare; mostly
    # tombstones).
    created_by: str | None = None
    last_modified_by: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class DriveDeltaPage:
    """One page of a Graph ``/drives/{drive-id}/root/delta`` response.

    Frozen per F42. ``next_link`` is non-``None`` while more pages remain
    in the current sync window; the caller follows it before advancing
    the cursor. ``delta_link`` is non-``None`` on the *final* page —
    that's the opaque token the connector persists as the cursor for
    the next worker tick.
    """

    items: tuple[DriveItemRef, ...]
    next_link: str | None
    delta_link: str | None


class SharePointGraphClient:
    """Thin Microsoft Graph wrapper for SharePoint document libraries.

    Args:
        auth: Initialised :class:`OAuth2ClientCredsAuth` for the tenant
            that owns the target sites. Required permissions on the AAD
            app: ``Sites.Read.All`` + ``Files.Read.All`` (application
            scope, granted with admin consent).
        graph_base: Optional override for sovereign clouds. Defaults to
            the public Microsoft Graph endpoint.
        http_client: Optional ``httpx.Client`` for the request path.
            Tests pass an :class:`httpx.MockTransport`-backed client so
            no real Graph call leaks from the test suite.
        sleep_fn: Optional sleep shim used by the throttling-retry loop.
            Defaults to :func:`time.sleep`; tests pass a recording no-op
            so the suite stays fast without monkey-patching stdlib.
        max_attempts: Total attempt count (initial call + retries) for
            any single Graph request. Defaults to
            :data:`_DEFAULT_MAX_ATTEMPTS` (5).
    """

    def __init__(
        self,
        *,
        auth: OAuth2ClientCredsAuth,
        graph_base: str | None = None,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._auth = auth
        self._graph_base = (graph_base or _DEFAULT_GRAPH_BASE).rstrip("/")
        self._http_client = http_client
        self._sleep_fn = sleep_fn
        self._max_attempts = max_attempts
        # Cache of the most recent delta-link by drive id so the
        # orchestrator can read it after a sync tick without re-walking
        # the delta endpoint.
        self._last_delta_link_by_drive: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public Graph surface
    # ------------------------------------------------------------------

    def list_sites(self) -> Iterator[SiteRef]:
        """Yield every SharePoint site the credential can read.

        Calls ``GET /sites?search=*`` — the documented Graph shape for
        "enumerate all sites the app has access to". Sovereign-cloud or
        Sites.Selected deployments can override the URL via the
        ``graph_base`` constructor argument; per-site grants must be
        added at the AAD app registration level outside this client.
        """
        url: str | None = f"{self._graph_base}/sites?search=*"
        while url is not None:
            body = self._authorised_get(url).json()
            for entry in _entries(body):
                yield _site_from(entry)
            url = _string_or_none(body.get(_ODATA_NEXT_LINK_KEY))

    def list_drives(self, site_id: str) -> Iterator[DriveRef]:
        """Yield every document library (drive) on the given site.

        Calls ``GET /sites/{site-id}/drives`` — returns every drive the
        credential has read access to. SharePoint typically exposes one
        ``Documents`` drive per site plus any operator-created
        libraries.
        """
        url: str | None = f"{self._graph_base}/sites/{site_id}/drives"
        while url is not None:
            body = self._authorised_get(url).json()
            for entry in _entries(body):
                yield _drive_from(entry, site_id=site_id)
            url = _string_or_none(body.get(_ODATA_NEXT_LINK_KEY))

    def initial_delta_url(self, drive_id: str) -> str:
        """Compose the seed delta URL for one drive.

        First sync (no cursor) starts here; subsequent syncs hand the
        previous response's ``deltaLink`` directly to
        :meth:`fetch_delta_page`. Exposed publicly so tests can pin the
        request URL without driving a real HTTP call.
        """
        return f"{self._graph_base}/drives/{drive_id}/root/delta"

    def fetch_delta_page(self, url: str) -> DriveDeltaPage:
        """Fetch one page from the given Graph URL (delta or nextLink).

        Args:
            url: The full Graph URL — either the seed
                :meth:`initial_delta_url`, a previous response's
                ``@odata.nextLink`` (more pages this run), or a stored
                ``@odata.deltaLink`` cursor (next sync tick).

        Returns:
            A :class:`DriveDeltaPage` carrying parsed envelope rows and
            the next-link / delta-link pointers for the caller's
            pagination loop.

        Raises:
            httpx.HTTPError: On non-2xx response after the single
                401-driven token refresh.
        """
        response = self._authorised_get(url)
        body = response.json()
        return _parse_delta_page(body)

    def iter_drive_items(self, drive_id: str, start_url: str | None = None) -> Iterator[DriveItemRef]:
        """Iterate envelope rows for one drive across all pages.

        Args:
            drive_id: The Graph drive identifier; populates the per-drive
                last-delta cache so the orchestrator can resume from the
                cursor on the next tick.
            start_url: Optional starting URL. ``None`` starts from
                :meth:`initial_delta_url(drive_id)` (full sync); a stored
                deltaLink starts from the previous cursor.

        Yields:
            One :class:`DriveItemRef` per envelope. The final page's
            ``deltaLink`` is cached on the client and accessible via
            :meth:`last_delta_link_for_drive` after iteration completes.
        """
        url: str | None = start_url or self.initial_delta_url(drive_id)
        last_delta: str | None = None
        while url is not None:
            page = self.fetch_delta_page(url)
            yield from page.items
            if page.delta_link is not None:
                last_delta = page.delta_link
            url = page.next_link
        if last_delta is not None:
            self._last_delta_link_by_drive[drive_id] = last_delta

    def fetch_item_content(self, drive_id: str, item_id: str) -> bytes:
        """Download the binary content of one drive item.

        Calls ``GET /drives/{drive-id}/items/{item-id}/content`` — Graph
        returns the raw bytes (it may also issue a redirect to a
        time-limited download URL; ``httpx`` follows redirects by
        default so the caller gets the bytes either way).

        The content fetch is the only call in this client that uses the
        :data:`_GRAPH_CONTENT_TIMEOUT_S` (120s) timeout instead of the
        default; large presentations / PDFs can outlast the 60s envelope
        timeout on a slow network.
        """
        url = f"{self._graph_base}/drives/{drive_id}/items/{item_id}/content"
        response = self._authorised_get(url, timeout=_GRAPH_CONTENT_TIMEOUT_S)
        return response.content

    def last_delta_link_for_drive(self, drive_id: str) -> str | None:
        """Return the deltaLink cached for ``drive_id`` after the most
        recent :meth:`iter_drive_items` run, or ``None`` if no delta
        completion has been observed yet.
        """
        return self._last_delta_link_by_drive.get(drive_id)

    def path_exists(self, drive_id: str, path: str) -> bool:
        """Return True when ``path`` (relative to the drive root) resolves.

        Calls ``GET /drives/{drive-id}/root:{path}``. Returns True on 200,
        False on 404. Any other status (5xx, transient network) propagates
        the underlying httpx exception — callers (typically the connector's
        startup probe) wrap in their own try/except so a transient Graph
        outage at boot doesn't kill connector init.
        """
        normalised = path if path.startswith("/") else "/" + path
        url = f"{self._graph_base}/drives/{drive_id}/root:{normalised}"
        try:
            response = self._authorised_get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise
        return response.status_code == 200

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _authorised_get(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        """Issue a GET with the current bearer + retry-with-backoff on throttle.

        Two layered retry behaviours, both intentionally narrow:

          1. **401 once.** A single ``401 Unauthorized`` invalidates the
             cached token and retries the request once with a freshly
             exchanged bearer. A second 401 raises — the credential is
             genuinely bad and no amount of waiting will help.
          2. **429 / 5xx with backoff.** Throttled (429) and Service
             Unavailable (503) responses honour the server's
             ``Retry-After`` header; other 5xx (500, 502, 504) fall back
             to exponential backoff. ``_DEFAULT_MAX_ATTEMPTS`` total
             attempts (initial call + retries). After exhaustion the
             final response is returned to ``raise_for_status`` which
             converts it to :class:`httpx.HTTPStatusError`.

        Other 4xx responses (e.g. 403 Forbidden, 404 Not Found) raise
        immediately — they're permanent for this URL + credential pair.
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
            # ``retry_if_result`` returns a "successful" outcome from
            # tenacity's perspective, so ``reraise=True`` can't lift an
            # exception (there is none). On stop-condition exhaustion
            # tenacity wraps the final attempt's result in
            # :class:`RetryError`; we lift the underlying response and
            # convert it via ``raise_for_status`` so callers see the
            # same :class:`httpx.HTTPStatusError` shape they did before
            # retry was added.
            final: httpx.Response = exc.last_attempt.result()
            final.raise_for_status()
            return final  # pragma: no cover — raise_for_status above always raises here
        response.raise_for_status()
        return response

    def _authorised_get_once(self, url: str, timeout: float | None) -> httpx.Response:
        """One bearer-authorised GET with the single 401 refresh step.

        Returns the raw :class:`httpx.Response` (never raises on status
        alone); the retry loop in :meth:`_authorised_get` inspects the
        status code via :func:`_is_retryable_response` and either retries
        or hands the response back to the caller for ``raise_for_status``.
        """
        token = self._auth.get_token()
        response = self._do_get(url, token, timeout=timeout)
        if response.status_code == 401:
            logger.info("sharepoint graph: received 401; invalidating token cache and retrying once")
            self._auth.invalidate()
            token = self._auth.get_token()
            response = self._do_get(url, token, timeout=timeout)
        return response

    def _wait_strategy(self, retry_state: RetryCallState) -> float:
        """Compute the wait between retries.

        For 429 / 503 responses honour the server's ``Retry-After`` header
        (seconds). For other retryable statuses (or when ``Retry-After``
        is missing / unparseable) fall back to exponential backoff
        between :data:`_DEFAULT_BACKOFF_MIN_S` and
        :data:`_DEFAULT_BACKOFF_MAX_S`.
        """
        outcome = retry_state.outcome
        if outcome is None or outcome.failed:  # pragma: no cover — exception path bypasses retry_if_result
            return _DEFAULT_BACKOFF_MIN_S
        response = outcome.result()
        retry_after = _parse_retry_after(response) if response.status_code in _THROTTLED_STATUS_CODES else None
        if retry_after is not None:
            logger.warning(
                "sharepoint graph: %s on attempt %d; honouring Retry-After=%.1fs",
                response.status_code,
                retry_state.attempt_number,
                retry_after,
            )
            return retry_after
        backoff = wait_exponential(multiplier=1, min=_DEFAULT_BACKOFF_MIN_S, max=_DEFAULT_BACKOFF_MAX_S)(retry_state)
        logger.warning(
            "sharepoint graph: %s on attempt %d; backing off %.1fs",
            response.status_code,
            retry_state.attempt_number,
            backoff,
        )
        return backoff

    def _do_get(self, url: str, token: str, *, timeout: float | None = None) -> httpx.Response:
        """Single HTTP GET. The bearer string is composed into the
        Authorization header here ONLY; never logged, never returned.

        ``follow_redirects=True`` is critical for the ``/content`` endpoint:
        Graph returns a 302 redirect to a time-limited Azure Blob URL
        rather than the bytes inline. Without this flag the binary fetch
        returns the 302 response itself + ``raise_for_status()`` errors,
        which dead-letters every SharePoint item. Caller-injected clients
        must also enable redirects.
        """
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        effective_timeout = timeout if timeout is not None else _GRAPH_REQUEST_TIMEOUT_S
        client = self._http_client
        if client is not None:
            return client.get(url, headers=headers, timeout=effective_timeout, follow_redirects=True)
        with httpx.Client(timeout=effective_timeout, follow_redirects=True) as owned:
            return owned.get(url, headers=headers)


# ---------------------------------------------------------------------------
# Free-function parsers — kept module-level so tests can pin them
# without constructing a client.
# ---------------------------------------------------------------------------


def _is_retryable_response(response: httpx.Response) -> bool:
    """``True`` when ``response.status_code`` is in
    :data:`_RETRYABLE_STATUS_CODES` (429 + 5xx subset).

    Used by the :class:`Retrying` loop in
    :meth:`SharePointGraphClient._authorised_get` to decide whether the
    request gets retried (with the wait dictated by
    :meth:`SharePointGraphClient._wait_strategy`) or returned to the
    caller for ``raise_for_status``.
    """
    return response.status_code in _RETRYABLE_STATUS_CODES


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Return the ``Retry-After`` header (seconds) as a float, or ``None``.

    Graph emits ``Retry-After`` as an integer second count per
    https://learn.microsoft.com/graph/throttling. The HTTP spec also
    allows an HTTP-date form; this client doesn't see that shape from
    Graph in practice, so we only parse the seconds form and fall back
    to exponential backoff for anything unparseable.
    """
    raw = response.headers.get(_RETRY_AFTER_HEADER)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the Graph collection's ``value`` array (always a list)."""
    raw = body.get("value")
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _site_from(entry: dict[str, Any]) -> SiteRef:
    return SiteRef(
        site_id=_string_or_empty(entry.get("id")),
        display_name=_string_or_empty(entry.get("displayName") or entry.get("name")),
        web_url=_string_or_empty(entry.get("webUrl")),
    )


def _drive_from(entry: dict[str, Any], *, site_id: str) -> DriveRef:
    return DriveRef(
        drive_id=_string_or_empty(entry.get("id")),
        site_id=site_id,
        name=_string_or_empty(entry.get("name")),
        web_url=_string_or_empty(entry.get("webUrl")),
    )


def _parse_delta_page(body: dict[str, Any]) -> DriveDeltaPage:
    """Parse one Graph ``/drives/{drive-id}/root/delta`` response.

    Tolerates the documented shape — ``value`` is the array of drive
    items; ``@odata.nextLink`` advances within the sync window;
    ``@odata.deltaLink`` is the next-tick cursor. Missing fields default
    to ``None`` / empty tuple so a sparse fixture parses cleanly. Folder
    entries (entries carrying a top-level ``folder`` key) are dropped at
    this layer — only file rows yield to the connector, which keeps the
    item enumeration cheap downstream.
    """
    items: list[DriveItemRef] = []
    drive_id_hint = _drive_id_hint(body)
    for entry in _entries(body):
        if _is_folder_entry(entry):
            continue
        items.append(_drive_item_from(entry, drive_id=drive_id_hint))
    next_link = _string_or_none(body.get(_ODATA_NEXT_LINK_KEY))
    delta_link = _string_or_none(body.get("@odata.deltaLink"))
    return DriveDeltaPage(items=tuple(items), next_link=next_link, delta_link=delta_link)


def _drive_item_from(entry: dict[str, Any], *, drive_id: str) -> DriveItemRef:
    """Lift one Graph drive-item envelope into the typed dataclass."""
    removed_block = entry.get("deleted") or entry.get("@removed")
    removed = bool(removed_block)
    file_block = entry.get("file") if isinstance(entry.get("file"), dict) else None
    mime = None
    if file_block is not None:
        mime_candidate = file_block.get("mimeType")
        if isinstance(mime_candidate, str) and mime_candidate:
            mime = mime_candidate
    parent_drive = drive_id
    parent_path: str | None = None
    parent = entry.get("parentReference")
    if isinstance(parent, dict):
        parent_drive_candidate = parent.get("driveId")
        if isinstance(parent_drive_candidate, str) and parent_drive_candidate:
            parent_drive = parent_drive_candidate
        parent_path = _normalise_parent_path(parent.get("path"))
    size_raw = entry.get("size")
    size: int | None = size_raw if isinstance(size_raw, int) else None
    created_by = _user_display_name(entry.get("createdBy"))
    last_modified_by = _user_display_name(entry.get("lastModifiedBy"))
    return DriveItemRef(
        item_id=_string_or_empty(entry.get("id")),
        drive_id=parent_drive,
        name=_string_or_empty(entry.get("name")),
        mime=mime,
        web_url=_string_or_none(entry.get("webUrl")),
        size=size,
        last_modified_at=_string_or_none(entry.get("lastModifiedDateTime")),
        removed=removed,
        parent_path=parent_path,
        created_by=created_by,
        last_modified_by=last_modified_by,
        created_at=_string_or_none(entry.get("createdDateTime")),
    )


def _user_display_name(block: object) -> str | None:
    """Extract ``user.displayName`` from a Graph ``createdBy`` / ``lastModifiedBy`` block.

    Graph emits ``{"user": {"displayName": "...", "email": "..."}}``;
    application-only tokens may surface ``{"application": {...}}``
    instead. Returns ``None`` for missing / malformed blocks so the
    downstream :class:`SourceMetadata` collapses author to ``None``
    cleanly.
    """
    if not isinstance(block, dict):
        return None
    user = block.get("user")
    if not isinstance(user, dict):
        return None
    name = user.get("displayName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _normalise_parent_path(raw: object) -> str | None:
    """Strip Graph's ``/drives/<id>/root:`` prefix from a parentReference.path.

    Graph returns ``/drives/<drive-id>/root:/Curated-Content/foo`` for an
    item under ``/Curated-Content/foo``. The operator-facing form is the
    suffix after ``root:`` — the part they wrote in include_paths /
    exclude_paths. Returns ``None`` for missing or malformed input (the
    filter treats ``None`` as "no path known" and applies the safe rule).
    """
    if not isinstance(raw, str) or not raw:
        return None
    marker = "/root:"
    idx = raw.find(marker)
    if idx == -1:
        return None
    suffix = raw[idx + len(marker) :]
    return suffix or "/"


def _is_folder_entry(entry: dict[str, Any]) -> bool:
    """``True`` for folder rows in a delta response (they carry ``folder``)."""
    return isinstance(entry.get("folder"), dict)


def _drive_id_hint(body: dict[str, Any]) -> str:
    """Pull a drive-id hint from the response wrapper when present.

    The drive-id is also threaded through ``parentReference.driveId`` on
    every drive item, but the response wrapper sometimes carries the
    canonical id at the top level via ``@odata.context``. We fall back
    to the per-item ``parentReference`` block when the wrapper omits it.

    Graph's ``@odata.context`` URL embeds the drive id as either a path
    segment (``.../drives/<id>/...``) or a metadata-fragment segment
    (``.../$metadata#drives/<id>``); both shapes appear in practice
    depending on the surface that emitted the response.
    """
    context = body.get("@odata.context")
    if not isinstance(context, str):
        return ""
    for marker in ("/drives/", "#drives/"):
        idx = context.find(marker)
        if idx < 0:
            continue
        tail = context[idx + len(marker) :]
        end = tail.find("/")
        return tail if end < 0 else tail[:end]
    return ""


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
